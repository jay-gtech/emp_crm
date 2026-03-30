from sqlalchemy import Column, Integer, ForeignKey, DateTime, Float, Enum, Date, func
from app.core.database import Base
import enum


class WorkMode(str, enum.Enum):
    office = "office"
    remote = "remote"


class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    clock_in_time = Column(DateTime, nullable=True)
    clock_out_time = Column(DateTime, nullable=True)
    total_hours = Column(Float, nullable=True)        # computed on clock-out
    total_break_hours = Column(Float, nullable=True, default=0.0)
    work_mode = Column(Enum(WorkMode), default=WorkMode.office)
    created_at = Column(DateTime, server_default=func.now())
