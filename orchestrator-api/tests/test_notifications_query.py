from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import init_db, log_notification
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


def test_get_notifications_with_filters():
    now = datetime.now(timezone.utc)
    time1 = (now - timedelta(hours=2)).isoformat()
    time2 = (now - timedelta(hours=1)).isoformat()
    time3 = now.isoformat()

    log_notification(
        agent_id="agent_a",
        user_id="user1",
        message="Message 1",
        status="sent",
        created_at=time1,
    )
    log_notification(
        agent_id="agent_b",
        user_id="user1",
        message="Message 2",
        status="sent",
        created_at=time2,
    )
    log_notification(
        agent_id="agent_a",
        user_id="user2",
        message="Message 3",
        status="failed",
        created_at=time3,
    )

    # Filter by agent_id
    resp = client.get("/api/notifications?agent_id=agent_a")
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["agent_id"] == "agent_a" for item in data)
    assert len(data) >= 2

    # Filter by since
    resp = client.get(f"/api/notifications?since={time2}")
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["created_at"] >= time2 for item in data)

    # Limit and offset
    resp = client.get("/api/notifications?limit=1&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_notifications_stats():
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(minutes=10)).isoformat()

    log_notification(agent_id="stats_agent_1", message="M1", status="sent", created_at=t1)
    log_notification(agent_id="stats_agent_1", message="M2", status="failed", created_at=t1)
    log_notification(agent_id="stats_agent_2", message="M3", status="sent", created_at=t1)

    resp = client.get("/api/notifications/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert "total" in stats
    assert "by_agent" in stats
    assert "by_status" in stats
    assert stats["by_agent"].get("stats_agent_1", 0) >= 2
    assert stats["by_agent"].get("stats_agent_2", 0) >= 1
    assert stats["by_status"].get("sent", 0) >= 2
    assert stats["by_status"].get("failed", 0) >= 1

    # Filter stats with since
    future_time = (now + timedelta(hours=1)).isoformat()
    resp_future = client.get(f"/api/notifications/stats?since={future_time}")
    assert resp_future.status_code == 200
    future_stats = resp_future.json()
    assert future_stats["total"] == 0
