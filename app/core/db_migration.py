"""
db_migration.py — Safe, additive schema migrations for SQLite.

Strategy
────────
• Uses ALTER TABLE … ADD COLUMN only (never DROP, never recreate).
• Idempotent: checks PRAGMA table_info before each ALTER so re-runs are safe.
• Logging only — no print() in library code; callers see output via the logger.

Alembic readiness
─────────────────
When you are ready to switch to Alembic:
  1. pip install alembic
  2. alembic init alembic
  3. Point alembic/env.py at app.core.database.Base and SQLALCHEMY_DATABASE_URL
  4. alembic revision --autogenerate -m "initial"
  5. alembic upgrade head
  6. Remove apply_safe_migrations() from main.py on_startup once Alembic takes over.
"""
import logging
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ── Migration registry ────────────────────────────────────────────────────────
# Each entry:  (table, column, ALTER SQL)
# Add new columns here — never remove old ones.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    (
        "users",
        "manager_id",
        "ALTER TABLE users ADD COLUMN manager_id INTEGER;",
    ),
    (
        "users",
        "team_lead_id",
        "ALTER TABLE users ADD COLUMN team_lead_id INTEGER;",
    ),
    (
        "users",
        "team_name",
        "ALTER TABLE users ADD COLUMN team_name VARCHAR(100);",
    ),
    (
        "users",
        "performance_score",
        "ALTER TABLE users ADD COLUMN performance_score FLOAT;",
    ),
    (
        "notifications",
        "audit_log_id",
        "ALTER TABLE notifications ADD COLUMN audit_log_id INTEGER REFERENCES audit_logs(id);",
    ),
    # ── Location-based access control (users) ─────────────────────────────────
    (
        "users",
        "work_mode",
        "ALTER TABLE users ADD COLUMN work_mode VARCHAR(10) DEFAULT 'office';",
    ),
    (
        "users",
        "office_lat",
        "ALTER TABLE users ADD COLUMN office_lat FLOAT;",
    ),
    (
        "users",
        "office_lng",
        "ALTER TABLE users ADD COLUMN office_lng FLOAT;",
    ),
    (
        "users",
        "office_radius",
        "ALTER TABLE users ADD COLUMN office_radius FLOAT DEFAULT 100;",
    ),
    # ── Task lifecycle time-tracking & approval ───────────────────────────────
    (
        "tasks",
        "start_time",
        "ALTER TABLE tasks ADD COLUMN start_time DATETIME;",
    ),
    (
        "tasks",
        "end_time",
        "ALTER TABLE tasks ADD COLUMN end_time DATETIME;",
    ),
    (
        "tasks",
        "duration_seconds",
        "ALTER TABLE tasks ADD COLUMN duration_seconds INTEGER;",
    ),
    (
        "tasks",
        "approved_by",
        "ALTER TABLE tasks ADD COLUMN approved_by INTEGER REFERENCES users(id);",
    ),
    (
        "tasks",
        "approved_at",
        "ALTER TABLE tasks ADD COLUMN approved_at DATETIME;",
    ),
    # ── Deadline & delay tracking ───────────────────────────────────────────
    (
        "tasks",
        "deadline",
        "ALTER TABLE tasks ADD COLUMN deadline DATETIME;",
    ),
    (
        "tasks",
        "is_delayed",
        "ALTER TABLE tasks ADD COLUMN is_delayed BOOLEAN DEFAULT 0 NOT NULL;",
    ),
    # ── Announcement audience targeting ────────────────────────────────────────
    (
        "announcements",
        "audience_type",
        "ALTER TABLE announcements ADD COLUMN audience_type VARCHAR(20) DEFAULT 'all';",
    ),
    (
        "announcements",
        "target_ids",
        "ALTER TABLE announcements ADD COLUMN target_ids TEXT;",
    ),
]


# ── New-table DDL statements ──────────────────────────────────────────────────
# Each entry: (table_name, CREATE TABLE … IF NOT EXISTS SQL)
_TABLE_MIGRATIONS: list[tuple[str, str]] = [
    (
        "location_logs",
        """
        CREATE TABLE IF NOT EXISTS location_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL REFERENCES users(id),
            latitude  FLOAT,
            longitude FLOAT,
            action    VARCHAR(50) NOT NULL,
            timestamp DATETIME NOT NULL
        );
        """,
    ),
    # ── Task Comments ──────────────────────────────────────────────────────────
    (
        "task_comments",
        """
        CREATE TABLE IF NOT EXISTS task_comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            comment    TEXT NOT NULL,
            created_at DATETIME DEFAULT (datetime('now'))
        );
        """,
    ),
]


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table});")).fetchall()
    return {row[1] for row in rows}


def apply_safe_migrations(engine: Engine) -> None:
    """
    Run all registered additive column migrations.
    Safe to call on every startup — skips columns that already exist.
    Never drops tables or columns.
    """
    try:
        with engine.begin() as conn:
            # ── Ensure new tables exist ───────────────────────────────────────
            for table_name, create_sql in _TABLE_MIGRATIONS:
                try:
                    conn.execute(text(create_sql))
                    logger.info("[migration] Ensured table %s exists ✓", table_name)
                except Exception as tbl_exc:
                    logger.warning("[migration] Table %s DDL failed: %s", table_name, tbl_exc)

            # Cache column sets per table to avoid redundant PRAGMA calls
            column_cache: dict[str, set[str]] = {}

            for table, column, sql in _COLUMN_MIGRATIONS:
                if table not in column_cache:
                    column_cache[table] = _existing_columns(conn, table)

                if column in column_cache[table]:
                    logger.debug("[migration] %s.%s already exists — skip.", table, column)
                    continue

                try:
                    conn.execute(text(sql))
                    column_cache[table].add(column)  # update cache
                    logger.info("[migration] Added column %s.%s ✓", table, column)
                except Exception as col_exc:
                    logger.warning(
                        "[migration] Could not add %s.%s: %s", table, column, col_exc
                    )

    except Exception as exc:
        logger.error("[migration] apply_safe_migrations failed: %s", exc)
