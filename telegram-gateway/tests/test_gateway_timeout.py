from main import get_worker_async_timeout


def test_get_worker_async_timeout_default(monkeypatch):
    """Verify default timeout is 14400.0 (4 hours)."""
    monkeypatch.delenv("WORKER_ASYNC_TIMEOUT", raising=False)
    assert get_worker_async_timeout() == 14400.0


def test_get_worker_async_timeout_override(monkeypatch):
    """Verify valid override."""
    monkeypatch.setenv("WORKER_ASYNC_TIMEOUT", "7200")
    assert get_worker_async_timeout() == 7200.0


def test_get_worker_async_timeout_invalid_fallback(monkeypatch):
    """Verify invalid values fall back to default."""
    monkeypatch.setenv("WORKER_ASYNC_TIMEOUT", "invalid")
    assert get_worker_async_timeout() == 14400.0

    monkeypatch.setenv("WORKER_ASYNC_TIMEOUT", "-100")
    assert get_worker_async_timeout() == 14400.0
