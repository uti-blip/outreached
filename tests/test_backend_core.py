"""Tests: Backend Core (Cluster 2)."""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["vertical"] == "saas_fr"


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "outreached"


def test_celery_ping():
    """Test that Celery ping task executes (requires Redis)."""
    from backend.app.agents.tasks import ping

    result = ping.delay()
    assert result.get(timeout=10) == "pong"
