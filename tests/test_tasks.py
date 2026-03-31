import pytest
from app.models.task import TaskPriority

@pytest.mark.asyncio
async def test_employee_lists_tasks(employee_client):
    response = await employee_client.get("/tasks/")
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_admin_create_task(admin_client, db_session):
    from app.models.user import User
    
    emp = db_session.query(User).filter_by(email="admin@test.com").first()
    
    data = {
        "title": "Fix the DB",
        "description": "Please fix it",
        "assigned_to": str(emp.id),
        "priority": "high",
        "due_date": "2026-12-31"
    }
    response = await admin_client.post("/tasks/create", data=data, follow_redirects=False)
    assert response.status_code in [302, 303]
    
    from app.models.task import Task
    task = db_session.query(Task).filter_by(title="Fix the DB").first()
    assert task is not None
    assert task.assigned_to == emp.id

@pytest.mark.asyncio
async def test_update_task_status(admin_client, db_session):
    from app.models.user import User
    from app.models.task import Task, TaskStatus
    
    emp = db_session.query(User).filter_by(email="admin@test.com").first()
    task = Task(title="Initial Task", assigned_to=emp.id, assigned_by=emp.id, status=TaskStatus.pending)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    
    response = await admin_client.post(f"/tasks/{task.id}/status", data={"status": "in_progress"}, follow_redirects=False)
    assert response.status_code in [302, 303]
    
    db_session.expire_all()
    updated_task = db_session.query(Task).filter_by(id=task.id).first()
    assert updated_task.status == TaskStatus.in_progress
