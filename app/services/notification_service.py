"""
Notification service.

Every public function is independently safe: each wraps its own logic in
try/except and returns a typed default so one failing call never crashes
the caller or the rest of the request pipeline.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationType


# ---------------------------------------------------------------------------
# 1.  Create a notification
# ---------------------------------------------------------------------------

def create_notification(
    db: Session,
    user_id: int,
    message: str,
    notif_type: str = "info",
) -> Notification | None:
    """
    Persist a notification for *user_id*.
    Returns the saved Notification or None on failure.
    """
    try:
        try:
            ntype = NotificationType(notif_type)
        except ValueError:
            ntype = NotificationType.info

        notif = Notification(
            user_id=user_id,
            message=message,
            type=ntype,
            is_read=False,
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return notif
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# 2.  Fetch notifications for a user
# ---------------------------------------------------------------------------

def get_notifications(
    db: Session,
    user_id: int,
    limit: int = 20,
    unread_only: bool = False,
) -> list[dict]:
    """
    Returns a list of notification dicts for *user_id*, newest first.
    Each dict has: id, message, type, is_read, created_at (ISO string).
    Returns [] on any failure.
    """
    try:
        q = db.query(Notification).filter(Notification.user_id == user_id)
        if unread_only:
            q = q.filter(Notification.is_read == False)  # noqa: E712
        rows = q.order_by(Notification.created_at.desc()).limit(limit).all()

        return [
            {
                "id": n.id,
                "message": n.message,
                "type": n.type.value,
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%d %b %Y, %H:%M") if n.created_at else "",
            }
            for n in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 3.  Count unread notifications
# ---------------------------------------------------------------------------

def get_unread_count(db: Session, user_id: int) -> int:
    """Returns the count of unread notifications for *user_id*. Returns 0 on failure."""
    try:
        return (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
            .count()
        )
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 4.  Mark notifications as read
# ---------------------------------------------------------------------------

def mark_as_read(
    db: Session,
    user_id: int,
    notification_id: int | None = None,
) -> bool:
    """
    Mark notifications as read for *user_id*.
    If *notification_id* is given → mark that single one.
    If None → mark ALL unread for the user.
    Returns True on success, False on failure.
    """
    try:
        q = db.query(Notification).filter(Notification.user_id == user_id)
        if notification_id is not None:
            q = q.filter(Notification.id == notification_id)
        else:
            q = q.filter(Notification.is_read == False)  # noqa: E712

        q.update({"is_read": True}, synchronize_session=False)
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False
