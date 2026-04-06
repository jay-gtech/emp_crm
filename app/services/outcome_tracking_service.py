import datetime
from sqlalchemy.orm import Session
from app.models.task import Task

def update_task_outcome(db: Session, task_id: int):
    """
    Logs the outcome of a task once it is completed.
    This creates the labeled dataset loop for supervised learning.
    """
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return

    # Handle enum safely
    is_completed = False
    if hasattr(task.status, 'value'):
        is_completed = (task.status.value == "completed")
    else:
        is_completed = (task.status == "completed")

    delay_days = 0
    was_delayed = False

    if is_completed:
        # Fallback to updated_at, created_at, or today
        completed_at = getattr(task, "updated_at", getattr(task, "created_at", datetime.date.today()))
        
        # Ensure it's a date object for comparison
        if type(completed_at) is datetime.datetime:
            completed_at = completed_at.date()

        if task.due_date:
            try:
                # Both should be date or datetime
                delay_delta = completed_at - task.due_date
                delay_days = delay_delta.days
                was_delayed = delay_days > 0
            except Exception:
                pass

    record = {
        "event_type": "outcome_update",
        "task_id": task_id,
        "outcome": {
            "completed": is_completed,
            "was_delayed": was_delayed,
            "delay_days": delay_days,
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    import json
    from app.ml.auto_assignment.logger import LOG_FILE, _LOG_DIR
    try:
        if not _LOG_DIR.exists():
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass
