from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.core.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id             = Column(Integer, primary_key=True, index=True)
    title          = Column(String, nullable=False)
    description    = Column(Text, nullable=True)
    scheduled_time = Column(DateTime, nullable=False)
    created_by     = Column(Integer, ForeignKey("users.id"))
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    creator_role   = Column(String)

    # Legacy single-participant column kept nullable for existing rows.
    # All new meetings use meeting_participants instead.
    participant_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    participants = relationship(
        "MeetingParticipant",
        back_populates="meeting",
        cascade="all, delete-orphan",
    )


class MeetingParticipant(Base):
    """One row per (meeting, user) pair — mirrors the task_assignments pattern."""
    __tablename__ = "meeting_participants"

    id         = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    meeting = relationship("Meeting", back_populates="participants")

    __table_args__ = (
        UniqueConstraint("meeting_id", "user_id", name="uq_meeting_participant"),
    )
