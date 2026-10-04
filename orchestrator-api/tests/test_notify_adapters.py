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


def test_crowdsec_adapter_single_alert():
    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        payload = {
            "scenario": "crowdsecurity/ssh-bf",
            "source": {"ip": "192.168.1.100", "as_name": "TestISP"},
            "message": "Ip 192.168.1.100 performed ssh-bf",
            "decisions": [{"duration": "4h", "type": "ban", "value": "192.168.1.100"}],
            "events_count": 5,
        }

        response = client.post("/api/notify/crowdsec?user_id=12345678", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["telegram_message_id"] == "101"

        history = get_notifications(agent_id="crowdsec", limit=5)
        assert len(history) >= 1
        record = history[0]
        assert record["agent_id"] == "crowdsec"
        assert record["status"] == "sent"
        assert "crowdsecurity/ssh-bf" in record["message"]
        assert "192.168.1.100" in record["message"]
        assert "ban (4h)" in record["message"]


def test_crowdsec_adapter_list_payload():
    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 102}})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        payload = [
            {
                "scenario": "crowdsecurity/http-probing",
                "source": "10.0.0.5",
                "decisions": [{"type": "ban", "duration": "1h"}],
            }
        ]

        response = client.post("/api/notify/crowdsec?user_id=12345678", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        history = get_notifications(agent_id="crowdsec", limit=5)
        assert len(history) >= 1
        assert "crowdsecurity/http-probing" in history[0]["message"]
        assert "10.0.0.5" in history[0]["message"]


def test_alertmanager_adapter():
    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 201}})
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        payload = {
            "status": "firing",
            "commonLabels": {
                "alertname": "HostHighCpuLoad",
                "severity": "warning",
            },
            "commonAnnotations": {
                "summary": "Host CPU load is > 90%",
            },
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HostHighCpuLoad",
                        "instance": "thinkcentre-01",
                        "severity": "warning",
                    },
                    "annotations": {
                        "description": "CPU usage has exceeded 90% for 5m",
                    },
                }
            ],
        }

        response = client.post("/api/notify/alertmanager?user_id=12345678", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["telegram_message_id"] == "201"

        history = get_notifications(agent_id="alertmanager", limit=5)
        assert len(history) >= 1
        record = history[0]
        assert record["agent_id"] == "alertmanager"
        assert record["status"] == "sent"
        assert "HostHighCpuLoad" in record["message"]
        assert "thinkcentre-01" in record["message"]
        assert "CPU usage has exceeded 90%" in record["message"]
