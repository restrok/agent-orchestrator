import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch as unittest_patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from app.db import (
    get_chat_messages,
    get_pending_for_ingest,
    init_db,
    insert_chat_message,
    mark_ingested,
)
from app.main import get_thread_lock
from app.state_pruning import prune_state_if_needed


@pytest.fixture(autouse=True)
def setup_database():
    init_db()


def test_insert_and_get_chat_messages():
    thread_id = f"test_thread_{uuid.uuid4().hex[:8]}"
    user_id = "test_user_456"

    # Insert user message
    msg_id1 = insert_chat_message(
        thread_id=thread_id,
        user_id=user_id,
        role="user",
        content="Hola, ¿cómo estás?",
        intent="general_conversation",
        source="chat",
    )
    assert msg_id1 > 0

    # Insert assistant message
    msg_id2 = insert_chat_message(
        thread_id=thread_id,
        user_id=user_id,
        role="assistant",
        content="¡Hola! Todo excelente por aquí.",
        intent="general_conversation",
        source="chat",
    )
    assert msg_id2 > msg_id1

    # Query by thread_id
    msgs = get_chat_messages(thread_id=thread_id, limit=10)
    assert len(msgs) >= 2
    user_msgs = [m for m in msgs if m["role"] == "user" and m["id"] == msg_id1]
    asst_msgs = [m for m in msgs if m["role"] == "assistant" and m["id"] == msg_id2]

    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Hola, ¿cómo estás?"
    assert user_msgs[0]["source"] == "chat"
    assert user_msgs[0]["ingested_to_brain"] == 0

    assert len(asst_msgs) == 1
    assert asst_msgs[0]["content"] == "¡Hola! Todo excelente por aquí."


def test_pending_ingest_and_mark_ingested():
    thread_id = f"test_thread_ingest_{uuid.uuid4().hex[:8]}"
    user_id = "test_user_ingest"

    msg_id = insert_chat_message(
        thread_id=thread_id,
        user_id=user_id,
        role="user",
        content="Mensaje para brain janitor",
        source="chat",
    )

    pending = get_pending_for_ingest(limit=100)
    pending_ids = [m["id"] for m in pending]
    assert msg_id in pending_ids

    # Mark ingested
    updated_count = mark_ingested([msg_id])
    assert updated_count == 1

    # Verify no longer in pending
    pending_after = get_pending_for_ingest(limit=100)
    assert msg_id not in [m["id"] for m in pending_after]


def test_get_thread_lock():
    lock1 = get_thread_lock("user_thread_1")
    lock2 = get_thread_lock("user_thread_1")
    lock3 = get_thread_lock("user_thread_2")

    assert lock1 is lock2
    assert lock1 is not lock3
    assert isinstance(lock1, asyncio.Lock)


@pytest.mark.asyncio
async def test_pruning_under_threshold():
    graph_mock = AsyncMock()
    # 20 messages <= 24 HWM
    msgs = [HumanMessage(content=f"msg {i}", id=f"id_{i}") for i in range(20)]
    state_mock = MagicMock()
    state_mock.values = {"messages": msgs}
    graph_mock.aget_state.return_value = state_mock

    config = {"configurable": {"thread_id": "thread_test"}}
    removed = await prune_state_if_needed(graph_mock, config)
    assert removed == 0
    graph_mock.aupdate_state.assert_not_called()


@pytest.mark.asyncio
async def test_pruning_above_threshold_simple():
    graph_mock = AsyncMock()
    # 26 messages > 24 HWM -> should remove 26 - 10 = 16 messages, keeping 10
    msgs = [HumanMessage(content=f"msg {i}", id=f"id_{i}") for i in range(26)]
    state_mock = MagicMock()
    state_mock.values = {"messages": msgs}
    graph_mock.aget_state.return_value = state_mock

    config = {"configurable": {"thread_id": "thread_test"}}
    removed = await prune_state_if_needed(graph_mock, config)
    assert removed == 16
    graph_mock.aupdate_state.assert_called_once()
    call_args = graph_mock.aupdate_state.call_args[0]
    assert call_args[0] == config
    removals = call_args[1]["messages"]
    assert len(removals) == 16
    assert all(isinstance(r, RemoveMessage) for r in removals)
    assert [r.id for r in removals] == [f"id_{i}" for i in range(16)]


@pytest.mark.asyncio
async def test_pruning_safe_tool_cut():
    graph_mock = AsyncMock()
    # 25 messages. target_remove_count = 15.
    # At index 14: AIMessage with tool_calls
    # At index 15: ToolMessage
    # Normal cut at 15 would remove up to 14 (leaving index 15 ToolMessage orphaned!)
    # Safe logic should advance cut to index 16 to remove the ToolMessage as well.
    msgs = []
    for i in range(14):
        msgs.append(HumanMessage(content=f"msg {i}", id=f"id_{i}"))

    ai_tool = AIMessage(
        content="executing tool",
        id="id_14",
        tool_calls=[{"name": "test_tool", "args": {}, "id": "call_1"}],
    )
    tool_msg = ToolMessage(content="tool output", id="id_15", tool_call_id="call_1")
    msgs.append(ai_tool)
    msgs.append(tool_msg)

    for i in range(16, 25):
        msgs.append(HumanMessage(content=f"msg {i}", id=f"id_{i}"))

    assert len(msgs) == 25
    state_mock = MagicMock()
    state_mock.values = {"messages": msgs}
    graph_mock.aget_state.return_value = state_mock

    config = {"configurable": {"thread_id": "thread_test"}}
    removed = await prune_state_if_needed(graph_mock, config)
    # Target was 15, but safe cut advances to 16 so tool_msg (id_15) is removed along with ai_tool (id_14)
    assert removed == 16
    call_args = graph_mock.aupdate_state.call_args[0]
    removals = call_args[1]["messages"]
    removal_ids = [r.id for r in removals]
    assert "id_14" in removal_ids
    assert "id_15" in removal_ids


def test_db_connection_wal_and_busy_timeout():
    from app.db import get_db_connection

    conn = get_db_connection()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert journal_mode.lower() == "wal"
        assert busy_timeout == 5000
    finally:
        conn.close()


def test_get_graph_outside_lifespan_error_in_prod(monkeypatch):
    import app.main

    monkeypatch.setattr(app.main, "graph", None)
    monkeypatch.delenv("ORCHESTRATOR_TESTING", raising=False)
    monkeypatch.delenv("TESTING", raising=False)

    with pytest.raises(RuntimeError, match="refusing to fall back to MemorySaver in production"):
        app.main.get_graph()


def test_get_graph_outside_lifespan_fallback_in_testing(monkeypatch):
    import app.main

    monkeypatch.setattr(app.main, "graph", None)
    monkeypatch.setenv("ORCHESTRATOR_TESTING", "true")

    g = app.main.get_graph()
    assert g is not None


@pytest.mark.asyncio
async def test_execute_scheduled_job_injects_into_agent_state(monkeypatch):
    """Verify scheduled reminders and weather checks are persisted into chat_messages and LangGraph state."""
    from fastapi.testclient import TestClient

    from app.main import app, execute_scheduled_job, get_graph

    monkeypatch.setenv("ORCHESTRATOR_TESTING", "true")
    get_graph()
    thread_id = f"sched_thread_{uuid.uuid4().hex[:8]}"

    with (
        unittest_patch("app.main.send_telegram_notification", new_callable=AsyncMock) as mock_send,
        unittest_patch("app.main.update_scheduled_task_status"),
    ):
        from app.main import TelegramNotificationResult

        mock_send.return_value = TelegramNotificationResult(
            success=True, chunks_sent=1, chunks_total=1, telegram_message_id="555", status="sent", chat_id=thread_id
        )

        await execute_scheduled_job(
            job_id="job_rem_1",
            task_type="reminder",
            user_id="fsirio",
            chat_id=thread_id,
            title="Rotar token",
            payload={"message": "Recordar rotar el token a las 12"},
        )

    # Check chat_messages
    msgs = get_chat_messages(thread_id=thread_id, source="notification")
    assert len(msgs) == 1
    assert "Recordatorio: Rotar token" in msgs[0]["content"]

    # Check LangGraph AgentState via /api/state/{thread_id}
    client = TestClient(app)
    resp = client.get(f"/api/state/{thread_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_message_count"] >= 1
    assert any("Recordatorio: Rotar token" in m["content"] for m in data["messages"])


@pytest.mark.asyncio
async def test_background_worker_completion_injects_into_agent_state(monkeypatch):
    """Verify background worker completion is recorded in AgentState and chat_messages, while periodic progress is not."""
    from app.main import TelegramNotificationResult, get_graph, run_background_worker_task

    monkeypatch.setenv("ORCHESTRATOR_TESTING", "true")
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "periodic")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL", "0.05")
    get_graph()
    thread_id = f"worker_thread_{uuid.uuid4().hex[:8]}"

    async def mock_send_telegram(_user_id, _msg, **_kwargs):
        return TelegramNotificationResult(
            success=True, chunks_sent=1, chunks_total=1, telegram_message_id="777", status="sent", chat_id=thread_id
        )

    async def mock_run_ssh(_task, _target_project, q=None, _mode="async", **_kwargs):
        if q:
            await q.put("⚙️ Worker (Paso 1): running")
        await asyncio.sleep(0.1)
        return 0, "Backup verificado sin errores", ""

    with (
        unittest_patch("app.main.send_telegram_notification", side_effect=mock_send_telegram),
        unittest_patch("app.main.run_ssh_worker_command", side_effect=mock_run_ssh),
        unittest_patch("app.main.register_background_worker"),
        unittest_patch("app.main.complete_background_worker"),
        unittest_patch("app.main.update_background_worker_progress"),
    ):
        await run_background_worker_task(
            user_id="fsirio",
            chat_id=thread_id,
            task="Verificar backups",
            target_project="homelab",
        )

    # Periodic progress should NOT pollute chat_messages/AgentState; only completion should be recorded
    msgs = get_chat_messages(thread_id=thread_id, source="notification")
    assert len(msgs) == 1
    assert "Worker Completado con Éxito" in msgs[0]["content"]
    assert "Avance Worker" not in msgs[0]["content"]

    state = await get_graph().aget_state({"configurable": {"thread_id": thread_id}})
    state_contents = [m.content for m in state.values.get("messages", [])]
    assert any("Worker Completado con Éxito" in c for c in state_contents)
    assert not any("Avance Worker" in c for c in state_contents)


@pytest.mark.asyncio
async def test_node_router_passes_recent_context():
    """Verify node_router passes up to 4 recent messages so follow-ups to notifications are contextualized."""
    from app.main import node_router
    from app.models import IntentClassifier

    captured_messages = []

    class DummyStructuredLLM:
        async def ainvoke(self, msgs):
            captured_messages.extend(msgs)
            return IntentClassifier(intent="general_chat", rationale="Contextual follow-up")

    class DummyLLM:
        def with_structured_output(self, *_args, **_kwargs):
            return DummyStructuredLLM()

    with unittest_patch("app.main.get_chat_model", return_value=DummyLLM()):
        state = {
            "messages": [
                AIMessage(content="[Notificación de auto-healer]: 🟢 Homelab Auto-Healing Report"),
                HumanMessage(content="¿Qué significa este reporte?"),
            ],
            "user_id": "fsirio",
            "thread_id": "t_router_ctx",
            "loop_count": 0,
        }
        res = await node_router(state)
        assert res["intent"] == "general_chat"
        # SystemMessage + 2 recent messages = 3 messages passed to classifier
        assert len(captured_messages) == 3
        assert "Notificación de auto-healer" in captured_messages[1].content
        assert "¿Qué significa este reporte?" in captured_messages[2].content


@pytest.mark.asyncio
async def test_callback_action_and_conversation_state_info_tool(monkeypatch):
    """Verify POST /api/callbacks/action records inline button actions and get_conversation_state_info summarizes thread."""
    from fastapi.testclient import TestClient

    from app.main import app, get_conversation_state_info, get_graph

    monkeypatch.setenv("ORCHESTRATOR_TESTING", "true")
    get_graph()
    thread_id = f"cb_thread_{uuid.uuid4().hex[:8]}"

    client = TestClient(app)
    resp = client.post(
        "/api/callbacks/action",
        json={"job_id": "job_asado_1", "action": "keep_plan", "user_id": "fsirio", "chat_id": thread_id},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "keep_plan"

    summary = await get_conversation_state_info.ainvoke({"limit": 10, "user_id": "fsirio", "thread_id": thread_id})
    assert thread_id in summary
    assert "Asado / actividad en marcha" in summary
