import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "orchestrator.db"


def _load_env_json(var_name: str) -> dict[str, str]:
    val = os.getenv(var_name, "").strip()
    if not val:
        return {}
    try:
        data = json.loads(val)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logger.warning(f"Failed to parse {var_name} from environment: {e}")
    return {}


CANONICAL_USERS = _load_env_json("CANONICAL_USER_MAPPING")
CANONICAL_ALIASES = _load_env_json("CANONICAL_USER_ALIASES")


def init_db():
    """Initializes SQLite database and tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            platform_user_id TEXT NOT NULL UNIQUE
        )
    """)

    # Plans pending human approval (e.g. Mercedes -> Fsirio)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS approval_plans (
            id TEXT PRIMARY KEY,
            requester_id TEXT NOT NULL,
            requester_telegram_id TEXT NOT NULL,
            title TEXT NOT NULL,
            plan_details TEXT NOT NULL,
            task TEXT NOT NULL,
            target_project TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected, executed
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP
        )
    """)

    # Background workers execution tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS background_workers (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            task TEXT NOT NULL,
            target_project TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running', -- running, completed, failed
            last_status_msg TEXT,
            result TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    """)

    # Scheduled tasks tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            intent_id TEXT,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL,
            task_type TEXT NOT NULL, -- weather_check, reminder, worker_execute
            payload TEXT NOT NULL, -- json string
            trigger_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled, triggered, cancelled
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for tid, pid in CANONICAL_USERS.items():
        cursor.execute(
            """
            INSERT INTO users (telegram_id, platform_user_id) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET platform_user_id = excluded.platform_user_id
        """,
            (tid, pid),
        )
    conn.commit()
    conn.close()
    logger.info(f"Database initialized with schema extensions at {DB_PATH}")


def get_user_mapping():
    """Returns current {telegram_id: platform_user_id} mapping."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, platform_user_id FROM users")
    mapping = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    for tid, pid in CANONICAL_USERS.items():
        mapping[tid] = pid
    return mapping


def register_user(telegram_id: str, platform_user_id: str):
    """Registers a new user in database."""
    if telegram_id in CANONICAL_USERS:
        platform_user_id = CANONICAL_USERS[telegram_id]
    elif platform_user_id.lower() in CANONICAL_ALIASES:
        platform_user_id = CANONICAL_ALIASES[platform_user_id.lower()]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (telegram_id, platform_user_id) VALUES (?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET platform_user_id = excluded.platform_user_id",
            (telegram_id, platform_user_id),
        )
        conn.commit()
        logger.info(f"Registered user: {telegram_id} -> {platform_user_id}")
        return True
    except Exception as e:
        logger.warning(f"Error registering user {telegram_id} -> {platform_user_id}: {e}")
        return False
    finally:
        conn.close()


def get_platform_id(telegram_id: str):
    """Retrieves platform_user_id for a given telegram_id."""
    if telegram_id in CANONICAL_USERS:
        return CANONICAL_USERS[telegram_id]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT platform_user_id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def get_telegram_id(platform_user_id: str):
    """Retrieves telegram_id for a given platform_user_id."""
    canonical_target = CANONICAL_ALIASES.get(platform_user_id.lower(), platform_user_id)
    for tid, pid in CANONICAL_USERS.items():
        if pid == canonical_target:
            return tid
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE platform_user_id = ?", (platform_user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# --- Approval Plans DB Helpers ---

def create_approval_plan(plan_id: str, requester_id: str, requester_telegram_id: str, title: str, plan_details: str, task: str, target_project: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO approval_plans (id, requester_id, requester_telegram_id, title, plan_details, task, target_project, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (plan_id, requester_id, requester_telegram_id, title, plan_details, task, target_project))
    conn.commit()
    conn.close()


def get_approval_plan(plan_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM approval_plans WHERE id = ?", (plan_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_approval_plan_status(plan_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE approval_plans SET status = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, plan_id))
    conn.commit()
    conn.close()


# --- Background Workers DB Helpers ---

def register_background_worker(worker_id: str, user_id: str, chat_id: str, task: str, target_project: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO background_workers (id, user_id, chat_id, task, target_project, status)
        VALUES (?, ?, ?, ?, ?, 'running')
    """, (worker_id, user_id, chat_id, task, target_project))
    conn.commit()
    conn.close()


def update_background_worker_progress(worker_id: str, last_status_msg: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE background_workers SET last_status_msg = ? WHERE id = ?", (last_status_msg, worker_id))
    conn.commit()
    conn.close()


def complete_background_worker(worker_id: str, status: str, result: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE background_workers 
        SET status = ?, result = ?, finished_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    """, (status, result, worker_id))
    conn.commit()
    conn.close()


# --- Scheduled Tasks DB Helpers ---

def register_scheduled_task(task_id: str, job_id: str, intent_id: str | None, user_id: str, chat_id: str, title: str, task_type: str, payload: dict, trigger_time: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO scheduled_tasks (id, job_id, intent_id, user_id, chat_id, title, task_type, payload, trigger_time, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')
    """, (task_id, job_id, intent_id, user_id, chat_id, title, task_type, json.dumps(payload), trigger_time))
    conn.commit()
    conn.close()


def list_active_scheduled_tasks() -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scheduled_tasks WHERE status = 'scheduled' ORDER BY trigger_time ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def update_scheduled_task_status(job_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE scheduled_tasks SET status = ? WHERE job_id = ?", (status, job_id))
    conn.commit()
    conn.close()
