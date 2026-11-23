from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from .geo import implied_speed_kmh

VELO_WINDOW = timedelta(minutes=10)
VELO_LIMIT = 5
VELO_PER_EXTRA = 8
VELO_CAP = 40
TRAVEL_MED = 500.0
TRAVEL_HIGH = 900.0
AMOUNT_WINDOW = timedelta(days=30)


@dataclass
class Event:
    ts: datetime
    lat: float
    lon: float
    amount: float


class AccountWindows:
    """In-memory sliding windows used on the hot scoring path."""

    def __init__(self) -> None:
        self._events: Dict[str, Deque[Event]] = defaultdict(deque)

    def history(self, account_id: str) -> Deque[Event]:
        return self._events[account_id]

    def last(self, account_id: str) -> Optional[Event]:
        hist = self._events[account_id]
        return hist[-1] if hist else None

    def seed(self, account_id: str, events: Iterable[Event]) -> None:
        dq = self._events[account_id]
        dq.clear()
        for ev in events:
            dq.append(ev)

    def add(self, account_id: str, event: Event) -> None:
        dq = self._events[account_id]
        dq.append(event)
        cutoff = event.ts - AMOUNT_WINDOW
        while dq and dq[0].ts < cutoff:
            dq.popleft()

    def velocity_count(self, account_id: str, now: datetime) -> int:
        cutoff = now - VELO_WINDOW
        return sum(1 for ev in self._events[account_id] if ev.ts >= cutoff)

    def amount_stats(self, account_id: str, now: datetime) -> Tuple[int, float, float]:
        cutoff = now - AMOUNT_WINDOW
        amounts: List[float] = [ev.amount for ev in self._events[account_id] if ev.ts >= cutoff]
        n = len(amounts)
        if n == 0:
            return 0, 0.0, 0.0
        mean = sum(amounts) / n
        if n < 5:
            return n, mean, 0.0
        var = sum((a - mean) ** 2 for a in amounts) / (n - 1)
        return n, mean, sqrt(var)


WINDOWS = AccountWindows()


def velocity_points(count_including_current: int) -> Tuple[int, str]:
    extra = max(0, count_including_current - VELO_LIMIT)
    pts = min(VELO_CAP, extra * VELO_PER_EXTRA)
    detail = "%d transactions in 10 minutes (limit %d)" % (count_including_current, VELO_LIMIT)
    return pts, detail


def travel_points(prev: Optional[Event], lat: float, lon: float, ts: datetime) -> Tuple[int, str]:
    if prev is None:
        return 0, "No prior transaction on this account"
    seconds = (ts - prev.ts).total_seconds()
    speed = implied_speed_kmh(prev.lat, prev.lon, lat, lon, seconds)
    if speed > TRAVEL_HIGH:
        return 50, "Implied speed %.0f km/h (> 900)" % speed
    if speed > TRAVEL_MED:
        return 25, "Implied speed %.0f km/h (> 500)" % speed
    return 0, "Implied speed %.0f km/h (normal travel)" % speed


def amount_points(amount: float, n: int, mean: float, std: float) -> Tuple[int, str]:
    if n < 5 or std <= 0:
        return 0, "Not enough history for amount baseline"
    z = (amount - mean) / std
    if z > 3:
        return 30, "Amount %.0f is %.1fσ above 30-day mean %.0f" % (amount, z, mean)
    if z > 2:
        return 15, "Amount %.0f is %.1fσ above 30-day mean %.0f" % (amount, z, mean)
    return 0, "Amount within 2σ of baseline (z=%.1f)" % z


def risk_level(score: int) -> str:
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"
