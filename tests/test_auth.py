import pytest
from app.models.user import UserRole

@pytest.mark.asyncio
async def test_login_success(client, db_session):
    from app.core.auth import hash_password
    from app.models.user import User
    
    user = User(name="Login Test", email="login@test.com", hashed_password=hash_password("password123"), role=UserRole.employee)
    db_session.add(user)
    db_session.commit()
    
    response = await client.post("/auth/login", data={"email": "login@test.com", "password": "password123"}, follow_redirects=False)
    assert response.status_code in [302, 303]
    assert "/dashboard" in response.headers.get("location")
    assert "session" in response.cookies

@pytest.mark.asyncio
async def test_login_invalid_password(client, db_session):
    from app.core.auth import hash_password
    from app.models.user import User
    
    user = User(name="Login Fail", email="fail@test.com", hashed_password=hash_password("correct_pass"), role=UserRole.employee)
    db_session.add(user)
    db_session.commit()
    
    response = await client.post("/auth/login", data={"email": "fail@test.com", "password": "wrong_password"})
    assert response.status_code == 400
    assert "Invalid email or password" in response.text

@pytest.mark.asyncio
async def test_logout(employee_client):
    response = await employee_client.get("/auth/logout", follow_redirects=False)
    assert response.status_code in [302, 303]
    assert response.headers.get("location") == "/auth/login"
