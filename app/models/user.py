from sqlalchemy import Column, Integer, String, Float, Enum, DateTime, func, ForeignKey
from app.core.database import Base
import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    team_lead = "team_lead"
    employee = "employee"
    security_guard = "security_guard"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.employee)
    department = Column(String(100), nullable=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    team_lead_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    team_name = Column(String(100), nullable=True)          # logical team label
    performance_score = Column(Float, nullable=True)         # 0–100; used by ML scorer
    is_active = Column(Integer, default=1)  # 1=active, 0=inactive
    created_at = Column(DateTime, server_default=func.now())

    # Location-based access control
    work_mode = Column(String(10), default="office")  # "office" | "wfh"
    office_lat = Column(Float, nullable=True)
    office_lng = Column(Float, nullable=True)
    office_radius = Column(Float, default=100)         # metres
