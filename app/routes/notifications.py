"""
Notifications router.

GET  /notifications/          → JSON list of notifications for current user
GET  /notifications/unread    → JSON {count: N} of unread
POST /notifications/read      → mark all as read (or a specific id via ?id=)
POST /notifications/read/{id} → mark a single notification as read
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import login_required

try:
    from app.services.notification_service import (
        get_notifications,
        get_unread_count,
        get_unread_by_module,
        mark_as_read,
    )
    _SVC_OK = True
except Exception:
    _SVC_OK = False


router = APIRouter(prefix="/notifications", tags=["notifications"])


# ---------------------------------------------------------------------------
# GET /notifications/  — full list (up to 20, newest first)
# ---------------------------------------------------------------------------
@router.get("/")
def list_notifications(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _SVC_OK:
        return JSONResponse({"notifications": [], "unread_count": 0})
    try:
        uid = current_user["user_id"]
        notifs = get_notifications(db, uid, limit=20)
        unread = get_unread_count(db, uid)
        return JSONResponse({"notifications": notifs, "unread_count": unread})
    except Exception:
        return JSONResponse({"notifications": [], "unread_count": 0})


# ---------------------------------------------------------------------------
# GET /notifications/unread  — lightweight count poll (bell + sidebar badges)
# ---------------------------------------------------------------------------
@router.get("/unread")
def unread_count(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _SVC_OK:
        return JSONResponse({
            "unread_count": 0, "total": 0,
            "task": 0, "leave": 0, "meeting": 0,
            "chat": 0, "announcement": 0, "expense": 0, "visitor": 0,
        })
    try:
        data = get_unread_by_module(db, current_user["user_id"])
        # Keep "unread_count" for the existing bell-badge JS in base.html
        data["unread_count"] = data["total"]
        return JSONResponse(data)
    except Exception:
        return JSONResponse({
            "unread_count": 0, "total": 0,
            "task": 0, "leave": 0, "meeting": 0,
            "chat": 0, "announcement": 0, "expense": 0, "visitor": 0,
        })


# ---------------------------------------------------------------------------
# POST /notifications/read   — mark ALL unread as read
# ---------------------------------------------------------------------------
@router.post("/read")
def mark_all_read(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _SVC_OK:
        return JSONResponse({"ok": True})
    try:
        mark_as_read(db, current_user["user_id"])
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /notifications/read/{notif_id}  — mark single as read
# ---------------------------------------------------------------------------
@router.post("/read/{notif_id}")
def mark_one_read(
    notif_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _SVC_OK:
        return JSONResponse({"ok": True})
    try:
        mark_as_read(db, current_user["user_id"], notification_id=notif_id)
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /notifications/read-module/{module}  — auto mark-as-read on page visit
# ---------------------------------------------------------------------------
@router.post("/read-module/{module}")
def mark_module_read(
    module: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """
    Mark all unread notifications for a given module as read.
    Called automatically via JS when the user navigates to a module page.
    """
    if not _SVC_OK:
        return JSONResponse({"ok": True})
    try:
        from app.models.notification import Notification
        db.query(Notification).filter(
            Notification.user_id == current_user["user_id"],
            Notification.module  == module,
            Notification.is_read == False,  # noqa: E712
        ).update({"is_read": True})
        db.commit()
        return JSONResponse({"ok": True})
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return JSONResponse({"ok": True})

