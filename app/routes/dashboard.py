from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required
from app.services.attendance_service import get_today_record, get_attendance_history
from app.services.task_service import list_tasks_for_employee, list_all_tasks, list_tasks_assigned_by
from app.services.leave_service import get_leave_balance, list_pending_leaves
from app.services.break_service import get_active_break, get_today_breaks
from app.models.task import TaskStatus

# New performance/insight service — imported lazily inside the route so that
# any import error in the new module never takes down the whole router.
try:
    from app.services.dashboard_service import (
        get_employee_performance,
        get_manager_insights,
        get_overdue_count,
        get_alerts,
        get_team_performance,
        get_low_performers,
        get_task_distribution,
    )
    _DASHBOARD_SERVICE_OK = True
except Exception:
    _DASHBOARD_SERVICE_OK = False

try:
    from app.services.hierarchy_service import is_user_in_scope
except ImportError:
    is_user_in_scope = None

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    role = current_user["role"]

    # ── Existing data (unchanged) ────────────────────────────────────────────
    today_attendance = get_today_record(db, uid)
    active_break = get_active_break(db, uid)
    today_breaks = get_today_breaks(db, uid)
    leave_balance = get_leave_balance(db, uid)

    if role == "admin":
        tasks = list_all_tasks(db)
        pending_leaves = list_pending_leaves(db)
    elif role in ("manager", "team_lead"):
        tasks = list_tasks_assigned_by(db, uid)
        all_pending = list_pending_leaves(db)
        if is_user_in_scope:
            pending_leaves = [l for l in all_pending if is_user_in_scope(db, current_user, l.employee_id)]
        else:
            pending_leaves = all_pending
    else:
        tasks = list_tasks_for_employee(db, uid)
        pending_leaves = []

    task_stats = {
        "total": len(tasks),
        "pending": sum(1 for t in tasks if t.status == TaskStatus.pending),
        "in_progress": sum(1 for t in tasks if t.status == TaskStatus.in_progress),
        "completed": sum(1 for t in tasks if t.status == TaskStatus.completed),
    }

    recent_history = get_attendance_history(db, uid, limit=7)

    # ── New performance / insight data (each guarded independently) ──────────
    performance: dict = {}
    manager_insights: dict = {}
    alerts: list = []
    overdue_count: int = 0
    team_performance: list = []
    low_performers: list = []
    task_distribution: dict = {}

    if _DASHBOARD_SERVICE_OK:
        try:
            performance = get_employee_performance(db, uid)
        except Exception:
            performance = {}

        if role in ("admin", "manager", "team_lead"):
            try:
                manager_insights = get_manager_insights(db, request_user=current_user)
            except Exception:
                manager_insights = {}

            try:
                team_performance = get_team_performance(db, request_user=current_user)
            except Exception:
                team_performance = []

            try:
                low_performers = get_low_performers(db, request_user=current_user)
            except Exception:
                low_performers = []

            try:
                task_distribution = get_task_distribution(db, request_user=current_user)
            except Exception:
                task_distribution = {}

        try:
            overdue_count = get_overdue_count(db)
        except Exception:
            overdue_count = 0

        try:
            alerts = get_alerts(db, role, uid)
        except Exception:
            alerts = []

    # Attach overdue to task_stats so the template has one source of truth
    task_stats["overdue"] = overdue_count

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            # ── existing keys (untouched) ──
            "request": request,
            "current_user": current_user,
            "today_attendance": today_attendance,
            "active_break": active_break,
            "today_breaks": today_breaks,
            "leave_balance": leave_balance,
            "task_stats": task_stats,
            "pending_leaves": pending_leaves,
            "recent_history": recent_history,
            # ── existing insight keys ──
            "performance": performance,
            "manager_insights": manager_insights,
            "alerts": alerts,
            # ── new manager-view keys ──
            "team_performance": team_performance,
            "low_performers": low_performers,
            "task_distribution": task_distribution,
        },
    )
