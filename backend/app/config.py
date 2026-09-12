"""Application settings — all values from environment, never hardcoded."""

import re
from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# ── Production safety constants ─────────────────────────
# Values that ship as examples must never reach production.
INSECURE_SECRET_VALUES = frozenset(
    {
        "change-me-in-production",
        "change-me-in-production-at-least-32-chars",
        "changeme",
        "change-me",
        "change_me",
        "secret",
        "secret-key",
        "password",
        "your-secret-key",
        "your-secret-key-here",
        "test",
        "dev",
        "example",
        "xxxxx",
    }
)

# Hosts/ports that only make sense on a developer machine.
DEV_HOSTS = frozenset({"localhost", "127.0.0.1", "testserver", "0.0.0.0"})

MIN_SECRET_LENGTH = 32


def _assert_strong_secret(name: str, value: str) -> None:
    """Reject absent, empty, placeholder or too-short production secrets."""
    if not value or not value.strip():
        raise RuntimeError(f"{name} est requis en production (valeur absente ou vide)")
    if value.strip().lower() in INSECURE_SECRET_VALUES:
        raise RuntimeError(f"{name} utilise une valeur d'exemple connue — remplacez-la")
    if any(character.isspace() for character in value):
        raise RuntimeError(f"{name} ne peut pas contenir d’espace en production")
    if len(value) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"{name} doit contenir au moins {MIN_SECRET_LENGTH} caractères en production"
        )


def _is_production_host(host: str) -> bool:
    if host.lower().rstrip(".") in DEV_HOSTS:
        return False
    try:
        address = ip_address(host)
        return not (address.is_loopback or address.is_unspecified)
    except ValueError:
        return bool(
            len(host) <= 253
            and re.fullmatch(
                r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
                r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+",
                host,
            )
        )


def _assert_production_hosts(hosts: list[str]) -> None:
    """ALLOWED_HOSTS must be explicit and real in production — never a wildcard."""
    if not hosts:
        raise RuntimeError("ALLOWED_HOSTS est requis en production (aucune valeur par défaut)")
    if any("*" in host for host in hosts):
        raise RuntimeError("ALLOWED_HOSTS ne peut pas contenir de joker '*' en production")
    if any(not _is_production_host(host) or ":" in host for host in hosts):
        raise RuntimeError(
            "ALLOWED_HOSTS doit lister les hôtes réels de production (valeurs de dev refusées)"
        )


def _assert_production_origins(origins: list[str]) -> None:
    """ALLOWED_ORIGINS must be explicit https origins in production — never a wildcard."""
    if not origins:
        raise RuntimeError("ALLOWED_ORIGINS est requis en production (aucune valeur par défaut)")
    if any("*" in origin for origin in origins):
        raise RuntimeError("ALLOWED_ORIGINS ne peut pas contenir de joker '*' en production")
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname is not None
                and _is_production_host(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
                and not any(character.isspace() for character in origin)
            )
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError(
                "ALLOWED_ORIGINS doit lister des origines HTTPS valides de production"
            )


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = False
    secret_key: str = ""
    workspace_db_path: str = "backend/lexia.db"
    workspace_database_url: str = Field(default="", repr=False)
    workspace_database_ssl_root_cert: str = ""
    workspace_api_key: str = ""
    allowed_origins: list[str] = []
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]

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
    langfuse_host: str = ""

    # ── Sentry ─────────────────────────────────────
    sentry_dsn: str = ""

    # ── Feature flags ──────────────────────────────
    dry_run_default: bool = True  # never send real email in dev/test
    # Server-side only source of truth: the commercial launch gate is NEVER
    # client-controlled. Absent or false => no real send is ever possible.
    commercial_launch_enabled: bool = False
    vertical: str = "saas_fr"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_environment(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    def validate_production(self) -> None:
        """Fail closed when production configuration is missing or unsafe.

        Called at startup. Development/test environments are left untouched.
        """
        if self.app_env != "production":
            return

        _assert_strong_secret("WORKSPACE_API_KEY", self.workspace_api_key)
        _assert_strong_secret("SECRET_KEY", self.secret_key)
        if self.workspace_api_key == self.secret_key:
            raise RuntimeError("WORKSPACE_API_KEY et SECRET_KEY doivent être distincts")

        _assert_production_hosts(self.allowed_hosts)
        _assert_production_origins(self.allowed_origins)
        if self.debug:
            raise RuntimeError("DEBUG doit être désactivé en production")
        if self.workspace_database_url:
            validate_database_url(self.workspace_database_url, production=True)
        elif not Path(self.workspace_db_path).is_absolute():
            raise RuntimeError(
                "WORKSPACE_DB_PATH doit être un chemin absolu sur un volume persistant en production"
            )

        if self.commercial_launch_enabled:
            missing = [
                name
                for name, value in (
                    ("SMARTLEAD_API_KEY", self.smartlead_api_key),
                    ("UNIPILE_API_KEY", self.unipile_api_key),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "COMMERCIAL_LAUNCH_ENABLED=true exige les transports réels : "
                    + ", ".join(missing)
                )


settings = Settings()


def validate_database_url(value: str, *, production: bool = False) -> None:
    """Only PostgreSQL URLs; never downgrade remote database TLS verification."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.username
            or not parsed.password
            or parsed.path in {"", "/"}
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        local = parsed.hostname == "localhost"
        with suppress(ValueError):
            local = local or ip_address(parsed.hostname).is_loopback
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(key not in {"sslmode", "sslrootcert", "channel_binding"} for key in query):
            raise ValueError
        if any(len(values) != 1 for values in query.values()):
            raise ValueError
        if (production or not local) and query.get("sslmode", ["verify-full"])[0] != "verify-full":
            raise ValueError
    except ValueError:
        raise RuntimeError(
            "WORKSPACE_DATABASE_URL doit être une URL PostgreSQL complète ; "
            "TLS sslmode=verify-full est requis hors des tests locaux."
        ) from None
