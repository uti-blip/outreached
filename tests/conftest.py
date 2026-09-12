"""Keep every test away from user databases and optional cloud services."""

import pytest

from backend.app.config import settings
from backend.app.db import supabase


@pytest.fixture(autouse=True)
def isolated_databases(tmp_path, monkeypatch):
    monkeypatch.setattr(supabase, "DB_PATH", tmp_path / "legacy.db")
    monkeypatch.setattr(supabase, "_client", None)
    monkeypatch.setattr(supabase, "_use_supabase", False)
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_key", "")
    monkeypatch.setattr(settings, "workspace_db_path", str(tmp_path / "lexia.db"))
    monkeypatch.setattr(settings, "workspace_api_key", "")
    monkeypatch.setattr(settings, "app_env", "test")
