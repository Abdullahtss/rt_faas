"""Numbers for the model monitoring page.

* Traffic: payments and alerts per minute over the last 30 minutes.
* Verdict sources: which detector raised each alert (rules, model, both,
  anomaly detector).
* Analyst-confirmed precision: of the alerts each detector raised that an
  analyst reviewed, how many did they block? This is the live, real-world
  counterpart of the offline test precision.
* Drift: Population Stability Index (PSI) between the model inputs seen live
  and the training days. PSI < 0.1 is stable, 0.1-0.25 is worth watching,
  > 0.25 means live traffic no longer looks like the training data.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import log
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..engine.windows import risk_level
from ..ml.features import split_merchant
from ..ml.model import ANOMALY_HIGH_PCT, MODEL
from ..models import Review, RiskResult, Transaction

WINDOW_ROWS = 3000
DRIFT_ROWS = 2000
MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def psi(expected: List[float], actual: List[float]) -> float:
    total = 0.0
    for e, a in zip(expected, actual):
        e, a = max(e, 1e-4), max(a, 1e-4)
        total += (a - e) * log(a / e)
    return total


def _drift(rows: List[str]) -> List[dict]:
    ref = MODEL.reference
    if not ref or not rows:
        return []
    import bisect

    feats = []
    for raw in rows:
        try:
            feats.append(json.loads(raw))
        except Exception:
            continue
    out = []
    for name, spec in ref.items():
        edges = spec["edges"]
        counts = [0] * (len(edges) + 1)
        n = 0
        for f in feats:
            if name in f:
                counts[bisect.bisect_right(edges, float(f[name]))] += 1
                n += 1
        if not n:
            continue
        live = [c / n for c in counts]
        value = psi(spec["frac"], live)
        out.append({
            "feature": name,
            "psi": round(value, 3),
            "status": "stable" if value < 0.1 else ("watch" if value < 0.25 else "drifted"),
            "train": [round(x, 3) for x in spec["frac"]],
            "live": [round(x, 3) for x in live],
            "rows": n,
        })
    out.sort(key=lambda d: -d["psi"])
    return out


async def snapshot(session: AsyncSession) -> Dict:
    rows = (await session.execute(
        select(
            Transaction.ingested_at, Transaction.merchant, Transaction.processing_ms,
            RiskResult.level, RiskResult.score, RiskResult.ml_flag, RiskResult.anomaly_pct,
            RiskResult.features,
        )
        .join(RiskResult, RiskResult.transaction_id == Transaction.id)
        .order_by(Transaction.ingested_at.desc())
        .limit(WINDOW_ROWS)
    )).all()

    now = _now()
    start = now - timedelta(minutes=MINUTES)
    per_min = [{"t": (start + timedelta(minutes=i + 1)).strftime("%H:%M"), "total": 0, "medium": 0, "high": 0}
               for i in range(MINUTES)]
    levels = Counter()
    high_by_cat = Counter()
    sources = Counter()
    latencies = []
    for ingested, merchant, ms, level, score, ml_flag, anom, _ in rows:
        levels[level] += 1
        if ms:
            latencies.append(ms)
        if ingested and ingested > start:
            i = min(MINUTES - 1, int((ingested - start).total_seconds() // 60))
            per_min[i]["total"] += 1
            if level == "High":
                per_min[i]["high"] += 1
            elif level == "Medium":
                per_min[i]["medium"] += 1
        rules_high = risk_level(score) == "High"
        model_high = bool(ml_flag)
        if level == "High":
            high_by_cat[split_merchant(merchant or "")[0]] += 1
            if rules_high and model_high:
                sources["both"] += 1
            elif model_high:
                sources["model only"] += 1
            elif rules_high:
                sources["rules only"] += 1
        if anom is not None and anom >= ANOMALY_HIGH_PCT:
            sources["anomaly flagged"] += 1

    latencies.sort()

    def pct(p):
        return round(latencies[min(len(latencies) - 1, int(len(latencies) * p))], 1) if latencies else None

    # Analyst-confirmed precision per detector.
    reviewed = (await session.execute(
        select(Review.decision, RiskResult.score, RiskResult.ml_flag, RiskResult.anomaly_pct)
        .join(RiskResult, RiskResult.transaction_id == Review.transaction_id)
    )).all()
    confirm = {k: {"reviewed": 0, "blocked": 0} for k in ("rules", "model", "anomaly")}
    for decision, score, ml_flag, anom in reviewed:
        hits = {
            "rules": risk_level(score) == "High",
            "model": bool(ml_flag),
            "anomaly": anom is not None and anom >= ANOMALY_HIGH_PCT,
        }
        for k, hit in hits.items():
            if hit:
                confirm[k]["reviewed"] += 1
                confirm[k]["blocked"] += 1 if decision == "blocked" else 0
    for v in confirm.values():
        v["precision"] = round(v["blocked"] / v["reviewed"], 3) if v["reviewed"] else None

    decisions = Counter(d for d, *_ in reviewed)
    total_txn = (await session.execute(select(func.count(Transaction.id)))).scalar_one()

    return {
        "generated_at": now.strftime("%H:%M:%S"),
        "window_rows": len(rows),
        "total_transactions": int(total_txn),
        "levels": {k: levels.get(k, 0) for k in ("Low", "Medium", "High")},
        "per_minute": per_min,
        "high_by_category": [{"category": c, "count": n} for c, n in high_by_cat.most_common(8)],
        "sources": dict(sources),
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": latencies[-1] if latencies else None},
        "feedback": {
            "reviewed": len(reviewed),
            "blocked": decisions.get("blocked", 0),
            "approved": decisions.get("approved", 0),
            "confirmed": confirm,
        },
        "drift": _drift([r[7] for r in rows[:DRIFT_ROWS] if r[7]]),
    }
