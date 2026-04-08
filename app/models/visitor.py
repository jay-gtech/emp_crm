from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func

from app.core.database import Base


class Visitor(Base):
    __tablename__ = "visitors"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False)
    phone       = Column(String(20), nullable=False)
    purpose     = Column(String(255), nullable=False)
    image_path  = Column(String(255), nullable=True)
    status      = Column(String(20), default="pending", nullable=False)  # pending | approved | rejected
    created_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
