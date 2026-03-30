from datetime import date
from sqlalchemy.orm import Session
from app.models.task import Task, TaskStatus, TaskPriority


class TaskError(Exception):
    pass


def create_task(
    db: Session,
    title: str,
    assigned_to: int,
    assigned_by: int,
    description: str | None = None,
    priority: str = "medium",
    due_date: date | None = None,
) -> Task:
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
        status=TaskStatus.pending,
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


def list_all_tasks(db: Session) -> list[Task]:
    return db.query(Task).order_by(Task.created_at.desc()).all()


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
