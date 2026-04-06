from sqlalchemy import Column, Integer, String, Text, ForeignKey, Enum, Date, DateTime, func
from app.core.database import Base
import enum


class TaskStatus(str, enum.Enum):
    todo = "todo"
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.todo, nullable=False)
    priority = Column(Enum(TaskPriority), default=TaskPriority.medium, nullable=False)
    due_date = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
