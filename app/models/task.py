from sqlalchemy import Column, Integer, String, Text, ForeignKey, Enum, Date, DateTime, Boolean, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class TaskStatus(str, enum.Enum):
    assigned         = "assigned"
    in_progress      = "in_progress"
    pending_approval = "pending_approval"
    completed        = "completed"
    # Legacy values kept for existing DB rows
    todo             = "todo"
    pending          = "pending"
    approved         = "approved"
    rejected         = "rejected"


class TaskPriority(str, enum.Enum):
    low    = "low"
    medium = "medium"
    high   = "high"


class AssignmentStatus(str, enum.Enum):
    assigned         = "assigned"
    in_progress      = "in_progress"
    pending_approval = "pending_approval"
    completed        = "completed"
    rejected         = "rejected"


class Task(Base):
    __tablename__ = "tasks"

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Aggregate status — reflects the "worst" assignment state.
    # Updated by _sync_task_aggregate_status() after every assignment change.
    status      = Column(Enum(TaskStatus), default=TaskStatus.assigned, nullable=True)
    priority    = Column(Enum(TaskPriority), default=TaskPriority.medium, nullable=False)
    due_date    = Column(Date, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())
    deadline    = Column(DateTime, nullable=True)
    is_delayed  = Column(Boolean, default=False, nullable=False)
    # Legacy time / approval columns — kept for existing single-user rows;
    # all new tracking lives in task_assignments.
    start_time       = Column(DateTime, nullable=True)
    end_time         = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    approved_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at      = Column(DateTime, nullable=True)
    batch_id         = Column(String(36), nullable=True, index=True)

    assignments = relationship(
        "TaskAssignment",
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskAssignment(Base):
    """Single source of truth: one row per (task, user) pair."""
    __tablename__ = "task_assignments"

    id               = Column(Integer, primary_key=True, index=True)
    task_id          = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status           = Column(Enum(AssignmentStatus), default=AssignmentStatus.assigned,
                              nullable=False)
    started_at       = Column(DateTime, nullable=True)
    completed_at     = Column(DateTime, nullable=True)
    start_time       = Column(DateTime, nullable=True)
    end_time         = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    approved_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at      = Column(DateTime, nullable=True)
    is_delayed       = Column(Boolean, default=False, nullable=False)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    task = relationship("Task", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_user_assignment"),
    )
