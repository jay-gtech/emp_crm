from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, Enum, DateTime, func
from app.core.database import Base
import enum





class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message      = Column(Text, nullable=False)
    audit_log_id = Column(Integer, ForeignKey("audit_logs.id"), nullable=True, index=True)
    is_read    = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
