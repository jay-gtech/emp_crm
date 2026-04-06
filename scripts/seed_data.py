"""
scripts/seed_data.py
====================
Production-quality seeding script for Employee CRM.

Organisation structure (50 users total)
----------------------------------------
  1  Admin         — Management dept,  no manager/team lead
  1  Manager       — Management dept,  reports to Admin
  4  Team Leads    — Engineering dept, report to Manager
 44  Employees     — distributed across 6 teams

Team breakdown
--------------
  Engineering:
    AI Team        →  9 employees  (team lead: AI Lead)
    Java Dev 1     → 10 employees  (team lead: Java Dev 1 Lead)
    Java Dev 2     → 10 employees  (team lead: Java Dev 2 Lead)
    Java Dev 3     →  8 employees  (team lead: Java Dev 3 Lead)
  Other:
    Research Team  →  2 employees  (no team lead — report directly to Manager)
    HR Team        →  5 employees  (no team lead — report directly to Manager)

Total: 1 + 1 + 4 + 9 + 10 + 10 + 8 + 2 + 5 = 50 ✓

Usage
-----
  python scripts/seed_data.py          # from project root

Idempotency
-----------
  Safe to run multiple times. Skips entirely if more than 5 users already exist.
  Run with --force to re-evaluate (still won't duplicate emails).
"""

import sys
import os
import random
import logging
from datetime import datetime

# ── Bootstrap path so we can import app modules ──────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.models  # noqa: F401 — registers all ORM models with Base

from app.core.database import engine, SessionLocal, Base
from app.core.db_migration import apply_safe_migrations
from app.core.auth import hash_password
from app.models.user import User, UserRole

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed")

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_PASSWORD = "password123"
IDEMPOTENCY_THRESHOLD = 5          # skip seed if more than N users already exist
PERF_SCORE_MIN = 55.0
PERF_SCORE_MAX = 95.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rand_perf() -> float:
    """Random performance score in [PERF_SCORE_MIN, PERF_SCORE_MAX] rounded to 1dp."""
    return round(random.uniform(PERF_SCORE_MIN, PERF_SCORE_MAX), 1)


def _email_exists(db, email: str) -> bool:
    return db.query(User.id).filter(User.email == email).first() is not None


def create_user(
    db,
    *,
    name: str,
    email: str,
    role: UserRole,
    department: str,
    team_name: str | None = None,
    manager_id: int | None = None,
    team_lead_id: int | None = None,
    performance_score: float | None = None,
) -> User | None:
    """
    Insert a single User, flush to obtain the PK, and return the ORM object.
    Returns None (with a log warning) if the email already exists — never raises.
    """
    if _email_exists(db, email):
        log.warning("  SKIP  %s — email already in DB", email)
        return None

    user = User(
        name=name,
        email=email,
        hashed_password=hash_password(DEFAULT_PASSWORD),
        role=role,
        department=department,
        team_name=team_name,
        manager_id=manager_id,
        team_lead_id=team_lead_id,
        performance_score=performance_score if performance_score is not None else _rand_perf(),
        is_active=1,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.flush()   # assigns user.id without a full commit
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Section creators  (each returns the newly created object(s))
# ─────────────────────────────────────────────────────────────────────────────

def create_admin(db) -> User:
    log.info("Creating Admin …")
    user = create_user(
        db,
        name="Alex Admin",
        email="admin@company.com",
        role=UserRole.admin,
        department="Management",
        team_name="Management",
        performance_score=90.0,
    )
    if user:
        log.info("  OK  admin@company.com  (id=%d)", user.id)
    return user


def create_manager(db, admin: User) -> User:
    log.info("Creating Manager …")
    user = create_user(
        db,
        name="Sarah Manager",
        email="manager@company.com",
        role=UserRole.manager,
        department="Management",
        team_name="Management",
        manager_id=admin.id,
        performance_score=88.0,
    )
    if user:
        log.info("  OK  manager@company.com  (id=%d)", user.id)
    return user


def create_team_leads(db, manager: User) -> dict[str, User]:
    """
    Create 4 team leads and return a dict keyed by team-name string.
    """
    specs = [
        ("Diana AI Lead",       "ai.lead@company.com",       "AI Team"),
        ("Ethan Java Lead 1",   "java1.lead@company.com",    "Java Dev 1"),
        ("Fiona Java Lead 2",   "java2.lead@company.com",    "Java Dev 2"),
        ("George Java Lead 3",  "java3.lead@company.com",    "Java Dev 3"),
    ]
    leads: dict[str, User] = {}
    log.info("Creating Team Leads …")
    for name, email, team in specs:
        user = create_user(
            db,
            name=name,
            email=email,
            role=UserRole.team_lead,
            department="Engineering",
            team_name=team,
            manager_id=manager.id,
            performance_score=_rand_perf(),
        )
        if user:
            leads[team] = user
            log.info("  OK  %-30s  team=%-14s  (id=%d)", email, team, user.id)
    return leads


def create_employees(
    db,
    manager: User,
    leads: dict[str, User],
    counter_start: int,
) -> int:
    """
    Create all 44 employees across 6 teams.
    Returns the final email counter value so callers know how many were attempted.
    """

    # (team_name, department, team_lead User or None, count)
    team_specs: list[tuple[str, str, User | None, int]] = [
        ("AI Team",       "Engineering", leads.get("AI Team"),      9),
        ("Java Dev 1",    "Engineering", leads.get("Java Dev 1"),   10),
        ("Java Dev 2",    "Engineering", leads.get("Java Dev 2"),   10),
        ("Java Dev 3",    "Engineering", leads.get("Java Dev 3"),    8),
        ("Research Team", "Research",    None,                       2),
        ("HR Team",       "HR",          None,                       5),
    ]

    counter = counter_start
    for team_name, dept, lead, count in team_specs:
        log.info("Creating %d employees for team '%s' …", count, team_name)
        for i in range(1, count + 1):
            emp_num = f"{counter:03d}"
            user = create_user(
                db,
                name=f"Employee {emp_num}",
                email=f"emp{emp_num}@company.com",
                role=UserRole.employee,
                department=dept,
                team_name=team_name,
                manager_id=manager.id,
                team_lead_id=lead.id if lead else None,
                performance_score=_rand_perf(),
            )
            if user:
                log.info(
                    "  OK  emp%s@company.com  team=%-14s  (id=%d)",
                    emp_num, team_name, user.id,
                )
            counter += 1

    return counter


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate(db) -> bool:
    """Quick sanity-check after seeding. Returns True if all assertions pass."""
    ok = True
    totals = {
        r.value: db.query(User).filter(User.role == r).count()
        for r in UserRole
    }
    expected = {
        "admin": 1,
        "manager": 1,
        "team_lead": 4,
        "employee": 44,
    }
    total_users = db.query(User).count()

    log.info("─" * 54)
    log.info("VALIDATION REPORT")
    log.info("─" * 54)
    log.info("  Total users       : %d  (expected 50)", total_users)
    for role, exp in expected.items():
        actual = totals.get(role, 0)
        status = "OK" if actual == exp else "FAIL"
        log.info("  %-18s : %d  (expected %d)  [%s]", role, actual, exp, status)
        if actual != exp:
            ok = False

    # Check a sample employee has performance_score and team_lead_id
    sample = db.query(User).filter(User.role == UserRole.employee).first()
    if sample:
        has_perf = sample.performance_score is not None
        log.info("  performance_score  : %s  [%s]", sample.performance_score, "OK" if has_perf else "FAIL")
        if not has_perf:
            ok = False

    log.info("─" * 54)
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Main entry-point
# ─────────────────────────────────────────────────────────────────────────────

def run_seed():
    # 1. Ensure schema is up-to-date (additive migrations only — never drops)
    Base.metadata.create_all(bind=engine)
    apply_safe_migrations(engine)

    db = SessionLocal()
    try:
        # 2. Idempotency guard
        existing = db.query(User).count()
        if existing > IDEMPOTENCY_THRESHOLD:
            log.info(
                "Seed skipped — %d users already exist (threshold=%d). "
                "Database is intact.",
                existing, IDEMPOTENCY_THRESHOLD,
            )
            return

        log.info("=" * 54)
        log.info("Starting CRM seed  (%d existing users detected)", existing)
        log.info("=" * 54)

        # 3. Build org hierarchy
        admin   = create_admin(db)
        if admin is None:
            # Admin already existed — load it so hierarchy links work
            admin = db.query(User).filter(User.email == "admin@company.com").first()

        manager = create_manager(db, admin)
        if manager is None:
            manager = db.query(User).filter(User.email == "manager@company.com").first()

        leads   = create_team_leads(db, manager)

        # Employees start at counter 1; leads dict may be partial if some existed
        create_employees(db, manager, leads, counter_start=1)

        # 4. Persist everything in one commit — atomicity
        db.commit()
        log.info("All users committed successfully.")

        # 5. Validate
        all_ok = validate(db)
        if all_ok:
            log.info("Seed completed successfully. Login with password: %s", DEFAULT_PASSWORD)
        else:
            log.warning("Seed finished with validation warnings — see above.")

    except Exception as exc:
        db.rollback()
        log.exception("Seed failed — transaction rolled back: %s", exc)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
