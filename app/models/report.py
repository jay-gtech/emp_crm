from datetime import datetime

from sqlalchemy import Column, Integer, Float, Text, DateTime, ForeignKey

from app.core.database import Base


class Report(Base):
    """Hourly work report submitted by an Employee or Team Lead."""

    __tablename__ = "reports"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    hours_spent = Column(Float, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
