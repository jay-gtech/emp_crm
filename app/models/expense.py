from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint

from app.core.database import Base


class ExpenseGroup(Base):
    """A shared expense that will be split among members."""

    __tablename__ = "expense_groups"

    id           = Column(Integer, primary_key=True, index=True)
    title        = Column(String(200), nullable=False)
    created_by   = Column(Integer, ForeignKey("users.id"), nullable=False)
    total_amount = Column(Float, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class ExpenseMember(Base):
    """A single member's share within an ExpenseGroup."""

    __tablename__ = "expense_members"

    id            = Column(Integer, primary_key=True, index=True)
    group_id      = Column(Integer, ForeignKey("expense_groups.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount_share  = Column(Float, nullable=False)
    status        = Column(String(20), default="pending", nullable=False)  # pending | paid

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_expense_member"),
    )
