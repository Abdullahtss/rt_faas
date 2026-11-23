from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..ml.features import feature_row
from ..ml.model import MODEL, anomaly_level
from ..models import Alert, Review, RiskResult, Transaction
from ..schemas import MlReason, Reason, ScoredTransaction, TransactionIn
from ..security import mask_account
from ..services.hub import HUB, dumps
from .windows import (
    WINDOWS,
    Event,
    amount_points,
    risk_level,
    travel_points,
    velocity_points,
)

_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ml_level(prob: float, threshold: float) -> str:
    """Turn a probability into the same three bands the rules use."""
    if prob >= threshold:
        return "High"
    if prob >= threshold / 2.0:
        return "Medium"
    return "Low"


def _combine(rule_lvl: str, ml_lvl: Optional[str], anom_lvl: Optional[str] = None) -> str:
    """Rules and model both get a veto: the more severe verdict wins.

    The anomaly detector is unsupervised and noisier, so it can only send a
    payment to the review queue (Medium). It never raises a High alert alone.
    """
    level = rule_lvl
    if ml_lvl is not None and _RANK[ml_lvl] > _RANK[level]:
        level = ml_lvl
    if anom_lvl == "High" and _RANK[level] < _RANK["Medium"]:
        level = "Medium"
    return level


def score_payload(payload: TransactionIn) -> ScoredTransaction:
    """Pure in-memory scoring (US-03, US-04, US-05). Does not touch the database."""
    ts = payload.timestamp or _now()
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    prev = WINDOWS.last(payload.account_id)
    n, mean, std = WINDOWS.amount_stats(payload.account_id, ts)
    t_pts, t_detail = travel_points(prev, payload.lat, payload.lon, ts)
    a_pts, a_detail = amount_points(payload.amount, n, mean, std)
    WINDOWS.add(payload.account_id, Event(ts, payload.lat, payload.lon, payload.amount))
    v_count = WINDOWS.velocity_count(payload.account_id, ts)
    v_pts, v_detail = velocity_points(v_count)
    score = min(100, v_pts + t_pts + a_pts)
    reasons = [
        Reason(rule="velocity", points=v_pts, detail=v_detail),
        Reason(rule="travel", points=t_pts, detail=t_detail),
        Reason(rule="amount", points=a_pts, detail=a_detail),
    ]

    # The model sees exactly the window statistics the rules just used, so the
    # features here are built the same way train_model.py built them.
    rule_lvl = risk_level(score)
    ml_score = None
    ml_flag = False
    ml_lvl = None
    ml_reasons: list = []
    anom_pct = None
    feats_json = None
    if MODEL.available:
        feats = feature_row(
            amount=payload.amount,
            merchant=payload.merchant,
            ts=ts,
            lat=payload.lat,
            lon=payload.lon,
            prev=prev,
            velocity_count=v_count,
            n=n,
            mean=mean,
            std=std,
        )
        prob = MODEL.probability(feats)
        if prob is not None:
            ml_score = int(round(prob * 100))
            ml_flag = prob >= MODEL.threshold
            ml_lvl = _ml_level(prob, MODEL.threshold)
            # Explaining costs an extra batch of predictions, so only do it for
            # transactions an analyst will actually open.
            if ml_lvl in ("Medium", "High"):
                ml_reasons = [MlReason(**r) for r in MODEL.explain(feats)]
        anom_pct = MODEL.anomaly(feats)
        feats_json = json.dumps(feats)
    anom_lvl = anomaly_level(anom_pct)

    return ScoredTransaction(
        id=str(uuid.uuid4()),
        account_id=payload.account_id,
        account_masked=mask_account(payload.account_id),
        amount=round(payload.amount, 2),
        currency=payload.currency,
        timestamp=ts,
        lat=payload.lat,
        lon=payload.lon,
        city=payload.city,
        merchant=payload.merchant,
        channel=payload.channel,
        scenario=payload.scenario,
        score=score,
        level=_combine(rule_lvl, ml_lvl, anom_lvl),  # type: ignore[arg-type]
        rule_level=rule_lvl,  # type: ignore[arg-type]
        velocity_pts=v_pts,
        travel_pts=t_pts,
        amount_pts=a_pts,
        reasons=reasons,
        processing_ms=0.0,
        ml_score=ml_score,
        ml_flag=ml_flag,
        ml_level=ml_lvl,  # type: ignore[arg-type]
        ml_reasons=ml_reasons,
        anomaly_pct=anom_pct,
        anomaly_level=anom_lvl,  # type: ignore[arg-type]
    ), feats_json


def _copy(obj):
    """Fresh, unsaved copy of an ORM row (the original is spent after a rollback)."""
    cols = {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key != "id" or isinstance(obj, Transaction)}
    return type(obj)(**cols)


def to_ws_dict(item: ScoredTransaction) -> dict:
    data = item.model_dump()
    data["timestamp"] = item.timestamp.isoformat()
    data["reasons"] = [r.model_dump() for r in item.reasons]
    return data


async def persist_and_publish(
    session: AsyncSession, item: ScoredTransaction, started: float, feats_json: Optional[str] = None
) -> ScoredTransaction:
    item.processing_ms = round((time.perf_counter() - started) * 1000.0, 2)
    txn = Transaction(
        id=item.id,
        account_id=item.account_id,
        amount=item.amount,
        currency=item.currency,
        ts=item.timestamp,
        lat=item.lat,
        lon=item.lon,
        city=item.city,
        merchant=item.merchant,
        channel=item.channel,
        ingested_at=_now(),
        processing_ms=item.processing_ms,
        scenario=item.scenario,
    )
    risk = RiskResult(
        transaction_id=item.id,
        score=item.score,
        level=item.level,
        velocity_pts=item.velocity_pts,
        travel_pts=item.travel_pts,
        amount_pts=item.amount_pts,
        reasons=json.dumps([r.model_dump() for r in item.reasons]),
        ml_score=item.ml_score,
        ml_flag=1 if item.ml_flag else 0,
        ml_reasons=json.dumps([r.model_dump() for r in item.ml_reasons]),
        anomaly_pct=item.anomaly_pct,
        features=feats_json,
    )
    # Alert sequence numbers come from an in-memory counter. If another
    # server process sharing this database used the same number first, the
    # unique constraint rejects it: re-read the highest number and try again.
    for attempt in range(3):
        session.add(txn)
        session.add(risk)
        if item.level == "High":
            item.seq = HUB.next_seq()
            session.add(
                Alert(
                    seq=item.seq,
                    transaction_id=item.id,
                    payload=dumps(to_ws_dict(item)),
                    created_at=_now(),
                )
            )
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if item.level != "High" or attempt == 2:
                raise
            HUB.load_seq(await load_max_seq(session))
            txn = _copy(txn)
            risk = _copy(risk)
    await HUB.broadcast({"type": "transaction", "data": to_ws_dict(item)})
    if item.level == "High":
        await HUB.broadcast({"type": "alert", "data": to_ws_dict(item)})
    return item


async def ingest(session: AsyncSession, payload: TransactionIn) -> ScoredTransaction:
    started = time.perf_counter()
    item, feats_json = score_payload(payload)
    return await persist_and_publish(session, item, started, feats_json)


def row_to_scored(txn: Transaction) -> ScoredTransaction:
    risk = txn.risk
    reasons = []
    ml_reasons = []
    if risk:
        try:
            reasons = [Reason(**r) for r in json.loads(risk.reasons)]
        except Exception:
            reasons = []
        try:
            ml_reasons = [MlReason(**r) for r in json.loads(risk.ml_reasons or "[]")]
        except Exception:
            ml_reasons = []
    review = txn.review.decision if txn.review else None
    notes = txn.review.notes if txn.review else None
    return ScoredTransaction(
        id=txn.id,
        account_id=txn.account_id,
        account_masked=mask_account(txn.account_id),
        amount=txn.amount,
        currency=txn.currency,
        timestamp=txn.ts,
        lat=txn.lat,
        lon=txn.lon,
        city=txn.city,
        merchant=txn.merchant,
        channel=txn.channel,
        scenario=txn.scenario,
        score=risk.score if risk else 0,
        level=(risk.level if risk else "Low"),  # type: ignore[arg-type]
        velocity_pts=risk.velocity_pts if risk else 0,
        travel_pts=risk.travel_pts if risk else 0,
        amount_pts=risk.amount_pts if risk else 0,
        reasons=reasons,
        processing_ms=txn.processing_ms,
        seq=txn.alert.seq if txn.alert else None,
        review=review,
        review_notes=notes,
        ml_score=risk.ml_score if risk else None,
        ml_flag=bool(risk.ml_flag) if risk and risk.ml_flag is not None else False,
        ml_level=(
            _ml_level(risk.ml_score / 100.0, MODEL.threshold)
            if risk and risk.ml_score is not None and MODEL.available
            else None
        ),
        ml_reasons=ml_reasons,
        rule_level=risk_level(risk.score) if risk else None,  # type: ignore[arg-type]
        anomaly_pct=risk.anomaly_pct if risk else None,
        anomaly_level=anomaly_level(risk.anomaly_pct) if risk else None,  # type: ignore[arg-type]
    )


async def load_max_seq(session: AsyncSession) -> int:
    result = await session.execute(select(func.max(Alert.seq)))
    value = result.scalar_one_or_none()
    return int(value or 0)


async def replay_alerts(session: AsyncSession, last_seq: int) -> list:
    result = await session.execute(
        select(Alert).where(Alert.seq > last_seq).order_by(Alert.seq.asc()).limit(500)
    )
    out = []
    for alert in result.scalars():
        try:
            out.append(json.loads(alert.payload))
        except Exception:
            continue
    return out


async def get_transaction(session: AsyncSession, txn_id: str) -> Optional[Transaction]:
    result = await session.execute(
        select(Transaction)
        .options(selectinload(Transaction.risk), selectinload(Transaction.review), selectinload(Transaction.alert))
        .where(Transaction.id == txn_id)
    )
    return result.scalar_one_or_none()
