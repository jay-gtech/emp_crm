#!/usr/bin/env python3
"""
Migration: DROP tasks.assigned_to column (final cleanup).

Pre-condition: migrate_assigned_to.py must have been run first.
That script already:
  1. Made the column nullable
  2. Backfilled task_assignments for every legacy row

This script:
  1. Verifies no task still relies on the column (all have task_assignments)
  2. Drops the column from the live DB
  3. Is safe to re-run (skips if column is already gone)

Run:
    python scripts/drop_assigned_to.py
"""
from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def _column_exists(engine) -> bool:
    from sqlalchemy import inspect as sa_inspect
    cols = {c["name"] for c in sa_inspect(engine).get_columns("tasks")}
    return "assigned_to" in cols


def run() -> None:
    from app.core.database import engine
    from sqlalchemy import text

    is_sqlite = str(engine.url).startswith("sqlite")

    log.info("=" * 60)
    log.info("Migration: DROP tasks.assigned_to")
    log.info("Dialect: %s", "SQLite" if is_sqlite else "PostgreSQL")
    log.info("=" * 60)

    if not _column_exists(engine):
        log.info("  ✓ tasks.assigned_to does not exist — nothing to do")
        return

    # ── Safety check ──────────────────────────────────────────────────────────
    with engine.connect() as conn:
        tasks_without = conn.execute(text("""
            SELECT COUNT(*) FROM tasks t
            WHERE t.assigned_to IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM task_assignments ta WHERE ta.task_id = t.id
            )
        """)).scalar()

    if tasks_without > 0:
        log.error(
            "ABORTED: %d task(s) still have assigned_to set but NO task_assignments "
            "row.  Run scripts/migrate_assigned_to.py first.",
            tasks_without,
        )
        sys.exit(1)

    log.info("\nStep 1: Safety check passed — all tasks have assignment records")

    # ── Drop the column ────────────────────────────────────────────────────────
    log.info("\nStep 2: Dropping tasks.assigned_to …")
    with engine.connect() as conn:
        if is_sqlite:
            # SQLite 3.35+ supports DROP COLUMN natively; older versions need
            # table recreation (same approach as migrate_assigned_to.py).
            try:
                conn.execute(text("ALTER TABLE tasks DROP COLUMN assigned_to"))
                conn.commit()
                log.info("  ✓ Dropped via ALTER TABLE DROP COLUMN (SQLite 3.35+)")
            except Exception as exc:
                conn.rollback()
                if "no such column" in str(exc).lower():
                    log.info("  ✓ Column already gone")
                elif "syntax error" in str(exc).lower():
                    log.warning("  SQLite < 3.35 detected — using table recreation")
                    _sqlite_drop_column(conn)
                else:
                    raise
        else:
            conn.execute(text("ALTER TABLE tasks DROP COLUMN IF EXISTS assigned_to"))
            conn.commit()
            log.info("  ✓ Dropped via ALTER TABLE DROP COLUMN IF EXISTS")

    # ── Verify ────────────────────────────────────────────────────────────────
    if _column_exists(engine):
        log.error("  ✗ Column still present after migration — check DB permissions")
        sys.exit(1)

    log.info("\n%s", "=" * 60)
    log.info("Migration complete — tasks.assigned_to has been removed.")
    log.info("%s", "=" * 60)


def _sqlite_drop_column(conn) -> None:
    from sqlalchemy import text
    conn.execute(text("PRAGMA foreign_keys = OFF"))
    conn.execute(text("""
        CREATE TABLE tasks_final (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            VARCHAR(200) NOT NULL,
            description      TEXT,
            assigned_by      INTEGER NOT NULL REFERENCES users(id),
            status           VARCHAR(50),
            priority         VARCHAR(10) NOT NULL DEFAULT 'medium',
            due_date         DATE,
            created_at       DATETIME DEFAULT (CURRENT_TIMESTAMP),
            updated_at       DATETIME DEFAULT (CURRENT_TIMESTAMP),
            start_time       DATETIME,
            end_time         DATETIME,
            duration_seconds INTEGER,
            approved_by      INTEGER REFERENCES users(id),
            approved_at      DATETIME,
            deadline         DATETIME,
            is_delayed       BOOLEAN NOT NULL DEFAULT 0,
            batch_id         VARCHAR(36)
        )
    """))
    conn.execute(text("""
        INSERT INTO tasks_final
        SELECT id, title, description, assigned_by, status, priority, due_date,
               created_at, updated_at, start_time, end_time, duration_seconds,
               approved_by, approved_at, deadline, is_delayed, batch_id
        FROM tasks
    """))
    conn.execute(text("DROP TABLE tasks"))
    conn.execute(text("ALTER TABLE tasks_final RENAME TO tasks"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_id ON tasks(id)"))
    conn.execute(text("PRAGMA foreign_keys = ON"))
    conn.commit()
    log.info("  ✓ Dropped via table recreation (SQLite < 3.35)")


if __name__ == "__main__":
    run()
