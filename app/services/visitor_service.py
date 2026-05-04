"""
Visitor service — all public functions are independently safe.
Each wraps its logic in try/except and returns typed defaults so one
failing call never crashes the rest of the request pipeline.
"""
from __future__ import annotations

import io
import logging
import os
import re
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

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/jpg", "image/webp"}
MAX_IMAGE_BYTES       = 5 * 1024 * 1024   # 5 MB hard ceiling (frontend compresses first)
_RECOMPRESS_THRESHOLD = 2 * 1024 * 1024   # re-compress server-side if still >2 MB
PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")


class VisitorError(Exception):
    """Domain-level error for visitor operations."""


# ---------------------------------------------------------------------------
# 1. Image validation & save
# ---------------------------------------------------------------------------

def _recompress_with_pillow(data: bytes) -> bytes:
    """
    Backend fallback: re-compress image bytes with Pillow at 75 % JPEG quality.
    Returns the re-compressed bytes, or the original bytes if Pillow is unavailable.
    """
    try:
        from PIL import Image  # pillow is an optional dependency
        img    = Image.open(io.BytesIO(data)).convert("RGB")
        buf    = io.BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        buf.seek(0)
        recompressed = buf.read()
        logger.info(
            "_recompress_with_pillow: %d KB → %d KB",
            len(data) // 1024, len(recompressed) // 1024,
        )
        return recompressed
    except ImportError:
        logger.warning("Pillow not installed — skipping server-side recompression.")
        return data
    except Exception as exc:
        logger.warning("Server-side recompression failed (%s) — using original.", exc)
        return data


def _save_image(image: UploadFile) -> str:
    """
    Validate, optionally recompress, and persist the uploaded visitor image.
    Returns the relative URL path (used for <img src="…">).
    Raises VisitorError on validation failure.
    """
    # Ensure upload directory exists (ephemeral filesystems recreate it on restart)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if image.content_type not in ALLOWED_CONTENT_TYPES:
        raise VisitorError("Only JPG and PNG images are accepted.")

    # Read into memory to check raw size
    data = image.file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise VisitorError(
            f"Image is too large ({len(data) // (1024*1024):.1f} MB). "
            "Maximum allowed size is 5 MB."
        )

    # Server-side safety recompression: if frontend compression was bypassed
    # and the image is still above the recompress threshold, apply Pillow.
    if len(data) > _RECOMPRESS_THRESHOLD:
        logger.info(
            "_save_image: image %d KB exceeds recompress threshold — applying server-side compression.",
            len(data) // 1024,
        )
        data = _recompress_with_pillow(data)
        # After recompression the content type is always JPEG
        ext = "jpg"
    else:
        ext = "jpg" if (image.content_type or "").lower() in {"image/jpeg", "image/jpg"} else "png"

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

    # ── Notify all managers + admins (high priority — needs action) ────────────────
    try:
        from app.models.user import User, UserRole
        from app.services.notification_service import create_notification

        managers = (
            db.query(User)
            .filter(
                User.role.in_([UserRole.manager, UserRole.admin]),
                User.is_active == 1,
            )
            .all()
        )
        msg = f"📍 New visitor: {name} — purpose: {purpose}"
        for mgr in managers:
            create_notification(
                db, mgr.id, "visitor", msg,
                entity_id=visitor.id,
                actor_id=created_by,
                priority="high",
            )
    except Exception as exc:
        logger.warning("visitor register: notification failed: %s", exc)

    return visitor


# ---------------------------------------------------------------------------
# 3. List pending visitors
# ---------------------------------------------------------------------------

def list_pending_visitors(
    db: Session,
    limit: int | None = None,
    offset: int = 0,
) -> list[Visitor]:
    """Return all visitors whose status is 'pending', newest first."""
    try:
        q = (
            db.query(Visitor)
            .filter(Visitor.status == "pending")
            .order_by(Visitor.created_at.desc())
        )
        if offset > 0:
            q = q.offset(offset)
        if limit is not None:
            q = q.limit(limit)
        return q.all()
    except Exception as exc:
        logger.error("list_pending_visitors failed: %s", exc)
        return []


def list_all_visitors(
    db: Session,
    limit: int | None = None,
    offset: int = 0,
) -> list[Visitor]:
    """Return all visitors, newest first."""
    try:
        q = db.query(Visitor).order_by(Visitor.created_at.desc())
        if offset > 0:
            q = q.offset(offset)
        if limit is not None:
            q = q.limit(limit)
        return q.all()
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

    # ── Notify the registering guard + all active guards (role-hierarchy aware) ───
    try:
        from app.models.user import User, UserRole
        from app.services.notification_service import create_notification

        verb      = "approved ✅" if new_status == "approved" else "rejected ❌"
        verb_past = "approved"     if new_status == "approved" else "rejected"

        # 1. High-priority: notify the guard who registered this visitor
        create_notification(
            db, visitor.created_by, "visitor",
            f"Visitor '{visitor.name}' was {verb} by the manager.",
            entity_id=visitor.id,
            actor_id=reviewer_id,
            priority="high",
        )

        # 2. Normal-priority: notify ALL other active security guards
        #    so they know the entry status at the gate.
        if new_status == "approved":
            other_guards = (
                db.query(User)
                .filter(
                    User.role == UserRole.security_guard,
                    User.is_active == 1,
                    User.id != visitor.created_by,   # already notified above
                )
                .all()
            )
            for guard in other_guards:
                create_notification(
                    db, guard.id, "visitor",
                    f"📍 Visitor '{visitor.name}' has been {verb_past} — allow entry.",
                    entity_id=visitor.id,
                    actor_id=reviewer_id,
                    priority="normal",
                )
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

def get_my_visitors(
    db: Session,
    user_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[Visitor]:
    """Return all visitors registered by *user_id*, newest first."""
    try:
        q = (
            db.query(Visitor)
            .filter(Visitor.created_by == user_id)
            .order_by(Visitor.created_at.desc())
        )
        if offset > 0:
            q = q.offset(offset)
        if limit is not None:
            q = q.limit(limit)
        return q.all()
    except Exception as exc:
        logger.error("get_my_visitors failed for user_id=%s: %s", user_id, exc)
        return []
