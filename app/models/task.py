from sqlalchemy import Column, Integer, String, Text, ForeignKey, Enum, Date, DateTime, Boolean, func
from app.core.database import Base
import enum


class TaskStatus(str, enum.Enum):
    # ── New lifecycle states (primary) ────────────────────────────────────────
    assigned         = "assigned"
    in_progress      = "in_progress"
    pending_approval = "pending_approval"
    completed        = "completed"
    # ── Legacy values kept for backward compat with existing rows ─────────────
    todo             = "todo"
    pending          = "pending"
    approved         = "approved"
    rejected         = "rejected"


class TaskPriority(str, enum.Enum):
    low    = "low"
    medium = "medium"
    high   = "high"


class Task(Base):
    __tablename__ = "tasks"

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    status      = Column(Enum(TaskStatus), default=TaskStatus.assigned, nullable=False)
    priority    = Column(Enum(TaskPriority), default=TaskPriority.medium, nullable=False)
    due_date    = Column(Date, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # ── Time-tracking & approval ──────────────────────────────────────────────
    start_time       = Column(DateTime, nullable=True)
    end_time         = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    approved_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at      = Column(DateTime, nullable=True)
    # ── Deadline & delay tracking ─────────────────────────────────────────────
    deadline         = Column(DateTime, nullable=True)
    is_delayed       = Column(Boolean, default=False, nullable=False)
