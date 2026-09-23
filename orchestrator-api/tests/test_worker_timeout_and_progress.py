import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.main import (
    check_critical_error,
    check_needs_input,
    format_duration_for_agy,
    format_timeout_human,
    get_worker_async_timeout,
    get_worker_heartbeat_interval,
    get_worker_progress_mode,
    get_worker_sync_timeout,
    parse_env_float,
    run_background_worker_task,
    run_ssh_worker_command,
)

# ==========================================
# 1. Tests for Env Parsing & Defaults
# ==========================================


def test_parse_env_float_valid_and_invalid(monkeypatch):
    """Verify parsing floats from environment with fallbacks for invalid values."""
    monkeypatch.setenv("TEST_TIMEOUT", "1200")
    assert parse_env_float("TEST_TIMEOUT", 900.0) == 1200.0

    monkeypatch.setenv("TEST_TIMEOUT", "not-a-number")
    assert parse_env_float("TEST_TIMEOUT", 900.0) == 900.0

    monkeypatch.setenv("TEST_TIMEOUT", "-50")
    assert parse_env_float("TEST_TIMEOUT", 900.0) == 900.0

    monkeypatch.delenv("TEST_TIMEOUT", raising=False)
    assert parse_env_float("TEST_TIMEOUT", 900.0) == 900.0


def test_worker_timeout_env_getters_defaults(monkeypatch):
    """Verify defaults for sync (900s) and async (14400s) timeouts."""
    monkeypatch.delenv("WORKER_SYNC_TIMEOUT", raising=False)
    monkeypatch.delenv("WORKER_ASYNC_TIMEOUT", raising=False)
    monkeypatch.delenv("WORKER_PROGRESS_MODE", raising=False)
    monkeypatch.delenv("WORKER_HEARTBEAT_INTERVAL", raising=False)

    assert get_worker_sync_timeout() == 900.0
    assert get_worker_async_timeout() == 14400.0
    assert get_worker_progress_mode() == "final_only"
    assert get_worker_heartbeat_interval() == 2700.0


def test_worker_timeout_env_overrides(monkeypatch):
    """Verify env overrides for timeouts and progress mode."""
    monkeypatch.setenv("WORKER_SYNC_TIMEOUT", "600")
    monkeypatch.setenv("WORKER_ASYNC_TIMEOUT", "7200")
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "periodic")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL", "1800")

    assert get_worker_sync_timeout() == 600.0
    assert get_worker_async_timeout() == 7200.0
    assert get_worker_progress_mode() == "periodic"
    assert get_worker_heartbeat_interval() == 1800.0


def test_worker_progress_mode_invalid_fallback(monkeypatch):
    """Verify invalid progress mode falls back to 'final_only'."""
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "invalid_mode_xyz")
    assert get_worker_progress_mode() == "final_only"


# ==========================================
# 2. Tests for Duration Formatting
# ==========================================


def test_format_duration_for_agy():
    """Verify Go duration string formatting for agy CLI."""
    assert format_duration_for_agy(900) == "15m"
    assert format_duration_for_agy(14400) == "4h"
    assert format_duration_for_agy(3600) == "1h"
    assert format_duration_for_agy(120) == "2m"
    assert format_duration_for_agy(45) == "45s"
    assert format_duration_for_agy(0) == "0s"


def test_format_timeout_human():
    """Verify human-readable timeout text in Spanish."""
    assert format_timeout_human(900) == "15 minutos"
    assert format_timeout_human(14400) == "4 horas"
    assert format_timeout_human(3600) == "1 hora"
    assert format_timeout_human(60) == "1 minuto"
    assert format_timeout_human(45) == "45 segundos"


# ==========================================
# 3. Tests for Timeout Selection by Mode
# ==========================================


@pytest.mark.asyncio
async def test_run_ssh_worker_command_sync_timeout_flag(monkeypatch):
    """Verify that sync mode uses WORKER_SYNC_TIMEOUT and --print-timeout 15m by default."""
    monkeypatch.delenv("WORKER_SYNC_TIMEOUT", raising=False)

    captured_cmd = []

    async def mock_subprocess_exec(*args, **_kwargs):
        captured_cmd.extend(args)
        proc = AsyncMock()
        proc.stdout.readline.side_effect = [b"", b""]
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        code, _stdout, _stderr = await run_ssh_worker_command(
            task="test task",
            target_project="test_proj",
            mode="sync",
        )
        assert code == 0
        remote_cmd = captured_cmd[-1]
        assert "--print-timeout 15m" in remote_cmd


@pytest.mark.asyncio
async def test_run_ssh_worker_command_async_timeout_flag(monkeypatch):
    """Verify that async mode uses WORKER_ASYNC_TIMEOUT and --print-timeout 4h by default."""
    monkeypatch.delenv("WORKER_ASYNC_TIMEOUT", raising=False)

    captured_cmd = []

    async def mock_subprocess_exec(*args, **_kwargs):
        captured_cmd.extend(args)
        proc = AsyncMock()
        proc.stdout.readline.side_effect = [b"", b""]
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        code, _stdout, _stderr = await run_ssh_worker_command(
            task="long async task",
            target_project="test_proj",
            mode="async",
        )
        assert code == 0
        remote_cmd = captured_cmd[-1]
        assert "--print-timeout 4h" in remote_cmd


@pytest.mark.asyncio
async def test_run_ssh_worker_command_timeout_env_override(monkeypatch):
    """Verify that setting env overrides dynamic timeout in command flags."""
    monkeypatch.setenv("WORKER_ASYNC_TIMEOUT", "7200")  # 2 hours

    captured_cmd = []

    async def mock_subprocess_exec(*args, **_kwargs):
        captured_cmd.extend(args)
        proc = AsyncMock()
        proc.stdout.readline.side_effect = [b"", b""]
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        code, _stdout, _stderr = await run_ssh_worker_command(
            task="override task",
            target_project="test_proj",
            mode="async",
        )
        assert code == 0
        remote_cmd = captured_cmd[-1]
        assert "--print-timeout 2h" in remote_cmd


# ==========================================
# 4. Tests for Needs Input & Error Detection
# ==========================================


def test_check_needs_input_patterns():
    """Verify detection of various approval/input keywords."""
    assert check_needs_input("We need user approval before continuing")
    assert check_needs_input("Worker needs input on database migration")
    assert check_needs_input("Esta acción requiere aprobación del administrador")
    assert check_needs_input("Please confirm deletion")
    assert check_needs_input("Requiere HITL para continuar")
    assert check_needs_input("Se generó un plan para aprobación")
    assert check_needs_input("state: waiting_for_input")
    assert check_needs_input("calling ask_question tool")

    # Non-matching phrases
    assert not check_needs_input("Updating files and building package")
    assert not check_needs_input("Analyzing repository structure")
    assert not check_needs_input("")


def test_check_critical_error_patterns():
    """Verify detection of critical error patterns."""
    assert check_critical_error("Fatal error: out of memory")
    assert check_critical_error("Error crítico en ejecución")
    assert check_critical_error("panic: nil pointer dereference")
    assert check_critical_error("segmentation fault (core dumped)")

    assert not check_critical_error("Normal debug trace")
    assert not check_critical_error("")


@pytest.mark.asyncio
async def test_run_ssh_worker_command_input_required_callback():
    """Verify on_input_required is called when input pattern is detected in stream."""
    lines = [
        b'{"event": "step_update", "step_update": {"step_index": 1, "tool_name": "ask_question", "text_delta": "Deseas continuar?"}}\n',
        b'{"event": "result", "result": {"response": "All done"}}\n',
        b"",
    ]

    async def mock_subprocess_exec(*_args, **_kwargs):
        proc = AsyncMock()
        proc.stdout.readline.side_effect = lines
        proc.stderr.read = AsyncMock(return_value=b"")
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        return proc

    input_alerts = []

    async def mock_input_cb(msg):
        input_alerts.append(msg)

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        code, stdout, _stderr = await run_ssh_worker_command(
            task="interactive task",
            target_project="test_proj",
            mode="sync",
            on_input_required=mock_input_cb,
        )
        assert code == 0
        assert stdout == "All done"
        assert len(input_alerts) == 1
        assert "ask_question" in input_alerts[0] or "Deseas continuar" in input_alerts[0]


# ==========================================
# 5. Tests for Progress Modes: final_only vs periodic
# ==========================================


@pytest.mark.asyncio
async def test_run_background_worker_task_final_only(monkeypatch):
    """Verify final_only mode does NOT send periodic updates, but sends final notification."""
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "final_only")

    sent_notifications = []

    async def mock_send_telegram(user_id, msg, **_kwargs):
        sent_notifications.append((user_id, msg))

    async def mock_run_ssh(_task, _target_project, q=None, _mode="async", **_kwargs):
        # Put some standard progress updates in the queue
        if q:
            await q.put("⚙️ Worker (Paso 1): view_file")
            await q.put("🧠 Worker (Paso 2): thinking")
        await asyncio.sleep(0.1)
        return 0, "Task finished successfully!", ""

    with (
        patch("app.main.send_telegram_notification", side_effect=mock_send_telegram),
        patch("app.main.run_ssh_worker_command", side_effect=mock_run_ssh),
        patch("app.main.register_background_worker"),
        patch("app.main.complete_background_worker"),
        patch("app.main.update_background_worker_progress"),
    ):
        await run_background_worker_task(
            user_id="fsirio",
            chat_id="12345",
            task="do something async",
            target_project="project_a",
        )

        # In final_only, only the completion message should have been sent (no 45s periodic updates)
        assert len(sent_notifications) == 1
        assert "Worker Completado con Éxito" in sent_notifications[0][1]
        assert "Task finished successfully!" in sent_notifications[0][1]


@pytest.mark.asyncio
async def test_run_background_worker_task_input_notification_in_final_only(monkeypatch):
    """Verify that in final_only mode, notifications for input-required ARE sent."""
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "final_only")

    sent_notifications = []

    async def mock_send_telegram(user_id, msg, **_kwargs):
        sent_notifications.append((user_id, msg))

    async def mock_run_ssh(_task, _target_project, _q=None, _mode="async", on_input_required=None, **_kwargs):
        if on_input_required:
            await on_input_required("Requiere aprobación de token de acceso")
        await asyncio.sleep(0.05)
        return 0, "Completed after input", ""

    with (
        patch("app.main.send_telegram_notification", side_effect=mock_send_telegram),
        patch("app.main.run_ssh_worker_command", side_effect=mock_run_ssh),
        patch("app.main.register_background_worker"),
        patch("app.main.complete_background_worker"),
        patch("app.main.update_background_worker_progress"),
    ):
        await run_background_worker_task(
            user_id="fsirio",
            chat_id="12345",
            task="need user credentials",
            target_project="project_a",
        )

        # Should have sent 2 notifications: 1 for input required, 1 for final completion
        assert len(sent_notifications) == 2
        assert "Worker Requiere Atención" in sent_notifications[0][1]
        assert "Requiere aprobación" in sent_notifications[0][1]
        assert "Worker Completado con Éxito" in sent_notifications[1][1]


@pytest.mark.asyncio
async def test_run_background_worker_task_heartbeat_in_final_only(monkeypatch):
    """Verify that in final_only mode, heartbeat is sent if inactivity exceeds WORKER_HEARTBEAT_INTERVAL."""
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "final_only")
    # Set a tiny heartbeat interval for testing (0.1 seconds)
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL", "0.1")

    sent_notifications = []

    async def mock_send_telegram(user_id, msg, **_kwargs):
        sent_notifications.append((user_id, msg))

    async def mock_run_ssh(_task, _target_project, _q=None, _mode="async", **_kwargs):
        # Wait 0.3s without emitting anything to queue
        await asyncio.sleep(0.3)
        return 0, "Done after delay", ""

    with (
        patch("app.main.send_telegram_notification", side_effect=mock_send_telegram),
        patch("app.main.run_ssh_worker_command", side_effect=mock_run_ssh),
        patch("app.main.register_background_worker"),
        patch("app.main.complete_background_worker"),
        patch("app.main.update_background_worker_progress"),
    ):
        await run_background_worker_task(
            user_id="fsirio",
            chat_id="12345",
            task="long silent task",
            target_project="project_a",
        )

        # Should have sent at least 1 heartbeat notification and 1 completion notification
        assert len(sent_notifications) >= 2
        heartbeat_msgs = [m for _, m in sent_notifications if "Tarea en curso" in m]
        assert len(heartbeat_msgs) >= 1
        assert "Worker Completado con Éxito" in sent_notifications[-1][1]


@pytest.mark.asyncio
async def test_run_background_worker_task_periodic_mode(monkeypatch):
    """Verify that in periodic mode, periodic progress reports are sent to Telegram."""
    monkeypatch.setenv("WORKER_PROGRESS_MODE", "periodic")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL", "0.1")

    sent_notifications = []

    async def mock_send_telegram(user_id, msg, **_kwargs):
        sent_notifications.append((user_id, msg))

    async def mock_run_ssh(_task, _target_project, q=None, _mode="async", **_kwargs):
        if q:
            await q.put("⚙️ Worker (Paso 1): test running")
        await asyncio.sleep(0.15)
        return 0, "Periodic task complete", ""

    with (
        patch("app.main.send_telegram_notification", side_effect=mock_send_telegram),
        patch("app.main.run_ssh_worker_command", side_effect=mock_run_ssh),
        patch("app.main.register_background_worker"),
        patch("app.main.complete_background_worker"),
        patch("app.main.update_background_worker_progress"),
    ):
        await run_background_worker_task(
            user_id="fsirio",
            chat_id="12345",
            task="test periodic reporting",
            target_project="project_a",
        )

    # In periodic mode, we should see periodic report AND completion
    periodic_msgs = [m for _, m in sent_notifications if "Avance Worker" in m]
    assert len(periodic_msgs) >= 1
    assert "Worker Completado con Éxito" in sent_notifications[-1][1]
