"""Pytest configuration and global fixtures for isolating test databases from production."""

import contextlib
import os
from pathlib import Path

import pytest


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """Set temporary test database paths using tmp_path_factory before test collection."""
    os.environ.setdefault("ORCHESTRATOR_TESTING", "true")
    if not os.getenv("ORCHESTRATOR_DB_PATH") or not os.getenv("ORCHESTRATOR_CHECKPOINTS_DB_PATH"):
        tmp_factory = getattr(config, "_tmp_path_factory", None)
        if tmp_factory:
            tmp_dir = tmp_factory.mktemp("orchestrator_session_data")
        else:
            import tempfile

            tmp_dir = Path(tempfile.mkdtemp(prefix="orch_session_"))
        if not os.getenv("ORCHESTRATOR_DB_PATH"):
            test_db = tmp_dir / "orchestrator_test.db"
            os.environ["ORCHESTRATOR_DB_PATH"] = str(test_db)
        if not os.getenv("ORCHESTRATOR_CHECKPOINTS_DB_PATH"):
            test_checkpoints = tmp_dir / "checkpoints_test.db"
            os.environ["ORCHESTRATOR_CHECKPOINTS_DB_PATH"] = str(test_checkpoints)


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database(tmp_path_factory):
    """Session-scoped fixture setting ORCHESTRATOR_DB_PATH and ORCHESTRATOR_CHECKPOINTS_DB_PATH.

    Initializes the SQLite schema in an isolated temporary database before any test runs.
    """
    os.environ.setdefault("ORCHESTRATOR_TESTING", "true")
    if "ORCHESTRATOR_DB_PATH" in os.environ and "ORCHESTRATOR_CHECKPOINTS_DB_PATH" in os.environ:
        test_db = Path(os.environ["ORCHESTRATOR_DB_PATH"])
        test_checkpoints = Path(os.environ["ORCHESTRATOR_CHECKPOINTS_DB_PATH"])
    else:
        tmp_dir = tmp_path_factory.mktemp("orchestrator_session_data")
        test_db = tmp_dir / "orchestrator_test.db"
        test_checkpoints = tmp_dir / "checkpoints_test.db"
        os.environ["ORCHESTRATOR_DB_PATH"] = str(test_db)
        os.environ["ORCHESTRATOR_CHECKPOINTS_DB_PATH"] = str(test_checkpoints)

    import app.db

    app.db.DB_PATH = test_db
    app.db.CHECKPOINTS_DB_PATH = test_checkpoints
    app.db.DB_DIR = test_db.parent

    app.db.init_db()

    import sys

    if "app.main" in sys.modules:
        import app.main

        app.main.DB_PATH = test_db

    yield test_db

    # Teardown: clean up environment variables and temporary test database files
    for env_key in ("ORCHESTRATOR_DB_PATH", "ORCHESTRATOR_CHECKPOINTS_DB_PATH", "ORCHESTRATOR_TESTING"):
        os.environ.pop(env_key, None)

    for db_path in (test_db, test_checkpoints):
        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ):
            if candidate.exists():
                with contextlib.suppress(OSError):
                    candidate.unlink()
