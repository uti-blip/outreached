"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.db.supabase import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Outreached",
        description="B2B outbound orchestration — agency-first",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS (Phase 1: open for local dev; tighten in Phase 2)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "vertical": settings.vertical}

    @app.get("/")
    async def root():
        return {"app": "outreached", "env": settings.app_env}

    return app


app = create_app()
