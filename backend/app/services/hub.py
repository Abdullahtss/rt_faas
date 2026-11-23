from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import WebSocket


class AlertHub:
    """Fan-out of live events plus sequence-numbered high-risk alerts for replay."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._seq = 0

    def load_seq(self, current: int) -> None:
        self._seq = max(self._seq, current)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def send(self, ws: WebSocket, message: Dict) -> None:
        await ws.send_json(message)

    async def broadcast(self, message: Dict) -> None:
        dead: List[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


HUB = AlertHub()


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def dumps(obj: Dict) -> str:
    return json.dumps(obj, default=str)
