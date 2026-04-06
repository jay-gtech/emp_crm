"""
Seed script — creates default admin account and sample data.
Run once: python seed.py

Idempotent: safe to re-run — will not create duplicate users.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import engine, SessionLocal, Base
import app.models  # ensure all models are registered

from app.models.user import User
from app.services.auth_service import register_user, AuthError

# ── Ensure tables exist without ever dropping existing data ──────────────────
Base.metadata.create_all(bind=engine)

db = SessionLocal()

users_to_create = [
    {
        "name": "Admin User",
        "email": "admin@company.com",
        "password": "Admin@123",
        "role": "admin",
        "department": "Management",
    },
    {
        "name": "Sarah Manager",
        "email": "manager@company.com",
        "password": "Manager@123",
        "role": "manager",
        "department": "Engineering",
    },
    {
        "name": "John Employee",
        "email": "employee@company.com",
        "password": "Employee@123",
        "role": "employee",
        "department": "Engineering",
    },
    {
        "name": "Lisa HR",
        "email": "hr@company.com",
        "password": "Hr@12345",
        "role": "employee",
        "department": "Human Resources",
    },
]

db = SessionLocal()
try:
    # ── Idempotency guard — skip entirely if any users already exist ───────────
    existing_count = db.query(User).count()
    if existing_count > 0:
        print(f"[seed.py] {existing_count} user(s) already exist. Skipping seed — data is safe.")
        sys.exit(0)

    created = 0
    for u in users_to_create:
        try:
            user = register_user(db, **u)
            print(f"  [OK] Created: {user.email}  role={user.role.value}")
            created += 1
        except AuthError as e:
            print(f"  [SKIP] {u['email']}: {e}")
        except Exception as e:
            print(f"  [ERROR] {u['email']}: {e}")

    print(f"\nSeed complete. {created} user(s) created. You can log in with the credentials above.")
finally:
    db.close()
