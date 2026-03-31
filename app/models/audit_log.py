import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from app.core.database import Base


class AuditAction(str, enum.Enum):
    task_created = "task_created"
    task_deleted = "task_deleted"
    leave_applied = "leave_applied"
    leave_approved = "leave_approved"
    leave_rejected = "leave_rejected"
    employee_updated = "employee_updated"
    employee_deactivated = "employee_deactivated"
    role_changed = "role_changed"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, nullable=False, index=True)
    action = Column(Enum(AuditAction), nullable=False)
    target_type = Column(String(50), nullable=False)   # "task", "leave", "employee"
    target_id = Column(Integer, nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
