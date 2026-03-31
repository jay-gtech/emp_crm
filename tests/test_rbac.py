import pytest

@pytest.mark.asyncio
async def test_auth_redirects_unauthenticated(client):
    response = await client.get("/dashboard/", follow_redirects=False)
    assert response.status_code in [302, 303]
    assert response.headers.get("location") == "/auth/login"

@pytest.mark.asyncio
async def test_employee_access_denied_admin_route(employee_client):
    response = await employee_client.get("/employees/new")
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_manager_access_denied_admin_route(manager_client):
    response = await manager_client.get("/employees/new")
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_admin_access_allowed_admin_route(admin_client):
    response = await admin_client.get("/employees/new")
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_manager_access_allowed_team_route(manager_client):
    response = await manager_client.get("/api/leave/export")
    assert response.status_code != 403
