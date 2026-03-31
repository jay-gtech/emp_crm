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

from app.core.auth import login_required
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

    # ── Personal performance (for the logged-in user) ─────────────────────────
    try:
        payload["my_performance"] = get_employee_performance(db, uid)
    except Exception as exc:
        logger.error("analytics_data my_performance: %s", exc)
        payload["my_performance"] = {}

    # ── Team comparison (admin / manager / team_lead only) ────────────────────
    role = current_user.get("role", "")
    if role in ("admin", "manager", "team_lead"):
        try:
            payload["team_comparison"] = get_team_comparison(db)
        except Exception as exc:
            logger.error("analytics_data team_comparison: %s", exc)
            payload["team_comparison"] = []
    else:
        payload["team_comparison"] = []

    return JSONResponse(payload)
