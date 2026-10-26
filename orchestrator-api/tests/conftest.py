"""Pytest configuration and global fixtures for isolating test databases from production."""

import os
import tempfile
from pathlib import Path

import pytest

# Ensure any import-time resolution during pytest test collection points away from prod DB
if not os.getenv("ORCHESTRATOR_DB_PATH"):
    _early_tmp = Path(tempfile.gettempdir()) / "orch_early_test_data"
    _early_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["ORCHESTRATOR_DB_PATH"] = str(_early_tmp / "orchestrator_early.db")
    os.environ["ORCHESTRATOR_CHECKPOINTS_DB_PATH"] = str(_early_tmp / "checkpoints_early.db")


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database(tmp_path_factory):
    """Session-scoped fixture setting ORCHESTRATOR_DB_PATH and ORCHESTRATOR_CHECKPOINTS_DB_PATH.

    Initializes the SQLite schema in an isolated temporary database before any test runs.
    """
    mp = pytest.MonkeyPatch()
    tmp_dir = tmp_path_factory.mktemp("orchestrator_session_data")
    test_db = tmp_dir / "orchestrator_test.db"
    test_checkpoints = tmp_dir / "checkpoints_test.db"

    mp.setenv("ORCHESTRATOR_DB_PATH", str(test_db))
    mp.setenv("ORCHESTRATOR_CHECKPOINTS_DB_PATH", str(test_checkpoints))

    import app.db

    app.db.DB_PATH = app.db.get_db_path()
    app.db.CHECKPOINTS_DB_PATH = app.db.get_checkpoints_db_path()
    app.db.DB_DIR = app.db.DB_PATH.parent

    app.db.init_db()

    yield test_db

    mp.undo()
