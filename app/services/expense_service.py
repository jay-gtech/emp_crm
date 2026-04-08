"""
Expense service — group expense tracking with equal split.

All public functions are independently safe: try/except wrappers ensure
failures never propagate to callers.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.expense import ExpenseGroup, ExpenseMember

logger = logging.getLogger(__name__)


class ExpenseError(Exception):
    """Domain-level error for expense operations."""


# ---------------------------------------------------------------------------
# 1. Create expense group (admin / manager / team_lead)
# ---------------------------------------------------------------------------

def create_expense_group(
    db: Session,
    title: str,
    total_amount: float,
    created_by: int,
) -> ExpenseGroup:
    """
    Create an expense group. The creator is automatically added as a member.
    Raises ExpenseError on validation failure.
    """
    title = title.strip()
    if not title:
        raise ExpenseError("Expense title cannot be empty.")
    if total_amount <= 0:
        raise ExpenseError("Total amount must be greater than zero.")

    group = ExpenseGroup(
        title=title,
        created_by=created_by,
        total_amount=total_amount,
    )
    db.add(group)
    db.flush()   # get group.id without committing

    # Creator is the first member; share will be recalculated when others join
    member = ExpenseMember(
        group_id=group.id,
        user_id=created_by,
        amount_share=total_amount,   # sole member initially
        status="pending",
    )
    db.add(member)
    db.commit()
    db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# 2. Add members & split equally
# ---------------------------------------------------------------------------

def add_members(
    db: Session,
    group_id: int,
    user_ids: list[int],
    requester_id: int,
) -> ExpenseGroup:
    """
    Add *user_ids* to the group and recalculate equal shares.
    Only the group creator can add members.
    Raises ExpenseError on validation or permission failure.
    """
    group = db.query(ExpenseGroup).filter(ExpenseGroup.id == group_id).first()
    if not group:
        raise ExpenseError("Expense group not found.")
    if group.created_by != requester_id:
        raise ExpenseError("Only the group creator can add members.")
    if not user_ids:
        raise ExpenseError("No users specified.")

    # Validate user IDs exist and are active
    from app.models.user import User
    valid_ids = {
        uid for (uid,) in
        db.query(User.id).filter(User.id.in_(user_ids), User.is_active == 1).all()
    }
    invalid = set(user_ids) - valid_ids
    if invalid:
        raise ExpenseError(f"Some users not found or inactive: {sorted(invalid)}")

    # Fetch existing member IDs
    existing_ids = {
        uid for (uid,) in
        db.query(ExpenseMember.user_id).filter(ExpenseMember.group_id == group_id).all()
    }

    # Add only new members (skip duplicates)
    new_ids = valid_ids - existing_ids
    for uid in new_ids:
        db.add(ExpenseMember(
            group_id=group_id,
            user_id=uid,
            amount_share=0.0,   # will be recalculated below
            status="pending",
        ))
    db.flush()

    # Recalculate equal split across ALL members
    all_members = db.query(ExpenseMember).filter(ExpenseMember.group_id == group_id).all()
    n = len(all_members)
    if n == 0:
        raise ExpenseError("Group has no members.")
    share = round(group.total_amount / n, 2)
    for m in all_members:
        m.amount_share = share

    db.commit()

    # Notify new members — fire-and-forget
    try:
        from app.services.notification_service import create_task_notification
        msg = f"You were added to expense '{group.title}' — your share: ₹{share:.2f}"
        for uid in new_ids:
            create_task_notification(db, uid, msg)
    except Exception as exc:
        logger.warning("expense add_members notify failed: %s", exc)

    db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# 3. Mark member as paid
# ---------------------------------------------------------------------------

def mark_paid(
    db: Session,
    group_id: int,
    target_user_id: int,
    requester_id: int,
) -> ExpenseMember:
    """
    Mark *target_user_id*'s share as paid.
    Allowed: the member themselves OR the group creator.
    Raises ExpenseError if not permitted or already paid.
    """
    group = db.query(ExpenseGroup).filter(ExpenseGroup.id == group_id).first()
    if not group:
        raise ExpenseError("Expense group not found.")

    member = (
        db.query(ExpenseMember)
        .filter(ExpenseMember.group_id == group_id, ExpenseMember.user_id == target_user_id)
        .first()
    )
    if not member:
        raise ExpenseError("User is not a member of this expense group.")
    if member.status == "paid":
        raise ExpenseError("Already marked as paid.")

    if requester_id != target_user_id and requester_id != group.created_by:
        raise ExpenseError("You can only mark your own share as paid.")

    member.status = "paid"
    db.commit()
    db.refresh(member)

    # Notify all members — fire-and-forget
    try:
        from app.models.user import User
        from app.services.notification_service import create_task_notification

        payer = db.query(User).filter(User.id == target_user_id).first()
        payer_name = payer.name if payer else f"User {target_user_id}"
        all_member_ids = [
            uid for (uid,) in
            db.query(ExpenseMember.user_id).filter(ExpenseMember.group_id == group_id).all()
        ]
        msg = f"{payer_name} paid ₹{member.amount_share:.2f} in '{group.title}'."
        for uid in all_member_ids:
            if uid != target_user_id:
                create_task_notification(db, uid, msg)
    except Exception as exc:
        logger.warning("expense mark_paid notify failed: %s", exc)

    return member


# ---------------------------------------------------------------------------
# 4. Fetch group detail (enriched)
# ---------------------------------------------------------------------------

def get_group_detail(db: Session, group_id: int, requester_id: int) -> dict | None:
    """
    Return full group detail with enriched member list.
    Returns None if not found or requester is not a member.
    """
    try:
        group = db.query(ExpenseGroup).filter(ExpenseGroup.id == group_id).first()
        if not group:
            return None

        members = (
            db.query(ExpenseMember)
            .filter(ExpenseMember.group_id == group_id)
            .all()
        )
        member_ids = {m.user_id for m in members}
        if requester_id not in member_ids and group.created_by != requester_id:
            return None   # not a member

        # Batch-load user names
        from app.models.user import User
        users = {u.id: u for u in db.query(User).filter(User.id.in_(member_ids)).all()}

        creator = db.query(User).filter(User.id == group.created_by).first()

        paid_count    = sum(1 for m in members if m.status == "paid")
        pending_count = len(members) - paid_count

        return {
            "id":            group.id,
            "title":         group.title,
            "total_amount":  group.total_amount,
            "created_by":    group.created_by,
            "creator_name":  creator.name if creator else "Unknown",
            "created_at":    group.created_at,
            "paid_count":    paid_count,
            "pending_count": pending_count,
            "members": [
                {
                    "user_id":      m.user_id,
                    "user_name":    users.get(m.user_id, type("X", (), {"name": "Unknown"})()).name,
                    "amount_share": m.amount_share,
                    "status":       m.status,
                }
                for m in members
            ],
        }
    except Exception as exc:
        logger.error("get_group_detail failed group_id=%s: %s", group_id, exc)
        return None


# ---------------------------------------------------------------------------
# 5. List groups the user belongs to (as member or creator)
# ---------------------------------------------------------------------------

def get_my_groups(db: Session, user_id: int) -> list[dict]:
    """Return all expense groups the user is a member of, newest first."""
    try:
        # Groups where user is a member
        group_ids = [
            gid for (gid,) in
            db.query(ExpenseMember.group_id)
            .filter(ExpenseMember.user_id == user_id)
            .all()
        ]
        # Also include groups created by user (in case they haven't added themselves yet)
        created_ids = [
            gid for (gid,) in
            db.query(ExpenseGroup.id)
            .filter(ExpenseGroup.created_by == user_id)
            .all()
        ]
        all_ids = list(set(group_ids + created_ids))

        if not all_ids:
            return []

        groups = (
            db.query(ExpenseGroup)
            .filter(ExpenseGroup.id.in_(all_ids))
            .order_by(ExpenseGroup.created_at.desc())
            .all()
        )

        # Batch load my membership rows for status
        my_memberships = {
            m.group_id: m for m in
            db.query(ExpenseMember)
            .filter(ExpenseMember.group_id.in_(all_ids), ExpenseMember.user_id == user_id)
            .all()
        }

        result = []
        for g in groups:
            m = my_memberships.get(g.id)
            result.append({
                "id":           g.id,
                "title":        g.title,
                "total_amount": g.total_amount,
                "created_by":   g.created_by,
                "created_at":   g.created_at,
                "my_share":     m.amount_share if m else 0.0,
                "my_status":    m.status if m else "pending",
                "is_creator":   g.created_by == user_id,
            })
        return result

    except Exception as exc:
        logger.error("get_my_groups failed for user_id=%s: %s", user_id, exc)
        return []
