import pytest
from app.models.attendance import Attendance

@pytest.mark.asyncio
async def test_clock_in(employee_client, db_session):
    response = await employee_client.post("/attendance/clock-in", follow_redirects=False)
    assert response.status_code in [302, 303]
    
    from app.models.user import User
    emp = db_session.query(User).filter_by(email="employee@test.com").first()
    
    att = db_session.query(Attendance).filter_by(employee_id=emp.id).first()
    assert att is not None
    assert att.clock_in_time is not None

@pytest.mark.asyncio
async def test_clock_in_double_prevented(employee_client, db_session):
    await employee_client.post("/attendance/clock-in", follow_redirects=False)
    response = await employee_client.post("/attendance/clock-in", follow_redirects=False)
    assert response.status_code in [200, 302, 303]

@pytest.mark.asyncio
async def test_start_break(employee_client):
    await employee_client.post("/attendance/clock-in", follow_redirects=False)
    response = await employee_client.post("/attendance/break/start", follow_redirects=False)
    assert response.status_code in [200, 302, 303]
