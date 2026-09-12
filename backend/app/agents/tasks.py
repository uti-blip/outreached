"""Background Celery worker tasks — Phase 1 agents."""

from backend.app.celery_app import celery_app


@celery_app.task(name="ping")
def ping() -> str:
    """Trivial health task for worker validation."""
    return "pong"


@celery_app.task(name="agent.sourcing")
def agent_sourcing(lead_data: dict) -> dict:
    """Reject an unavailable task instead of fabricating a sourcing success."""
    from backend.app.adapters.base import ProviderCapabilityUnavailableError

    raise ProviderCapabilityUnavailableError("Le sourcing asynchrone n’est pas implémenté.")
