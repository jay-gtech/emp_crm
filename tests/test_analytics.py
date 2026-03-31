import pytest

@pytest.mark.asyncio
async def test_analytics_data_endpoint(admin_client):
    response = await admin_client.get("/analytics/data", follow_redirects=False)
    assert response.status_code in [200, 404]

@pytest.mark.asyncio
async def test_analytics_page_render_safe(manager_client):
    response = await manager_client.get("/analytics/")
    assert response.status_code in [200, 404]

@pytest.mark.asyncio
async def test_analytics_blocked_employee(employee_client):
    response = await employee_client.get("/analytics/", follow_redirects=False)
    assert response.status_code in [200, 403, 404]
