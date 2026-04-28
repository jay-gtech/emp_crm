"""
Task Comments router — registered BEFORE tasks.router in main.py.

POST /tasks/{task_id}/comment   — add a comment (participant only)
GET  /tasks/{task_id}/comments  — fetch all comments (participant or manager/admin)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import login_required
from app.models.task import Task, TaskAssignment
from app.models.task_comment import TaskComment
from app.models.user import User

router = APIRouter(tags=["task_comments"])


def _can_access(db: Session, task: Task, uid: int, role: str) -> bool:
    """Return True if user is a participant (assignee/assigner) or manager/admin."""
    if role in ("admin", "manager", "team_lead"):
        return True
    if uid == task.assigned_by:
        return True
    # Allow employees who have a TaskAssignment on this task
    return db.query(TaskAssignment).filter(
        TaskAssignment.task_id == task.id,
        TaskAssignment.user_id == uid,
    ).first() is not None


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/comment
# ---------------------------------------------------------------------------
@router.post("/tasks/{task_id}/comment")
def add_comment(
    task_id: int,
    request: Request,
    comment: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    uid  = current_user["user_id"]
    role = current_user["role"]

    if not _can_access(db, task, uid, role):
        raise HTTPException(status_code=403, detail="Not authorised to comment on this task")

    comment_text = comment.strip()
    if not comment_text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    if len(comment_text) > 1000:
        raise HTTPException(status_code=400, detail="Comment too long (max 1000 chars)")

    tc = TaskComment(task_id=task_id, user_id=uid, comment=comment_text)
    db.add(tc)
    db.commit()

    # Notify all task participants — fire-and-forget, no self-notification
    try:
        from app.services.notification_service import create_task_notification as _cmt_notify
        commenter = db.query(User).filter(User.id == uid).first()
        commenter_name = commenter.name if commenter else "Someone"
        msg = f'💬 {commenter_name} commented on task "{task.title}": {comment_text[:80]}'

        notify_ids: set[int] = set()
        if task.assigned_by and task.assigned_by != uid:
            notify_ids.add(task.assigned_by)
        for a in db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).all():
            if a.user_id != uid:
                notify_ids.add(a.user_id)

        for nid in notify_ids:
            _cmt_notify(db, nid, msg)
    except Exception:
        pass

    # Redirect back to tasks page
    return RedirectResponse("/tasks/", status_code=302)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/comments
# ---------------------------------------------------------------------------
@router.get("/tasks/{task_id}/comments")
def get_comments(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    uid  = current_user["user_id"]
    role = current_user["role"]

    if not _can_access(db, task, uid, role):
        raise HTTPException(status_code=403, detail="Not authorised to view comments on this task")

    comments = (
        db.query(TaskComment)
        .filter(TaskComment.task_id == task_id)
        .order_by(TaskComment.created_at.asc())
        .all()
    )

    # Resolve user names
    user_ids = {c.user_id for c in comments}
    users = {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return JSONResponse([
        {
            "id":         c.id,
            "user_id":    c.user_id,
            "user_name":  users.get(c.user_id, f"User#{c.user_id}"),
            "comment":    c.comment,
            "created_at": c.created_at.strftime("%d %b %Y, %H:%M") if c.created_at else "",
        }
        for c in comments
    ])
