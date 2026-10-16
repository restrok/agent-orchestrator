"""Unit tests for dynamic Antigravity Worker model configuration, resolution, and tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from db import get_system_setting, init_db, set_system_setting

from app.main import (
    DEFAULT_WORKER_EFFORT,
    DEFAULT_WORKER_MODEL,
    call_antigravity_worker,
    get_active_worker_config,
    manage_worker_model,
    resolve_worker_model_and_effort,
    run_ssh_worker_command,
)


@pytest.fixture(autouse=True)
def reset_worker_model_settings():
    """Ensure SQLite system_settings table is initialized and reset before/after each test."""
    init_db()
    set_system_setting("worker_model", DEFAULT_WORKER_MODEL)
    set_system_setting("worker_effort", DEFAULT_WORKER_EFFORT)
    yield
    set_system_setting("worker_model", DEFAULT_WORKER_MODEL)
    set_system_setting("worker_effort", DEFAULT_WORKER_EFFORT)


def test_resolve_worker_model_and_effort_exact_and_aliases():
    """Verify exact catalog IDs and natural language aliases resolve properly."""
    # Exact IDs
    assert resolve_worker_model_and_effort("gemini-3.8-flash-medium") == ("gemini-3.8-flash-medium", "medium")
    assert resolve_worker_model_and_effort("claude-opus-5-5-high") == ("claude-opus-5-5-high", "high")
    assert resolve_worker_model_and_effort("gemini-4-argon-low") == ("gemini-4-argon-low", "low")
    assert resolve_worker_model_and_effort("gpt-oss-120b-medium") == ("gpt-oss-120b-medium", "medium")

    # Natural language aliases
    assert resolve_worker_model_and_effort("sonnet high") == ("claude-sonnet-5-5-high", "high")
    assert resolve_worker_model_and_effort("Claude Opus Medium") == ("claude-opus-5-5-medium", "medium")
    assert resolve_worker_model_and_effort("gemini 4 argon high") == ("gemini-4-argon-high", "high")
    assert resolve_worker_model_and_effort("flash low") == ("gemini-3.8-flash-low", "low")
    assert resolve_worker_model_and_effort("gpt 120b") == ("gpt-oss-120b-medium", "medium")

    # Explicit effort parameter overrides default effort
    assert resolve_worker_model_and_effort("claude-sonnet-5-5-high", "max") == ("claude-sonnet-5-5-high", "max")

    # Unknown model returns None
    assert resolve_worker_model_and_effort("unknown-model-xyz") is None
    assert resolve_worker_model_and_effort("") is None


@pytest.mark.asyncio
async def test_manage_worker_model_lifecycle():
    """Verify status, list, set, unauthorized rejection, and reset via manage_worker_model tool."""
    # 1. Default status
    status_res = await manage_worker_model.ainvoke({"action": "status", "user_id": "fsirio"})
    assert "gemini-3.8-flash-medium" in status_res

    # 2. List models
    list_res = await manage_worker_model.ainvoke({"action": "list", "user_id": "fsirio"})
    assert "claude-sonnet-5-5-high" in list_res
    assert "gemini-4-argon-medium" in list_res

    # 3. Unauthorized user cannot change model
    unauth_res = await manage_worker_model.ainvoke(
        {"action": "set", "model_name": "sonnet high", "user_id": "mercedes"}
    )
    assert "No tenés permisos" in unauth_res
    assert get_system_setting("worker_model") == DEFAULT_WORKER_MODEL

    # 4. Authorized user changes model using alias
    set_res = await manage_worker_model.ainvoke({"action": "set", "model_name": "sonnet high", "user_id": "fsirio"})
    assert "claude-sonnet-5-5-high" in set_res
    assert get_system_setting("worker_model") == "claude-sonnet-5-5-high"
    assert get_system_setting("worker_effort") == "high"
    assert get_active_worker_config() == ("claude-sonnet-5-5-high", "high")

    # 5. Reset to default
    reset_res = await manage_worker_model.ainvoke({"action": "reset", "user_id": "fsirio"})
    assert DEFAULT_WORKER_MODEL in reset_res
    assert get_active_worker_config() == (DEFAULT_WORKER_MODEL, DEFAULT_WORKER_EFFORT)


@pytest.mark.asyncio
async def test_run_ssh_worker_command_uses_persisted_and_override_models():
    """Verify that run_ssh_worker_command passes the persisted model or per-task model_override to agy CLI."""
    captured_cmds: list[str] = []

    async def mock_subprocess_exec(*args, **_kwargs):
        captured_cmds.append(args[-1])
        proc = AsyncMock()
        proc.stdout.readline.side_effect = [b"", b""]
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    set_system_setting("worker_model", "gemini-4-argon-high")
    set_system_setting("worker_effort", "high")

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        # 1. Uses persisted SQLite setting
        code, _, _ = await run_ssh_worker_command("check status", "homelab", mode="sync")
        assert code == 0
        assert "--model gemini-4-argon-high" in captured_cmds[-1]
        assert "--effort high" in captured_cmds[-1]

        # 2. Per-task model_override takes precedence without mutating SQLite
        code, _, _ = await run_ssh_worker_command(
            "complex refactor",
            "homelab",
            mode="sync",
            model_override="opus medium",
        )
        assert code == 0
        assert "--model claude-opus-5-5-medium" in captured_cmds[-1]
        assert "--effort medium" in captured_cmds[-1]
        assert get_system_setting("worker_model") == "gemini-4-argon-high"


@pytest.mark.asyncio
async def test_call_antigravity_worker_with_model_override():
    """Verify call_antigravity_worker passes model_override and displays active model."""
    with patch("app.main.run_ssh_worker_command", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = (0, "Todo listo", "")
        res = await call_antigravity_worker.ainvoke(
            {
                "task": "Analizar logs",
                "target_project": "homelab",
                "mode": "sync",
                "model_override": "claude-sonnet-5-5-high",
                "user_id": "fsirio",
                "thread_id": "thread_1",
            }
        )
        assert "claude-sonnet-5-5-high" in res
        mock_ssh.assert_called_once()
        assert mock_ssh.call_args.kwargs.get("model_override") == "claude-sonnet-5-5-high"
