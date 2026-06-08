"""Background Celery worker tasks — Phase 1 agents."""

from backend.app.celery_app import celery_app


@celery_app.task(name="ping")
def ping() -> str:
    """Trivial health task for worker validation."""
    return "pong"


@celery_app.task(name="agent.sourcing")
def agent_sourcing(lead_data: dict) -> dict:
    """Stub: sourcing agent (L3)."""
    return {"status": "ok", "agent": "sourcing", "lead": lead_data.get("email", "")}
