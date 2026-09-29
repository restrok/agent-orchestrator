"""Unit tests verifying user_id injection and hybrid spaces propagation for Exocortex MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from models import AgentState

from app.main import (
    _invoke_mcp_tool,
    remember_in_brain,
    search_brain,
)


@pytest.mark.asyncio
async def test_invoke_mcp_tool_propagates_user_id_in_payload():
    """Verify that _invoke_mcp_tool injects user_id into the JSON-RPC arguments payload."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = 'data: {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"text": "Result from brain"}]}}\n\n'

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        # Case 1: user_id passed as parameter
        result = await _invoke_mcp_tool(
            "brain_search",
            {"query": "honda fit", "limit": 3},
            user_id="mercedes",
        )
        assert result == "Result from brain"
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["name"] == "brain_search"
        assert payload["params"]["arguments"]["user_id"] == "mercedes"
        assert payload["params"]["arguments"]["query"] == "honda fit"
        assert payload["params"]["arguments"]["limit"] == 3

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        # Case 2: user_id already inside arguments dict
        result = await _invoke_mcp_tool(
            "brain_remember",
            {"content": "nota", "title": "titulo", "user_id": "fsirio"},
        )
        assert result == "Result from brain"
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["name"] == "brain_remember"
        assert payload["params"]["arguments"]["user_id"] == "fsirio"


@pytest.mark.asyncio
async def test_search_brain_with_injected_state_in_graph():
    """Verify that StateGraph compiles ToolNode and injects user_id from AgentState into search_brain."""
    with patch("app.main._invoke_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = "Grounded search results"

        workflow = StateGraph(AgentState)
        workflow.add_node("tools", ToolNode([search_brain]))
        workflow.add_edge(START, "tools")
        workflow.add_edge("tools", END)
        graph = workflow.compile()

        # Simulate LLM tool call without user_id in tool arguments (InjectedState source)
        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_brain",
                    "args": {"query": "recuperacion fisiologica", "space_id": "personal"},
                    "id": "call_123",
                }
            ],
        )

        state: AgentState = {
            "messages": [ai_message],
            "user_id": "mercedes",
            "thread_id": "thread-001",
            "loop_count": 0,
            "next_worker": None,
            "intent": None,
            "intermediate_steps": [],
        }

        result = await graph.ainvoke(state)
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[1].content == "Grounded search results"

        # Verify _invoke_mcp_tool received injected user_id="mercedes"
        mock_mcp.assert_called_once_with(
            "brain_search",
            {
                "query": "recuperacion fisiologica",
                "limit": 5,
                "user_id": "mercedes",
                "space_id": "personal",
            },
            user_id="mercedes",
        )


@pytest.mark.asyncio
async def test_remember_in_brain_with_injected_state_in_graph():
    """Verify that StateGraph compiles ToolNode and injects user_id from AgentState into remember_in_brain."""
    with patch("app.main._invoke_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = "Memory persisted"

        workflow = StateGraph(AgentState)
        workflow.add_node("tools", ToolNode([remember_in_brain]))
        workflow.add_edge(START, "tools")
        workflow.add_edge("tools", END)
        graph = workflow.compile()

        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "remember_in_brain",
                    "args": {
                        "content": "Protocolo post-corticoide completado",
                        "title": "Protocolo clinico",
                        "space_id": "personal",
                    },
                    "id": "call_456",
                }
            ],
        )

        state: AgentState = {
            "messages": [ai_message],
            "user_id": "mercedes",
            "thread_id": "thread-002",
            "loop_count": 0,
            "next_worker": None,
            "intent": None,
            "intermediate_steps": [],
        }

        result = await graph.ainvoke(state)
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[1].content == "Memory persisted"

        # Verify _invoke_mcp_tool received injected user_id="mercedes"
        mock_mcp.assert_called_once_with(
            "brain_remember",
            {
                "content": "Protocolo post-corticoide completado",
                "title": "Protocolo clinico",
                "space_id": "personal",
                "user_id": "mercedes",
            },
            user_id="mercedes",
        )


@pytest.mark.asyncio
async def test_search_brain_default_space_omitted_for_backend_resolution():
    """Verify that when space_id is not specified in search_brain, backend gets space_id=None."""
    with patch("app.main._invoke_mcp_tool", new_callable=AsyncMock) as mock_mcp:
        mock_mcp.return_value = "Search result"

        workflow = StateGraph(AgentState)
        workflow.add_node("tools", ToolNode([search_brain]))
        workflow.add_edge(START, "tools")
        workflow.add_edge("tools", END)
        graph = workflow.compile()

        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_brain",
                    "args": {"query": "service honda fit"},
                    "id": "call_789",
                }
            ],
        )

        state: AgentState = {
            "messages": [ai_message],
            "user_id": "fsirio",
            "thread_id": "thread-003",
            "loop_count": 0,
            "next_worker": None,
            "intent": None,
            "intermediate_steps": [],
        }

        await graph.ainvoke(state)

        # space_id should not be in args dictionary if omitted
        mock_mcp.assert_called_once_with(
            "brain_search",
            {
                "query": "service honda fit",
                "limit": 5,
                "user_id": "fsirio",
            },
            user_id="fsirio",
        )
