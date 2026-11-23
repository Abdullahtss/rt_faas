from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.rest import router as rest_router
from .api.stream import router as stream_router
from .config import get_settings
from .db import configure, init_db, session_factory
from .engine.pipeline import load_max_seq
from .ml.model import MODEL
from .services.hub import HUB
from .services.simulator import SIMULATOR, seed_windows


def _website_dir() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "website", here.parents[1] / "website"):
        if candidate.exists():
            return candidate
    return here.parents[1] / "website"


STATIC_DIR = _website_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure()
    await init_db()
    seed_windows()
    if MODEL.load():
        print("[rt-faas] fraud model loaded: %s (threshold %.4f)" % (MODEL.name, MODEL.threshold))
    else:
        print("[rt-faas] running on rules only - %s" % MODEL.error)
    async with session_factory()() as session:
        HUB.load_seq(await load_max_seq(session))
    yield
    SIMULATOR.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RT-FAAS",
        description="Real-Time Transaction Fraud & Anomaly Alerting System",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins + ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(stream_router, prefix="/api/v1", tags=["ingest"])
    app.include_router(rest_router, prefix="/api/v1", tags=["analyst"])

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        """Make the browser re-check /js and /css instead of serving a stale
        copy after an edit."""
        response = await call_next(request)
        if request.url.path.startswith(("/js/", "/css/")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    if STATIC_DIR.exists():
        app.mount("/css", StaticFiles(directory=STATIC_DIR / "css"), name="css")
        app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")

        @app.get("/")
        async def home():
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/console")
        async def console():
            return FileResponse(STATIC_DIR / "console.html")

        @app.get("/search")
        async def search():
            return FileResponse(STATIC_DIR / "search.html")

        @app.get("/monitor")
        async def monitor_page():
            return FileResponse(STATIC_DIR / "monitor.html")

    return app


app = create_app()
