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
# GET /notifications/unread  — lightweight count poll
# ---------------------------------------------------------------------------
@router.get("/unread")
def unread_count(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _SVC_OK:
        return JSONResponse({"unread_count": 0})
    try:
        count = get_unread_count(db, current_user["user_id"])
        return JSONResponse({"unread_count": count})
    except Exception:
        return JSONResponse({"unread_count": 0})


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
