"""Analyst feedback loop: turn Approve / Block decisions into a better model.

1. Collect every reviewed payment that has stored model inputs.
   Blocked -> label 1 (fraud), Approved -> label 0 (genuine).
2. Drop BankSim rows that come from the validation or test days. Those days
   judge the model; letting their labels into training would inflate the
   score and make the old-vs-new comparison unfair.
3. Retrain in a background thread (the server keeps scoring meanwhile).
4. Save the result as a new version. Promote it only if its held-out PR-AUC
   is not worse than the serving model's; then hot-swap it, no restart.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select

from ..services.banksin import BASE
from . import registry
from .model import MODEL
from .training import TRAIN_END_DAY, train


class RetrainJob:
    def __init__(self) -> None:
        self.state = "idle"          # idle | running | done | failed
        self.stage = ""
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.log: List[str] = []
        self.result: Optional[dict] = None
        self.error = ""
        self._task: Optional[asyncio.Task] = None

    def status(self) -> dict:
        return {
            "state": self.state,
            "stage": self.stage,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": self.log[-14:],
            "result": self.result,
            "error": self.error,
        }

    def _log(self, msg: str) -> None:
        self.log.append(msg.strip())

    def _stage(self, msg: str) -> None:
        self.stage = msg

    async def start(self, session_factory) -> dict:
        if self.state == "running":
            return self.status()
        self.state = "running"
        self.stage = "Collecting analyst decisions"
        self.started_at = time.strftime("%H:%M:%S")
        self.finished_at = None
        self.log, self.result, self.error = [], None, ""
        self._task = asyncio.create_task(self._run(session_factory))
        return self.status()

    async def _run(self, session_factory) -> None:
        try:
            async with session_factory()() as session:
                feedback, summary = await collect_feedback(session)
            self._log("Analyst decisions: %d usable, %d kept out (validation/test days), "
                      "%d without stored inputs" % (summary["used"], summary["excluded_eval_days"],
                                                    summary["missing_features"]))
            old = registry.current_metrics()
            bundle, metrics = await asyncio.to_thread(
                train, feedback, ("gradient_boosting",), self._log, self._stage
            )
            metrics["feedback"].update(summary)
            self.stage = "Comparing with the serving model"
            entry = await asyncio.to_thread(registry.record, bundle, metrics, "retrain button")
            if entry["promoted"]:
                MODEL.load()
            self.result = {
                "entry": entry,
                "old": _brief(old),
                "new": _brief(metrics),
                "feedback": metrics["feedback"],
            }
            self.state = "done"
            self.stage = "Promoted v%d" % entry["version"] if entry["promoted"] else \
                "Kept the current model (v%d was worse)" % entry["version"]
        except Exception as exc:  # pragma: no cover - surfaced in the UI
            self.state = "failed"
            self.error = str(exc)
            self.stage = "Failed"
        finally:
            self.finished_at = time.strftime("%H:%M:%S")


def _brief(metrics: Optional[dict]) -> Optional[dict]:
    if not metrics:
        return None
    t = metrics["test"]["model"]
    return {
        "version": metrics.get("version"),
        "pr_auc": t["pr_auc"],
        "precision": t["precision"],
        "recall": t["recall"],
        "f1": t["f1"],
        "threshold": metrics["threshold"],
    }


async def collect_feedback(session) -> tuple:
    from ..models import Review, RiskResult, Transaction

    rows = (await session.execute(
        select(Review.decision, RiskResult.features, Transaction.scenario, Transaction.ts)
        .join(Transaction, Transaction.id == Review.transaction_id)
        .join(RiskResult, RiskResult.transaction_id == Review.transaction_id)
    )).all()
    feedback, excluded, missing = [], 0, 0
    for decision, features, scenario, ts in rows:
        if not features:
            missing += 1
            continue
        if scenario.startswith("banksim") and isinstance(ts, datetime) and (ts - BASE).days > TRAIN_END_DAY:
            excluded += 1
            continue
        feedback.append({"features": json.loads(features), "label": 1 if decision == "blocked" else 0})
    blocked = sum(f["label"] for f in feedback)
    return feedback, {
        "reviewed": len(rows),
        "used": len(feedback),
        "blocked": blocked,
        "approved": len(feedback) - blocked,
        "excluded_eval_days": excluded,
        "missing_features": missing,
    }


RETRAIN = RetrainJob()
