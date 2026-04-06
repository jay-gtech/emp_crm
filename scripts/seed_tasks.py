"""
scripts/seed_tasks.py
=====================
Generates 250 realistic tasks for AI/ML training data.

Distribution
------------
  Status  : 60% completed (~150), 25% pending (~63), 15% overdue (~37)
  Priority: 30% high, 50% medium, 20% low

Workload imbalance (intentional — required for auto-assignment AI testing)
--------------------------------------------------------------------------
   5 employees → heavy load  : 10-15 tasks each
  10 employees → medium load :  5-8  tasks each
  rest         → light load  :  1-3  tasks each

Usage
-----
  python scripts/seed_tasks.py    # from project root

Idempotency
-----------
  Skips entirely if >50 tasks already exist. Safe to re-run.
"""

import sys
import os
import random
import logging
from datetime import datetime, date, timedelta

# ── Bootstrap path ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.models  # noqa — ensures all ORM models register with Base

from app.core.database import engine, SessionLocal, Base
from app.models.user import User, UserRole
from app.models.task import Task, TaskStatus, TaskPriority

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_tasks")

# ── Constants ─────────────────────────────────────────────────────────────────
TOTAL_TASKS           = 250
IDEMPOTENCY_THRESHOLD = 50
TODAY                 = date.today()
NOW                   = datetime.utcnow()

# Status distribution weights
STATUS_WEIGHTS = [
    (TaskStatus.completed, 0.60),
    (TaskStatus.pending,   0.25),
    (TaskStatus.in_progress, 0.15),  # maps to "overdue in_progress" via date logic
]

# Priority distribution weights
PRIORITY_WEIGHTS = [
    (TaskPriority.high,   0.30),
    (TaskPriority.medium, 0.50),
    (TaskPriority.low,    0.20),
]

# ── Realistic task templates ───────────────────────────────────────────────────
TASK_TEMPLATES: list[tuple[str, str]] = [
    # (title_template, description_template)
    ("Fix API bug in {module}",
     "Investigate and resolve the reported bug in the {module} API endpoint. Add regression test."),
    ("Implement {feature} feature",
     "Build and test the {feature} feature end-to-end including unit and integration tests."),
    ("Write unit tests for {module}",
     "Increase test coverage for {module} to at least 80%. Document edge cases found."),
    ("Database optimization for {table}",
     "Profile slow queries against {table}, add missing indexes, and benchmark the improvement."),
    ("Prepare {report} report",
     "Compile data, generate charts, and present findings for the {report} report to stakeholders."),
    ("Research {topic} integration",
     "Evaluate feasibility of integrating {topic} into the existing system. Produce a one-page summary."),
    ("Client meeting prep for {client}",
     "Prepare agenda, slides, and demo environment for the upcoming {client} client meeting."),
    ("Code review for {module} PR",
     "Review the open pull request for {module}, leave actionable comments, and approve or request changes."),
    ("Deploy {service} to staging",
     "Package, deploy, and smoke-test {service} on the staging environment. Update changelog."),
    ("Refactor {module} module",
     "Clean up {module}: reduce duplication, improve naming, ensure PEP-8 compliance."),
    ("Performance profiling of {service}",
     "Profile {service} under load, identify top 3 bottlenecks, and suggest fixes."),
    ("Security audit for {module}",
     "Review {module} for OWASP Top-10 issues. Document findings and remediation steps."),
    ("Set up CI pipeline for {module}",
     "Configure GitHub Actions workflow for automated build, test, and lint for {module}."),
    ("Documentation update for {module}",
     "Rewrite outdated docs for {module}. Add usage examples and a troubleshooting section."),
    ("Data migration for {table}",
     "Write and test migration script to move {table} data to new schema. Include rollback plan."),
]

MODULES   = ["auth", "tasks", "users", "notifications", "analytics", "leave", "attendance", "reporting"]
FEATURES  = ["auto-assign", "leave approval", "real-time alerts", "dashboard export", "bulk upload", "2FA", "dark mode"]
TABLES    = ["users", "tasks", "leaves", "attendance", "audit_logs"]
REPORTS   = ["monthly KPI", "quarterly leave", "team performance", "project status", "budget"]
TOPICS    = ["AI scheduling", "face recognition", "NLP feedback", "predictive analytics", "graph DB"]
CLIENTS   = ["Acme Corp", "TechStart", "GlobalBank", "MediSoft", "RetailPro"]
SERVICES  = ["auth-service", "notification-service", "task-engine", "analytics-api", "email-worker"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _weighted_choice(choices: list[tuple]) -> object:
    """Pick from [(value, weight), ...] respecting weights."""
    values, weights = zip(*choices)
    return random.choices(values, weights=weights, k=1)[0]


def generate_task_title() -> tuple[str, str]:
    """Return (title, description) from a random template with filled placeholders."""
    tmpl_title, tmpl_desc = random.choice(TASK_TEMPLATES)
    subs = {
        "{module}":  random.choice(MODULES),
        "{feature}": random.choice(FEATURES),
        "{table}":   random.choice(TABLES),
        "{report}":  random.choice(REPORTS),
        "{topic}":   random.choice(TOPICS),
        "{client}":  random.choice(CLIENTS),
        "{service}": random.choice(SERVICES),
    }
    title = tmpl_title
    desc  = tmpl_desc
    for placeholder, value in subs.items():
        title = title.replace(placeholder, value)
        desc  = desc.replace(placeholder, value)
    return title, desc


def generate_dates(status: TaskStatus) -> tuple[datetime, date, datetime | None]:
    """
    Return (created_at, due_date, updated_at).

    Rules:
      - created_at  : random day in last 30 days
      - due_date    : created_at + 1..10 days
      - completed:  due_date <= today, updated_at between created_at and due_date
      - pending:    due_date >= today (not overdue)
      - in_progress (repurposed as "overdue"): due_date < today, updated_at = created_at
    """
    days_ago    = random.randint(2, 30)
    created_at  = NOW - timedelta(days=days_ago)
    span        = random.randint(1, 10)

    if status == TaskStatus.completed:
        # Ensure due_date is in the past so completed tasks are resolved
        due_date   = (created_at + timedelta(days=span)).date()
        # completed somewhere between creation and due_date
        finish_offset = random.uniform(0, span * 0.9)
        updated_at = created_at + timedelta(days=finish_offset)
        return created_at, due_date, updated_at

    elif status == TaskStatus.pending:
        # Not overdue — due date is today or in future
        future_days = random.randint(1, 10)
        due_date    = TODAY + timedelta(days=future_days)
        updated_at  = created_at
        return created_at, due_date, updated_at

    else:  # in_progress used as "overdue in progress"
        # due_date already passed
        overdue_days = random.randint(1, 14)
        due_date     = TODAY - timedelta(days=overdue_days)
        # Make sure created_at is before due_date
        if created_at.date() >= due_date:
            created_at = datetime.combine(due_date - timedelta(days=random.randint(1, 5)),
                                          datetime.min.time())
        updated_at = created_at
        return created_at, due_date, updated_at


def build_workload_map(employees: list[User]) -> dict[int, int]:
    """
    Assign a target task-count to each employee based on workload tier.

    Returns {employee_id: target_task_count}
    """
    random.shuffle(employees)
    n = len(employees)

    heavy_n  = 5
    medium_n = min(10, n - heavy_n)
    # rest are light

    workload: dict[int, int] = {}
    for i, emp in enumerate(employees):
        if i < heavy_n:
            workload[emp.id] = random.randint(10, 15)
        elif i < heavy_n + medium_n:
            workload[emp.id] = random.randint(5, 8)
        else:
            workload[emp.id] = random.randint(1, 3)

    return workload


def build_task_pool(workload: dict[int, int]) -> list[tuple[int, int]]:
    """
    Expand workload map into a flat list of (employee_id, slot_index) pairs,
    then shuffle — gives us the assignment order for tasks.
    """
    pool: list[int] = []
    for emp_id, count in workload.items():
        pool.extend([emp_id] * count)
    random.shuffle(pool)
    return pool


# ─────────────────────────────────────────────────────────────────────────────
# Core creator
# ─────────────────────────────────────────────────────────────────────────────

def create_task(
    db,
    *,
    assigned_to_id: int,
    assigned_by_id: int,
    status: TaskStatus,
    priority: TaskPriority,
) -> Task:
    title, description = generate_task_title()
    created_at, due_date, updated_at = generate_dates(status)

    task = Task(
        title=title,
        description=description,
        assigned_to=assigned_to_id,
        assigned_by=assigned_by_id,
        status=status,
        priority=priority,
        due_date=due_date,
        created_at=created_at,
        updated_at=updated_at,
    )
    db.add(task)
    return task


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate(db) -> bool:
    ok = True
    total = db.query(Task).count()

    status_counts = {
        s: db.query(Task).filter(Task.status == s).count()
        for s in TaskStatus
    }
    priority_counts = {
        p: db.query(Task).filter(Task.priority == p).count()
        for p in TaskPriority
    }

    log.info("─" * 60)
    log.info("VALIDATION REPORT")
    log.info("─" * 60)
    log.info("  Total tasks     : %d  (target %d)", total, TOTAL_TASKS)

    for status, count in status_counts.items():
        pct = count / total * 100 if total else 0
        log.info("  %-14s : %3d  (%.0f%%)", status.value, count, pct)

    log.info("  ─ priorities ─")
    for priority, count in priority_counts.items():
        pct = count / total * 100 if total else 0
        log.info("  %-14s : %3d  (%.0f%%)", priority.value, count, pct)

    # Workload spread check
    from sqlalchemy import func as sqla_func
    loads = (
        db.query(Task.assigned_to, sqla_func.count(Task.id).label("cnt"))
        .group_by(Task.assigned_to)
        .all()
    )
    counts = sorted([r.cnt for r in loads], reverse=True)
    log.info("  ─ workload ─")
    log.info("  Top 5 loads     : %s", counts[:5])
    log.info("  Bottom 5 loads  : %s", counts[-5:] if len(counts) >= 5 else counts)
    log.info("  Employees with tasks : %d", len(counts))

    if total < TOTAL_TASKS * 0.9:
        log.warning("  WARN: fewer tasks than expected (%d < %d)", total, TOTAL_TASKS)
        ok = False

    log.info("─" * 60)
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # ── Idempotency guard ────────────────────────────────────────────────
        existing = db.query(Task).count()
        if existing > IDEMPOTENCY_THRESHOLD:
            log.info(
                "Seed skipped — %d tasks already exist (threshold=%d). DB is intact.",
                existing, IDEMPOTENCY_THRESHOLD,
            )
            return

        # ── Fetch actors ─────────────────────────────────────────────────────
        employees = db.query(User).filter(User.role == UserRole.employee, User.is_active == 1).all()
        if not employees:
            log.error("No employees found. Run scripts/seed_data.py first.")
            sys.exit(1)

        # Use manager as the "assigned_by" authority for all seeded tasks
        manager = db.query(User).filter(User.role == UserRole.manager, User.is_active == 1).first()
        if not manager:
            manager = db.query(User).filter(User.role == UserRole.admin, User.is_active == 1).first()
        assigned_by_id = manager.id

        log.info("=" * 60)
        log.info("Starting task seed")
        log.info("  Employees available : %d", len(employees))
        log.info("  Tasks to generate   : %d", TOTAL_TASKS)
        log.info("=" * 60)

        # ── Build workload map ───────────────────────────────────────────────
        workload = build_workload_map(employees)
        pool     = build_task_pool(workload)

        # Log tier summary
        heavy  = [(e, c) for e, c in workload.items() if c >= 10]
        medium = [(e, c) for e, c in workload.items() if 5 <= c < 10]
        light  = [(e, c) for e, c in workload.items() if c < 5]
        log.info("  Workload tiers  — heavy: %d  medium: %d  light: %d",
                 len(heavy), len(medium), len(light))

        # Pool may not exactly equal TOTAL_TASKS; we stretch or trim
        if len(pool) < TOTAL_TASKS:
            # Pad by repeating low-load employees
            light_ids = [e for e, _ in light] or [emp.id for emp in employees]
            while len(pool) < TOTAL_TASKS:
                pool.append(random.choice(light_ids))
        elif len(pool) > TOTAL_TASKS:
            pool = pool[:TOTAL_TASKS]

        random.shuffle(pool)

        # ── Generate tasks ───────────────────────────────────────────────────
        # Pre-compute status + priority assignment for the full run
        target_completed   = int(TOTAL_TASKS * 0.60)
        target_pending     = int(TOTAL_TASKS * 0.25)
        target_in_progress = TOTAL_TASKS - target_completed - target_pending

        status_sequence: list[TaskStatus] = (
            [TaskStatus.completed]   * target_completed +
            [TaskStatus.pending]     * target_pending +
            [TaskStatus.in_progress] * target_in_progress
        )
        random.shuffle(status_sequence)

        priority_sequence: list[TaskPriority] = random.choices(
            [TaskPriority.high, TaskPriority.medium, TaskPriority.low],
            weights=[0.30, 0.50, 0.20],
            k=TOTAL_TASKS,
        )

        batch_size = 50
        created    = 0
        for idx, (emp_id, status, priority) in enumerate(
            zip(pool, status_sequence, priority_sequence)
        ):
            create_task(
                db,
                assigned_to_id=emp_id,
                assigned_by_id=assigned_by_id,
                status=status,
                priority=priority,
            )
            created += 1

            if created % batch_size == 0:
                db.flush()
                log.info("  ... flushed %d / %d tasks", created, TOTAL_TASKS)

        db.commit()
        log.info("All %d tasks committed.", created)

        # ── Validate ─────────────────────────────────────────────────────────
        validate(db)
        log.info("Task seed completed successfully.")

    except Exception as exc:
        db.rollback()
        log.exception("Task seed failed — transaction rolled back: %s", exc)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
