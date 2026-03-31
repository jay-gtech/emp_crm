import pytest
from app.models.audit_log import AuditLog

@pytest.mark.asyncio
async def test_audit_log_created_on_action(admin_client, db_session):
    from app.models.user import User, UserRole
    # First create an employee to edit
    emp = User(name="Audit Test", email="audit2@test.com", hashed_password="pw", role=UserRole.employee)
    db_session.add(emp)
    db_session.commit()
    db_session.refresh(emp)
    
    data = {
        "name": "Audit Test Edited",
        "department": "Engineering",
        "role": "employee",
    }
    
    response = await admin_client.post(f"/employees/{emp.id}/edit", data=data, follow_redirects=False)
    assert response.status_code in [200, 302, 303]
    
    log = db_session.query(AuditLog).filter_by(target_type="employee").first()
    assert log is not None, "Audit log was not successfully generated"
    assert "employee" in log.target_type
