from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..engine.pipeline import ingest
from ..engine.windows import WINDOWS, Event
from ..schemas import TransactionIn
from ..services.banksin import fraud_indexes, load as load_banksin
from ..services.hub import HUB

CITIES = [
    ("Karachi", 24.8607, 67.0011),
    ("Lahore", 31.5204, 74.3587),
    ("Islamabad", 33.6844, 73.0479),
    ("Faisalabad", 31.4504, 73.1350),
    ("Peshawar", 34.0151, 71.5249),
]
FAR_CITIES = [
    ("London", 51.5074, -0.1278),
    ("Dubai", 25.2048, 55.2708),
    ("New York", 40.7128, -74.0060),
]
MERCHANTS = [
    "Daraz", "Foodpanda", "PSO Fuel", "Metro Cash", "Careem",
    "Netflix", "Sastaticket", "McDonald's", "Shell", "Apple Pay",
]


def _accounts() -> List[Dict]:
    rows = []
    for i in range(1, 21):
        city = CITIES[i % len(CITIES)]
        mean = random.choice([2500, 4000, 6500, 9000, 12000])
        rows.append(
            {
                "id": "ACC-%04d" % (1000 + i),
                "city": city[0],
                "lat": city[1],
                "lon": city[2],
                "mean": mean,
            }
        )
    return rows


ACCOUNTS = _accounts()


def seed_windows() -> None:
    """Give every account a 30-day amount baseline so US-05 works on day one."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for acc in ACCOUNTS:
        events = []
        for d in range(30, 0, -1):
            ts = now - timedelta(days=d, hours=random.randint(0, 20))
            jitter = acc["mean"] * random.uniform(-0.25, 0.25)
            events.append(
                Event(
                    ts=ts,
                    lat=acc["lat"] + random.uniform(-0.02, 0.02),
                    lon=acc["lon"] + random.uniform(-0.02, 0.02),
                    amount=max(200.0, acc["mean"] + jitter),
                )
            )
        WINDOWS.seed(acc["id"], events)


class Simulator:
    def __init__(self) -> None:
        self.running = False
        self.tps = 2.0
        self._task: Optional[asyncio.Task] = None
        self.produced = 0
        self.cursor = 0
        self.fraud_cursor = 0
        self.source = "BankSim"

    def status(self) -> dict:
        return {
            "running": self.running,
            "tps": self.tps,
            "produced": self.produced,
            "cursor": self.cursor,
            "source": self.source,
            "ws_clients": HUB.client_count,
        }

    def start(self, session_factory, tps: float) -> None:
        self.tps = tps
        if self.running:
            return
        self.running = True
        self.source = "BankSim"
        self._task = asyncio.create_task(self._banksin_loop(session_factory))

    def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def _normal(self) -> TransactionIn:
        acc = random.choice(ACCOUNTS)
        jitter = acc["mean"] * random.uniform(-0.3, 0.4)
        return TransactionIn(
            account_id=acc["id"],
            amount=round(max(150.0, acc["mean"] + jitter), 2),
            lat=acc["lat"] + random.uniform(-0.03, 0.03),
            lon=acc["lon"] + random.uniform(-0.03, 0.03),
            city=acc["city"],
            merchant=random.choice(MERCHANTS),
            scenario="normal",
        )

    async def _banksin_loop(self, session_factory) -> None:
        try:
            rows = await asyncio.to_thread(load_banksin)
            while self.running and self.cursor < len(rows):
                async with session_factory() as session:
                    await ingest(session, rows[self.cursor])
                self.cursor += 1
                self.produced += 1
                await asyncio.sleep(max(0.05, 1.0 / self.tps))
        except asyncio.CancelledError:
            return
        finally:
            self.running = False

    async def next_fraud(self, session: AsyncSession):
        rows = await asyncio.to_thread(load_banksin)
        indexes = fraud_indexes()
        if self.fraud_cursor >= len(indexes):
            self.fraud_cursor = 0
        payload = rows[indexes[self.fraud_cursor]]
        self.fraud_cursor += 1
        item = await ingest(session, payload)
        self.produced += 1
        return item

    async def _loop(self, session_factory) -> None:
        try:
            while self.running:
                async with session_factory() as session:
                    await ingest(session, self._normal())
                    self.produced += 1
                await asyncio.sleep(max(0.05, 1.0 / self.tps))
        except asyncio.CancelledError:
            return

    async def inject(self, session: AsyncSession, scenario: str, account_id: Optional[str] = None) -> list:
        acc = next((a for a in ACCOUNTS if a["id"] == account_id), None) or random.choice(ACCOUNTS)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payloads: List[TransactionIn] = []
        if scenario in ("burst", "combo"):
            for i in range(8):
                payloads.append(
                    TransactionIn(
                        account_id=acc["id"],
                        amount=round(acc["mean"] * random.uniform(0.8, 1.2), 2),
                        timestamp=now + timedelta(seconds=i * 0.4),
                        lat=acc["lat"],
                        lon=acc["lon"],
                        city=acc["city"],
                        merchant="ATM Withdrawal",
                        scenario="burst",
                    )
                )
        if scenario in ("travel", "combo"):
            far = random.choice(FAR_CITIES)
            payloads.append(
                TransactionIn(
                    account_id=acc["id"],
                    amount=round(acc["mean"] * 1.1, 2),
                    timestamp=now + timedelta(seconds=4),
                    lat=far[1],
                    lon=far[2],
                    city=far[0],
                    merchant="Airport Duty Free",
                    scenario="travel",
                )
            )
        if scenario in ("amount", "combo"):
            payloads.append(
                TransactionIn(
                    account_id=acc["id"],
                    amount=round(acc["mean"] * 8.5, 2),
                    timestamp=now + timedelta(seconds=5),
                    lat=acc["lat"],
                    lon=acc["lon"],
                    city=acc["city"],
                    merchant="Jewellery House",
                    scenario="amount",
                )
            )
        scored = []
        for p in payloads:
            scored.append(await ingest(session, p))
            self.produced += 1
        return scored


SIMULATOR = Simulator()
