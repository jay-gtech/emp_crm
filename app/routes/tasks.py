from datetime import date
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.task_service import (
    create_task, list_tasks_for_employee, list_tasks_assigned_by,
    list_all_tasks, update_task_status, delete_task, TaskError,
)
from app.services.employee_service import list_employees

# Audit trigger — imported defensively

try:
    from app.services.audit_service import log_action as _audit
    _AUDIT_OK = True
except Exception:
    _AUDIT_OK = False

router = APIRouter(prefix="/tasks", tags=["tasks"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def task_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    role = current_user["role"]

    if role in ("admin", "manager", "team_lead"):
        tasks = list_all_tasks(db, request_user=current_user)
    else:
        tasks = list_tasks_for_employee(db, uid)

    employees = list_employees(db, request_user=current_user) if role in ("admin", "manager", "team_lead") else []

    # Admin can only assign tasks to managers and team leads (not employees)
    if role == "admin":
        employees = [e for e in employees if e.role.value in ("manager", "team_lead")]

    # Manager can only assign tasks to their own team leads (not employees)
    if role == "manager":
        employees = [e for e in employees if e.role.value == "team_lead"]

    # Team Lead can only assign tasks to their own employees (not TLs or managers)
    if role == "team_lead":
        employees = [e for e in employees if e.role.value == "employee"]

    return templates.TemplateResponse(
        "tasks/list.html",
        {
            "request": request,
            "current_user": current_user,
            "tasks": tasks,
            "employees": employees,
        },
    )


@router.post("/create")
def create_task_post(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    assigned_to: int = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    dd = None
    if due_date:
        try:
            dd = date.fromisoformat(due_date)
        except ValueError:
            pass

    # Hierarchy-aware assignment validation for admin / manager / team_lead
    if current_user["role"] in ("admin", "manager", "team_lead"):
        from app.services.employee_service import get_employee
        from app.services.hierarchy_service import is_user_in_scope
        try:
            assignee = get_employee(db, assigned_to)
        except Exception:
            return RedirectResponse("/tasks/", status_code=302)
        _role = current_user["role"]
        if _role == "admin":
            # Admin → manager or team_lead only
            if assignee.role.value not in ("manager", "team_lead"):
                return RedirectResponse("/tasks/", status_code=302)
        elif _role == "manager":
            # Manager → team_lead only, within their hierarchy
            if assignee.role.value != "team_lead":
                return RedirectResponse("/tasks/", status_code=302)
            if not is_user_in_scope(db, current_user, assignee.id):
                return RedirectResponse("/tasks/", status_code=302)
        else:  # team_lead
            # Team Lead → employee only, within their direct team
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
        )

        if _AUDIT_OK:
            try:
                _audit(db, current_user["user_id"], "task_created", "task", task.id,
                       f'"{task.title}" assigned to user {assigned_to}')
            except Exception:
                pass
    except TaskError:
        pass
    return RedirectResponse("/tasks/", status_code=302)


from app.models.task import Task, TaskStatus
from app.models.user import User

@router.post("/{task_id}/update-status")
@router.post("/{task_id}/status")  # Backwards compatibility
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

    VALID_STATUSES = ["todo", "pending", "in_progress", "completed", "pending_approval", "approved", "rejected"]
    if status not in VALID_STATUSES:
        raise HTTPException(400, "Invalid status")

    assignee = db.query(User).get(task.assigned_to)
    role = current_user.get("role")
    uid = current_user.get("user_id")

    # Employee flow (or anyone acting on their own assigned task)
    if uid == task.assigned_to:
        if status == "completed":
            task.status = TaskStatus("pending_approval")
        elif status in ["todo", "in_progress"]:
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
                raise HTTPException(403, "Task must be in pending_approval state before it can be approved or rejected")
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
                raise HTTPException(403, "Task must be in pending_approval state before it can be approved or rejected")
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
    
    # ML Outcomes if finalized
    if task.status in [TaskStatus.completed, TaskStatus.approved]:
        try:
            from app.services.outcome_tracking_service import update_task_outcome
            update_task_outcome(db, task.id)
        except Exception:
            pass

    return RedirectResponse("/tasks/", status_code=302)

@router.post("/{task_id}/approve")
def approve_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    return update_status(task_id, request, "approved", db, current_user)

@router.post("/{task_id}/reject")
def reject_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    return update_status(task_id, request, "rejected", db, current_user)


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
