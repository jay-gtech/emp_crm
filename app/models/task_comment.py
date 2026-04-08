"""
TaskComment model — stores threaded comments on tasks.
Only task participants (assigned_to, assigned_by) can post; enforced at route level.
"""
from sqlalchemy import Column, Integer, Text, ForeignKey, DateTime, func
from app.core.database import Base


class TaskComment(Base):
    __tablename__ = "task_comments"

    id         = Column(Integer, primary_key=True, index=True)
    task_id    = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    comment    = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
