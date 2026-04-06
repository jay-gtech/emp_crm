"""
app/routes/auto_assign.py
==========================
REST API for the Smart Task Auto-Assignment system.

Endpoints
---------
POST /ai/auto-assign/{task_id}
    Assign an existing task to the best eligible employee via ML scoring.
    Requires: Admin, Manager, or Team Lead session.

GET  /ai/auto-assign/log
    Return the last 50 assignment log entries (Admin only).
    Useful for inspecting the ML training dataset.
"""

from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_session_user
from app.models.task import Task
from app.services.auto_assignment_service import auto_assign_task
from app.services.hierarchy_service import is_user_in_scope
from app.ml.auto_assignment.logger import read_log

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI Auto-Assignment"])


# ───────────────────────────────────────────────────────────────────────────────
# GET /ai/health
# ───────────────────────────────────────────────────────────────────────────────

@router.get("/health", summary="AI system health check", tags=["AI Health"])
def ai_health_check():
    """
    Lightweight AI health probe suitable for uptime monitors and load balancers.
    Does NOT require authentication.

    Returns a JSON object::

        {
            "model_loaded":          true,
            "model_version":         "v2",
            "last_retrained":        "2026-04-02T11:00:00+00:00",
            "ml_usage_rate":         0.95,
            "fallback_rate":         0.05,
            "avg_inference_time_ms": 8.5
        }
    """
    # ── Model status ───────────────────────────────────────────────────────────────
    try:
        from app.ml.training.model import is_model_available
        model_loaded = is_model_available()
    except Exception:
        model_loaded = False

    # ── Model version (from registry) ──────────────────────────────────────────────
    model_version  = None
    last_retrained = None
    try:
        from app.ml.retraining.model_registry import ModelRegistry
        registry      = ModelRegistry()
        model_version = registry.current_version()
        history       = registry.get_retrain_history(limit=1)
        if history:
            last_retrained = history[-1].get("timestamp")
    except Exception as exc:
        log.debug("[health] Registry read failed: %s", exc)

    # ── Runtime stats from in-memory counters (zero-cost) ───────────────────────
    try:
        from app.ml.auto_assignment.scorer import get_fallback_stats, get_inference_stats
        fb_stats  = get_fallback_stats()
        inf_stats = get_inference_stats()
        fallback_rate         = fb_stats["fallback_rate"]
        ml_usage_rate         = fb_stats["ml_usage_rate"]
        avg_inference_time_ms = inf_stats["avg_ms"]
    except Exception:
        fallback_rate         = 0.0
        ml_usage_rate         = 0.0
        avg_inference_time_ms = 0.0

    return {
        "model_loaded":          model_loaded,
        "model_version":         model_version,
        "last_retrained":        last_retrained,
        "ml_usage_rate":         ml_usage_rate,
        "fallback_rate":         fallback_rate,
        "avg_inference_time_ms": avg_inference_time_ms,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /ai/auto-assign/{task_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/auto-assign/{task_id}", summary="Smart auto-assign a task")
def api_auto_assign(
    task_id: int,
    request: Request,
    db:      Session = Depends(get_db),
):
    """
    Assign an existing task to the best eligible employee using ML scoring.

    **Scoring factors:**
    - Active task count (penalised)
    - Overdue task count (heavily penalised)
    - Completed task history (rewarded)
    - performance_score (rewarded)

    **Response includes:**
    - `assigned_to` — name of selected employee
    - `score`       — numeric fitness score
    - `reason`      — human-readable explanation
    - `top_candidates` — top-3 ranked alternatives
    """
    # ── Auth guard ────────────────────────────────────────────────────────────
    current_user = get_session_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    allowed_roles = {"admin", "manager", "team_lead"}
    if current_user.get("role") not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Only Admins, Managers, and Team Leads can auto-assign tasks.",
        )

    # ── Fetch task ────────────────────────────────────────────────────────────
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")

    # ── Scope check ───────────────────────────────────────────────────────────
    # Prevent re-assignment of tasks that belong to a different manager's team
    if task.assigned_to and not is_user_in_scope(db, current_user, task.assigned_to):
        raise HTTPException(
            status_code=403,
            detail="This task is outside your management scope.",
        )

    # ── Run auto-assignment ───────────────────────────────────────────────────
    log.info(
        "[auto_assign_route] user=%s role=%s → assigning task_id=%d",
        current_user.get("name"), current_user.get("role"), task_id,
    )
    result = auto_assign_task(db, task, current_user)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /ai/auto-assign/log   (Admin inspection endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/auto-assign/log", summary="View recent assignment log entries (Admin only)")
def api_assignment_log(
    request: Request,
    limit:   int = 50,
):
    """
    Return the most recent entries from the ML assignment log.
    This log is the raw training dataset for future model training.

    Admin only. Use `?limit=N` to control how many entries are returned (max 200).
    """
    current_user = get_session_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    limit  = max(1, min(limit, 200))
    entries = read_log(limit=limit)
    return {
        "count":   len(entries),
        "entries": entries,
    }
