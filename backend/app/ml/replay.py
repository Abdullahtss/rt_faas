"""Replay the BankSim file in time order to build a training set.

Features are built *causally*: every row is scored using only this account's
earlier rows, in exactly the order `engine.pipeline.score_payload` uses at
serving time. The rules score is computed in the same pass so the model can be
compared against the hand-written rules on identical inputs.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..engine.windows import (
    AccountWindows,
    Event,
    amount_points,
    travel_points,
    velocity_points,
)
from ..services import banksin
from .features import feature_row

FRAUD_SCENARIO = "banksim-fraud"


def build_dataset() -> Tuple[List[Dict[str, object]], List[int], List[int], List[int]]:
    """Return (features, labels, rule_scores, days) aligned row by row."""
    rows = sorted(banksin.load(), key=lambda r: r.timestamp)
    windows = AccountWindows()
    origin = rows[0].timestamp

    features: List[Dict[str, object]] = []
    labels: List[int] = []
    rule_scores: List[int] = []
    days: List[int] = []

    for row in rows:
        ts = row.timestamp
        prev = windows.last(row.account_id)
        n, mean, std = windows.amount_stats(row.account_id, ts)

        t_pts, _ = travel_points(prev, row.lat, row.lon, ts)
        a_pts, _ = amount_points(row.amount, n, mean, std)
        windows.add(row.account_id, Event(ts, row.lat, row.lon, row.amount))
        v_count = windows.velocity_count(row.account_id, ts)
        v_pts, _ = velocity_points(v_count)

        features.append(
            feature_row(
                amount=row.amount,
                merchant=row.merchant,
                ts=ts,
                lat=row.lat,
                lon=row.lon,
                prev=prev,
                velocity_count=v_count,
                n=n,
                mean=mean,
                std=std,
            )
        )
        labels.append(1 if row.scenario == FRAUD_SCENARIO else 0)
        rule_scores.append(min(100, v_pts + t_pts + a_pts))
        days.append((ts - origin).days)

    return features, labels, rule_scores, days
