"""
Analytics routes.

Provides:
  GET /analytics/         — HTML dashboard page
  GET /api/analytics/data — JSON payload for the charts (consumed by the page's JS)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required, role_required
from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Lazy import guard — if analytics_service is broken the rest of the app is
# completely unaffected.
# ---------------------------------------------------------------------------
try:
    from app.services.analytics_service import (
        get_attendance_trends,
        get_task_trends,
        get_leave_trends,
        get_employee_performance,
        get_team_comparison,
        get_summary_kpis,
    )
    _SVC_OK = True
except Exception as _exc:
    logger.error("analytics_service failed to import: %s", _exc)
    _SVC_OK = False

try:
    from app.services.analytics_service import (
        get_ai_system_metrics,
        get_workload_distribution,
        get_model_registry_metrics,
        get_reason_tag_distribution,
        get_recent_ai_assignments,
        get_data_quality_check,
    )
    _AI_SVC_OK = True
except Exception as _exc:
    logger.error("analytics_service AI functions failed to import: %s", _exc)
    _AI_SVC_OK = False


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    current_user: dict = Depends(login_required),
):
    """Render the analytics dashboard shell — data is loaded via fetch()."""
    return templates.TemplateResponse(
        "analytics/index.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


# ---------------------------------------------------------------------------
# JSON data endpoint — consumed by Chart.js on the page
# ---------------------------------------------------------------------------

@router.get("/data")
def analytics_data(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """
    Returns all analytics data in one JSON response.
    Every section is independently guarded so a single failing query
    does not hide all other data.
    """
    uid = current_user["user_id"]

    if not _SVC_OK:
        return JSONResponse({"error": "Analytics service unavailable"}, status_code=503)

    payload: dict = {}

    # ── KPI cards ────────────────────────────────────────────────────────────
    try:
        payload["kpis"] = get_summary_kpis(db)
    except Exception as exc:
        logger.error("analytics_data kpis: %s", exc)
        payload["kpis"] = {}

    # ── Attendance trend ─────────────────────────────────────────────────────
    try:
        payload["attendance_trends"] = get_attendance_trends(db)
    except Exception as exc:
        logger.error("analytics_data attendance_trends: %s", exc)
        payload["attendance_trends"] = {"labels": [], "present": [], "remote": []}

    # ── Task trend ───────────────────────────────────────────────────────────
    try:
        payload["task_trends"] = get_task_trends(db)
    except Exception as exc:
        logger.error("analytics_data task_trends: %s", exc)
        payload["task_trends"] = {"labels": [], "created": [], "completed": []}

    # ── Leave trend ──────────────────────────────────────────────────────────
    try:
        payload["leave_trends"] = get_leave_trends(db)
    except Exception as exc:
        logger.error("analytics_data leave_trends: %s", exc)
        payload["leave_trends"] = {"labels": [], "days": [], "by_type": {}}

    # ── Personal performance (non-admin only — admin has no personal stats) ───
    if current_user.get("role") != "admin":
        try:
            payload["my_performance"] = get_employee_performance(db, uid)
        except Exception as exc:
            logger.error("analytics_data my_performance: %s", exc)
            payload["my_performance"] = {}
    else:
        payload["my_performance"] = {}

    # ── Team comparison (admin / manager / team_lead only) ────────────────────
    role = current_user.get("role", "")
    if role in ("admin", "manager", "team_lead"):
        try:
            all_rows = get_team_comparison(db)
            if role in ("manager", "team_lead"):
                # Scope to this role's team only
                from app.services.hierarchy_service import is_user_in_scope
                payload["team_comparison"] = [
                    r for r in all_rows
                    if is_user_in_scope(db, current_user, r["user_id"])
                ]
            else:
                payload["team_comparison"] = all_rows
        except Exception as exc:
            logger.error("analytics_data team_comparison: %s", exc)
            payload["team_comparison"] = []
    else:
        payload["team_comparison"] = []

    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# AI Monitoring — HTML page
# ---------------------------------------------------------------------------

@router.get("/ai", response_class=HTMLResponse)
def ai_dashboard_page(
    request: Request,
    current_user: dict = Depends(role_required("admin")),
):
    """Render the AI monitoring dashboard shell — data loaded via fetch()."""
    return templates.TemplateResponse(
        "analytics/dashboard.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


# ---------------------------------------------------------------------------
# AI Monitoring — JSON data endpoint
# ---------------------------------------------------------------------------

@router.get("/ai/data")
def ai_analytics_data(
    db: Session = Depends(get_db),
    _current_user: dict = Depends(role_required("admin")),
):
    """
    Returns all AI/ML monitoring data in one JSON response.
    Every section is independently guarded.
    """
    if not _AI_SVC_OK:
        return JSONResponse({"error": "AI analytics service unavailable"}, status_code=503)

    payload: dict = {}

    try:
        payload["system_metrics"] = get_ai_system_metrics()
    except Exception as exc:
        logger.error("ai_analytics_data system_metrics: %s", exc)
        payload["system_metrics"] = {}

    try:
        payload["workload"] = get_workload_distribution(db)
    except Exception as exc:
        logger.error("ai_analytics_data workload: %s", exc)
        payload["workload"] = {"employees": [], "active": [], "completed": [], "overdue": []}

    try:
        payload["model_registry"] = get_model_registry_metrics()
    except Exception as exc:
        logger.error("ai_analytics_data model_registry: %s", exc)
        payload["model_registry"] = {"current_version": None, "total_versions": 0, "versions": []}

    try:
        payload["reason_tags"] = get_reason_tag_distribution()
    except Exception as exc:
        logger.error("ai_analytics_data reason_tags: %s", exc)
        payload["reason_tags"] = {"tags": [], "counts": []}

    try:
        payload["recent_assignments"] = get_recent_ai_assignments(limit=20)
    except Exception as exc:
        logger.error("ai_analytics_data recent_assignments: %s", exc)
        payload["recent_assignments"] = []

    try:
        payload["data_quality"] = get_data_quality_check()
    except Exception as exc:
        logger.error("ai_analytics_data data_quality: %s", exc)
        payload["data_quality"] = {}

    try:
        from app.ml.auto_assignment.scorer import get_inference_stats
        payload["inference_stats"] = get_inference_stats()
    except Exception as exc:
        logger.error("ai_analytics_data inference_stats: %s", exc)
        payload["inference_stats"] = {}

    return JSONResponse(payload)
