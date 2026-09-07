import sqlite3
from pathlib import Path

db_path = Path("/app/data/orchestrator.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Update mapping for Telegram ID 963420066 -> fsirio
cursor.execute(
    "UPDATE users SET platform_user_id = ? WHERE telegram_id = ?",
    ("fsirio", "963420066"),
)
if cursor.rowcount == 0:
    cursor.execute(
        "INSERT OR REPLACE INTO users (telegram_id, platform_user_id) VALUES (?, ?)",
        ("963420066", "fsirio"),
    )

conn.commit()

cursor.execute("SELECT telegram_id, platform_user_id FROM users")
mapping = dict(cursor.fetchall())
conn.close()

print("✅ User mapping updated successfully:")
print(mapping)
