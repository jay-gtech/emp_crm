import pytest
import pytest_asyncio
import os

# Must be set before ANY app imports so settings.ENV evaluates to "test"
os.environ["ENV"] = "test"

from httpx import AsyncClient, ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.main import app as fastapi_app
from app.core.database import Base, get_db
import app.models  # Ensures all models are registered with Base.metadata before create_all
from app.models.user import User, UserRole

# Use TEST_DATABASE_URL directly — avoids ENV timing issues with class attributes
SQLALCHEMY_DATABASE_URL = settings.TEST_DATABASE_URL

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool, # Better for SQLite in-memory, but fine for file-based tests too
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

fastapi_app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(autouse=True)
def clean_database():
    db = TestingSessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    db.close()

@pytest.fixture(scope="function")
def db_session(clean_database):
    """Provides a fresh database session for a test function."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest_asyncio.fixture(scope="function")
async def client():
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

def create_user(db, name: str, email: str, role: UserRole) -> User:
    from app.core.auth import hash_password
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
    db.refresh(user)
    return user

@pytest_asyncio.fixture(scope="function")
async def admin_client(client, db_session):
    admin = create_user(db_session, "Test Admin", "admin@test.com", UserRole.admin)
    response = await client.post("/auth/login", data={"email": "admin@test.com", "password": "testpass123"}, follow_redirects=False)
    assert response.status_code in [302, 303]
    return client

@pytest_asyncio.fixture(scope="function")
async def manager_client(client, db_session):
    manager = create_user(db_session, "Test Manager", "manager@test.com", UserRole.manager)
    response = await client.post("/auth/login", data={"email": "manager@test.com", "password": "testpass123"}, follow_redirects=False)
    assert response.status_code in [302, 303]
    return client

@pytest_asyncio.fixture(scope="function")
async def employee_client(client, db_session):
    employee = create_user(db_session, "Test Employee", "employee@test.com", UserRole.employee)
    response = await client.post("/auth/login", data={"email": "employee@test.com", "password": "testpass123"}, follow_redirects=False)
    assert response.status_code in [302, 303]
    return client
