import pytest
from app.models.notification import Notification

@pytest.mark.asyncio
async def test_notification_creation_flow(admin_client, db_session):
    from app.models.user import User, UserRole
    from tests.conftest import create_user
    
    admin = db_session.query(User).filter_by(email="admin@test.com").first()
    emp = create_user(db_session, "Employee 2", "emp2@test.com", UserRole.employee)

    
    data = {
        "title": "Notification Task",
        "description": "Trigger a notif",
        "assigned_to": str(emp.id),
        "priority": "medium",
        "due_date": "2026-12-31"
    }
    response = await admin_client.post("/tasks/create", data=data, follow_redirects=False)
    assert response.status_code in [302, 303]
    
    notif = db_session.query(Notification).filter_by(user_id=emp.id).first()
    assert notif is not None

@pytest.mark.asyncio
async def test_get_notifications_api(employee_client):
    response = await employee_client.get("/notifications/")
    assert response.status_code == 200
    data = response.json()
    assert "notifications" in data
    assert "unread_count" in data
