"""
Announcement service — create, list, and notify.

All public functions are independently safe: try/except wrappers ensure
one failure never crashes the rest of the request pipeline.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models.announcement import Announcement
from app.models.user import User

logger = logging.getLogger(__name__)

_CREATOR_ROLES = {"admin", "manager", "team_lead"}


class AnnouncementError(Exception):
    """Domain-level error for announcement operations."""


# ---------------------------------------------------------------------------
# 1. Create announcement
# ---------------------------------------------------------------------------

def create_announcement(
    db: Session,
    title: str,
    message: str,
    created_by: int,
    sender_role: str,
    audience_type: str = "all",   # all | team | specific
    target_ids: list[int] | None = None,
) -> Announcement:
    """
    Persist an announcement, resolve recipients, and fire batch notifications.
    Raises AnnouncementError on validation failure.
    """
    title   = title.strip()
    message = message.strip()

    if not title:
        raise AnnouncementError("Title cannot be empty.")
    if not message:
        raise AnnouncementError("Message cannot be empty.")
    if audience_type not in ("all", "team", "specific"):
        raise AnnouncementError("Invalid audience type.")
    if audience_type == "specific" and not target_ids:
        raise AnnouncementError("At least one target user is required for 'specific' audience.")

    target_ids_json: str | None = None
    if audience_type == "specific" and target_ids:
        # Deduplicate, keep only valid active user IDs
        valid_ids = [
            uid for (uid,) in
            db.query(User.id).filter(User.id.in_(target_ids), User.is_active == 1).all()
        ]
        if not valid_ids:
            raise AnnouncementError("None of the specified users are active.")
        target_ids_json = json.dumps(valid_ids)

    ann = Announcement(
        title=title,
        message=message,
        created_by=created_by,
        sender_role=sender_role,
        audience_type=audience_type,
        target_ids=target_ids_json,
    )
    db.add(ann)
    db.commit()
    db.refresh(ann)

    # Batch-notify recipients — fire-and-forget
    try:
        _notify_recipients(db, ann)
    except Exception as exc:
        logger.warning("announcement notify failed for ann_id=%s: %s", ann.id, exc)

    return ann


# ---------------------------------------------------------------------------
# Internal: resolve recipients and batch-notify
# ---------------------------------------------------------------------------

def _resolve_recipient_ids(db: Session, ann: Announcement) -> list[int]:
    """
    Return the list of user IDs who should receive a notification.
    No N+1: uses single queries per audience branch.
    """
    audience = (ann.audience_type or "all").strip()

    if audience == "all":
        return [uid for (uid,) in db.query(User.id).filter(User.is_active == 1).all()]

    if audience == "specific":
        try:
            return json.loads(ann.target_ids or "[]")
        except (ValueError, TypeError):
            return []

    # audience == "team": users whose team_lead_id or manager_id points at creator
    if audience == "team":
        try:
            from app.services.hierarchy_service import is_user_in_scope  # noqa: F401

            all_active = [uid for (uid,) in db.query(User.id).filter(User.is_active == 1).all()]
            sender_dict = {"role": ann.sender_role, "user_id": ann.created_by}
            return [
                uid for uid in all_active
                if uid != ann.created_by
                and is_user_in_scope(db, sender_dict, uid)
            ]
        except Exception as exc:
            logger.warning("team audience resolution failed: %s", exc)
            return []

    return []


def _notify_recipients(db: Session, ann: Announcement) -> None:
    """Bulk create notifications — single commit for all recipients."""
    from app.models.notification import Notification

    recipient_ids = _resolve_recipient_ids(db, ann)
    if not recipient_ids:
        return

    msg = f"New announcement: {ann.title}"
    notifs = [
        Notification(user_id=uid, message=msg, is_read=False)
        for uid in set(recipient_ids)
        if uid != ann.created_by   # don't notify the author
    ]
    if notifs:
        db.bulk_save_objects(notifs)
        db.commit()


# ---------------------------------------------------------------------------
# 2. Get visible announcements for a user
# ---------------------------------------------------------------------------

def get_visible_announcements(
    db: Session,
    current_user: dict,
    limit: int = 100,
) -> list[dict]:
    """
    Return announcements visible to *current_user*, enriched with sender name.
    Visibility rules:
      - audience_type = 'all'        → everyone
      - audience_type = 'team'       → hierarchy check
      - audience_type = 'specific'   → user_id in target_ids
      - legacy rows (audience_type NULL) → treat as 'all'
    Batch-loads senders — no N+1.
    """
    try:
        all_ann = (
            db.query(Announcement)
            .order_by(Announcement.created_at.desc())
            .limit(limit)
            .all()
        )

        creator_ids = {a.created_by for a in all_ann}
        senders = (
            {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}
            if creator_ids else {}
        )

        try:
            from app.services.hierarchy_service import is_user_in_scope
            _scope_ok = True
        except Exception:
            _scope_ok = False

        uid    = current_user["user_id"]
        role   = current_user.get("role", "employee")
        result = []

        for a in all_ann:
            audience = (a.audience_type or "all").strip()

            if audience == "all":
                result.append(_enrich(a, senders))
                continue

            if audience == "specific":
                try:
                    ids = json.loads(a.target_ids or "[]")
                    if uid in ids or uid == a.created_by:
                        result.append(_enrich(a, senders))
                except Exception:
                    pass
                continue

            # audience == "team"
            sender = senders.get(a.created_by)
            if not sender:
                continue
            if uid == a.created_by:
                result.append(_enrich(a, senders))
            elif _scope_ok:
                sender_dict = {"role": sender.role.value, "user_id": sender.id}
                if is_user_in_scope(db, sender_dict, uid):
                    result.append(_enrich(a, senders))

        return result

    except Exception as exc:
        logger.error("get_visible_announcements failed: %s", exc)
        return []


def _enrich(ann: Announcement, senders: dict) -> dict:
    """Return a plain dict with sender name attached."""
    sender = senders.get(ann.created_by)
    return {
        "id":            ann.id,
        "title":         ann.title,
        "message":       ann.message,
        "sender_role":   ann.sender_role or "",
        "sender_name":   sender.name if sender else "Unknown",
        "audience_type": ann.audience_type or "all",
        "created_at":    ann.created_at,
    }


# ---------------------------------------------------------------------------
# 3. Fetch all users for "specific" audience picker
# ---------------------------------------------------------------------------

def get_all_active_users(db: Session) -> list[dict]:
    """Return id + name for every active user (for the audience picker)."""
    try:
        return [
            {"id": u.id, "name": u.name, "role": u.role.value}
            for u in db.query(User).filter(User.is_active == 1).order_by(User.name).all()
        ]
    except Exception as exc:
        logger.error("get_all_active_users failed: %s", exc)
        return []
