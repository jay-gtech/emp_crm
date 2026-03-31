from datetime import date
from fastapi import APIRouter, Request, Form, Depends
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


@router.post("/{task_id}/status")
def update_status(
    task_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        update_task_status(db, task_id, status, current_user["user_id"])
    except TaskError:
        pass
    return RedirectResponse("/tasks/", status_code=302)


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
