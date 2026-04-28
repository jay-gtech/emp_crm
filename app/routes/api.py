"""
JSON + CSV REST API layer.
All endpoints require login.  Role-based scoping mirrors the HTML routes.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.task_service import list_all_tasks, list_tasks_for_employee
from app.services.leave_service import list_all_leaves, list_leaves_for_employee
from app.services.attendance_service import get_attendance_history
from app.services.employee_service import list_employees

try:
    from app.services.dashboard_service import (
        get_employee_performance,
        get_manager_insights,
        get_overdue_count,
        get_team_performance,
        get_task_distribution,
    )
    _DASH_OK = True
except Exception:
    _DASH_OK = False

try:
    from app.services.notification_service import get_notifications
    _NOTIF_OK = True
except Exception:
    _NOTIF_OK = False

try:
    from app.services.audit_service import list_audit_logs
    _AUDIT_OK = True
except Exception:
    _AUDIT_OK = False

router = APIRouter(prefix="/api", tags=["api"])

# Role → which parent role is valid
_VALID_PARENT_ROLE: dict[str, str] = {
    "manager":   "admin",
    "team_lead": "manager",
    "employee":  "team_lead",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_dict(t) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "assigned_to": getattr(t, "assigned_to", None),  # removed column; kept for API compat
        "assigned_by": t.assigned_by,
        "priority": t.priority.value if t.priority else None,
        "status":   t.status.value   if t.status   else None,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _leave_dict(l) -> dict:
    return {
        "id": l.id,
        "employee_id": l.employee_id,
        "leave_type": l.leave_type.value,
        "start_date": l.start_date.isoformat(),
        "end_date": l.end_date.isoformat(),
        "total_days": l.total_days,
        "reason": l.reason,
        "status": l.status.value,
        "reviewed_by": l.reviewed_by,
        "review_note": l.review_note,
        "created_at": l.created_at.isoformat() if l.created_at else None,
    }


def _attendance_dict(a) -> dict:
    return {
        "id": a.id,
        "employee_id": a.employee_id,
        "date": a.date.isoformat(),
        "clock_in_time": a.clock_in_time.isoformat() if a.clock_in_time else None,
        "clock_out_time": a.clock_out_time.isoformat() if a.clock_out_time else None,
        "total_hours": a.total_hours,
        "total_break_hours": a.total_break_hours,
        "work_mode": a.work_mode.value if a.work_mode else None,
    }


# ---------------------------------------------------------------------------
# Hierarchy helpers
# ---------------------------------------------------------------------------

@router.get("/users/valid-parents")
def get_valid_parents(
    role: str = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Return users eligible to be the parent of a given role (any logged-in user)."""
    from app.models.user import User, UserRole
    parent_role_str = _VALID_PARENT_ROLE.get(role)
    if not parent_role_str:
        return JSONResponse([])
    try:
        parent_role = UserRole(parent_role_str)
    except ValueError:
        return JSONResponse([])
    users = (
        db.query(User)
        .filter(User.role == parent_role, User.is_active == 1)
        .order_by(User.name)
        .all()
    )
    return JSONResponse([
        {"id": u.id, "name": u.name, "role": u.role.value}
        for u in users
    ])


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------

@router.get("/employees/export")
def export_employees_csv(
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    employees = list_employees(db, request_user=current_user)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "email", "role", "department"])
    for e in employees:
        writer.writerow([e.id, e.name, e.email, e.role.value, e.department or ""])

    buf.seek(0)
    filename = f"employees_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/tasks")
def api_tasks(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    role = current_user["role"]
    if role in ("admin", "manager", "team_lead"):
        tasks = list_all_tasks(db, request_user=current_user)
    else:
        tasks = list_tasks_for_employee(db, uid)
    return JSONResponse([_task_dict(t) for t in tasks])


@router.get("/tasks/export")
def export_tasks_csv(
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    tasks = list_all_tasks(db, request_user=current_user)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "assigned_to", "assigned_by", "priority",
                     "status", "due_date", "created_at"])
    for t in tasks:
        writer.writerow([
            t.id, t.title, getattr(t, "assigned_to", ""), t.assigned_by,
            t.priority.value if t.priority else "",
            t.status.value   if t.status   else "",
            t.due_date.isoformat() if t.due_date else "",
            t.created_at.isoformat() if t.created_at else "",
        ])

    buf.seek(0)
    filename = f"tasks_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

@router.get("/attendance")
def api_attendance(
    limit: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    records = get_attendance_history(db, current_user["user_id"], limit=limit)
    return JSONResponse([_attendance_dict(a) for a in records])


@router.get("/attendance/export")
def export_attendance_csv(
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    from app.models.attendance import Attendance as AttModel
    records = db.query(AttModel).order_by(AttModel.date.desc()).limit(1000).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "employee_id", "date", "clock_in_time", "clock_out_time",
                     "total_hours", "total_break_hours", "work_mode"])
    for a in records:
        writer.writerow([
            a.id, a.employee_id, a.date.isoformat(),
            a.clock_in_time.isoformat() if a.clock_in_time else "",
            a.clock_out_time.isoformat() if a.clock_out_time else "",
            a.total_hours or "", a.total_break_hours or "",
            a.work_mode.value if a.work_mode else "",
        ])

    buf.seek(0)
    filename = f"attendance_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------

@router.get("/leave")
def api_leave(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    role = current_user["role"]
    if role in ("admin", "manager", "team_lead"):
        leaves = list_all_leaves(db)
    else:
        leaves = list_leaves_for_employee(db, current_user["user_id"])
    return JSONResponse([_leave_dict(l) for l in leaves])


@router.get("/leave/export")
def export_leave_csv(
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    leaves = list_all_leaves(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "employee_id", "leave_type", "start_date", "end_date",
                     "total_days", "status", "reviewed_by", "review_note"])
    for l in leaves:
        writer.writerow([
            l.id, l.employee_id, l.leave_type.value,
            l.start_date.isoformat(), l.end_date.isoformat(),
            l.total_days, l.status.value,
            l.reviewed_by or "", l.review_note or "",
        ])

    buf.seek(0)
    filename = f"leaves_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------

@router.get("/dashboard")
def api_dashboard(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    payload: dict = {"user_id": uid, "role": current_user["role"]}

    if _DASH_OK:
        try:
            payload["performance"] = get_employee_performance(db, uid)
        except Exception:
            payload["performance"] = {}

        if current_user["role"] in ("admin", "manager", "team_lead"):
            try:
                payload["manager_insights"] = get_manager_insights(db, request_user=current_user)
            except Exception:
                payload["manager_insights"] = {}
            try:
                payload["team_performance"] = get_team_performance(db, request_user=current_user)
            except Exception:
                payload["team_performance"] = []
            try:
                payload["task_distribution"] = get_task_distribution(db, request_user=current_user)
            except Exception:
                payload["task_distribution"] = {}

        try:
            payload["overdue_count"] = get_overdue_count(db)
        except Exception:
            payload["overdue_count"] = 0

    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@router.get("/notifications")
def api_notifications(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if not _NOTIF_OK:
        return JSONResponse([])
    notifs = get_notifications(db, current_user["user_id"], limit=50)
    return JSONResponse(notifs)


# ---------------------------------------------------------------------------
# Audit log (admin only)
# ---------------------------------------------------------------------------

@router.get("/audit")
def api_audit(
    actor_id: int | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin")),
):
    if not _AUDIT_OK:
        return JSONResponse([])
    logs = list_audit_logs(db, actor_id=actor_id, target_type=target_type,
                           target_id=target_id, limit=limit)
    return JSONResponse(logs)
