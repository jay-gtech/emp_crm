import sys
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.models.task import Task
from sqlalchemy import or_
from app.services.hierarchy_service import get_subordinate_ids

db = SessionLocal()
mgr = db.query(User).filter(User.role == UserRole.manager).first()
if not mgr:
    print("No manager found")
    sys.exit(0)
    
subordinate_ids = get_subordinate_ids(db, mgr.id)
print(f"Manager ID: {mgr.id}")
print(f"Subordinate IDs: {subordinate_ids}")

if subordinate_ids:
    task_filter = or_(
        Task.assigned_to == mgr.id,
        Task.assigned_to.in_(subordinate_ids),
    )
else:
    task_filter = (Task.assigned_to == mgr.id)
tasks = (
    db.query(Task)
    .filter(task_filter)
    .order_by(Task.created_at.desc())
    .all()
)
print(f"Tasks found via SQL: {[t.assigned_to for t in tasks]}")

db.close()
