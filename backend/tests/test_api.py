import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import get_settings
from app.db import configure, init_db, session_factory
from app.engine.windows import WINDOWS
from app.main import create_app
from app.services.simulator import seed_windows

TOKEN = "rtfaas-dev-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest_asyncio.fixture
async def client():
    get_settings.cache_clear()
    configure("sqlite+aiosqlite:///:memory:")
    await init_db()
    WINDOWS._events.clear()
    seed_windows()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_ingest_and_health(client: AsyncClient):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = {
        "account_id": "ACC-1001",
        "amount": 4200,
        "lat": 24.86,
        "lon": 67.00,
        "city": "Karachi",
        "merchant": "Test",
    }
    r = await client.post("/api/v1/transactions", json=body, headers=HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["level"] in ("Low", "Medium", "High")
    assert "score" in data
    assert data["processing_ms"] >= 0


@pytest.mark.asyncio
async def test_combo_inject_creates_high_alert(client: AsyncClient):
    r = await client.post("/api/v1/simulator/inject", json={"scenario": "combo"}, headers=HEADERS)
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["level"] == "High" for i in items)
    alerts = [i for i in items if i["level"] == "High"]
    assert alerts[0]["seq"] >= 1


@pytest.mark.asyncio
async def test_review_and_search(client: AsyncClient):
    r = await client.post("/api/v1/simulator/inject", json={"scenario": "combo"}, headers=HEADERS)
    high = next(i for i in r.json()["items"] if i["level"] == "High")
    r = await client.post(
        "/api/v1/transactions/%s/review" % high["id"],
        json={"decision": "blocked", "notes": "confirmed fraud"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["review"] == "blocked"
    r = await client.get("/api/v1/transactions", params={"level": "High"}, headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_auth_required(client: AsyncClient):
    r = await client.post("/api/v1/transactions", json={"account_id": "ACC-1", "amount": 1, "lat": 0, "lon": 0})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_monitor_and_feedback_endpoints(client: AsyncClient):
    r = await client.post("/api/v1/simulator/inject", json={"scenario": "combo"}, headers=HEADERS)
    high = next(i for i in r.json()["items"] if i["level"] == "High")
    await client.post("/api/v1/transactions/%s/review" % high["id"],
                      json={"decision": "blocked"}, headers=HEADERS)

    r = await client.get("/api/v1/monitor", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body["per_minute"]) == 30
    assert body["feedback"]["blocked"] >= 1
    assert body["levels"]["High"] >= 1

    r = await client.get("/api/v1/model/retrain", headers=HEADERS)
    assert r.status_code == 200
    fb = r.json()["feedback"]
    assert fb["reviewed"] >= 1
    assert r.json()["state"] in ("idle", "done", "failed", "running")

    r = await client.get("/api/v1/model/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_monitor_requires_token(client: AsyncClient):
    r = await client.get("/api/v1/monitor")
    assert r.status_code == 401
    r = await client.post("/api/v1/model/retrain")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_alert_seq_collision_from_another_process_is_recovered(client: AsyncClient):
    """A second server sharing the database already took the next number."""
    from datetime import datetime

    from app.models import Alert, Transaction
    from app.services.hub import HUB

    taken = HUB.seq + 1
    async with session_factory()() as s:
        s.add(Transaction(id="other-proc", account_id="ACC-X", amount=1.0, ts=datetime.utcnow(),
                          lat=0.0, lon=0.0, ingested_at=datetime.utcnow()))
        s.add(Alert(seq=taken, transaction_id="other-proc", payload="{}", created_at=datetime.utcnow()))
        await s.commit()

    r = await client.post("/api/v1/simulator/inject", json={"scenario": "combo"}, headers=HEADERS)
    assert r.status_code == 200
    seqs = [i["seq"] for i in r.json()["items"] if i["level"] == "High"]
    assert seqs and all(s > taken for s in seqs)
