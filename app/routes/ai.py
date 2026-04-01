"""
AI Routes
=========
GET /ai/task-suggestions
    Returns the current user's tasks annotated with AI priority, confidence,
    human reason, delay risk, and urgency rank.  Requires an active session.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import login_required
from app.core.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/task-suggestions")
def task_suggestions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """
    Response shape (one object per active task, ranked most-urgent first):
        [
          {
            "task_id":          1,
            "title":            "Prepare report",
            "status":           "pending",
            "due_date":         "2025-04-05",
            "ai_priority":      "🔥 High",
            "ai_priority_raw":  "high",
            "confidence":       0.91,
            "confidence_pct":   91,
            "reason":           "Due in 1 day",
            "at_risk_of_delay": true,
            "delay_label":      "⚠️ At Risk",
            "rank":             1,
            "workload":         5
          },
          ...
        ]
    """
    try:
        from app.services.ai_task_service import get_ai_task_suggestions
        suggestions = get_ai_task_suggestions(db, current_user)
        # Serialise date objects — JSONResponse cannot handle date natively
        for s in suggestions:
            if s.get("due_date") is not None:
                s["due_date"] = s["due_date"].isoformat()
        return JSONResponse(content=suggestions)
    except Exception as exc:
        return JSONResponse(
            content={"error": str(exc), "suggestions": []},
            status_code=500,
        )
