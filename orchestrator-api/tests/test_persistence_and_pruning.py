import asyncio
from unittest.mock import AsyncMock, MagicMock
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
    thread_id = "test_thread_123"
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
    thread_id = "test_thread_ingest"
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
