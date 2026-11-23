from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session, session_factory
from ..engine.pipeline import ingest, replay_alerts
from ..schemas import InjectIn, SimulatorConfig, TransactionIn
from ..security import require_token, ws_token
from ..services.hub import HUB
from ..services.simulator import SIMULATOR

router = APIRouter()


@router.post("/transactions")
async def post_transaction(
    payload: TransactionIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    item = await ingest(session, payload)
    return item


@router.post("/simulator/start")
async def start_sim(cfg: SimulatorConfig, _: str = Depends(require_token)):
    SIMULATOR.start(session_factory(), cfg.tps)
    return SIMULATOR.status()


@router.post("/simulator/stop")
async def stop_sim(_: str = Depends(require_token)):
    SIMULATOR.stop()
    return SIMULATOR.status()


@router.get("/simulator/status")
async def sim_status(_: str = Depends(require_token)):
    return SIMULATOR.status()


@router.post("/simulator/next-fraud")
async def next_fraud(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    item = await SIMULATOR.next_fraud(session)
    return item


@router.post("/simulator/inject")
async def inject(
    body: InjectIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
):
    items = await SIMULATOR.inject(session, body.scenario, body.account_id)
    return {"injected": len(items), "items": items}


@router.websocket("/ws/alerts")
async def alerts_ws(
    websocket: WebSocket,
    token: str = "",
    last_seq: int = Query(default=0),
):
    await ws_token(websocket, token)
    await HUB.connect(websocket)
    try:
        async with session_factory()() as session:
            missed = await replay_alerts(session, last_seq)
        await HUB.send(
            websocket,
            {"type": "hello", "data": {"seq": HUB.seq, "replayed": len(missed)}},
        )
        for payload in missed:
            await HUB.send(websocket, {"type": "alert", "data": payload, "replay": True})
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=20)
                if msg == "ping":
                    await HUB.send(websocket, {"type": "pong"})
            except asyncio.TimeoutError:
                await HUB.send(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        HUB.disconnect(websocket)
    except Exception:
        HUB.disconnect(websocket)
