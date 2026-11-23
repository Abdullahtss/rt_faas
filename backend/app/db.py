import os
from typing import AsyncIterator, Optional

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import get_settings
from .models import Base

_engine: Optional[AsyncEngine] = None
_Session: Optional[async_sessionmaker[AsyncSession]] = None


def _make_engine(url: str) -> AsyncEngine:
    kwargs = dict(echo=False, future=True)
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        # `timeout` is how long sqlite3 waits for a write lock instead of
        # raising "database is locked" straight away.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in url or url.rstrip("/").endswith("sqlite"):
            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("///")[-1]
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
    engine = create_async_engine(url, **kwargs)

    if is_sqlite:
        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            """Default SQLite serialises writers and gives up immediately when
            one is busy, which shows up as 500s under concurrent ingest.
            Write-ahead logging lets readers run while a write is in flight."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


def configure(url: Optional[str] = None, force: bool = False) -> async_sessionmaker[AsyncSession]:
    global _engine, _Session
    if _engine is not None and not force and url is None:
        assert _Session is not None
        return _Session
    settings = get_settings()
    _engine = _make_engine(url or settings.database_url)
    _Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _Session


def get_engine() -> AsyncEngine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _Session is None:
        configure()
    assert _Session is not None
    return _Session


# Columns added after the first release. `create_all` only creates missing
# tables, so an existing database needs these filled in by hand.
_ADDED_COLUMNS = {
    "risk_results": {
        "ml_score": "INTEGER",
        "ml_flag": "INTEGER",
        "ml_reasons": "TEXT",
        "anomaly_pct": "FLOAT",
        "features": "TEXT",
    },
}


def _add_missing_columns(conn) -> None:
    inspector = sa_inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl_type in columns.items():
            if name not in have:
                conn.exec_driver_sql(
                    "ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl_type)
                )


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = session_factory()
    async with factory() as session:
        yield session
