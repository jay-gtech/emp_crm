"""
scripts/migrate_batch_id.py
───────────────────────────
One-time migration: add nullable `batch_id` column to the `tasks` table.
Supports both SQLite (dev) and PostgreSQL (production).
Safe to re-run — checks for column existence before altering.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import engine
from sqlalchemy import text


def column_exists(conn, dialect_name: str) -> bool:
    if dialect_name == "sqlite":
        rows = conn.execute(text("PRAGMA table_info(tasks)")).fetchall()
        return any(row[1] == "batch_id" for row in rows)
    else:
        # PostgreSQL / MySQL — use information_schema
        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tasks' AND column_name = 'batch_id'"
        )).fetchall()
        return len(result) > 0


def list_columns(conn, dialect_name: str) -> list:
    if dialect_name == "sqlite":
        rows = conn.execute(text("PRAGMA table_info(tasks)")).fetchall()
        return [row[1] for row in rows]
    else:
        rows = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tasks' ORDER BY ordinal_position"
        )).fetchall()
        return [row[0] for row in rows]


def run():
    dialect = engine.dialect.name
    print(f"Database dialect: {dialect}")

    with engine.connect() as conn:
        print(f"Current columns: {list_columns(conn, dialect)}")

        if column_exists(conn, dialect):
            print("SKIPPED: batch_id column already exists — no change needed.")
        else:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN batch_id VARCHAR(36)"))
            conn.commit()
            print("SUCCESS: batch_id column added to tasks table.")

        # Final verification
        final = list_columns(conn, dialect)
        print(f"Final columns: {final}")
        print(f"batch_id present: {'batch_id' in final}")


if __name__ == "__main__":
    run()
