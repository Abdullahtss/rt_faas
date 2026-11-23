"""Replay the public BankSim payment file (Lopez-Rojas & Axelsson, 2014).

The published CSV is synthetic card payments calibrated on a Spanish bank.
Every zip code in the file is 28007, so every payment is placed in Madrid.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..engine.windows import WINDOWS, Event
from ..schemas import TransactionIn

CSV_PATH = Path(__file__).resolve().parents[3] / "data" / "bs140513_032310.csv"
MADRID = (40.4168, -3.7038)
BASE = datetime(2012, 11, 1)

_rows: Optional[List[TransactionIn]] = None
_fraud_at: Optional[List[int]] = None


def _clean(value: str) -> str:
    return (value or "").strip().strip("'").strip('"')


def _place(merchant: str) -> Tuple[float, float]:
    """Keep the payment in Madrid, with a small stable offset per merchant."""
    total = sum(ord(ch) for ch in merchant)
    nudge = (total % 1000) / 1000.0
    return MADRID[0] + (nudge - 0.5) * 0.08, MADRID[1] + (((total * 17) % 1000) / 1000.0 - 0.5) * 0.08


def load() -> List[TransactionIn]:
    global _rows, _fraud_at
    if _rows is not None:
        return _rows
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            "BankSim file missing at %s. Download bs140513_032310.csv into the data folder." % CSV_PATH
        )
    raw = []
    with CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            amount = float(row["amount"])
            if amount <= 0:
                continue
            raw.append(
                (
                    int(row["step"]),
                    _clean(row["customer"]),
                    _clean(row["merchant"]),
                    _clean(row["category"]).replace("es_", "").replace("_", " "),
                    round(amount, 2),
                    int(row["fraud"]),
                )
            )
    per_step = Counter(item[0] for item in raw)
    seen = Counter()
    baselines: Dict[str, List[float]] = defaultdict(list)
    rows: List[TransactionIn] = []
    fraud_at: List[int] = []
    for step, customer, merchant, category, amount, fraud in raw:
        seen[step] += 1
        seconds = int((seen[step] - 1) * 20 * 3600 / max(per_step[step], 1))
        lat, lon = _place(merchant)
        if not fraud and len(baselines[customer]) < 40:
            baselines[customer].append(amount)
        if fraud:
            fraud_at.append(len(rows))
        rows.append(
            TransactionIn(
                account_id=customer,
                amount=amount,
                currency="EUR",
                timestamp=BASE + timedelta(days=step, seconds=seconds),
                lat=round(lat, 5),
                lon=round(lon, 5),
                city="Madrid",
                merchant="%s · %s" % (category, merchant),
                channel="card",
                scenario="banksim-fraud" if fraud else "banksim",
            )
        )
    _seed_baselines(baselines)
    _rows = rows
    _fraud_at = fraud_at
    return rows


def fraud_indexes() -> List[int]:
    load()
    return _fraud_at or []


def _seed_baselines(baselines: Dict[str, List[float]]) -> None:
    """Give the amount rule a 30-day view of each customer's normal BankSim spend."""
    for customer, amounts in baselines.items():
        if len(amounts) < 5:
            continue
        events = []
        for day, amount in enumerate(amounts[:30], start=1):
            events.append(Event(BASE - timedelta(days=day), MADRID[0], MADRID[1], amount))
        WINDOWS.seed(customer, events)
