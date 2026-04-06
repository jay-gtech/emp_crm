from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required
from app.models.meeting import Meeting
from app.models.user import User
from app.services.hierarchy_service import is_user_in_scope
from datetime import datetime

router = APIRouter(tags=["meetings"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def meetings_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        all_meetings = db.query(Meeting).order_by(Meeting.scheduled_time.desc()).all()
        creator_ids = {m.created_by for m in all_meetings}
        creators = (
            {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}
            if creator_ids else {}
        )
        
        role = current_user.get("role")
        user_id = current_user["user_id"]
        
        visible = []
        if role == "admin":
            visible = all_meetings
        else:
            for m in all_meetings:
                creator = creators.get(m.created_by)
                if not creator:
                    continue
                if creator.id == user_id:
                    visible.append(m)
                    continue
                
                if getattr(m, "participant_id", None) == user_id:
                    if m not in visible:
                        visible.append(m)
                    continue
                    
                creator_dict = {"role": creator.role.value, "user_id": creator.id}
                if creator.role.value in ("admin", "manager", "team_lead"):
                    if is_user_in_scope(db, creator_dict, user_id):
                        if m not in visible:
                            visible.append(m)

        filtered_users = []
        if role == "admin":
            filtered_users = db.query(User).filter(User.is_active == 1).all()
        elif role == "manager":
            filtered_users = db.query(User).filter(User.manager_id == user_id, User.role == "team_lead", User.is_active == 1).all()
        elif role == "team_lead":
            filtered_users = db.query(User).filter(User.team_lead_id == user_id, User.role == "employee", User.is_active == 1).all()

    except Exception:
        visible = []
        filtered_users = []

    return templates.TemplateResponse(
        "meetings/index.html",
        {
            "request": request,
            "current_user": current_user,
            "meetings": visible,
            "filtered_users": filtered_users,
            "now": datetime.now(),
        },
    )

@router.post("/create")
def create_meeting(
    title: str = Form(...),
    description: str = Form(""),
    scheduled_time: str = Form(...),
    participant_id: int = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    role = current_user.get("role")
    if role not in ["admin", "manager", "team_lead"]:
        raise HTTPException(status_code=403, detail="Not allowed")

    try:
        dt = datetime.fromisoformat(scheduled_time)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    meeting = Meeting(
        title=title,
        description=description,
        scheduled_time=dt,
        created_by=current_user["user_id"],
        creator_role=role,
        participant_id=participant_id
    )

    db.add(meeting)
    db.commit()

    return RedirectResponse(url="/meetings/", status_code=302)

@router.get("/list")
def get_meetings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required)
):
    meetings = db.query(Meeting).order_by(Meeting.scheduled_time.desc()).all()
    if not meetings:
        return []
        
    role = current_user.get("role")
    if role == "admin":
        return meetings

    # Batch-load all creators in one query to avoid N+1
    creator_ids = {m.created_by for m in meetings}
    creators = {u.id: u for u in db.query(User).filter(User.id.in_(creator_ids)).all()}

    visible = []
    user_id = current_user["user_id"]
    for m in meetings:
        creator = creators.get(m.created_by)
        if not creator:
            continue

        if creator.id == user_id:
            visible.append(m)
            continue
            
        if getattr(m, "participant_id", None) == user_id:
            if m not in visible:
                visible.append(m)
            continue

        creator_dict = {"role": creator.role.value, "user_id": creator.id}
        if creator.role.value in ("admin", "manager", "team_lead"):
            if is_user_in_scope(db, creator_dict, user_id):
                if m not in visible:
                    visible.append(m)

    return visible
