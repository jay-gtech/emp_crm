from sqlalchemy import Column, Integer, ForeignKey, DateTime, Enum, func
from app.core.database import Base
import enum


class BreakStatus(str, enum.Enum):
    active = "active"
    completed = "completed"


class BreakRecord(Base):
    __tablename__ = "break_records"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    attendance_id = Column(Integer, ForeignKey("attendance.id"), nullable=False)
    start_time = Column(DateTime, nullable=False, default=func.now())
    end_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)  # computed on end_break
    status = Column(Enum(BreakStatus), default=BreakStatus.active, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
