import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from app.core.database import Base


class AuditAction(str, enum.Enum):
    # Task lifecycle
    task_created   = "task_created"
    task_started   = "task_started"
    task_submitted = "task_submitted"
    task_approved  = "task_approved"
    task_rejected  = "task_rejected"
    task_deleted   = "task_deleted"
    # Leave lifecycle
    leave_applied   = "leave_applied"
    leave_approved  = "leave_approved"
    leave_rejected  = "leave_rejected"
    leave_forwarded = "leave_forwarded"
    # Meeting
    meeting_created = "meeting_created"
    # Employee management
    employee_updated     = "employee_updated"
    employee_deactivated = "employee_deactivated"
    role_changed         = "role_changed"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, nullable=False, index=True)
    action = Column(Enum(AuditAction), nullable=False)
    target_type = Column(String(50), nullable=False)   # "task", "leave", "employee"
    target_id = Column(Integer, nullable=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
