"""FastAPI application factory."""

from contextlib import asynccontextmanager
from secrets import compare_digest
from sqlite3 import Error as SQLiteError

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app.adapters.factory import LiveSendNotConfiguredError
from backend.app.campaign_runner import run_campaign
from backend.app.config import settings
from backend.app.prospecting import CleanModel, router, validate_email
from backend.app.workspace_store import connect, init_workspace

# Routes served without a workspace secret. Everything else under /api/ is
# gated by the Bearer token check in protect_workspace.
PUBLIC_ROUTES = frozenset({"/health", "/health/ready", "/"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — production configuration is validated fail-closed."""
    settings.validate_production()
    init_workspace()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Outreached",
        description="B2B outbound orchestration — agency-first",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are a development aid; not exposed in production.
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None if settings.app_env == "production" else "/redoc",
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def protect_workspace(request: Request, call_next):
        """Authentication + transport hardening for every /api/* route.

        Authentication is the Bearer token only. Origin is a defence-in-depth
        check for browser callers: a missing Origin (curl, CLI, server-to-server)
        is not a bypass — the token is still required. Preflight OPTIONS cannot
        carry credentials and is therefore never authenticated.
        """
        if request.url.path.startswith("/api/") and request.url.path not in PUBLIC_ROUTES:
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                return JSONResponse({"detail": "Origine non autorisée"}, status_code=403)
            if settings.workspace_api_key and request.method != "OPTIONS":
                expected = f"Bearer {settings.workspace_api_key}"
                if not compare_digest(
                    request.headers.get("authorization", "").encode(), expected.encode()
                ):
                    return JSONResponse(
                        {"detail": "Clé d’accès requise ou invalide"}, status_code=401
                    )
            # Bound actual bytes, including requests without Content-Length.
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1_500_000:
                    return JSONResponse({"detail": "Requête trop volumineuse"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    app.include_router(router)

    # ── Routes ────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "vertical": settings.vertical}

    @app.get("/health/ready")
    def readiness():
        try:
            with connect(write=False) as conn:
                conn.execute("SELECT id FROM profile LIMIT 1").fetchone()
        except (SQLiteError, OSError):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.get("/")
    async def root():
        return {"app": "outreached", "env": settings.app_env}

    @app.post("/api/campaign/run")
    async def campaign_run(data: CampaignInput):
        """Run the outbound pipeline.

        Dry-run is the default and the only mode reachable without an explicit
        server-side commercial launch flag: no request body field can enable a
        real send. Live sending requires COMMERCIAL_LAUNCH_ENABLED=true in the
        environment and APP_ENV=production.
        """
        if not data.dry_run:
            if not settings.commercial_launch_enabled:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Lancement commercial désactivé côté serveur. "
                        "Envoi réel refusé (COMMERCIAL_LAUNCH_ENABLED)."
                    ),
                )
            if settings.app_env != "production":
                raise HTTPException(
                    status_code=403,
                    detail="Envoi réel refusé hors production.",
                )

        try:
            result = await run_campaign(
                seed_list=[seed.model_dump() for seed in data.seed_list],
                campaign_name=data.campaign_name,
                dry_run=data.dry_run,
            )
        except LiveSendNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "mode": "live" if not data.dry_run else "demo",
            "simulated": data.dry_run,
            "campaign": result.campaign_name,
            "leads_processed": result.leads_processed,
            "sequences_generated": result.sequences_generated,
            "replies_classified": result.replies_classified,
            "total_cost_eur": round(result.total_cost_eur, 6),
            "errors": result.errors,
            "details": result.details,
        }

    return app


class CampaignSeed(CleanModel):
    company_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(default="", max_length=253)
    email: str = Field(default="", max_length=254)
    linkedin_url: str = Field(default="", max_length=1000)

    _email = field_validator("email")(validate_email)


class CampaignInput(CleanModel):
    """Client input — deliberately carries no launch-authorisation control.

    `extra="forbid"` makes any attempt to inject a removed field (for example
    `commercial_launch_enabled`) a hard 422 instead of a silently ignored key.
    """

    seed_list: list[CampaignSeed] = Field(min_length=1, max_length=100)
    campaign_name: str = Field(default="campaign-demo", min_length=1, max_length=150)
    dry_run: bool = True


app = create_app()
