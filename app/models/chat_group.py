from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime
from app.core.database import Base


class ChatGroup(Base):
    __tablename__ = "chat_groups"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChatGroupMember(Base):
    __tablename__ = "chat_group_members"

    id        = Column(Integer, primary_key=True, index=True)
    group_id  = Column(Integer, ForeignKey("chat_groups.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_chat_group_member"),
    )
