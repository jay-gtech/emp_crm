from datetime import date
from fastapi import APIRouter, Request, Form, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required
from app.services.attendance_service import (
    clock_in, clock_out, get_today_record, get_attendance_history, AttendanceError,
)
from app.services.break_service import (
    start_break, end_break, get_today_breaks, get_active_break, BreakError,
)
from app.services.location_service import validate_user_location, save_location_log

# Late clock-in thresholds (09:30)
_LATE_HOUR = 9
_LATE_MINUTE = 30

router = APIRouter(prefix="/attendance", tags=["attendance"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def attendance_page(
    request: Request,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    emp_id = current_user["user_id"]

    # ── Admin: org-wide attendance observer view ──────────────────────────────
    if current_user["role"] == "admin":
        from app.services.hierarchy_service import get_org_attendance_today
        admin_attendance = get_org_attendance_today(db)
        return templates.TemplateResponse(
            "attendance/index.html",
            {
                "request": request,
                "current_user": current_user,
                "today": None,
                "history": [],
                "breaks": [],
                "active_break": None,
                "date_from": date_from or "",
                "date_to": date_to or "",
                "late_hour": _LATE_HOUR,
                "late_minute": _LATE_MINUTE,
                "admin_attendance": admin_attendance,
                "manager_team_attendance": None,
                "tl_team_attendance": None,
            },
        )

    # ── Non-admin: existing personal view ────────────────────────────────────
    today_record = get_today_record(db, emp_id)
    breaks = get_today_breaks(db, emp_id)
    active_break = get_active_break(db, emp_id)

    df: date | None = None
    dt: date | None = None
    try:
        if date_from:
            df = date.fromisoformat(date_from)
    except ValueError:
        pass
    try:
        if date_to:
            dt = date.fromisoformat(date_to)
    except ValueError:
        pass

    history = get_attendance_history(db, emp_id, date_from=df, date_to=dt)

    # ── Manager & Team Lead: fetch their team's attendance today ──────────────
    manager_team_attendance = None
    tl_team_attendance = None
    
    if current_user["role"] == "manager":
        from app.services.hierarchy_service import get_manager_team_attendance_today
        try:
            manager_team_attendance = get_manager_team_attendance_today(db, emp_id)
        except Exception:
            manager_team_attendance = []
            
    if current_user["role"] == "team_lead":
        from app.services.hierarchy_service import get_team_lead_team_attendance_today
        try:
            tl_data = get_team_lead_team_attendance_today(db, emp_id)
            tl_team_attendance = tl_data.get("rows", []) if isinstance(tl_data, dict) else []
        except Exception:
            tl_team_attendance = None

    return templates.TemplateResponse(
        "attendance/index.html",
        {
            "request": request,
            "current_user": current_user,
            "today": today_record,
            "history": history,
            "breaks": breaks,
            "active_break": active_break,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "late_hour": _LATE_HOUR,
            "late_minute": _LATE_MINUTE,
            "error": None,
            "admin_attendance": None,
            "manager_team_attendance": manager_team_attendance,
            "tl_team_attendance": tl_team_attendance,
        },
    )


@router.post("/clock-in")
def do_clock_in(
    request: Request,
    work_mode: str = Form("office"),
    latitude: float = Form(None),
    longitude: float = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user["role"] == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot perform this action")

    # Load full user object for location check
    from app.models.user import User
    user = db.get(User, current_user["user_id"])

    if user:
        valid, error = validate_user_location(user, latitude, longitude)
        if not valid:
            return templates.TemplateResponse(
                "attendance/index.html",
                {
                    "request": request,
                    "current_user": current_user,
                    "error": error,
                    "today": None, "history": [], "breaks": [],
                    "active_break": None, "date_from": "", "date_to": "",
                    "late_hour": _LATE_HOUR, "late_minute": _LATE_MINUTE,
                    "admin_attendance": None,
                    "manager_team_attendance": None,
                    "tl_team_attendance": None,
                },
                status_code=403,
            )

    try:
        clock_in(db, current_user["user_id"], work_mode)
    except AttendanceError:
        pass  # already clocked in — ignore

    save_location_log(db, current_user["user_id"], latitude, longitude, "clock_in")
    return RedirectResponse("/attendance/", status_code=302)


@router.post("/clock-out")
def do_clock_out(
    request: Request,
    latitude: float = Form(None),
    longitude: float = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user["role"] == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot perform this action")

    # Load full user object for location check
    from app.models.user import User
    user = db.get(User, current_user["user_id"])

    if user:
        valid, error = validate_user_location(user, latitude, longitude)
        if not valid:
            return templates.TemplateResponse(
                "attendance/index.html",
                {
                    "request": request,
                    "current_user": current_user,
                    "error": error,
                    "today": None, "history": [], "breaks": [],
                    "active_break": None, "date_from": "", "date_to": "",
                    "late_hour": _LATE_HOUR, "late_minute": _LATE_MINUTE,
                    "admin_attendance": None,
                    "manager_team_attendance": None,
                    "tl_team_attendance": None,
                },
                status_code=403,
            )

    try:
        clock_out(db, current_user["user_id"])
    except AttendanceError:
        pass

    save_location_log(db, current_user["user_id"], latitude, longitude, "clock_out")
    return RedirectResponse("/attendance/", status_code=302)


@router.post("/break/start")
def do_start_break(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user["role"] == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot perform this action")

    try:
        start_break(db, current_user["user_id"])
    except BreakError:
        pass
    return RedirectResponse("/attendance/", status_code=302)


@router.post("/break/end")
def do_end_break(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user["role"] == "admin":
        raise HTTPException(status_code=403, detail="Admins cannot perform this action")

    try:
        end_break(db, current_user["user_id"])
    except BreakError:
        pass
    return RedirectResponse("/attendance/", status_code=302)
