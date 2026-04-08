"""
Announcements router — extended with audience targeting + service layer.
Existing route paths (/announcements/, /announcements/create, /announcements/list)
are preserved for full backward compatibility.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required
from app.core.database import get_db
from app.services.announcement_service import (
    AnnouncementError,
    create_announcement,
    get_all_active_users,
    get_visible_announcements,
)

router    = APIRouter(tags=["announcements"])
templates = Jinja2Templates(directory="app/templates")

_CREATOR_ROLES = ("admin", "manager", "team_lead")


# ---------------------------------------------------------------------------
# GET / — main announcements page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def announcements_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    announcements = get_visible_announcements(db, current_user)
    users_for_picker: list = []
    if current_user.get("role") in _CREATOR_ROLES:
        users_for_picker = get_all_active_users(db)

    return templates.TemplateResponse(
        "announcements/index.html",
        {
            "request":          request,
            "current_user":     current_user,
            "announcements":    announcements,
            "users_for_picker": users_for_picker,
            "flash":            request.query_params.get("flash"),
            "error":            request.query_params.get("error"),
        },
    )


# ---------------------------------------------------------------------------
# POST /create — create announcement (admin / manager / team_lead)
# ---------------------------------------------------------------------------

@router.post("/create")
def create_announcement_post(
    request: Request,
    title: str = Form(...),
    message: str = Form(...),
    audience_type: str = Form(default="all"),
    target_ids_raw: str = Form(default=""),   # comma-separated user IDs
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    role = current_user.get("role")
    if role not in _CREATOR_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed to post announcements.")

    # Parse target_ids
    target_ids: list[int] = []
    if audience_type == "specific" and target_ids_raw.strip():
        try:
            target_ids = [int(x.strip()) for x in target_ids_raw.split(",") if x.strip()]
        except ValueError:
            return RedirectResponse(
                "/announcements/?error=Invalid+user+IDs+specified.", status_code=303
            )

    try:
        create_announcement(
            db=db,
            title=title,
            message=message,
            created_by=current_user["user_id"],
            sender_role=role,
            audience_type=audience_type,
            target_ids=target_ids or None,
        )
    except AnnouncementError as exc:
        return RedirectResponse(
            f"/announcements/?error={quote(str(exc))}", status_code=303
        )

    return RedirectResponse("/announcements/?flash=created", status_code=302)


# ---------------------------------------------------------------------------
# GET /list — JSON API (kept for backward compat)
# ---------------------------------------------------------------------------

@router.get("/list")
def get_announcements_json(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    return get_visible_announcements(db, current_user)
