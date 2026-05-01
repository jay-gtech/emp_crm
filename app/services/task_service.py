from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus, TaskPriority, TaskAssignment, AssignmentStatus
from app.models.user import User
from fastapi import HTTPException

log = logging.getLogger(__name__)

try:
    from app.services.hierarchy_service import apply_hierarchy_filter
except ImportError:
    apply_hierarchy_filter = None


class TaskError(Exception):
    pass


# ── Time helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def calculate_duration(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds())


# ── Delay flagging ────────────────────────────────────────────────────────────

def _flag_assignment_delay(assignment: TaskAssignment, deadline: datetime | None) -> None:
    if not deadline:
        assignment.is_delayed = False
        return
    now = _now()
    try:
        ref = assignment.end_time if assignment.status == AssignmentStatus.completed else now
        assignment.is_delayed = ref > deadline
    except Exception:
        pass


# ── Aggregate task status ──────────────────────────────────────────────────────

def _sync_task_aggregate_status(db: Session, task_id: int) -> None:
    """
    Recompute task.status from all its assignments after any status change.

    Rules (evaluated in priority order):
      all completed          → completed
      any pending_approval   → pending_approval
      any in_progress        → in_progress
      else                   → assigned

    Called inside the caller's open transaction; caller is responsible
    for commit().  Never raises — failures are logged and swallowed.
    """
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return

        assignments = (
            db.query(TaskAssignment.status)
            .filter(TaskAssignment.task_id == task_id)
            .all()
        )
        if not assignments:
            return

        statuses = [a.status for a in assignments]

        if all(s == AssignmentStatus.completed for s in statuses):
            new_status = TaskStatus.completed
        elif any(s == AssignmentStatus.pending_approval for s in statuses):
            new_status = TaskStatus.pending_approval
        elif any(s == AssignmentStatus.in_progress for s in statuses):
            new_status = TaskStatus.in_progress
        else:
            new_status = TaskStatus.assigned

        if task.status != new_status:
            task.status = new_status
    except Exception as exc:
        log.warning("_sync_task_aggregate_status(%s) failed: %s", task_id, exc)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_task_or_404(db: Session, task_id: int) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _get_task_or_403(db: Session, task_id: int) -> Task:
    return _get_task_or_404(db, task_id)


# ── Row builder ───────────────────────────────────────────────────────────────

def _assignment_to_row(assignment: TaskAssignment, task: Task | None = None) -> SimpleNamespace:
    """
    Build a template-compatible row from an assignment + its parent task.

    Pass `task` explicitly (from a JOIN query) to avoid lazy-loading the
    relationship — this eliminates the `tasks_1` alias that joinedload adds
    and makes the query immune to mapper cache issues.
    """
    if task is None:
        task = assignment.task  # fallback for callers that don't join
    return SimpleNamespace(
        id=task.id,
        assignment_id=assignment.id,
        title=task.title,
        description=task.description,
        priority=task.priority,
        due_date=task.due_date,
        deadline=task.deadline,
        created_at=task.created_at,
        updated_at=task.updated_at,
        batch_id=task.batch_id,
        assigned_by=task.assigned_by,
        # assigned_to maps to the user who owns this assignment row —
        # used by the template for "uid == task.assigned_to" checks.
        assigned_to=assignment.user_id,
        status=assignment.status,
        start_time=assignment.start_time,
        end_time=assignment.end_time,
        duration_seconds=assignment.duration_seconds,
        approved_by=assignment.approved_by,
        approved_at=assignment.approved_at,
        is_delayed=assignment.is_delayed,
    )


# ── Assignment validation ─────────────────────────────────────────────────────

def validate_assignment(db: Session, assigner_id: int, assignee_id: int) -> None:
    assigner = db.query(User).filter(User.id == assigner_id).first()
    assignee = db.query(User).filter(User.id == assignee_id).first()
    if not assigner or not assignee:
        return

    from app.services.hierarchy_service import can_assign, is_manager_of

    if assigner.role.value == "admin":
        if not can_assign(assigner.role.value, assignee.role.value):
            raise HTTPException(403, "Invalid role assignment")
        return

    if not can_assign(assigner.role.value, assignee.role.value):
        raise HTTPException(403, "Cannot assign to this role")

    if not is_manager_of(db, assigner.id, assignee.id):
        raise HTTPException(403, "User not in your hierarchy")


# ── Task creation ─────────────────────────────────────────────────────────────

def create_task(
    db: Session,
    title: str,
    assigned_to: int,
    assigned_by: int,
    description: str | None = None,
    priority: str = "medium",
    due_date: date | None = None,
    deadline: datetime | None = None,
) -> Task:
    """Create one Task + one TaskAssignment.  assigned_to is the assignee's user_id."""
    validate_assignment(db, assigned_by, assigned_to)

    try:
        p = TaskPriority(priority)
    except ValueError:
        p = TaskPriority.medium

    try:
        task = Task(
            title=title,
            description=description,
            assigned_by=assigned_by,
            priority=p,
            due_date=due_date,
            deadline=deadline,
            status=TaskStatus.assigned,
        )
        db.add(task)
        db.flush()

        db.add(TaskAssignment(
            task_id=task.id,
            user_id=assigned_to,
            status=AssignmentStatus.assigned,
        ))
        db.commit()
        db.refresh(task)
    except Exception as exc:
        log.error("create_task failed: %s", exc, exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create task: {exc}")

    return task


def create_tasks_bulk(
    db: Session,
    *,
    title: str,
    assigned_to_ids: list[int],
    assigned_by: int,
    description: str | None = None,
    priority: str = "medium",
    due_date: date | None = None,
    deadline: datetime | None = None,
) -> Task:
    """
    Create ONE Task with N TaskAssignments (one per unique assignee).

    Validates every assignee against RBAC + hierarchy.
    Rolls back the entire batch on any failure.
    """
    from app.core.constants import MAX_BATCH_ASSIGN

    seen: set[int] = set()
    unique_ids: list[int] = []
    for uid in assigned_to_ids:
        if uid not in seen:
            seen.add(uid)
            unique_ids.append(uid)

    if not unique_ids:
        raise HTTPException(400, "At least one assignee must be selected.")
    if len(unique_ids) > MAX_BATCH_ASSIGN:
        raise HTTPException(400, f"Cannot assign to more than {MAX_BATCH_ASSIGN} employees at once.")

    for assignee_id in unique_ids:
        validate_assignment(db, assigned_by, assignee_id)

    try:
        p = TaskPriority(priority)
    except ValueError:
        p = TaskPriority.medium

    try:
        task = Task(
            title=title,
            description=description,
            assigned_by=assigned_by,
            priority=p,
            due_date=due_date,
            deadline=deadline,
            status=TaskStatus.assigned,
        )
        db.add(task)
        db.flush()

        for assignee_id in unique_ids:
            db.add(TaskAssignment(
                task_id=task.id,
                user_id=assignee_id,
                status=AssignmentStatus.assigned,
            ))

        db.commit()
        db.refresh(task)
    except Exception as exc:
        log.error("create_tasks_bulk failed: %s", exc, exc_info=True)
        db.rollback()
        raise HTTPException(500, f"Failed to create task assignments: {exc}")

    return task


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def get_task(db: Session, task_id: int) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskError("Task not found.")
    return task


def get_batch_tasks(db: Session, batch_id: str) -> list[Task]:
    return (
        db.query(Task)
        .filter(Task.batch_id == batch_id)
        .order_by(Task.id)
        .all()
    )


def list_tasks_for_employee(db: Session, employee_id: int) -> list[SimpleNamespace]:
    """
    Return one row per assignment for a single employee.
    Uses an explicit JOIN — no joinedload, no tasks_1 alias.
    """
    rows = (
        db.query(TaskAssignment, Task)
        .join(Task, TaskAssignment.task_id == Task.id)
        .filter(TaskAssignment.user_id == employee_id)
        .order_by(Task.created_at.desc())   # 108: newest tasks first
        .all()
    )
    return [_assignment_to_row(a, t) for a, t in rows]


def _manager_rows(
    db: Session,
    manager_id: int,
    subordinate_ids: list[int] = (),
) -> list[SimpleNamespace]:
    """
    Assignment rows visible to a manager/team_lead:
      1. Assignments on tasks the manager created, restricted to their hierarchy
         (manager + direct/transitive subordinates). No extra DB query — filtered
         in-memory from the already-fetched result set.
      2. The manager's own assignments on tasks created by someone else (always
         included — these are tasks assigned TO the manager, not by them).
    Both queries use explicit JOINs — no joinedload, no tasks_1 alias.
    """
    created_rows = (
        db.query(TaskAssignment, Task)
        .join(Task, TaskAssignment.task_id == Task.id)
        .filter(Task.assigned_by == manager_id)
        .order_by(Task.created_at.desc())
        .all()
    )

    own_rows = (
        db.query(TaskAssignment, Task)
        .join(Task, TaskAssignment.task_id == Task.id)
        .filter(
            TaskAssignment.user_id == manager_id,
            Task.assigned_by != manager_id,
        )
        .order_by(Task.created_at.desc())
        .all()
    )

    # Only show assignments to people within this manager's hierarchy.
    # Includes the manager themselves (self-assigned tasks are valid).
    visible_set: set[int] = set(subordinate_ids) | {manager_id}

    seen: set[tuple[int, int]] = set()
    rows: list[SimpleNamespace] = []

    for a, t in created_rows:
        if a.user_id not in visible_set:
            continue  # assignee outside hierarchy — skip (prevents batch summary leak)
        k = (a.task_id, a.user_id)
        if k not in seen:
            seen.add(k)
            rows.append(_assignment_to_row(a, t))

    for a, t in own_rows:
        k = (a.task_id, a.user_id)
        if k not in seen:
            seen.add(k)
            rows.append(_assignment_to_row(a, t))

    return rows


def list_visible_tasks(
    db: Session,
    user_id: int,
    _subordinate_ids: list[int] = (),
) -> list[SimpleNamespace]:
    return _manager_rows(db, user_id, subordinate_ids=_subordinate_ids)


def list_all_assignment_rows(db: Session) -> list[SimpleNamespace]:
    """Admin: one row per TaskAssignment across all tasks. Explicit JOIN — no joinedload."""
    rows = (
        db.query(TaskAssignment, Task)
        .join(Task, TaskAssignment.task_id == Task.id)
        .order_by(Task.created_at.desc())
        .all()
    )
    return [_assignment_to_row(a, t) for a, t in rows]


def list_tasks_assigned_by(db: Session, manager_id: int) -> list[Task]:
    return (
        db.query(Task)
        .filter(Task.assigned_by == manager_id)
        .order_by(Task.created_at.desc())
        .all()
    )


def list_all_tasks(db: Session, request_user: dict | None = None) -> list[Task]:
    tasks = db.query(Task).order_by(Task.created_at.desc()).all()
    if request_user and apply_hierarchy_filter:
        tasks = apply_hierarchy_filter(db, request_user, tasks)
    return tasks


# ── Generic status update (admin override) ───────────────────────────────────

def update_task_status(db: Session, task_id: int, status: str, requester_id: int) -> Task:
    task = get_task(db, task_id)
    is_assignee = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.user_id == requester_id,
    ).first() is not None
    if not is_assignee and task.assigned_by != requester_id:
        raise TaskError("Not authorized to update this task.")

    try:
        task.status = TaskStatus(status)
    except ValueError:
        raise TaskError(f"Invalid status: {status}")

    assignment = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.user_id == requester_id,
    ).first()
    if assignment:
        try:
            assignment.status = AssignmentStatus(status)
        except ValueError:
            pass

    _sync_task_aggregate_status(db, task_id)
    db.commit()
    db.refresh(task)

    if task.status == TaskStatus.completed:
        try:
            from app.services.outcome_tracking_service import update_task_outcome
            update_task_outcome(db, task.id)
        except Exception as e:
            log.warning("update_task_status outcome log failed: %s", e)

    return task


def update_task(
    db: Session,
    task_id: int,
    requester_id: int,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    due_date: date | None = None,
) -> Task:
    task = get_task(db, task_id)
    if task.assigned_by != requester_id:
        raise TaskError("Only the assigner can edit task details.")
    if title:
        task.title = title
    if description is not None:
        task.description = description
    if priority:
        try:
            task.priority = TaskPriority(priority)
        except ValueError:
            raise TaskError(f"Invalid priority: {priority}")
    if due_date is not None:
        task.due_date = due_date
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int, requester_id: int) -> None:
    task = get_task(db, task_id)
    if task.assigned_by != requester_id:
        raise TaskError("Only the assigner can delete this task.")
    db.delete(task)
    db.commit()


# ── Lifecycle: Start → Submit → Approve / Reject ──────────────────────────────

def start_task(db: Session, task_id: int, current_user: dict) -> tuple[Task, TaskAssignment]:
    """Assignee starts their assignment: assigned → in_progress."""
    uid  = current_user["user_id"]
    task = _get_task_or_404(db, task_id)

    assignment = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.user_id == uid,
    ).first()

    if not assignment:
        raise HTTPException(status_code=403, detail="No assignment found for this task")

    if assignment.status not in (AssignmentStatus.assigned,):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start: current status is '{assignment.status.value}'. Must be 'assigned'.",
        )

    assignment.status     = AssignmentStatus.in_progress
    assignment.start_time = _now()
    if not assignment.started_at:
        assignment.started_at = assignment.start_time
    _flag_assignment_delay(assignment, task.deadline)
    _sync_task_aggregate_status(db, task_id)
    db.commit()
    db.refresh(assignment)
    return task, assignment


def submit_task(db: Session, task_id: int, current_user: dict) -> tuple[Task, TaskAssignment]:
    """Assignee submits for approval: in_progress → pending_approval."""
    uid  = current_user["user_id"]
    task = _get_task_or_404(db, task_id)

    assignment = db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task_id,
        TaskAssignment.user_id == uid,
    ).first()

    if not assignment:
        raise HTTPException(status_code=403, detail="No assignment found for this task")
    if assignment.status == AssignmentStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Task is already pending approval")
    if assignment.status == AssignmentStatus.completed:
        raise HTTPException(status_code=400, detail="Task is already completed")
    if assignment.status != AssignmentStatus.in_progress:
        raise HTTPException(
            status_code=400,
            detail="Task must be in_progress before submitting. Start it first.",
        )

    if not assignment.start_time:
        assignment.start_time = _now()
    assignment.status = AssignmentStatus.pending_approval
    _flag_assignment_delay(assignment, task.deadline)
    _sync_task_aggregate_status(db, task_id)
    db.commit()
    db.refresh(assignment)
    return task, assignment


def approve_task(
    db: Session,
    task_id: int,
    current_user: dict,
    assignment_id: int | None = None,
) -> tuple[Task, TaskAssignment | None]:
    """Assigner approves a specific assignment: pending_approval → completed."""
    uid  = current_user["user_id"]
    task = _get_task_or_404(db, task_id)

    if task.assigned_by != uid:
        raise HTTPException(status_code=403, detail="Only the task assigner can approve")

    if not assignment_id:
        raise HTTPException(status_code=400, detail="assignment_id is required")

    assignment = db.query(TaskAssignment).filter(
        TaskAssignment.id == assignment_id,
        TaskAssignment.task_id == task_id,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment.user_id == uid:
        raise HTTPException(status_code=403, detail="You cannot approve your own assignment")
    if assignment.status == AssignmentStatus.completed:
        raise HTTPException(status_code=400, detail="Assignment is already completed")
    if assignment.status != AssignmentStatus.pending_approval:
        raise HTTPException(
            status_code=400,
            detail=f"Assignment must be pending_approval to approve. Current: '{assignment.status.value}'",
        )

    now = _now()
    assignment.status       = AssignmentStatus.completed
    assignment.end_time     = now
    assignment.approved_by  = uid
    assignment.approved_at  = now
    assignment.completed_at = now
    if assignment.start_time:
        assignment.duration_seconds = calculate_duration(assignment.start_time, now)
    _flag_assignment_delay(assignment, task.deadline)

    # Update task aggregate status (sets task.status = completed if all done)
    _sync_task_aggregate_status(db, task_id)

    # Propagate end_time to task when fully completed
    if task.status == TaskStatus.completed:
        task.end_time    = now
        task.approved_by = uid
        task.approved_at = now

    db.commit()
    db.refresh(assignment)

    try:
        from app.services.outcome_tracking_service import update_task_outcome
        update_task_outcome(db, task.id)
    except Exception as e:
        log.warning("approve_task outcome log failed: %s", e)

    return task, assignment


def reject_assignment(
    db: Session,
    task_id: int,
    current_user: dict,
    assignment_id: int | None = None,
) -> tuple[Task, TaskAssignment | None]:
    """Assigner rejects — sends the assignment back to in_progress."""
    uid  = current_user["user_id"]
    task = _get_task_or_404(db, task_id)

    if task.assigned_by != uid and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the task assigner can reject")

    if not assignment_id:
        raise HTTPException(status_code=400, detail="assignment_id is required")

    assignment = db.query(TaskAssignment).filter(
        TaskAssignment.id == assignment_id,
        TaskAssignment.task_id == task_id,
    ).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment.status != AssignmentStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Assignment must be pending_approval to reject")

    assignment.status = AssignmentStatus.in_progress
    _sync_task_aggregate_status(db, task_id)
    db.commit()
    db.refresh(assignment)
    return task, assignment
