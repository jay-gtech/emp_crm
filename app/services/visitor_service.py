"""
Visitor service — all public functions are independently safe.
Each wraps its logic in try/except and returns typed defaults so one
failing call never crashes the rest of the request pipeline.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.visitor import Visitor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path("app/static/uploads/visitors")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/jpg"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024          # 2 MB
PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")


class VisitorError(Exception):
    """Domain-level error for visitor operations."""


# ---------------------------------------------------------------------------
# 1. Image validation & save
# ---------------------------------------------------------------------------

def _save_image(image: UploadFile) -> str:
    """
    Validate and persist the uploaded visitor image.
    Returns the relative URL path (used for <img src="…">).
    Raises VisitorError on validation failure.
    """
    if image.content_type not in ALLOWED_CONTENT_TYPES:
        raise VisitorError("Only JPG and PNG images are accepted.")

    # Read into memory to check size
    data = image.file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise VisitorError("Image must be smaller than 2 MB.")

    ext = "jpg" if "jpeg" in (image.content_type or "") else "png"
    filename = f"{uuid.uuid4().hex}.{ext}"
    dest = UPLOAD_DIR / filename

    with open(dest, "wb") as f:
        f.write(data)

    return f"/static/uploads/visitors/{filename}"


# ---------------------------------------------------------------------------
# 2. Register visitor
# ---------------------------------------------------------------------------

def register_visitor(
    db: Session,
    name: str,
    phone: str,
    purpose: str,
    image: UploadFile,
    created_by: int,
) -> Visitor:
    """
    Validate inputs, save image, create Visitor row, notify managers.
    Raises VisitorError on validation failure.
    """
    name    = name.strip()
    phone   = phone.strip()
    purpose = purpose.strip()

    if not name:
        raise VisitorError("Visitor name is required.")
    if not PHONE_RE.match(phone):
        raise VisitorError("Invalid phone number format.")
    if not purpose:
        raise VisitorError("Purpose of visit is required.")

    image_path = _save_image(image)

    visitor = Visitor(
        name=name,
        phone=phone,
        purpose=purpose,
        image_path=image_path,
        status="pending",
        created_by=created_by,
        approved_by=None,
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)

    # Notify all managers/admins — fire-and-forget
    try:
        from app.models.user import User, UserRole
        from app.services.notification_service import create_task_notification

        managers = (
            db.query(User)
            .filter(User.role.in_([UserRole.manager, UserRole.admin]), User.is_active == 1)
            .all()
        )
        msg = f"New visitor registered: {name} — purpose: {purpose}"
        for mgr in managers:
            create_task_notification(db, mgr.id, msg)
    except Exception as exc:
        logger.warning("visitor register: notification failed: %s", exc)

    return visitor


# ---------------------------------------------------------------------------
# 3. List pending visitors
# ---------------------------------------------------------------------------

def list_pending_visitors(db: Session) -> list[Visitor]:
    """Return all visitors whose status is 'pending', newest first."""
    try:
        return (
            db.query(Visitor)
            .filter(Visitor.status == "pending")
            .order_by(Visitor.created_at.desc())
            .all()
        )
    except Exception as exc:
        logger.error("list_pending_visitors failed: %s", exc)
        return []


def list_all_visitors(db: Session) -> list[Visitor]:
    """Return all visitors, newest first."""
    try:
        return db.query(Visitor).order_by(Visitor.created_at.desc()).all()
    except Exception as exc:
        logger.error("list_all_visitors failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 4. Approve / Reject
# ---------------------------------------------------------------------------

def _review_visitor(
    db: Session,
    visitor_id: int,
    reviewer_id: int,
    new_status: str,   # "approved" | "rejected"
) -> Visitor:
    """
    Internal helper — update status and notify creator.
    Raises VisitorError if visitor not found or already reviewed.
    """
    visitor = db.query(Visitor).filter(Visitor.id == visitor_id).first()
    if not visitor:
        raise VisitorError("Visitor not found.")
    if visitor.status != "pending":
        raise VisitorError(f"Visitor is already {visitor.status}.")

    visitor.status      = new_status
    visitor.approved_by = reviewer_id
    db.commit()
    db.refresh(visitor)

    # Notify the security guard who registered the visitor — fire-and-forget
    try:
        from app.services.notification_service import create_task_notification

        verb = "approved" if new_status == "approved" else "rejected"
        msg  = f"Visitor '{visitor.name}' has been {verb}."
        create_task_notification(db, visitor.created_by, msg)
    except Exception as exc:
        logger.warning("visitor review: notification failed: %s", exc)

    return visitor


def approve_visitor(db: Session, visitor_id: int, reviewer_id: int) -> Visitor:
    return _review_visitor(db, visitor_id, reviewer_id, "approved")


def reject_visitor(db: Session, visitor_id: int, reviewer_id: int) -> Visitor:
    return _review_visitor(db, visitor_id, reviewer_id, "rejected")


# ---------------------------------------------------------------------------
# 5. Security guard's own visitor log
# ---------------------------------------------------------------------------

def get_my_visitors(db: Session, user_id: int) -> list[Visitor]:
    """Return all visitors registered by *user_id*, newest first."""
    try:
        return (
            db.query(Visitor)
            .filter(Visitor.created_by == user_id)
            .order_by(Visitor.created_at.desc())
            .all()
        )
    except Exception as exc:
        logger.error("get_my_visitors failed for user_id=%s: %s", user_id, exc)
        return []
