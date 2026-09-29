"""Unit tests for scheduler fixes, cancel validation, and dynamic spaces injection."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from models import AgentState

from app.db import (
    get_scheduled_task_by_job_id,
    list_active_scheduled_tasks,
)
from app.main import (
    cancel_scheduled_task,
    create_space_in_brain,
    schedule_task,
    supervisor_node,
)


@pytest.mark.asyncio
async def test_cancel_scheduled_task_not_found():
    """cancel_scheduled_task returns not_found error when job does not exist."""
    res = await cancel_scheduled_task.ainvoke({"job_id": "nonexistent_job_12345"})
    assert "no encontrada" in res or "not_found" in res


@pytest.mark.asyncio
async def test_schedule_task_misfire_grace_time():
    """schedule_task adds job with misfire_grace_time=3600."""
    with patch("app.main.scheduler.add_job") as mock_add_job, patch("app.main.register_scheduled_task"):
        res = await schedule_task.coroutine(
            title="Test Task",
            trigger_time_iso="2026-09-30T10:00:00-03:00",
            task_type="reminder",
            payload={"message": "hello"},
            user_id="fsirio",
            thread_id="123",
        )
        assert "agendada exitosamente" in res
        mock_add_job.assert_called_once()
        _, kwargs = mock_add_job.call_args
        assert kwargs.get("misfire_grace_time") == 3600


def test_list_active_scheduled_tasks_reconciliation(tmp_path, monkeypatch):
    """list_active_scheduled_tasks marks tasks as expired if not in apscheduler_jobs."""
    test_db = tmp_path / "test_orch.db"
    monkeypatch.setattr("app.db.DB_PATH", test_db)

    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE scheduled_tasks (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            intent_id TEXT,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL,
            task_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            trigger_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE apscheduler_jobs (
            id TEXT PRIMARY KEY,
            next_run_time REAL,
            job_state BLOB
        )
    """)
    # Insert 2 tasks: job_real exists in apscheduler_jobs, job_ghost does not
    cursor.execute("""
        INSERT INTO scheduled_tasks (id, job_id, user_id, chat_id, title, task_type, payload, trigger_time, status)
        VALUES ('1', 'job_real', 'fsirio', '123', 'Real', 'reminder', '{}', '2026-09-30T10:00:00', 'scheduled')
    """)
    cursor.execute("""
        INSERT INTO scheduled_tasks (id, job_id, user_id, chat_id, title, task_type, payload, trigger_time, status)
        VALUES ('2', 'job_ghost', 'fsirio', '123', 'Ghost', 'reminder', '{}', '2026-09-20T10:00:00', 'scheduled')
    """)
    cursor.execute("""
        INSERT INTO apscheduler_jobs (id, next_run_time) VALUES ('job_real', 123456789.0)
    """)
    conn.commit()
    conn.close()

    active = list_active_scheduled_tasks()
    assert len(active) == 1
    assert active[0]["job_id"] == "job_real"

    ghost = get_scheduled_task_by_job_id("job_ghost")
    assert ghost is not None
    assert ghost["status"] == "expired"


@pytest.mark.asyncio
async def test_create_space_in_brain_tool():
    """create_space_in_brain delegates to brain_create_space MCP tool."""
    with patch("app.main._invoke_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = '{"status": "ok", "space": "work-dataart"}'
        res = await create_space_in_brain.ainvoke(
            {
                "name": "work-dataart",
                "owner": "fsirio",
                "space_type": "work",
                "description": "DataArt contract work",
            }
        )
        assert "work-dataart" in res
        mock_mcp.assert_called_once_with(
            "brain_create_space",
            {
                "name": "work-dataart",
                "owner": "fsirio",
                "space_type": "work",
                "description": "DataArt contract work",
            },
        )


@pytest.mark.asyncio
async def test_supervisor_node_injects_time_and_dynamic_spaces():
    """supervisor_node dynamically injects current system time and user allowed spaces into prompt."""
    state: AgentState = {
        "messages": [HumanMessage(content="Hola, ¿en qué espacios puedo buscar?")],
        "user_id": "mercedes",
        "loop_count": 0,
    }

    mock_llm = MagicMock()
    mock_llm_with_tools = AsyncMock()
    mock_llm.bind_tools.return_value = mock_llm_with_tools
    mock_llm_with_tools.ainvoke.return_value = MagicMock(content="Respuesta")

    with (
        patch("app.main.get_chat_model", return_value=mock_llm),
        patch(
            "app.main.get_user_allowed_spaces",
            new_callable=AsyncMock,
            return_value=["personal-mercedes", "shared"],
        ),
    ):
        result = await supervisor_node(state)
        assert result["loop_count"] == 1
        mock_llm_with_tools.ainvoke.assert_called_once()
        messages_sent = mock_llm_with_tools.ainvoke.call_args[0][0]
        sys_msg = messages_sent[0].content

        # Verify time injection
        assert "CURRENT SYSTEM TIME:" in sys_msg
        assert "Never schedule tasks in the past" in sys_msg

        # Verify dynamic spaces injection for mercedes
        assert "KNOWLEDGE SPACES FOR USER 'mercedes':" in sys_msg
        assert "'personal-mercedes'" in sys_msg
        assert "'shared'" in sys_msg
        assert "'work'" not in sys_msg
