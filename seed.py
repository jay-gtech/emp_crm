"""
Seed script — creates default admin account and sample data.
Run once: python seed.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import engine, SessionLocal, Base
import app.models  # ensure all models are registered

from app.services.auth_service import register_user, AuthError

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

for u in users_to_create:
    try:
        user = register_user(db, **u)
        print(f"[OK] Created: {user.email}  role={user.role.value}")
    except AuthError as e:
        print(f"[SKIP] {u['email']}: {e}")

db.close()
print("\nSeed complete. You can log in with the credentials above.")
