"""Feature extraction shared by offline training and the live scoring path.

Both `train_model.py` and `engine.pipeline.score_payload` call `feature_row`
with window statistics computed the same way, so a feature can never be built
one way during training and another way at serving time (train/serve skew).
"""
from __future__ import annotations

from datetime import datetime
from math import log1p
from typing import Dict, Optional, Tuple

from ..engine.geo import implied_speed_kmh
from ..engine.windows import Event

# Numeric features are passed through to the model as-is.
NUMERIC_FEATURES = [
    "log_amount",
    "amount_z",
    "has_baseline",
    "amount_over_mean",
    "velocity_10m",
    "implied_speed",
    "log_gap_seconds",
    "has_prev",
    "history_count",
    "hour",
    "is_night",
]
# String features are one-hot encoded by the DictVectorizer in the pipeline.
CATEGORICAL_FEATURES = ["category", "merchant_id"]

SPEED_CAP = 2000.0
RATIO_CAP = 50.0
NIGHT_START, NIGHT_END = 23, 6


def split_merchant(merchant: str) -> Tuple[str, str]:
    """'travel - M1823072687' -> ('travel', 'M1823072687').

    BankSim rows reach the API with the category and merchant id joined by a
    middle dot. Anything else is treated as a bare merchant id.
    """
    if "\u00b7" in merchant:
        left, _, right = merchant.partition("\u00b7")
        return left.strip().lower(), right.strip()
    return "unknown", merchant.strip()


def feature_row(
    amount: float,
    merchant: str,
    ts: datetime,
    lat: float,
    lon: float,
    prev: Optional[Event],
    velocity_count: int,
    n: int,
    mean: float,
    std: float,
) -> Dict[str, object]:
    """Build one feature dict from a transaction plus that account's windows.

    `prev`, `n`, `mean` and `std` describe the account *before* this
    transaction; `velocity_count` includes it, matching the rules engine.
    """
    category, merchant_id = split_merchant(merchant)
    has_baseline = 1 if (n >= 5 and std > 0) else 0
    amount_z = (amount - mean) / std if has_baseline else 0.0
    amount_over_mean = min(RATIO_CAP, amount / mean) if mean > 0 else 0.0

    if prev is None:
        implied = 0.0
        log_gap = -1.0
        has_prev = 0
    else:
        seconds = (ts - prev.ts).total_seconds()
        implied = min(SPEED_CAP, implied_speed_kmh(prev.lat, prev.lon, lat, lon, seconds))
        log_gap = log1p(max(0.0, seconds))
        has_prev = 1

    hour = ts.hour
    return {
        "log_amount": log1p(amount),
        "amount_z": float(amount_z),
        "has_baseline": has_baseline,
        "amount_over_mean": float(amount_over_mean),
        "velocity_10m": float(velocity_count),
        "implied_speed": float(implied),
        "log_gap_seconds": float(log_gap),
        "has_prev": has_prev,
        "history_count": float(n),
        "hour": float(hour),
        "is_night": 1 if (hour >= NIGHT_START or hour < NIGHT_END) else 0,
        "category": category,
        "merchant_id": merchant_id,
    }
