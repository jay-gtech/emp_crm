from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from app.core.database import Base


class LocationLog(Base):
    __tablename__ = "location_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    action = Column(String(50), nullable=False)   # "login" | "clock_in" | "clock_out"
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
