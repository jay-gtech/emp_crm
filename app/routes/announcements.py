from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required
from app.models.announcement import Announcement
from app.models.user import User
from app.services.hierarchy_service import is_user_in_scope

router = APIRouter(tags=["announcements"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def announcements_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        all_announcements = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
        creator_ids = {a.created_by for a in all_announcements}
        senders = (
            {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}
            if creator_ids else {}
        )
        visible = []
        for a in all_announcements:
            sender = senders.get(a.created_by)
            if not sender:
                continue
            if sender.role.value == "admin":
                visible.append(a)
            elif sender.role.value in ("manager", "team_lead"):
                if sender.id == current_user["user_id"]:
                    visible.append(a)
                elif is_user_in_scope(
                    db, {"role": sender.role.value, "user_id": sender.id}, current_user["user_id"]
                ):
                    visible.append(a)
    except Exception:
        visible = []

    return templates.TemplateResponse(
        "announcements/index.html",
        {
            "request": request,
            "current_user": current_user,
            "announcements": visible,
        },
    )

@router.post("/create")
def create_announcement(
    title: str = Form(...),
    message: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    role = current_user.get("role")
    if role not in ["admin", "manager", "team_lead"]:
        raise HTTPException(status_code=403, detail="Not allowed")

    announcement = Announcement(
        title=title,
        message=message,
        created_by=current_user["user_id"],
        sender_role=role
    )

    db.add(announcement)
    db.commit()

    return RedirectResponse(url="/announcements/", status_code=302)

@router.get("/list")
def get_announcements(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    # This route returns visible announcements as a JSON list.
    announcements = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    if not announcements:
        return []

    # Batch-load all senders in one query to avoid N+1
    creator_ids = {a.created_by for a in announcements}
    senders = {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}

    visible = []
    for a in announcements:
        sender = senders.get(a.created_by)
        if not sender:
            continue

        if sender.role.value == "admin":
            visible.append(a)
        elif sender.role.value in ("manager", "team_lead"):
            # If the current user is the sender themselves, they can see it.
            if sender.id == current_user["user_id"]:
                visible.append(a)
            else:
                # The RBAC logic: is the reading user in the scope of the sender?
                sender_dict = {"role": sender.role.value, "user_id": sender.id}
                if is_user_in_scope(db, sender_dict, current_user["user_id"]):
                    visible.append(a)

    return visible
