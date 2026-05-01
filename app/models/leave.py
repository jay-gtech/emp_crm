from sqlalchemy import Column, Integer, String, Text, ForeignKey, Enum, Date, DateTime, func
from app.core.database import Base
import enum


class LeaveType(str, enum.Enum):
    casual = "casual"
    sick = "sick"
    annual = "annual"
    unpaid = "unpaid"


class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    pending_manager = "pending_manager"  # forwarded by team_lead, awaiting manager


class Leave(Base):
    __tablename__ = "leaves"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    leave_type = Column(Enum(LeaveType), nullable=False, default=LeaveType.casual)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    total_days = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(Enum(LeaveStatus), default=LeaveStatus.pending, nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(String(300), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
