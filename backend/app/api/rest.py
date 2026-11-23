from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session, session_factory
from ..engine.pipeline import get_transaction, row_to_scored
from ..ml import registry
from ..ml.model import MODEL
from ..ml.retrain import RETRAIN, collect_feedback
from ..models import Review, RiskResult, Transaction
from ..schemas import ReviewIn, ThresholdIn
from ..security import require_token
from ..services import monitor
from ..services.hub import HUB
from ..services.simulator import SIMULATOR

router = APIRouter()


def _base_query():
    return (
        select(Transaction)
        .options(selectinload(Transaction.risk), selectinload(Transaction.review), selectinload(Transaction.alert))
        .order_by(Transaction.ingested_at.desc())
    )


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "RT-FAAS",
        "ws_clients": HUB.client_count,
        "alert_seq": HUB.seq,
        "simulator": SIMULATOR.status(),
        "model": MODEL.info(),
    }


@router.get("/model")
async def model_info():
    """What model is serving, and how it scored on the held-out test days."""
    info = MODEL.info()
    metrics_path = Path(__file__).resolve().parents[3] / "models" / "metrics.json"
    if metrics_path.exists():
        try:
            info["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            info["metrics"] = None
    else:
        info["metrics"] = None
    return info


@router.get("/model/history")
async def model_history():
    """Every trained version, newest last, with whether it was promoted."""
    return registry.history()


@router.post("/model/threshold")
async def set_threshold(body: ThresholdIn, _: str = Depends(require_token)):
    """Move the model's alert threshold live (null restores the trained one)."""
    if not MODEL.available:
        raise HTTPException(409, "No model loaded")
    MODEL.set_threshold(body.threshold)
    return MODEL.info()


@router.get("/model/retrain")
async def retrain_status(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    _fb, summary = await collect_feedback(session)
    status = RETRAIN.status()
    status["feedback"] = summary
    return status


@router.post("/model/retrain")
async def retrain(_: str = Depends(require_token)):
    """Retrain on BankSim plus analyst decisions, in the background."""
    return await RETRAIN.start(session_factory)


@router.get("/monitor")
async def monitor_snapshot(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    data = await monitor.snapshot(session)
    data["model"] = MODEL.info()
    data["retrain"] = RETRAIN.status()["state"]
    return data


@router.get("/transactions")
async def list_transactions(
    account_id: Optional[str] = None,
    level: Optional[str] = None,
    decision: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = Query(default=80, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    q = _base_query()
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if date_from:
        q = q.where(Transaction.ts >= date_from)
    if date_to:
        q = q.where(Transaction.ts <= date_to)
    if level:
        q = q.join(RiskResult).where(RiskResult.level == level)
    if decision:
        q = q.join(Review).where(Review.decision == decision)
    q = q.limit(limit).offset(offset)
    rows = (await session.execute(q)).scalars().unique().all()
    return [row_to_scored(r) for r in rows]


@router.get("/transactions/{txn_id}")
async def one_transaction(
    txn_id: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    row = await get_transaction(session, txn_id)
    if not row:
        raise HTTPException(404, "Transaction not found")
    return row_to_scored(row)


@router.get("/reviews/queue")
async def review_queue(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    q = (
        _base_query()
        .join(RiskResult)
        .where(RiskResult.level.in_(["Medium", "High"]))
        .where(~Transaction.review.has())
        .limit(80)
    )
    rows = (await session.execute(q)).scalars().unique().all()
    return [row_to_scored(r) for r in rows]


@router.post("/transactions/{txn_id}/review")
async def review_transaction(
    txn_id: str,
    body: ReviewIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    row = await get_transaction(session, txn_id)
    if not row:
        raise HTTPException(404, "Transaction not found")
    if row.review:
        raise HTTPException(409, "Already reviewed")
    session.add(
        Review(
            transaction_id=txn_id,
            decision=body.decision,
            notes=body.notes,
            reviewer=body.reviewer,
            created_at=datetime.utcnow(),
        )
    )
    await session.commit()
    session.expire_all()
    row = await get_transaction(session, txn_id)
    scored = row_to_scored(row)
    await HUB.broadcast({"type": "review", "data": scored.model_dump(mode="json")})
    return scored


@router.get("/export.csv")
async def export_csv(
    account_id: Optional[str] = None,
    level: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    q = _base_query().limit(1000)
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if level:
        q = q.join(RiskResult).where(RiskResult.level == level)
    rows = (await session.execute(q)).scalars().unique().all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "account_id", "amount", "timestamp", "city", "merchant", "score", "level", "reasons", "review"]
    )
    for r in rows:
        item = row_to_scored(r)
        writer.writerow(
            [
                item.id,
                item.account_id,
                item.amount,
                item.timestamp.isoformat(),
                item.city,
                item.merchant,
                item.score,
                item.level,
                "; ".join("%s +%d" % (x.rule, x.points) for x in item.reasons if x.points),
                item.review or "",
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rtfaas-audit.csv"},
    )
