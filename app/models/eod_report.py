from datetime import datetime

from sqlalchemy import Column, Integer, Text, Date, DateTime, ForeignKey, UniqueConstraint

from app.core.database import Base


class EODReport(Base):
    """End-of-Day summary submitted by a Team Lead (once per day)."""

    __tablename__ = "eod_reports"

    id           = Column(Integer, primary_key=True, index=True)
    team_lead_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    summary      = Column(Text, nullable=False)
    report_date  = Column(Date, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("team_lead_id", "report_date", name="uq_eod_tl_date"),
    )
