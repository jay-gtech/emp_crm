from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from app.core.database import get_db
from app.core.auth import login_required
from app.models.message import Message
from app.models.user import User
from app.services.hierarchy_service import is_user_in_scope

router = APIRouter(tags=["chat"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def chat_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    role = current_user["role"]

    try:
        all_users = db.query(User).filter(User.is_active == 1).all()
        current_db_user = db.query(User).filter(User.id == uid).first()
        chat_users = []
        for u in all_users:
            if u.id == uid:
                continue
            u_dict = {"role": u.role.value, "user_id": u.id}
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

    return templates.TemplateResponse(
        "chat/index.html",
        {
            "request": request,
            "current_user": current_user,
            "chat_users": chat_users,
        },
    )

@router.post("/send")
def send_message(
    receiver_id: int = Form(...),
    content: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    if not content or str(content).strip() == "":
        raise HTTPException(400, "Message cannot be empty")

    if current_user["user_id"] == receiver_id:
        raise HTTPException(400, "Cannot message self")

    receiver = db.query(User).get(receiver_id)
    if not receiver:
        raise HTTPException(404, "Receiver not found")

    sender_id = current_user["user_id"]
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

    message = Message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        content=content
    )

    db.add(message)
    db.commit()

    # If it was sent from a web form, redirect back, else return JSON
    return {"message": "Sent"}

@router.get("/history/{user_id}")
def get_chat(
    user_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    # Verify scope for viewing
    receiver_dict_or_target = db.query(User).get(user_id)
    if not receiver_dict_or_target:
        raise HTTPException(404, "User not found")

    receiver_dict = {"role": receiver_dict_or_target.role.value, "user_id": user_id}
    sender_id = current_user["user_id"]
    
    allowed = False
    if is_user_in_scope(db, current_user, user_id):
        allowed = True
    elif is_user_in_scope(db, receiver_dict, sender_id):
        allowed = True
    elif current_user["role"] == "employee" and receiver_dict_or_target.role.value == "employee":
        sender = db.query(User).get(sender_id)
        if sender and sender.team_lead_id and sender.team_lead_id == receiver_dict_or_target.team_lead_id:
            allowed = True

    if not allowed:
        raise HTTPException(403, "Not allowed")

    # Fetch the most-recent `limit` messages newest-first (uses index tail scan),
    # then reverse in Python so the caller always receives oldest→newest order.
    messages = db.query(Message).filter(
        or_(
            and_(Message.sender_id == sender_id, Message.receiver_id == user_id),
            and_(Message.sender_id == user_id, Message.receiver_id == sender_id)
        )
    ).order_by(Message.timestamp.desc()).limit(limit).all()

    return list(reversed(messages))
