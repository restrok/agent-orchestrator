import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
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

    # Notifications history tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            user_id TEXT,
            chat_id TEXT,
            message TEXT NOT NULL,
            parse_mode TEXT,
            status TEXT NOT NULL, -- sent, failed, partial
            telegram_message_id TEXT,
            error TEXT,
            chunks_total INTEGER,
            chunks_sent INTEGER
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications_history (created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notifications_agent ON notifications_history (agent_id)")

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


def create_approval_plan(
    plan_id: str,
    requester_id: str,
    requester_telegram_id: str,
    title: str,
    plan_details: str,
    task: str,
    target_project: str,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO approval_plans (id, requester_id, requester_telegram_id, title, plan_details, task, target_project, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """,
        (plan_id, requester_id, requester_telegram_id, title, plan_details, task, target_project),
    )
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
    cursor.execute(
        "UPDATE approval_plans SET status = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, plan_id)
    )
    conn.commit()
    conn.close()


# --- Background Workers DB Helpers ---


def register_background_worker(worker_id: str, user_id: str, chat_id: str, task: str, target_project: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO background_workers (id, user_id, chat_id, task, target_project, status)
        VALUES (?, ?, ?, ?, ?, 'running')
    """,
        (worker_id, user_id, chat_id, task, target_project),
    )
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
    cursor.execute(
        """
        UPDATE background_workers 
        SET status = ?, result = ?, finished_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    """,
        (status, result, worker_id),
    )
    conn.commit()
    conn.close()


# --- Scheduled Tasks DB Helpers ---


def register_scheduled_task(
    task_id: str,
    job_id: str,
    intent_id: str | None,
    user_id: str,
    chat_id: str,
    title: str,
    task_type: str,
    payload: dict,
    trigger_time: str,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO scheduled_tasks (id, job_id, intent_id, user_id, chat_id, title, task_type, payload, trigger_time, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')
    """,
        (task_id, job_id, intent_id, user_id, chat_id, title, task_type, json.dumps(payload), trigger_time),
    )
    conn.commit()
    conn.close()


def list_active_scheduled_tasks() -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='apscheduler_jobs'")
    has_apscheduler = cursor.fetchone() is not None

    if has_apscheduler:
        cursor.execute("SELECT id FROM apscheduler_jobs")
        real_job_ids = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT * FROM scheduled_tasks WHERE status = 'scheduled' ORDER BY trigger_time ASC")
        all_scheduled = [dict(r) for r in cursor.fetchall()]
        active = []
        for task in all_scheduled:
            if task["job_id"] in real_job_ids:
                active.append(task)
            else:
                cursor.execute(
                    "UPDATE scheduled_tasks SET status = 'expired' WHERE job_id = ?",
                    (task["job_id"],),
                )
        conn.commit()
        conn.close()
        return active

    cursor.execute("SELECT * FROM scheduled_tasks WHERE status = 'scheduled' ORDER BY trigger_time ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_scheduled_task_by_job_id(job_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scheduled_tasks WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_scheduled_task_status(job_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE scheduled_tasks SET status = ? WHERE job_id = ?", (status, job_id))
    conn.commit()
    conn.close()


# --- Notifications History DB Helpers ---


def log_notification(
    agent_id: str,
    message: str,
    status: str,
    user_id: str | None = None,
    chat_id: str | None = None,
    parse_mode: str | None = None,
    telegram_message_id: str | None = None,
    error: str | None = None,
    chunks_total: int | None = None,
    chunks_sent: int | None = None,
    created_at: str | None = None,
) -> int:
    """Inserts a notification record into notifications_history and returns its id."""
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO notifications_history (
            created_at, agent_id, user_id, chat_id, message, parse_mode,
            status, telegram_message_id, error, chunks_total, chunks_sent
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            agent_id,
            user_id,
            chat_id,
            message,
            parse_mode,
            status,
            telegram_message_id,
            error,
            chunks_total,
            chunks_sent,
        ),
    )
    inserted_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return inserted_id


def get_notifications(
    agent_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieves notifications history filtered by agent_id and/or since ISO date."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM notifications_history"
    conditions = []
    params: list[Any] = []

    if agent_id:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if since:
        conditions.append("created_at >= ?")
        params.append(since)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_notification_stats(since: str | None = None) -> dict[str, Any]:
    """Retrieves notification counts grouped by agent_id and status."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    base_where = ""
    params: list[Any] = []
    if since:
        base_where = " WHERE created_at >= ?"
        params.append(since)

    cursor.execute(f"SELECT COUNT(*) FROM notifications_history{base_where}", params)
    total = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT agent_id, COUNT(*) FROM notifications_history{base_where} GROUP BY agent_id",
        params,
    )
    by_agent = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute(
        f"SELECT status, COUNT(*) FROM notifications_history{base_where} GROUP BY status",
        params,
    )
    by_status = {row[0]: row[1] for row in cursor.fetchall()}

    conn.close()
    return {
        "total": total,
        "by_agent": by_agent,
        "by_status": by_status,
    }
