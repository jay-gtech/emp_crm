from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models.task import Task, TaskStatus, TaskPriority
from app.models.user import User
from fastapi import HTTPException

try:
    from app.services.hierarchy_service import apply_hierarchy_filter
except ImportError:
    apply_hierarchy_filter = None


class TaskError(Exception):
    pass


# ── Delay tracking helper ─────────────────────────────────────────────────────

def check_and_flag_delay(task: Task) -> None:
    """
    Evaluate task.deadline and update task.is_delayed in place.
    Call this before db.commit() on any lifecycle transition.
    Safe to call on tasks without a deadline (no-op).
    Does NOT commit — caller is responsible.
    """
    if not getattr(task, "deadline", None):
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        if task.status == TaskStatus.completed:
            # Delayed if it finished after the deadline
            end = task.end_time or now
            task.is_delayed = end > task.deadline
        else:
            # Delayed if still open and deadline has passed
            task.is_delayed = now > task.deadline
    except Exception:
        pass  # never crash the caller


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Return current UTC time (timezone-naive for SQLite compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def calculate_duration(start: datetime, end: datetime) -> int:
    """Return elapsed seconds between two datetimes."""
    return int((end - start).total_seconds())


def _get_task_or_403(db: Session, task_id: int) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── Assignment validation ─────────────────────────────────────────────────────

def validate_assignment(db: Session, assigner_id: int, assignee_id: int):
    assigner = db.query(User).filter(User.id == assigner_id).first()
    assignee = db.query(User).filter(User.id == assignee_id).first()

    if not assigner or not assignee:
        return

    if assigner.role.value == "manager" and assignee.role.value != "team_lead":
        raise HTTPException(403, "Manager can only assign to Team Lead")

    if assigner.role.value == "team_lead" and assignee.role.value != "employee":
        raise HTTPException(403, "Team Lead can only assign to Employee")


# ── CRUD ──────────────────────────────────────────────────────────────────────

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
    validate_assignment(db, assigned_by, assigned_to)

    try:
        p = TaskPriority(priority)
    except ValueError:
        p = TaskPriority.medium

    task = Task(
        title=title,
        description=description,
        assigned_to=assigned_to,
        assigned_by=assigned_by,
        priority=p,
        due_date=due_date,
        deadline=deadline,
        status=TaskStatus.assigned,   # new default: "assigned"
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: int) -> Task:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise TaskError("Task not found.")
    return task


def list_tasks_for_employee(db: Session, employee_id: int) -> list[Task]:
    return (
        db.query(Task)
        .filter(Task.assigned_to == employee_id)
        .order_by(Task.due_date.asc().nullslast(), Task.created_at.desc())
        .all()
    )


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


def update_task_status(db: Session, task_id: int, status: str, requester_id: int) -> Task:
    task = get_task(db, task_id)
    # Employee can only update their own tasks
    if task.assigned_to != requester_id and task.assigned_by != requester_id:
        raise TaskError("Not authorized to update this task.")
    try:
        task.status = TaskStatus(status)
    except ValueError:
        raise TaskError(f"Invalid status: {status}")
    db.commit()
    db.refresh(task)

    if task.status == TaskStatus.completed:
        try:
            from app.services.outcome_tracking_service import update_task_outcome
            update_task_outcome(db, task.id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to log task outcome: %s", e)

    return task


def update_task(
    db: Session,
    task_id: int,
    requester_id: int,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    due_date: date | None = None,
    assigned_to: int | None = None,
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
    if assigned_to is not None:
        task.assigned_to = assigned_to
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int, requester_id: int) -> None:
    task = get_task(db, task_id)
    if task.assigned_by != requester_id:
        raise TaskError("Only the assigner can delete this task.")
    db.delete(task)
    db.commit()


# ── Lifecycle: Start → Submit → Approve ──────────────────────────────────────

def start_task(db: Session, task_id: int, current_user: dict) -> Task:
    """
    assigned_to only.  Status must be 'assigned' (or legacy 'todo'/'pending').
    Sets status=in_progress, start_time=now().
    """
    uid = current_user["user_id"]
    task = _get_task_or_403(db, task_id)

    if task.assigned_to != uid:
        raise HTTPException(status_code=403, detail="Only the assignee can start this task")

    allowed_start_states = {TaskStatus.assigned, TaskStatus.todo, TaskStatus.pending}
    if task.status not in allowed_start_states:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start task with status '{task.status.value}'. Must be 'assigned'."
        )

    task.status = TaskStatus.in_progress
    task.start_time = _now()
    check_and_flag_delay(task)
    db.commit()
    db.refresh(task)
    return task


def submit_task(db: Session, task_id: int, current_user: dict) -> Task:
    """
    assigned_to only.  Status must be 'in_progress'.
    Sets status=pending_approval.
    Auto-starts if start_time is missing (safeguard) rather than hard-reject.
    """
    uid = current_user["user_id"]
    task = _get_task_or_403(db, task_id)

    if task.assigned_to != uid:
        raise HTTPException(status_code=403, detail="Only the assignee can submit this task")

    if task.status == TaskStatus.pending_approval:
        raise HTTPException(status_code=400, detail="Task is already pending approval")

    if task.status == TaskStatus.completed:
        raise HTTPException(status_code=400, detail="Task is already completed")

    if task.status != TaskStatus.in_progress:
        raise HTTPException(
            status_code=400,
            detail="Task must be in_progress before submitting. Start it first."
        )

    # Safeguard: set start_time if somehow missing
    if not task.start_time:
        task.start_time = _now()

    task.status = TaskStatus.pending_approval
    check_and_flag_delay(task)
    db.commit()
    db.refresh(task)
    return task


def approve_task(db: Session, task_id: int, current_user: dict) -> Task:
    """
    assigned_by only.  Status must be 'pending_approval'.
    Sets status=completed, end_time=now(), duration_seconds, approved_by, approved_at.
    """
    uid = current_user["user_id"]
    task = _get_task_or_403(db, task_id)

    if task.assigned_by != uid:
        raise HTTPException(status_code=403, detail="Only the task assigner can approve this task")

    # Extra guard: prevent self-approval even if assigned_to == assigned_by
    if task.assigned_to == uid:
        raise HTTPException(status_code=403, detail="You cannot approve your own task")

    if task.status == TaskStatus.completed:
        raise HTTPException(status_code=400, detail="Task is already completed")

    if task.status != TaskStatus.pending_approval:
        raise HTTPException(
            status_code=400,
            detail=f"Task must be pending_approval to approve. Current: '{task.status.value}'"
        )

    now = _now()
    task.status = TaskStatus.completed
    task.end_time = now
    task.approved_by = uid
    task.approved_at = now

    if task.start_time:
        task.duration_seconds = calculate_duration(task.start_time, now)

    check_and_flag_delay(task)
    db.commit()
    db.refresh(task)

    # Trigger ML outcome tracking
    try:
        from app.services.outcome_tracking_service import update_task_outcome
        update_task_outcome(db, task.id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to log task outcome after approve: %s", e)

    return task
