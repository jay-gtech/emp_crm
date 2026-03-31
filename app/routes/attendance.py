from datetime import date
from fastapi import APIRouter, Request, Form, Depends, Query
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
        },
    )


@router.post("/clock-in")
def do_clock_in(
    request: Request,
    work_mode: str = Form("office"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        clock_in(db, current_user["user_id"], work_mode)
    except AttendanceError:
        pass  # already clocked in — ignore
    return RedirectResponse("/attendance/", status_code=302)


@router.post("/clock-out")
def do_clock_out(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        clock_out(db, current_user["user_id"])
    except AttendanceError:
        pass
    return RedirectResponse("/attendance/", status_code=302)


@router.post("/break/start")
def do_start_break(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
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
    try:
        end_break(db, current_user["user_id"])
    except BreakError:
        pass
    return RedirectResponse("/attendance/", status_code=302)
