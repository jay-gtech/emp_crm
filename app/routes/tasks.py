from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.task_service import (
    create_task, list_tasks_for_employee, list_tasks_assigned_by,
    list_all_tasks, update_task_status, delete_task, TaskError,
    start_task, submit_task, approve_task,
)
from app.services.employee_service import list_employees
from app.models.task import Task, TaskStatus
from app.models.user import User

# Notification helper — fire-and-forget
try:
    from app.services.notification_service import create_task_notification as _notify
    _NOTIFY_OK = True
except Exception:
    _NOTIFY_OK = False
    def _notify(*a, **kw): pass  # noqa: E302

# Analytics helper — optional
try:
    from app.services.analytics_service import (
        get_user_task_stats, get_manager_team_stats, get_system_task_stats
    )
    _ANALYTICS_OK = True
except Exception:
    _ANALYTICS_OK = False

# Audit trigger — imported defensively
try:
    from app.services.audit_service import log_action as _audit
    _AUDIT_OK = True
except Exception:
    _AUDIT_OK = False

router = APIRouter(prefix="/tasks", tags=["tasks"])
templates = Jinja2Templates(directory="app/templates")


# ── Task list ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def task_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid  = current_user["user_id"]
    role = current_user["role"]

    if role in ("admin", "manager", "team_lead"):
        tasks = list_all_tasks(db, request_user=current_user)
    else:
        tasks = list_tasks_for_employee(db, uid)

    employees = list_employees(db, request_user=current_user) if role in ("admin", "manager", "team_lead") else []

    # Role-based assignee filtering
    if role == "admin":
        employees = [e for e in employees if e.role.value in ("manager", "team_lead")]
    elif role == "manager":
        employees = [e for e in employees if e.role.value == "team_lead"]
    elif role == "team_lead":
        employees = [e for e in employees if e.role.value == "employee"]

    # Build approved_by user map for display
    approved_by_ids = {t.approved_by for t in tasks if t.approved_by}
    approver_map: dict[int, str] = {}
    if approved_by_ids:
        approvers = db.query(User).filter(User.id.in_(approved_by_ids)).all()
        approver_map = {u.id: u.name for u in approvers}

    # Build analytics context
    task_stats: dict = {}
    if _ANALYTICS_OK:
        try:
            if role == "employee":
                task_stats = get_user_task_stats(db, uid)
            elif role == "team_lead":
                task_stats = get_user_task_stats(db, uid)
                task_stats.update(get_manager_team_stats(db, uid))
            elif role == "manager":
                task_stats = get_manager_team_stats(db, uid)
            elif role == "admin":
                task_stats = get_system_task_stats(db)
        except Exception:
            task_stats = {}

    return templates.TemplateResponse(
        "tasks/list.html",
        {
            "request":      request,
            "current_user": current_user,
            "tasks":        tasks,
            "employees":    employees,
            "approver_map": approver_map,
            "task_stats":   task_stats,
        },
    )


# ── Create task ───────────────────────────────────────────────────────────────

@router.post("/create")
def create_task_post(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    assigned_to: int = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(""),
    deadline: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    dd = None
    if due_date:
        try:
            dd = date.fromisoformat(due_date)
        except ValueError:
            pass

    dl: datetime | None = None
    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
        except ValueError:
            pass

    # Hierarchy-aware assignment validation
    if current_user["role"] in ("admin", "manager", "team_lead"):
        from app.services.employee_service import get_employee
        from app.services.hierarchy_service import is_user_in_scope
        try:
            assignee = get_employee(db, assigned_to)
        except Exception:
            return RedirectResponse("/tasks/", status_code=302)
        _role = current_user["role"]
        if _role == "admin":
            if assignee.role.value not in ("manager", "team_lead"):
                return RedirectResponse("/tasks/", status_code=302)
        elif _role == "manager":
            if assignee.role.value != "team_lead":
                return RedirectResponse("/tasks/", status_code=302)
            if not is_user_in_scope(db, current_user, assignee.id):
                return RedirectResponse("/tasks/", status_code=302)
        else:  # team_lead
            if assignee.role.value != "employee":
                raise HTTPException(status_code=403, detail="Team Lead can only assign to employees")
            if not is_user_in_scope(db, current_user, assignee.id):
                raise HTTPException(status_code=403, detail="User not in your team")

    try:
        task = create_task(
            db,
            title=title,
            assigned_to=assigned_to,
            assigned_by=current_user["user_id"],
            description=description or None,
            priority=priority,
            due_date=dd,
            deadline=dl,
        )
        # Notify the assignee
        if _NOTIFY_OK:
            try:
                _notify(db, task.assigned_to, f'📋 New task assigned to you: "{task.title}"')
            except Exception:
                pass
        if _AUDIT_OK:
            try:
                _audit(db, current_user["user_id"], "task_created", "task", task.id,
                       f'"{task.title}" assigned to user {assigned_to}')
            except Exception:
                pass
    except TaskError:
        pass
    return RedirectResponse("/tasks/", status_code=302)


# ── Generic status update (backward compat / admin overrides) ─────────────────

@router.post("/{task_id}/update-status")
@router.post("/{task_id}/status")
def update_status(
    task_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    task = db.query(Task).get(task_id)
    if not task:
        return RedirectResponse("/tasks/", status_code=302)

    VALID_STATUSES = ["todo", "pending", "assigned", "in_progress", "completed",
                      "pending_approval", "approved", "rejected"]
    if status not in VALID_STATUSES:
        raise HTTPException(400, "Invalid status")

    assignee = db.query(User).get(task.assigned_to)
    role = current_user.get("role")
    uid  = current_user.get("user_id")

    # Assignee flow
    if uid == task.assigned_to:
        if status == "completed":
            # Assignee cannot directly complete — redirect to lifecycle
            task.status = TaskStatus("pending_approval")
        elif status in ["todo", "assigned", "in_progress"]:
            task.status = TaskStatus(status)
        else:
            raise HTTPException(403, "Invalid action for assignee")

    # Team Lead flow
    elif role == "team_lead":
        if assignee and assignee.role.value == "employee":
            from app.services.hierarchy_service import is_user_in_scope
            if not is_user_in_scope(db, current_user, task.assigned_to):
                raise HTTPException(403, "This task is outside your team scope")
            if task.status != TaskStatus.pending_approval:
                raise HTTPException(403, "Task must be pending_approval before approval/rejection")
            if status in ["approved", "rejected"]:
                task.status = TaskStatus("in_progress") if status == "rejected" else TaskStatus("approved")
            else:
                raise HTTPException(403, "Invalid approval scope")
        else:
            raise HTTPException(403, "Invalid approval scope")

    # Manager flow
    elif role == "manager":
        if assignee and assignee.role.value == "team_lead":
            from app.services.hierarchy_service import is_user_in_scope
            if not is_user_in_scope(db, current_user, task.assigned_to):
                raise HTTPException(403, "This task is outside your management scope")
            if task.status != TaskStatus.pending_approval:
                raise HTTPException(403, "Task must be pending_approval before approval/rejection")
            if status in ["approved", "rejected"]:
                task.status = TaskStatus("in_progress") if status == "rejected" else TaskStatus("approved")
            else:
                raise HTTPException(403, "Invalid approval scope")
        else:
            raise HTTPException(403, "Invalid approval scope")

    # Admin fallback
    elif role == "admin":
        task.status = TaskStatus("in_progress") if status == "rejected" else TaskStatus(status)

    else:
        raise HTTPException(403, "Not allowed")

    db.commit()

    # ML outcomes if finalized
    if task.status in [TaskStatus.completed, TaskStatus.approved]:
        try:
            from app.services.outcome_tracking_service import update_task_outcome
            update_task_outcome(db, task.id)
        except Exception:
            pass

    return RedirectResponse("/tasks/", status_code=302)


# ── Lifecycle endpoints ───────────────────────────────────────────────────────

@router.post("/{task_id}/start")
def start_task_route(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assignee starts the task — status: assigned → in_progress, timer starts."""
    task = start_task(db, task_id, current_user)
    # Notify the assigner that work has begun
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_by, f'▶ Task started: "{task.title}" is now in progress.')
        except Exception:
            pass
    if _AUDIT_OK:
        try:
            _audit(db, current_user["user_id"], "task_started", "task", task_id)
        except Exception:
            pass
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/{task_id}/submit")
def submit_task_route(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assignee submits the task — status: in_progress → pending_approval."""
    task = submit_task(db, task_id, current_user)
    # Notify the assigner that approval is needed
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_by, f'⏳ Task ready for your approval: "{task.title}"')
        except Exception:
            pass
    if _AUDIT_OK:
        try:
            _audit(db, current_user["user_id"], "task_submitted", "task", task_id)
        except Exception:
            pass
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/{task_id}/approve")
def approve_task_route(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assigner approves the task — status: pending_approval → completed, timer stops."""
    task = approve_task(db, task_id, current_user)
    # Notify the assignee their work is approved
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_to, f'✅ Your task was approved: "{task.title}"')
        except Exception:
            pass
    if _AUDIT_OK:
        try:
            _audit(db, current_user["user_id"], "task_approved", "task", task_id)
        except Exception:
            pass
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/{task_id}/reject")
def reject_task_route(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assigner rejects the task — sends back to in_progress."""
    task = db.query(Task).get(task_id)
    if not task:
        return RedirectResponse("/tasks/", status_code=302)
    uid = current_user.get("user_id")
    role = current_user.get("role")
    if task.assigned_by != uid and role != "admin":
        raise HTTPException(403, "Only the task assigner can reject this task")
    if task.status != TaskStatus.pending_approval:
        raise HTTPException(400, "Task must be pending_approval to reject")
    task.status = TaskStatus.in_progress
    db.commit()
    # Notify the assignee of the rejection
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_to, f'↩ Your task was sent back for revision: "{task.title}"')
        except Exception:
            pass
    if _AUDIT_OK:
        try:
            _audit(db, uid, "task_rejected", "task", task_id)
        except Exception:
            pass
    return RedirectResponse("/tasks/", status_code=302)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{task_id}/delete")
def delete_task_post(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    try:
        delete_task(db, task_id, current_user["user_id"])
        if _AUDIT_OK:
            try:
                _audit(db, current_user["user_id"], "task_deleted", "task", task_id)
            except Exception:
                pass
    except TaskError:
        pass
    return RedirectResponse("/tasks/", status_code=302)
