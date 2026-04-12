"""
db_migration.py — Safe, additive schema migrations (SQLite + PostgreSQL).

Strategy
────────
• Uses ALTER TABLE … ADD COLUMN only (never DROP, never recreate).
• Idempotent: checks information_schema (PG) or PRAGMA (SQLite) before each
  ALTER, so re-runs are always safe.
• On a fresh PostgreSQL database every column already exists via create_all(),
  so this function becomes a no-op — zero risk, zero cost.
• Logging only — no print() in library code; callers see output via the logger.

Alembic readiness
─────────────────
When you are ready to switch to Alembic:
  1. pip install alembic
  2. alembic init alembic
  3. Point alembic/env.py at app.core.database.Base and DATABASE_URL
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
# ALTER TABLE syntax is identical for SQLite and PostgreSQL.
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
        "ALTER TABLE tasks ADD COLUMN start_time TIMESTAMP;",
    ),
    (
        "tasks",
        "end_time",
        "ALTER TABLE tasks ADD COLUMN end_time TIMESTAMP;",
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
        "ALTER TABLE tasks ADD COLUMN approved_at TIMESTAMP;",
    ),
    # ── Deadline & delay tracking ─────────────────────────────────────────────
    (
        "tasks",
        "deadline",
        "ALTER TABLE tasks ADD COLUMN deadline TIMESTAMP;",
    ),
    (
        "tasks",
        "is_delayed",
        "ALTER TABLE tasks ADD COLUMN is_delayed BOOLEAN DEFAULT FALSE NOT NULL;",
    ),
    # ── Announcement audience targeting ───────────────────────────────────────
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
    # ── Group chat columns on messages ────────────────────────────────────────
    (
        "messages",
        "group_id",
        "ALTER TABLE messages ADD COLUMN group_id INTEGER REFERENCES chat_groups(id) ON DELETE CASCADE;",
    ),
    (
        "messages",
        "file_url",
        "ALTER TABLE messages ADD COLUMN file_url VARCHAR(500);",
    ),
]


# ── New-table DDL (SQLite only — PostgreSQL gets these via create_all()) ──────
# These tables all have SQLAlchemy models, so create_all() handles them on PG.
# Kept here for SQLite backward-compatibility only.
_SQLITE_TABLE_MIGRATIONS: list[tuple[str, str]] = [
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
    (
        "chat_groups",
        """
        CREATE TABLE IF NOT EXISTS chat_groups (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       VARCHAR(100) NOT NULL,
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at DATETIME DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        "chat_group_members",
        """
        CREATE TABLE IF NOT EXISTS chat_group_members (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id  INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
            user_id   INTEGER NOT NULL REFERENCES users(id),
            joined_at DATETIME DEFAULT (datetime('now')),
            UNIQUE(group_id, user_id)
        );
        """,
    ),
]


# ── Dialect helpers ───────────────────────────────────────────────────────────

def _is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def _existing_columns_sqlite(conn, table: str) -> set[str]:
    """SQLite: use PRAGMA table_info."""
    rows = conn.execute(text(f"PRAGMA table_info({table});")).fetchall()
    return {row[1] for row in rows}


def _existing_columns_postgres(conn, table: str) -> set[str]:
    """PostgreSQL: use information_schema.columns."""
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND table_schema = 'public';"
        ),
        {"t": table},
    ).fetchall()
    return {row[0] for row in rows}


def _existing_columns(conn, table: str, is_pg: bool) -> set[str]:
    if is_pg:
        return _existing_columns_postgres(conn, table)
    return _existing_columns_sqlite(conn, table)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_safe_migrations(engine: Engine) -> None:
    """
    Run all registered additive column migrations.
    Safe to call on every startup — skips columns that already exist.
    Works with both SQLite and PostgreSQL.
    Never drops tables or columns.
    """
    pg = _is_postgres(engine)
    dialect_label = "PostgreSQL" if pg else "SQLite"
    logger.debug("[migration] Dialect detected: %s", dialect_label)

    try:
        with engine.begin() as conn:
            # ── Ensure new tables exist (SQLite only) ─────────────────────────
            # PostgreSQL gets all tables via Base.metadata.create_all() at startup,
            # so the SQLite-specific DDL is skipped to avoid syntax errors.
            if not pg:
                for table_name, create_sql in _SQLITE_TABLE_MIGRATIONS:
                    try:
                        conn.execute(text(create_sql))
                        logger.info("[migration] Ensured table %s exists ✓", table_name)
                    except Exception as tbl_exc:
                        logger.warning("[migration] Table %s DDL failed: %s", table_name, tbl_exc)
            else:
                logger.info(
                    "[migration] PostgreSQL: table creation handled by create_all() — skipping DDL block."
                )

            # ── Additive column migrations (both dialects) ────────────────────
            column_cache: dict[str, set[str]] = {}

            for table, column, sql in _COLUMN_MIGRATIONS:
                if table not in column_cache:
                    try:
                        column_cache[table] = _existing_columns(conn, table, pg)
                    except Exception as exc:
                        logger.warning(
                            "[migration] Could not read columns for %s: %s — skipping table.", table, exc
                        )
                        column_cache[table] = set()

                if column in column_cache[table]:
                    logger.debug("[migration] %s.%s already exists — skip.", table, column)
                    continue

                try:
                    conn.execute(text(sql))
                    column_cache[table].add(column)
                    logger.info("[migration] Added column %s.%s ✓", table, column)
                except Exception as col_exc:
                    logger.warning(
                        "[migration] Could not add %s.%s: %s", table, column, col_exc
                    )

    except Exception as exc:
        logger.error("[migration] apply_safe_migrations failed: %s", exc)
