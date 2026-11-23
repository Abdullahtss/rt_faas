from fastapi import Header, HTTPException, Query, WebSocket

from .config import get_settings


def _check(token: str) -> None:
    expected = get_settings().api_token
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_token(authorization: str = Header(default="")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    _check(token)
    return token


async def ws_token(websocket: WebSocket, token: str = Query(default="")) -> str:
    settings = get_settings()
    if token != settings.api_token:
        await websocket.close(code=4401)
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


def mask_account(account_id: str) -> str:
    if len(account_id) <= 4:
        return "****"
    return account_id[:3] + "****" + account_id[-4:]
