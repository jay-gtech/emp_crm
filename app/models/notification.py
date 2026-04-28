from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, func
from app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message      = Column(Text, nullable=False)
    audit_log_id = Column(Integer, ForeignKey("audit_logs.id"), nullable=True, index=True)
    is_read      = Column(Boolean, default=False, nullable=False)
    created_at   = Column(DateTime, server_default=func.now())

    # ── Unified module routing (added via db_migration, nullable for old rows) ──
    module    = Column(String(50), nullable=True, index=True)   # "task"|"leave"|"meeting"|"chat"|"announcement"|"expense"
    entity_id = Column(Integer, nullable=True)                   # FK to the triggering entity (optional)
    priority  = Column(String(10), nullable=True)                # "low" | "normal" | "high" (None = normal)
