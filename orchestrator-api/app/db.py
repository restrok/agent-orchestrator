import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "orchestrator.db"


def init_db():
    """Initializes the SQLite database and creates the users table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            platform_user_id TEXT NOT NULL UNIQUE
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def get_user_mapping():
    """Returns the current {telegram_id: platform_user_id} mapping."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, platform_user_id FROM users")
    mapping = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return mapping


def register_user(telegram_id: str, platform_user_id: str):
    """Registers a new user in the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (telegram_id, platform_user_id) VALUES (?, ?)", (telegram_id, platform_user_id)
        )
        conn.commit()
        logger.info(f"Registered user: {telegram_id} -> {platform_user_id}")
        return True
    except sqlite3.IntegrityError:
        logger.warning(f"User {telegram_id} or {platform_user_id} already exists.")
        return False
    finally:
        conn.close()


def get_platform_id(telegram_id: str):
    """Retrieves the platform_user_id for a given telegram_id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT platform_user_id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def get_telegram_id(platform_user_id: str):
    """Retrieves the telegram_id for a given platform_user_id."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE platform_user_id = ?", (platform_user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None
