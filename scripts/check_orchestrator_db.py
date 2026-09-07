import sqlite3

db_path = "/app/data/orchestrator.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
try:
    rows = cursor.execute("SELECT * FROM users").fetchall()
    print("Users in DB before:", rows)
except sqlite3.Error as e:
    print("Error querying users table:", e)

cursor.execute(
    "UPDATE users SET platform_user_id = 'fsirio' WHERE telegram_id = '963420066' OR platform_user_id = 'fedeale_s'"
)
conn.commit()

rows_after = cursor.execute("SELECT * FROM users").fetchall()
print("Users in DB after:", rows_after)
