from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import get_notifications, init_db
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setattr("app.main.TELEGRAM_BOT_TOKEN", "123456:TEST_BOT_TOKEN")
    init_db()


def test_notify_records_history_success():
    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 999}})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        payload = {
            "user_id": "12345678",
            "agent_id": "test_agent",
            "message": "Test notification message",
        }
        response = client.post("/api/notify", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["telegram_message_id"] == "999"

        # Check in notifications_history
        history = get_notifications(agent_id="test_agent", limit=5)
        assert len(history) >= 1
        record = history[0]
        assert record["agent_id"] == "test_agent"
        assert record["user_id"] == "12345678"
        assert record["status"] == "sent"
        assert record["telegram_message_id"] == "999"
        assert "🔔 <b>Notificación (test_agent)</b>" in record["message"]
        assert record["parse_mode"] == "HTML"
        assert record["chunks_sent"] == 1
        assert record["chunks_total"] == 1


def test_notify_raw_true_omits_prefix():
    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 1000}})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        payload = {
            "user_id": "12345678",
            "agent_id": "raw_agent",
            "message": "Raw plain message without headers",
            "raw": True,
            "parse_mode": None,
        }
        response = client.post("/api/notify", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        history = get_notifications(agent_id="raw_agent", limit=5)
        assert len(history) >= 1
        record = history[0]
        assert "🔔" not in record["message"]
        assert record["message"] == "Raw plain message without headers"
        assert record["parse_mode"] is None


def test_notify_failure_records_failed_status():
    mock_resp = httpx.Response(500, json={"ok": False, "description": "Telegram API Down"})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        payload = {
            "user_id": "12345678",
            "agent_id": "failing_agent",
            "message": "This will fail to send",
        }
        response = client.post("/api/notify", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "error"

        history = get_notifications(agent_id="failing_agent", limit=5)
        assert len(history) >= 1
        record = history[0]
        assert record["status"] == "failed"
        assert "Telegram API Down" in (record["error"] or "")
        assert record["chunks_sent"] == 0
