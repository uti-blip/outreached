"""Application settings — all values from environment, never hardcoded."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"

    # ── Supabase ───────────────────────────────────
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""

    # ── LLM Providers ──────────────────────────────
    deepseek_api_key: str = ""
    kimi_api_key: str = ""
    anthropic_api_key: str = ""

    # ── Redis / Celery ─────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Adapters ───────────────────────────────────
    smartlead_api_key: str = ""
    apollo_api_key: str = ""
    unipile_api_key: str = ""

    # ── Observability ──────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ── Feature flags ──────────────────────────────
    dry_run_default: bool = True  # never send real email in dev/test
    vertical: str = "saas_fr"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
