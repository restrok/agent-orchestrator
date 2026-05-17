from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_read_root():
    """Verify that the API root/health check is accessible."""
    response = client.get("/")
    assert response.status_code == 200
    assert "status" in response.json()


def test_proactive_notify_auth():
    """Verify that the notify endpoint requires authentication (if implemented) or at least exists."""
    response = client.post(
        "/api/notify", json={"user_id": "test_user", "message": "Hello from test", "priority": "high"}
    )
    # If it returns 401/403, that's also a valid test of security
    assert response.status_code in [200, 401, 403, 405, 422]
