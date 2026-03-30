from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, Enum, DateTime, func
from app.core.database import Base
import enum


class NotificationType(str, enum.Enum):
    task_assigned  = "task_assigned"
    leave_approved = "leave_approved"
    leave_rejected = "leave_rejected"
    info           = "info"


class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message    = Column(Text, nullable=False)
    type       = Column(Enum(NotificationType), default=NotificationType.info, nullable=False)
    is_read    = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
