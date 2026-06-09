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

    @app.post("/api/campaign/run")
    async def campaign_run(data: dict):
        """Run campaign — mock mode (deterministic, no API keys needed)."""
        from backend.app.campaign_runner_mock import run_campaign_mock

        seed_list = data.get("seed_list", [])
        campaign_name = data.get("campaign_name", "campaign-api")
        result = await run_campaign_mock(seed_list=seed_list, campaign_name=campaign_name)
        return {
            "campaign": result.campaign_name,
            "leads_processed": result.leads_processed,
            "sequences_generated": result.sequences_generated,
            "replies_classified": result.replies_classified,
            "total_cost_eur": round(result.total_cost_eur, 6),
            "errors": result.errors,
            "details": result.details,
        }

    return app


app = create_app()
