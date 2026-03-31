import os
import subprocess
import time
import requests
import pytest
from app.models.user import UserRole

# Ensure tests/conftest.py fixtures are available (implicitly loaded by pytest but good to import helpers)
from tests.conftest import TestingSessionLocal

def create_ui_user(db, name: str, email: str, role: UserRole):
    from app.core.auth import hash_password
    from app.models.user import User
    user = User(
        name=name,
        email=email,
        hashed_password=hash_password("testpass123"),
        role=role,
        department="Engineering",
        is_active=1
    )
    db.add(user)
    db.commit()
    return user

@pytest.fixture(scope="session", autouse=True)
def live_server(setup_database):
    """
    Spins up uvicorn mapped to the test database so Playwright can navigate to it.
    It depends on `setup_database` from root conftest to guarantee tables exist.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = "sqlite:///./test_crm.db"
    
    server_process = subprocess.Popen(
        ["python", "-m", "uvicorn", "app.main:app", "--port", "8000"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    url = "http://127.0.0.1:8000"
    for _ in range(30):
        try:
            r = requests.get(f"{url}/auth/login")
            if r.status_code == 200:
                break
        except requests.ConnectionError:
            time.sleep(0.5)
            
    yield url
    
    server_process.terminate()
    server_process.wait()


@pytest.fixture(scope="function")
def ui_seed_db(clean_database):
    """Seeds the DB with basic users for UI tests to login with."""
    db = TestingSessionLocal()
    admin = create_ui_user(db, "UI Admin", "admin@test.com", UserRole.admin)
    emp = create_ui_user(db, "UI Employee", "employee@test.com", UserRole.employee)
    db.close()
