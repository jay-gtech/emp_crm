"""
Notification service.

Every public function is independently safe: each wraps its own logic in
try/except and returns a typed default so one failing call never crashes
the caller or the rest of the request pipeline.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)

# Lazy guard — email_service import failure must never break notifications.
try:
    from app.services import email_service as _email_svc
    _EMAIL_SVC_OK = True
except Exception:
    _EMAIL_SVC_OK = False


# ---------------------------------------------------------------------------
# 1.  Create a notification
# ---------------------------------------------------------------------------

def create_notification_from_audit(db: Session, audit_log) -> Notification | None:
    """
    Parses an AuditLog event, determines hierarchical target recipients,
    and bulk creates Notifications if applicable.
    """
    try:
        from app.models.task import Task
        from app.models.leave import Leave
        from app.models.user import User

        user_ids_to_notify = []
        message = ""

        # 1. Task Assigned
        if audit_log.action.value == "task_created" and audit_log.target_type == "task":
            task = db.query(Task).filter(Task.id == audit_log.target_id).first()
            if task and task.assigned_to and task.assigned_to != audit_log.actor_id:
                user_ids_to_notify.append(task.assigned_to)
                message = f'You have a new task: "{task.title}"'

        # 2. Leave Applied
        elif audit_log.action.value == "leave_applied" and audit_log.target_type == "leave":
            leave = db.query(Leave).filter(Leave.id == audit_log.target_id).first()
            if leave:
                employee = db.query(User).filter(User.id == leave.employee_id).first()
                if employee:
                    target_manager = employee.team_lead_id or employee.manager_id
                    if target_manager:
                        user_ids_to_notify.append(target_manager)
                    message = f'{employee.name} applied for {leave.total_days} day(s) of {leave.leave_type.value} leave.'

        # 3. Leave Reviewed
        elif audit_log.action.value in ("leave_approved", "leave_rejected") and audit_log.target_type == "leave":
            leave = db.query(Leave).filter(Leave.id == audit_log.target_id).first()
            if leave and leave.employee_id != audit_log.actor_id:
                user_ids_to_notify.append(leave.employee_id)
                verb = "approved" if audit_log.action.value == "leave_approved" else "rejected"
                message = f'Your {leave.leave_type.value} leave for {leave.total_days} day(s) was {verb}.'

        if not user_ids_to_notify:
            return None

        # ── Build notification records ───────────────────────────────────────
        # Also collect (user_id → email) for the email delivery step below.
        from app.models.user import User  # local import avoids circular deps

        saved_notif = None
        uid_to_email: dict[int, str] = {}

        for uid in set(user_ids_to_notify):
            notif = Notification(
                user_id=uid,
                audit_log_id=audit_log.id,
                message=message,
                is_read=False,
            )
            db.add(notif)
            saved_notif = notif

            # Grab the recipient's email address while we have the session open.
            try:
                user = db.query(User).filter(User.id == uid).first()
                if user and user.email:
                    uid_to_email[uid] = user.email
            except Exception:
                pass  # email delivery is best-effort; skip if lookup fails

        db.commit()
        if saved_notif:
            db.refresh(saved_notif)

        # ── Email delivery — entirely decoupled from the DB transaction ──────
        # Any failure here is logged + swallowed; it NEVER affects the return
        # value or the notifications already persisted above.
        if _EMAIL_SVC_OK and uid_to_email and message:
            subject = _build_email_subject(audit_log)
            for uid, to_email in uid_to_email.items():
                try:
                    _email_svc.send_email(
                        to_email=to_email,
                        subject=subject,
                        body=message,
                    )
                except Exception as exc:  # pragma: no cover
                    logger.error(
                        "Email delivery failed for user_id=%s (%s): %s",
                        uid, to_email, exc,
                    )

        return saved_notif

    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ACTION_SUBJECTS: dict[str, str] = {
    "task_created":   "📋 New Task Assigned — Employee CRM",
    "leave_applied":  "📥 Leave Application Received — Employee CRM",
    "leave_approved": "✅ Your Leave Has Been Approved — Employee CRM",
    "leave_rejected": "❌ Your Leave Application Was Rejected — Employee CRM",
}


def _build_email_subject(audit_log) -> str:
    """Map an audit action to a human-readable email subject line."""
    try:
        action_value = (
            audit_log.action.value
            if hasattr(audit_log.action, "value")
            else str(audit_log.action)
        )
        return _ACTION_SUBJECTS.get(action_value, "🔔 Notification — Employee CRM")
    except Exception:
        return "🔔 Notification — Employee CRM"


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
                "type": "info", # Hardcoded gracefully for UI compatibility
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


# ---------------------------------------------------------------------------
# 5.  Direct task lifecycle notification (no audit log required)
# ---------------------------------------------------------------------------

def create_task_notification(
    db: Session,
    user_id: int,
    message: str,
    notif_type: str = "info",   # kept for forward-compat; stored as plain message
) -> bool:
    """
    Create a single notification for *user_id* directly.
    Safe to call from task routes — returns True on success, False on any error.
    Deliberately fire-and-forget: failures never propagate to callers.
    """
    try:
        notif = Notification(
            user_id=user_id,
            message=message,
            is_read=False,
        )
        db.add(notif)
        db.commit()
        return True
    except Exception as exc:
        logger.warning("create_task_notification failed for user_id=%s: %s", user_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False
