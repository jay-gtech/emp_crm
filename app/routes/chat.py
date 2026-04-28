"""
chat.py — Chat routes.

Existing DM routes (GET /, POST /send, GET /history/{user_id}) are preserved
exactly as before.  New group-chat routes and the WebSocket endpoint are added
below without touching the DM logic.
"""
import logging

from fastapi import (
    APIRouter, Depends, Form, HTTPException, Query,
    Request, UploadFile, File, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.core.database import get_db
from app.core.auth import login_required, get_session_user
from app.models.message import Message
from app.models.user import User
from app.services.hierarchy_service import is_user_in_scope
from app.services import chat_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="app/templates")


# ══════════════════════════════════════════════════════════════════════════════
# ── EXISTING DM ROUTES (unchanged) ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/", response_class=HTMLResponse)
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid  = current_user["user_id"]
    role = current_user["role"]

    try:
        all_users       = db.query(User).filter(User.is_active == 1).all()
        current_db_user = db.query(User).filter(User.id == uid).first()
        chat_users = []
        for u in all_users:
            if u.id == uid:
                continue
            u_dict  = {"role": u.role.value, "user_id": u.id}
            allowed = False
            if is_user_in_scope(db, current_user, u.id):
                allowed = True
            elif is_user_in_scope(db, u_dict, uid):
                allowed = True
            elif role == "employee" and u.role.value == "employee":
                if (
                    current_db_user
                    and current_db_user.team_lead_id
                    and current_db_user.team_lead_id == u.team_lead_id
                ):
                    allowed = True
            if allowed:
                chat_users.append(u)
    except Exception:
        chat_users = []

    # Groups the current user belongs to
    try:
        my_groups = chat_service.get_my_groups(db, uid)
    except Exception:
        my_groups = []

    # All active users for the "create group" member picker
    try:
        all_active = db.query(User).filter(User.is_active == 1, User.id != uid).all()
    except Exception:
        all_active = []

    return templates.TemplateResponse(
        "chat/index.html",
        {
            "request":     request,
            "current_user": current_user,
            "chat_users":  chat_users,
            "my_groups":   my_groups,
            "all_active":  all_active,
        },
    )


@router.post("/send")
async def send_message(
    receiver_id: int  = Form(...),
    content:     str  = Form(default=""),
    file:        UploadFile = File(default=None),
    db:          Session = Depends(get_db),
    current_user: dict  = Depends(login_required),
):
    content = (content or "").strip()

    file_url: str | None = None
    if file and file.filename:
        try:
            file_url = await chat_service.save_uploaded_file(file)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    if not content and not file_url:
        raise HTTPException(400, "Message or file required")

    if current_user["user_id"] == receiver_id:
        raise HTTPException(400, "Cannot message self")

    receiver = db.query(User).get(receiver_id)
    if not receiver:
        raise HTTPException(404, "Receiver not found")

    sender_id     = current_user["user_id"]
    receiver_dict = {"role": receiver.role.value, "user_id": receiver.id}

    allowed = False
    if is_user_in_scope(db, current_user, receiver.id):
        allowed = True
    elif is_user_in_scope(db, receiver_dict, sender_id):
        allowed = True
    elif current_user["role"] == "employee" and receiver.role.value == "employee":
        sender = db.query(User).get(sender_id)
        if sender and sender.team_lead_id and sender.team_lead_id == receiver.team_lead_id:
            allowed = True

    if not allowed:
        raise HTTPException(403, "Not allowed")

    message = Message(sender_id=sender_id, receiver_id=receiver_id, content=content, file_url=file_url)
    db.add(message)
    db.commit()

    # Notify receiver — fire-and-forget (module-tagged for sidebar badge)
    try:
        from app.services.notification_service import create_notification as _notif
        sender_obj = db.query(User).filter(User.id == sender_id).first()
        sender_name = sender_obj.name if sender_obj else "Someone"
        preview = str(content).strip()[:60] if content else "Sent a file"
        _notif(
            db, receiver_id, "chat",
            f"💬 New message from {sender_name}: {preview}",
            actor_id=sender_id,
        )
    except Exception:
        pass

    return {"message": "Sent"}


@router.get("/history/{user_id}")
def get_chat(
    user_id: int,
    limit:   int     = Query(default=100, ge=1, le=500),
    db:      Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    target = db.query(User).get(user_id)
    if not target:
        raise HTTPException(404, "User not found")

    receiver_dict = {"role": target.role.value, "user_id": user_id}
    sender_id     = current_user["user_id"]

    allowed = False
    if is_user_in_scope(db, current_user, user_id):
        allowed = True
    elif is_user_in_scope(db, receiver_dict, sender_id):
        allowed = True
    elif current_user["role"] == "employee" and target.role.value == "employee":
        sender = db.query(User).get(sender_id)
        if sender and sender.team_lead_id and sender.team_lead_id == target.team_lead_id:
            allowed = True

    if not allowed:
        raise HTTPException(403, "Not allowed")

    messages = (
        db.query(Message)
        .filter(
            or_(
                and_(Message.sender_id == sender_id, Message.receiver_id == user_id),
                and_(Message.sender_id == user_id,   Message.receiver_id == sender_id),
            ),
            Message.group_id.is_(None),   # DMs only
        )
        .order_by(Message.timestamp.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(messages))


# ══════════════════════════════════════════════════════════════════════════════
# ── GROUP CHAT ROUTES (new) ────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/groups/create")
def create_group(
    name:        str  = Form(...),
    member_ids:  str  = Form(default=""),   # comma-separated user IDs
    db:          Session = Depends(get_db),
    current_user: dict  = Depends(login_required),
):
    uid = current_user["user_id"]
    ids: list[int] = []
    for part in member_ids.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))

    try:
        chat_service.create_group(db, name, uid, ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return JSONResponse({"ok": True})


@router.get("/groups/{group_id}/history")
def group_history(
    group_id: int,
    limit:    int     = Query(default=100, ge=1, le=500),
    db:       Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    if not chat_service.is_group_member(db, group_id, uid):
        raise HTTPException(403, "Not a member of this group")

    return chat_service.get_group_history(db, group_id, limit)


@router.get("/groups/{group_id}/members")
def group_members(
    group_id: int,
    db:       Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    if not chat_service.is_group_member(db, group_id, uid):
        raise HTTPException(403, "Not a member of this group")
    return chat_service.get_group_members(db, group_id)


@router.post("/groups/{group_id}/add-members")
def add_group_members(
    group_id:   int,
    member_ids: str  = Form(default=""),
    db:         Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid  = current_user["user_id"]
    ids: list[int] = []
    for part in member_ids.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))

    try:
        added = chat_service.add_members(db, group_id, ids, uid)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    return {"added": added}


@router.post("/groups/{group_id}/send")
async def send_group_message(
    group_id: int,
    content:  str        = Form(default=""),
    file:     UploadFile = File(default=None),
    db:       Session    = Depends(get_db),
    current_user: dict   = Depends(login_required),
):
    uid = current_user["user_id"]
    if not chat_service.is_group_member(db, group_id, uid):
        raise HTTPException(403, "Not a member of this group")

    content = (content or "").strip()

    # Handle optional file upload
    file_url: str | None = None
    if file and file.filename:
        try:
            file_url = await chat_service.save_uploaded_file(file)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    if not content and not file_url:
        raise HTTPException(400, "Message or file required")

    msg_dict = chat_service.save_group_message(db, group_id, uid, content or "", file_url)

    # Broadcast over WebSocket to all online members of this group
    try:
        await chat_service.manager.broadcast(group_id, {
            "type":        "message",
            "sender_id":   msg_dict["sender_id"],
            "sender_name": msg_dict["sender_name"],
            "content":     msg_dict["content"],
            "file_url":    msg_dict["file_url"],
            "timestamp":   msg_dict["timestamp"],
        })
    except Exception:
        pass

    # Notify group members who are NOT currently in the group WS (offline members)
    try:
        from app.services.notification_service import create_notification as _notif
        online_uids = {
            u_id for u_id, _ in chat_service.manager._connections.get(group_id, [])
        }
        sender_name = msg_dict["sender_name"]
        preview     = (content or "Sent a file")[:60]
        for m in chat_service.get_group_members(db, group_id):
            mid = m.get("user_id")
            if mid and mid != uid and mid not in online_uids:
                _notif(
                    db, mid, "chat",
                    f"💬 {sender_name} in group: {preview}",
                    actor_id=uid,
                )
    except Exception:
        pass

    return msg_dict


# ══════════════════════════════════════════════════════════════════════════════
# ── WebSocket endpoint (group real-time) ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/group/{group_id}")
async def group_ws(
    websocket: WebSocket,
    group_id:  int,
    db:        Session = Depends(get_db),
):
    """
    WebSocket for real-time group chat.
    Auth: reads user_id from the session cookie (Starlette SessionMiddleware
    processes the WS upgrade request just like HTTP).
    """
    user = get_session_user(websocket)
    if not user:
        await websocket.close(code=4001)
        return

    uid = user["user_id"]
    if not chat_service.is_group_member(db, group_id, uid):
        await websocket.close(code=4003)
        return

    await chat_service.manager.connect(group_id, uid, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            content   = (data.get("content") or "").strip()
            if not content:
                continue

            msg_dict = chat_service.save_group_message(db, group_id, uid, content)
            await chat_service.manager.broadcast(group_id, {
                "type":        "message",
                "sender_id":   msg_dict["sender_id"],
                "sender_name": msg_dict["sender_name"],
                "content":     msg_dict["content"],
                "file_url":    msg_dict["file_url"],
                "timestamp":   msg_dict["timestamp"],
            })

            # Notify offline group members (those not in the group WS right now)
            try:
                from app.services.notification_service import create_notification as _notif
                online_uids = {
                    u_id for u_id, _ in chat_service.manager._connections.get(group_id, [])
                }
                sender_name = msg_dict["sender_name"]
                preview     = content[:60]
                for m in chat_service.get_group_members(db, group_id):
                    mid = m.get("user_id")
                    if mid and mid != uid and mid not in online_uids:
                        _notif(
                            db, mid, "chat",
                            f"💬 {sender_name} in group: {preview}",
                            actor_id=uid,
                        )
            except Exception:
                pass
    except WebSocketDisconnect:
        chat_service.manager.disconnect(group_id, websocket)
    except Exception as exc:
        logger.warning("[ws] group %s uid %s error: %s", group_id, uid, exc)
        chat_service.manager.disconnect(group_id, websocket)
