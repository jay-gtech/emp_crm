from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from datetime import datetime
from app.core.database import Base

class Message(Base):
    __tablename__ = "messages"

    id          = Column(Integer, primary_key=True, index=True)

    sender_id   = Column(Integer, ForeignKey("users.id"))
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Group chat — NULL for 1-to-1 DMs
    group_id    = Column(Integer, ForeignKey("chat_groups.id", ondelete="CASCADE"),
                         nullable=True, index=True)

    content     = Column(Text, nullable=False)
    file_url    = Column(String(500), nullable=True)
    timestamp   = Column(DateTime, default=datetime.utcnow)
