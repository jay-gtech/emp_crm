from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from app.core.validators import validate_text as _validate_text
from app.core.constants  import MAX_TITLE_LENGTH


def _validate_title(title: str, field: str = "Title") -> None:
    _validate_text(title, field=field, max_len=MAX_TITLE_LENGTH)

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.task_service import (
    create_task, create_tasks_bulk,
    list_tasks_for_employee, list_visible_tasks, list_all_assignment_rows,
    delete_task, TaskError,
    start_task, submit_task, approve_task, reject_assignment,
)
from app.models.task import Task, TaskAssignment, AssignmentStatus
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_emp_map(db: Session, task_rows: list) -> tuple[dict, dict]:
    """Return (name_map, role_map) keyed by user_id, covering all assignees in task_rows."""
    user_ids = {r.assigned_to for r in task_rows if r.assigned_to}
    if not user_ids:
        return {}, {}
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    return (
        {u.id: u.name for u in users},
        {u.id: u.role.value for u in users},
    )


def _build_batch_summary(task_rows: list, manager_uid: int, role: str, emp_map: dict) -> list[dict]:
    """
    Group task rows by task_id (for tasks with >1 assignee) into a progress panel.
    Replaces the old batch_id-based grouping.
    """
    from collections import defaultdict
    groups: dict[int, list] = defaultdict(list)
    for row in task_rows:
        groups[row.id].append(row)

    summary = []
    for task_id, rows in groups.items():
        # Only show progress panel for tasks with multiple assignees
        if len(rows) < 2:
            continue
        # For non-admin, only show tasks this user assigned
        if role != "admin" and not any(r.assigned_by == manager_uid for r in rows):
            continue

        total    = len(rows)
        sv_list  = [r.status.value for r in rows]
        completed     = sum(1 for s in sv_list if s in ("completed", "approved"))
        in_progress   = sum(1 for s in sv_list if s == "in_progress")
        pending_appr  = sum(1 for s in sv_list if s == "pending_approval")

        summary.append({
            "task_id":          task_id,
            "title":            rows[0].title,
            "total":            total,
            "completed":        completed,
            "in_progress":      in_progress,
            "pending_approval": pending_appr,
            "assigned":         total - completed - in_progress - pending_appr,
            "pct":              round(completed / total * 100) if total else 0,
            "assignees": [
                {
                    "name":          emp_map.get(r.assigned_to, f"#{r.assigned_to}"),
                    "status":        r.status.value,
                    "assignment_id": r.assignment_id,
                    "task_id":       r.id,
                }
                for r in rows
            ],
        })
    return summary


# ── Task list ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def task_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
    # ── Filter query parameters ───────────────────────────────────────────────
    title:      Optional[str] = None,
    employee:   Optional[str] = None,
    start_date: Optional[str] = None,   # kept as str; parsed safely below
    end_date:   Optional[str] = None,   # kept as str; parsed safely below
):
    uid  = current_user["user_id"]
    role = current_user["role"]

    # ── Build task rows (SimpleNamespace objects, one per assignment) ─────────
    if role == "admin":
        task_rows = list_all_assignment_rows(db)
    elif role in ("manager", "team_lead"):
        from app.services.hierarchy_service import get_subordinate_ids
        subordinate_ids = get_subordinate_ids(db, uid)
        task_rows = list_visible_tasks(db, uid, subordinate_ids)
    else:
        task_rows = list_tasks_for_employee(db, uid)

    # ── User name / role maps (built from actual assignees in task_rows) ──────
    emp_map, role_map = _build_emp_map(db, task_rows)

    # ── Approved-by map for display ───────────────────────────────────────────
    approved_by_ids = {r.approved_by for r in task_rows if r.approved_by}
    approver_map: dict[int, str] = {}
    if approved_by_ids:
        approvers = db.query(User).filter(User.id.in_(approved_by_ids)).all()
        approver_map = {u.id: u.name for u in approvers}

    # ── Normalise filter inputs (empty strings → None) ───────────────────────
    title    = title.strip()    or None if title    else None
    employee = employee.strip() or None if employee else None

    def _parse_date(value: Optional[str]) -> Optional[date]:
        """Return a date object, or None if value is blank/invalid."""
        if not value or not value.strip():
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    parsed_start = _parse_date(start_date)
    parsed_end   = _parse_date(end_date)

    # ── Server-side filtering ────────────────────────────────────────────────
    # emp_map is keyed by user_id; r.assigned_to == assignment.user_id (SimpleNamespace).
    filters_active = any([title, employee, parsed_start, parsed_end])

    if title:
        _t = title.lower()
        task_rows = [r for r in task_rows if _t in (r.title or "").lower()]

    if employee:
        _e = employee.lower()
        task_rows = [
            r for r in task_rows
            if _e in emp_map.get(r.assigned_to, "").lower()
        ]

    if parsed_start:
        task_rows = [
            r for r in task_rows
            if r.created_at and r.created_at.date() >= parsed_start
        ]

    if parsed_end:
        task_rows = [
            r for r in task_rows
            if r.created_at and r.created_at.date() <= parsed_end
        ]

    # ── Assignable employees for the "create task" form ───────────────────────
    if role == "admin":
        from app.models.user import UserRole as _UserRole
        employees = (
            db.query(User)
            .filter(
                User.is_active == 1,
                User.role.in_([_UserRole.manager, _UserRole.team_lead]),
            )
            .order_by(User.name)
            .all()
        )
    elif role == "manager":
        from app.services.hierarchy_service import get_subordinate_ids as _sub_ids
        from app.models.user import UserRole as _UserRole
        sub_ids = _sub_ids(db, uid)
        employees = (
            db.query(User)
            .filter(
                User.id.in_(sub_ids),
                User.role == _UserRole.team_lead,
                User.is_active == 1,
            )
            .order_by(User.name)
            .all()
        ) if sub_ids else []
    elif role == "team_lead":
        from app.services.hierarchy_service import get_subordinate_ids as _sub_ids
        from app.models.user import UserRole as _UserRole
        sub_ids = _sub_ids(db, uid)
        employees = (
            db.query(User)
            .filter(
                User.id.in_(sub_ids),
                User.role == _UserRole.employee,
                User.is_active == 1,
            )
            .order_by(User.name)
            .all()
        ) if sub_ids else []
    else:
        employees = []

    # ── Analytics ─────────────────────────────────────────────────────────────
    task_stats: dict = {}
    if _ANALYTICS_OK:
        try:
            if role == "employee":
                task_stats = get_user_task_stats(db, uid)
            elif role in ("manager", "team_lead"):
                task_stats = get_user_task_stats(db, uid)
                task_stats.update(get_manager_team_stats(db, uid))
            elif role == "admin":
                task_stats = get_system_task_stats(db)
        except Exception:
            task_stats = {}

    # ── Batch/multi-assignment progress panel (managers + admins) ─────────────
    batch_summary: list[dict] = []
    if role in ("admin", "manager", "team_lead"):
        try:
            batch_summary = _build_batch_summary(task_rows, uid, role, emp_map)
        except Exception:
            batch_summary = []

    return templates.TemplateResponse(
        "tasks/list.html",
        {
            "request":         request,
            "current_user":    current_user,
            "tasks":           task_rows,
            "employees":       employees,
            "emp_map":         emp_map,
            "role_map":        role_map,
            "approver_map":    approver_map,
            "task_stats":      task_stats,
            "batch_summary":   batch_summary,
            # ── Filter state (so the form retains values) ─────────────────
            "filter_title":      title        or "",
            "filter_employee":   employee     or "",
            "filter_start_date": str(parsed_start) if parsed_start else "",
            "filter_end_date":   str(parsed_end)   if parsed_end   else "",
            "filters_active":    filters_active,
        },
    )


# ── Create task ───────────────────────────────────────────────────────────────

@router.post("/create")
def create_task_post(
    title: str = Form(...),
    description: str = Form(""),
    assigned_to: List[int] = Form(...),
    priority: str = Form("medium"),
    due_date: str = Form(""),
    deadline: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    _validate_title(title, "Task title")
    title = title.strip()

    today = date.today()
    now   = datetime.now(timezone.utc).replace(tzinfo=None)

    dd = None
    if due_date:
        try:
            dd = date.fromisoformat(due_date)
            if dd < today:
                return RedirectResponse("/tasks/?error=due_date_past", status_code=302)
        except ValueError:
            pass

    dl: datetime | None = None
    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
            if dl < now:
                return RedirectResponse("/tasks/?error=deadline_past", status_code=302)
        except ValueError:
            pass

    if not assigned_to:
        return RedirectResponse("/tasks/?error=no_assignee", status_code=302)

    assigner_id = current_user["user_id"]

    if len(assigned_to) == 1:
        # ── Single assignee ───────────────────────────────────────────────────
        try:
            task = create_task(
                db,
                title=title,
                assigned_to=assigned_to[0],
                assigned_by=assigner_id,
                description=description or None,
                priority=priority,
                due_date=dd,
                deadline=dl,
            )
            if _NOTIFY_OK:
                try:
                    _notify(db, assigned_to[0],
                            f'📋 New task assigned to you: "{task.title}"',
                            actor_id=assigner_id)
                except Exception:
                    pass
            if _AUDIT_OK:
                try:
                    _audit(db, assigner_id, "task_created", "task", task.id,
                           f'"{task.title}" assigned to user {assigned_to[0]}')
                except Exception:
                    pass
        except TaskError:
            pass

    else:
        # ── Multiple assignees — ONE task + N assignments ─────────────────────
        try:
            task = create_tasks_bulk(
                db,
                title=title,
                assigned_to_ids=assigned_to,
                assigned_by=assigner_id,
                description=description or None,
                priority=priority,
                due_date=dd,
                deadline=dl,
            )
            # Notify each assignee via the task's assignment records
            if _NOTIFY_OK:
                for assignment in db.query(TaskAssignment).filter(
                    TaskAssignment.task_id == task.id
                ).all():
                    try:
                        _notify(db, assignment.user_id,
                                f'📋 New task assigned to you: "{task.title}"',
                                actor_id=assigner_id)
                    except Exception:
                        pass
            if _AUDIT_OK:
                try:
                    ids_str = ", ".join(str(i) for i in assigned_to)
                    _audit(db, assigner_id, "task_created", "task", task.id,
                           f'"{title}" multi-assigned to users [{ids_str}]')
                except Exception:
                    pass
        except TaskError:
            pass

    return RedirectResponse("/tasks/", status_code=302)


# ── Generic status update (admin override / backward compat) ──────────────────

@router.post("/{task_id}/update-status")
@router.post("/{task_id}/status")
def update_status(
    task_id: int,
    status: str = Form(...),
    assignment_id: int = Form(0),
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

    role = current_user.get("role")
    uid  = current_user.get("user_id")

    # Try to find the specific assignment
    # Resolve assignment — prefer explicit assignment_id, then current user's
    assignment = None
    if assignment_id:
        assignment = db.query(TaskAssignment).filter(
            TaskAssignment.id == assignment_id,
            TaskAssignment.task_id == task_id,
        ).first()
    if not assignment:
        assignment = db.query(TaskAssignment).filter(
            TaskAssignment.task_id == task_id,
            TaskAssignment.user_id == uid,
        ).first()

    if not assignment:
        raise HTTPException(404, "No assignment found for this task")

    assignee_id = assignment.user_id

    # Assignee: update their own assignment status
    if uid == assignee_id:
        try:
            assignment.status = AssignmentStatus(status)
        except ValueError:
            raise HTTPException(400, "Invalid status for assignee")

    # Manager / Team Lead: approve or reject an assignment in their scope
    elif role in ("team_lead", "manager"):
        from app.services.hierarchy_service import is_user_in_scope
        if not is_user_in_scope(db, current_user, assignee_id):
            raise HTTPException(403, "This task is outside your scope")
        if status == "approved":
            assignment.status = AssignmentStatus.completed
        elif status == "rejected":
            assignment.status = AssignmentStatus.in_progress
        else:
            raise HTTPException(403, "Managers may only approve or reject assignments")

    # Admin: set any valid status directly
    elif role == "admin":
        try:
            assignment.status = AssignmentStatus(status)
        except ValueError:
            raise HTTPException(400, "Invalid status")

    else:
        raise HTTPException(403, "Not allowed")

    from app.services.task_service import _sync_task_aggregate_status
    _sync_task_aggregate_status(db, task_id)
    db.commit()

    if assignment.status == AssignmentStatus.completed:
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
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assignee starts their assignment — status: assigned → in_progress."""
    task, assignment = start_task(db, task_id, current_user)
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_by,
                    f'▶ Task started: "{task.title}" is now in progress.')
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
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assignee submits for approval — status: in_progress → pending_approval."""
    task, assignment = submit_task(db, task_id, current_user)
    if _NOTIFY_OK:
        try:
            _notify(db, task.assigned_by,
                    f'⏳ Task ready for your approval: "{task.title}"')
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
    assignment_id: int = Form(0),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assigner approves a specific assignment — status: pending_approval → completed."""
    task, assignment = approve_task(
        db, task_id, current_user,
        assignment_id=assignment_id or None,
    )
    notify_uid = assignment.user_id if assignment else None
    if _NOTIFY_OK and notify_uid:
        try:
            _notify(db, notify_uid, f'✅ Your task was approved: "{task.title}"')
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
    assignment_id: int = Form(0),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Assigner rejects — sends the assignment back to in_progress."""
    task, assignment = reject_assignment(
        db, task_id, current_user,
        assignment_id=assignment_id or None,
    )
    notify_uid = assignment.user_id if assignment else None
    if _NOTIFY_OK and notify_uid:
        try:
            _notify(db, notify_uid,
                    f'↩ Your task was sent back for revision: "{task.title}"')
        except Exception:
            pass
    if _AUDIT_OK:
        try:
            _audit(db, current_user["user_id"], "task_rejected", "task", task_id)
        except Exception:
            pass
    return RedirectResponse("/tasks/", status_code=302)


# ── Delete ────────────────────────────────────────────────────────────────────

@router.post("/{task_id}/delete")
def delete_task_post(
    task_id: int,
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
