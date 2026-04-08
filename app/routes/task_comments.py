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
from app.models.task import Task
from app.models.task_comment import TaskComment
from app.models.user import User

router = APIRouter(tags=["task_comments"])


def _can_access(task: Task, uid: int, role: str) -> bool:
    """Return True if user is a participant (assignee/assigner) or manager/admin."""
    return (
        uid in (task.assigned_to, task.assigned_by)
        or role in ("admin", "manager", "team_lead")
    )


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

    if not _can_access(task, uid, role):
        raise HTTPException(status_code=403, detail="Not authorised to comment on this task")

    comment_text = comment.strip()
    if not comment_text:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    if len(comment_text) > 1000:
        raise HTTPException(status_code=400, detail="Comment too long (max 1000 chars)")

    tc = TaskComment(task_id=task_id, user_id=uid, comment=comment_text)
    db.add(tc)
    db.commit()

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

    if not _can_access(task, uid, role):
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
