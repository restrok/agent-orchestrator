"""Unit tests for the memory_janitor module."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.memory_janitor import (
    build_payload,
    derive_space_id,
    is_mcp_success,
    is_notification_only,
    parse_iso_utc,
    process_batch,
    run_janitor_cycle,
    should_process,
)


def test_parse_iso_utc():
    # Offset +00:00
    dt1 = parse_iso_utc("2026-10-26T03:00:00.123456+00:00")
    assert dt1.tzinfo == timezone.utc
    assert dt1.hour == 3

    # Trailing Z
    dt2 = parse_iso_utc("2026-10-26T03:00:00Z")
    assert dt2.tzinfo == timezone.utc
    assert dt2 == dt1.replace(microsecond=0)

    # Local host offset (e.g. -03:00) converted properly to UTC
    dt3 = parse_iso_utc("2026-10-26T00:00:00-03:00")
    assert dt3.tzinfo == timezone.utc
    assert dt3.hour == 3  # 00:00 -03:00 is 03:00 UTC


def test_should_process_triggers():
    now_utc = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Idle condition met (> 30 min ago)
    t_35_min_ago = (now_utc - timedelta(minutes=35)).isoformat()
    batch_idle_met = [
        {"id": 1, "created_at": t_35_min_ago, "role": "user", "thread_id": "t1"}
    ]
    assert should_process(batch_idle_met, now=now_utc, idle_minutes=30, volume_threshold=20) is True

    # 2. Idle NOT met (< 30 min ago, < 20 msgs)
    t_10_min_ago = (now_utc - timedelta(minutes=10)).isoformat()
    batch_not_idle = [
        {"id": 1, "created_at": t_10_min_ago, "role": "user", "thread_id": "t1"}
    ]
    assert should_process(batch_not_idle, now=now_utc, idle_minutes=30, volume_threshold=20) is False

    # 3. Volume met (>= 20 msgs even if only 1 minute ago)
    t_1_min_ago = (now_utc - timedelta(minutes=1)).isoformat()
    batch_volume_met = [
        {"id": i, "created_at": t_1_min_ago, "role": "user", "thread_id": "t1"}
        for i in range(20)
    ]
    assert should_process(batch_volume_met, now=now_utc, idle_minutes=30, volume_threshold=20) is True

    # 4. Both idle and volume met
    batch_both_met = [
        {"id": i, "created_at": t_35_min_ago, "role": "user", "thread_id": "t1"}
        for i in range(25)
    ]
    assert should_process(batch_both_met, now=now_utc, idle_minutes=30, volume_threshold=20) is True

    # 5. Neither met
    batch_neither = [
        {"id": i, "created_at": t_10_min_ago, "role": "user", "thread_id": "t1"}
        for i in range(15)
    ]
    assert should_process(batch_neither, now=now_utc, idle_minutes=30, volume_threshold=20) is False

    # 6. Force = True ignores timer and volume
    assert should_process(batch_neither, now=now_utc, force=True) is True

    # 7. Empty batch
    assert should_process([], now=now_utc, force=True) is False


def test_timezone_boundary_31min_vs_29min():
    """Validates boundary condition: 31 minutes ago is eligible, 29 minutes ago is not."""
    now_utc = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)

    # Message 31 minutes ago
    t_31m = (now_utc - timedelta(minutes=31)).isoformat()
    batch_31m = [{"id": 1, "created_at": t_31m, "role": "user", "thread_id": "t1"}]
    assert should_process(batch_31m, now=now_utc, idle_minutes=30) is True

    # Message 29 minutes ago
    t_29m = (now_utc - timedelta(minutes=29)).isoformat()
    batch_29m = [{"id": 2, "created_at": t_29m, "role": "user", "thread_id": "t1"}]
    assert should_process(batch_29m, now=now_utc, idle_minutes=30) is False

    # Message exactly 30 minutes ago (should be False because condition is strictly > 30 min)
    t_30m = (now_utc - timedelta(minutes=30)).isoformat()
    batch_30m = [{"id": 3, "created_at": t_30m, "role": "user", "thread_id": "t1"}]
    assert should_process(batch_30m, now=now_utc, idle_minutes=30) is False


def test_is_notification_only():
    # Only notifications / system messages
    batch_notifications = [
        {"id": 1, "role": "assistant", "source": "notification", "content": "Alert 1"},
        {"id": 2, "role": "assistant", "source": "notification", "content": "Alert 2"},
    ]
    assert is_notification_only(batch_notifications) is True

    # Mixed: user message + notification
    batch_mixed = [
        {"id": 1, "role": "user", "source": "chat", "content": "Hello bot"},
        {"id": 2, "role": "assistant", "source": "notification", "content": "System alert"},
        {"id": 3, "role": "assistant", "source": "chat", "content": "Hello user"},
    ]
    assert is_notification_only(batch_mixed) is False

    # Pure user chat
    batch_user_only = [
        {"id": 1, "role": "user", "source": "chat", "content": "First"},
        {"id": 2, "role": "user", "source": "chat", "content": "Second"},
    ]
    assert is_notification_only(batch_user_only) is False


def test_build_payload_deterministic_id_and_transcript():
    batch = [
        {
            "id": 105,
            "thread_id": "chat_99",
            "user_id": "fsirio",
            "role": "user",
            "content": "Can you check the database?",
            "created_at": "2026-10-26T01:00:00+00:00",
            "intent": "antigravity_worker",
        },
        {
            "id": 106,
            "thread_id": "chat_99",
            "user_id": "fsirio",
            "role": "assistant",
            "content": "[Notificación]: Disk usage is 40%",
            "created_at": "2026-10-26T01:01:00+00:00",
            "intent": "notification",
        },
        {
            "id": 107,
            "thread_id": "chat_99",
            "user_id": "fsirio",
            "role": "assistant",
            "content": "Database check completed successfully.",
            "created_at": "2026-10-26T01:02:00+00:00",
            "intent": "antigravity_worker",
        },
    ]

    payload1 = build_payload(batch)
    payload2 = build_payload(batch)

    # 1. Deterministic conversation_id format: tg_{thread_id}_{min_id}_{max_id}
    assert payload1["conversation_id"] == "tg_chat_99_105_107"
    assert payload1["conversation_id"] == payload2["conversation_id"]

    # 2. Space ID resolved deterministically
    assert payload1["space_id"] == "work"

    # 3. Transcript format: JSONL with 'type', 'role', 'content', 'created_at'
    lines = payload1["transcript_jsonl"].strip().split("\n")
    assert len(lines) == 3

    event0 = json.loads(lines[0])
    assert event0["type"] == "USER_INPUT"
    assert event0["role"] == "user"
    assert event0["content"] == "Can you check the database?"

    event1 = json.loads(lines[1])
    assert event1["type"] == "PLANNER_RESPONSE"
    assert event1["role"] == "assistant"
    assert "[Notificación]" in event1["content"]

    event2 = json.loads(lines[2])
    assert event2["type"] == "PLANNER_RESPONSE"
    assert event2["role"] == "assistant"


def test_derive_space_id_hybrid():
    # User mercedes -> always personal
    batch_mercedes = [
        {"id": 1, "user_id": "mercedes", "intent": "general_chat", "role": "user"}
    ]
    assert derive_space_id(batch_mercedes) == "personal"

    # Biometric intent -> personal
    batch_biometric = [
        {"id": 1, "user_id": "fsirio", "intent": "biometric_expert", "role": "user"}
    ]
    assert derive_space_id(batch_biometric) == "personal"

    # Worker / brain intent -> work
    batch_work = [
        {"id": 1, "user_id": "fsirio", "intent": "antigravity_worker", "role": "user"}
    ]
    assert derive_space_id(batch_work) == "work"

    # Default -> work
    batch_default = [
        {"id": 1, "user_id": "fsirio", "intent": None, "role": "user"}
    ]
    assert derive_space_id(batch_default) == "work"


@pytest.mark.asyncio
async def test_process_batch_atomicity_on_mcp_failure():
    """Verify that if MCP fails, rows remain un-marked (0) and are not marked 1."""
    mock_mcp = AsyncMock(return_value="Error connecting to Exocortex MCP: HTTP 503")
    mock_mark_ingested = MagicMock()
    mock_mark_skipped = MagicMock()

    batch = [
        {"id": 1, "thread_id": "t1", "user_id": "u1", "role": "user", "content": "hi", "created_at": "2026-10-26T00:00:00+00:00"}
    ]

    res = await process_batch(
        thread_id="t1",
        batch=batch,
        mcp_invoker=mock_mcp,
        mark_ingested_fn=mock_mark_ingested,
        mark_skipped_fn=mock_mark_skipped,
    )

    assert res["status"] == "error"
    mock_mcp.assert_called_once()
    mock_mark_ingested.assert_not_called()
    mock_mark_skipped.assert_not_called()


@pytest.mark.asyncio
async def test_process_batch_atomicity_on_mcp_exception():
    """Verify that if MCP throws an exception, it is caught cleanly and rows remain 0."""
    mock_mcp = AsyncMock(side_effect=ConnectionResetError("Connection refused"))
    mock_mark_ingested = MagicMock()
    mock_mark_skipped = MagicMock()

    batch = [
        {"id": 1, "thread_id": "t1", "user_id": "u1", "role": "user", "content": "hi", "created_at": "2026-10-26T00:00:00+00:00"}
    ]

    res = await process_batch(
        thread_id="t1",
        batch=batch,
        mcp_invoker=mock_mcp,
        mark_ingested_fn=mock_mark_ingested,
        mark_skipped_fn=mock_mark_skipped,
    )

    assert res["status"] == "error"
    assert "Connection refused" in res["error"]
    mock_mark_ingested.assert_not_called()


@pytest.mark.asyncio
async def test_process_batch_success_marks_ingested():
    """Verify that upon successful MCP response, rows are marked with ingested_to_brain = 1."""
    mcp_ok_json = json.dumps({
        "schema_version": 2,
        "status": "ok",
        "method": "ingest_antigravity_transcript",
        "data": {"records_processed": 1, "notes_created": ["note_123"]},
    })
    mock_mcp = AsyncMock(return_value=mcp_ok_json)
    mock_mark_ingested = MagicMock(return_value=2)
    mock_mark_skipped = MagicMock()

    batch = [
        {"id": 10, "thread_id": "t1", "user_id": "u1", "role": "user", "content": "msg 1", "created_at": "2026-10-26T00:00:00+00:00"},
        {"id": 11, "thread_id": "t1", "user_id": "u1", "role": "assistant", "content": "msg 2", "created_at": "2026-10-26T00:01:00+00:00"},
    ]

    res = await process_batch(
        thread_id="t1",
        batch=batch,
        mcp_invoker=mock_mcp,
        mark_ingested_fn=mock_mark_ingested,
        mark_skipped_fn=mock_mark_skipped,
    )

    assert res["status"] == "ingested"
    assert res["message_count"] == 2
    mock_mcp.assert_called_once()
    mock_mark_ingested.assert_called_once_with([10, 11])
    mock_mark_skipped.assert_not_called()


@pytest.mark.asyncio
async def test_process_batch_notification_only_skips_mcp():
    """Verify that notification-only batches are marked skipped (2) and do NOT call MCP."""
    mock_mcp = AsyncMock()
    mock_mark_ingested = MagicMock()
    mock_mark_skipped = MagicMock(return_value=3)

    batch = [
        {"id": 1, "thread_id": "notif_thread", "user_id": "u1", "role": "assistant", "source": "notification", "content": "Notif 1", "created_at": "2026-10-26T00:00:00+00:00"},
        {"id": 2, "thread_id": "notif_thread", "user_id": "u1", "role": "assistant", "source": "notification", "content": "Notif 2", "created_at": "2026-10-26T00:01:00+00:00"},
        {"id": 3, "thread_id": "notif_thread", "user_id": "u1", "role": "assistant", "source": "notification", "content": "Notif 3", "created_at": "2026-10-26T00:02:00+00:00"},
    ]

    res = await process_batch(
        thread_id="notif_thread",
        batch=batch,
        mcp_invoker=mock_mcp,
        mark_ingested_fn=mock_mark_ingested,
        mark_skipped_fn=mock_mark_skipped,
    )

    assert res["status"] == "skipped"
    assert res["message_count"] == 3
    mock_mcp.assert_not_called()
    mock_mark_skipped.assert_called_once_with([1, 2, 3])
    mock_mark_ingested.assert_not_called()


@pytest.mark.asyncio
async def test_run_janitor_cycle_orchestration():
    """Full cycle test with mixed pending threads: eligible, not eligible, notification only."""
    now_utc = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
    t_old = (now_utc - timedelta(minutes=40)).isoformat()
    t_new = (now_utc - timedelta(minutes=5)).isoformat()

    pending_messages = [
        # Thread 1: eligible (old user message) -> should ingest
        {"id": 1, "thread_id": "thread_1", "user_id": "fsirio", "role": "user", "content": "Hello", "created_at": t_old},
        # Thread 2: not eligible (recent, only 1 message) -> should be skipped by should_process
        {"id": 2, "thread_id": "thread_2", "user_id": "fsirio", "role": "user", "content": "Recent", "created_at": t_new},
        # Thread 3: eligible by idle, notification-only -> should mark skipped (2)
        {"id": 3, "thread_id": "thread_3", "user_id": "fsirio", "role": "assistant", "source": "notification", "content": "Alert", "created_at": t_old},
    ]

    mock_mcp = AsyncMock(return_value=json.dumps({"status": "ok"}))
    mock_mark_ingested = MagicMock(return_value=1)
    mock_mark_skipped = MagicMock(return_value=1)

    summary = await run_janitor_cycle(
        now=now_utc,
        idle_minutes=30,
        volume_threshold=20,
        mcp_invoker=mock_mcp,
        get_pending_fn=lambda: pending_messages,
        mark_ingested_fn=mock_mark_ingested,
        mark_skipped_fn=mock_mark_skipped,
    )

    assert summary["status"] == "ok"
    assert summary["total_pending"] == 3
    assert summary["total_threads_pending"] == 3
    assert summary["processed_threads"] == 2
    assert summary["ingested_messages"] == 1
    assert summary["skipped_messages"] == 1
    assert len(summary["errors"]) == 0

    # MCP should only have been called for thread_1
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args[0][1]["conversation_id"] == "tg_thread_1_1_1"
    mock_mark_ingested.assert_called_once_with([1])
    mock_mark_skipped.assert_called_once_with([3])


def test_is_mcp_success_formats():
    assert is_mcp_success({"status": "ok"})[0] is True
    assert is_mcp_success({"schema_version": 2, "status": "ok"})[0] is True
    assert is_mcp_success({"error": "Failed"})[0] is False
    assert is_mcp_success('{"status": "ok"}')[0] is True
    assert is_mcp_success("Error connecting to Exocortex MCP: HTTP 500")[0] is False
    assert is_mcp_success("Exocortex MCP Error: Invalid parameters")[0] is False
    assert is_mcp_success("Exocortex MCP request timed out.")[0] is False
    assert is_mcp_success(None)[0] is False


def test_post_flush_endpoint():
    """Verify that POST /api/messages/flush invokes run_janitor_cycle with force=True."""
    from unittest.mock import ANY, patch

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    expected_summary = {
        "status": "ok",
        "total_pending": 5,
        "total_threads_pending": 2,
        "processed_threads": 2,
        "ingested_messages": 3,
        "skipped_messages": 2,
        "details": [],
        "errors": [],
    }

    with patch("app.main.run_janitor_cycle", new_callable=AsyncMock) as mock_cycle:
        mock_cycle.return_value = expected_summary
        response = client.post("/api/messages/flush")
        assert response.status_code == 200
        assert response.json() == expected_summary
        mock_cycle.assert_called_once_with(
            force=True,
            mcp_invoker=ANY,
            get_lock_fn=ANY,
        )

