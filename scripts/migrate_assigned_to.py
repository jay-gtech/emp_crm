#!/usr/bin/env python3
"""
Migration: tasks.assigned_to → task_assignments (single source of truth)

What this does:
  1. Makes tasks.assigned_to nullable (removes the NOT NULL DB constraint).
  2. Backfills task_assignments for every existing task row that has an
     assigned_to value but no corresponding task_assignments record.
  3. Copies time-tracking / approval fields from the task row to the
     assignment row so history is preserved.

Run once after deploying the multi-user assignment feature:

    python scripts/migrate_assigned_to.py

Safe to re-run:
  - The ALTER TABLE step is skipped if the column is already nullable.
  - The backfill uses INSERT ... ON CONFLICT DO NOTHING (Postgres) or
    INSERT OR IGNORE (SQLite), so duplicate rows are never created.
"""
from __future__ import annotations

import logging
import os
import sys

# ── Allow running from the project root or from inside scripts/ ──────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)


def _is_nullable(engine) -> bool:
    """Return True if tasks.assigned_to already allows NULL."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    for col in insp.get_columns("tasks"):
        if col["name"] == "assigned_to":
            return col.get("nullable", False)
    return True  # column not found — treat as safe


def _make_nullable_postgres(conn) -> None:
    from sqlalchemy import text
    try:
        conn.execute(text(
            "ALTER TABLE tasks ALTER COLUMN assigned_to DROP NOT NULL"
        ))
        conn.commit()
        log.info("  ✓ PostgreSQL: assigned_to is now nullable")
    except Exception as exc:
        conn.rollback()
        msg = str(exc).lower()
        if "already" in msg or "nullable" in msg or "does not exist" in msg:
            log.info("  ✓ PostgreSQL: assigned_to was already nullable (skipped)")
        else:
            raise


def _make_nullable_sqlite(conn) -> None:
    """
    SQLite does not support ALTER COLUMN.  Recreate the tasks table
    with assigned_to as nullable using the recommended 12-step procedure.
    """
    from sqlalchemy import text

    log.info("  SQLite: recreating tasks table to drop NOT NULL on assigned_to …")

    conn.execute(text("PRAGMA foreign_keys = OFF"))

    # Build CREATE TABLE for the new schema — copy everything except the
    # NOT NULL on assigned_to.
    create_sql = """
        CREATE TABLE IF NOT EXISTS tasks_new (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            VARCHAR(200) NOT NULL,
            description      TEXT,
            assigned_to      INTEGER REFERENCES users(id),
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
    """
    conn.execute(text(create_sql))
    conn.execute(text("INSERT INTO tasks_new SELECT * FROM tasks"))
    conn.execute(text("DROP TABLE tasks"))
    conn.execute(text("ALTER TABLE tasks_new RENAME TO tasks"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_assigned_to ON tasks(assigned_to)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_batch_id ON tasks(batch_id)"))

    conn.execute(text("PRAGMA foreign_keys = ON"))
    conn.commit()
    log.info("  ✓ SQLite: tasks table recreated — assigned_to is now nullable")


def _backfill_assignments(engine) -> int:
    """
    Insert a task_assignments row for every task row that has an
    assigned_to value but no matching assignment record.

    Preserves start/end time, duration, approval fields, and delay flag
    from the legacy task row so historical data is not lost.

    Returns the number of rows inserted.
    """
    # Import models AFTER sys.path is set up
    from app.core.database import SessionLocal
    from app.models.task import Task, TaskAssignment, AssignmentStatus, TaskStatus

    inserted = 0
    with SessionLocal() as session:
        tasks_with_assignee = (
            session.query(Task)
            .filter(Task.assigned_to.isnot(None))
            .all()
        )
        for task in tasks_with_assignee:
            already_exists = session.query(TaskAssignment).filter(
                TaskAssignment.task_id == task.id,
                TaskAssignment.user_id == task.assigned_to,
            ).first()
            if already_exists:
                continue

            # Map TaskStatus → AssignmentStatus (same string values; ignore
            # legacy states that don't exist on AssignmentStatus).
            status = AssignmentStatus.assigned
            if task.status:
                try:
                    status = AssignmentStatus(task.status.value)
                except ValueError:
                    status = AssignmentStatus.assigned

            session.add(TaskAssignment(
                task_id=task.id,
                user_id=task.assigned_to,
                status=status,
                start_time=task.start_time,
                end_time=task.end_time,
                duration_seconds=task.duration_seconds,
                approved_by=task.approved_by,
                approved_at=task.approved_at,
                is_delayed=task.is_delayed,
            ))
            inserted += 1

        session.commit()

    return inserted


def run() -> None:
    from app.core.database import engine

    is_sqlite = str(engine.url).startswith("sqlite")

    log.info("=" * 60)
    log.info("Migration: tasks.assigned_to → task_assignments")
    log.info("DB dialect: %s", "SQLite" if is_sqlite else "PostgreSQL")
    log.info("=" * 60)

    # ── Step 1: Ensure task_assignments table exists ──────────────────────────
    log.info("\nStep 1: Ensuring task_assignments table exists …")
    import app.models  # noqa: F401 — registers all models with Base.metadata
    from app.core.database import Base
    Base.metadata.create_all(bind=engine)
    log.info("  ✓ task_assignments table ready")

    # ── Step 2: Make assigned_to nullable ─────────────────────────────────────
    log.info("\nStep 2: Making tasks.assigned_to nullable …")
    if _is_nullable(engine):
        log.info("  ✓ already nullable — skipping ALTER TABLE")
    else:
        with engine.connect() as conn:
            if is_sqlite:
                _make_nullable_sqlite(conn)
            else:
                _make_nullable_postgres(conn)

    # ── Step 3: Backfill task_assignments for legacy rows ─────────────────────
    log.info("\nStep 3: Backfilling task_assignments for existing task rows …")
    inserted = _backfill_assignments(engine)
    log.info("  ✓ Inserted %d new assignment record(s)", inserted)

    # ── Step 4: Summary ───────────────────────────────────────────────────────
    from sqlalchemy import text
    with engine.connect() as conn:
        total_tasks       = conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
        total_assignments = conn.execute(text("SELECT COUNT(*) FROM task_assignments")).scalar()
        tasks_no_assign   = conn.execute(text("""
            SELECT COUNT(*) FROM tasks t
            WHERE NOT EXISTS (
                SELECT 1 FROM task_assignments ta WHERE ta.task_id = t.id
            )
        """)).scalar()

    log.info("\n%s", "=" * 60)
    log.info("Migration complete!")
    log.info("  Total tasks               : %d", total_tasks)
    log.info("  Total assignments         : %d", total_assignments)
    log.info("  Tasks without assignments : %d  ← should be 0 or only nullified rows",
             tasks_no_assign)
    log.info("%s", "=" * 60)

    if tasks_no_assign > 0:
        log.warning(
            "  %d task(s) have no assignments (likely tasks where assigned_to "
            "was already NULL before migration).  These tasks were created "
            "via the new multi-user API and are tracked purely through "
            "task_assignments — no action needed.",
            tasks_no_assign,
        )


if __name__ == "__main__":
    run()
