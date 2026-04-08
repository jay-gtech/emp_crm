"""
Reports router — Hourly reports + EOD reports.

RBAC matrix:
  Action          Employee  Team Lead  Manager/Admin
  Submit hourly      ✅         ✅           ❌
  View own           ✅         ✅           ✅
  View team          ❌         ✅           ✅
  Submit EOD         ❌         ✅           ❌
  View all           ❌         ❌           ✅
"""
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required, role_required
from app.core.database import get_db
from app.services.report_service import (
    ReportError,
    enrich_eod_with_names,
    enrich_reports_with_names,
    get_all_eod_reports,
    get_all_reports,
    get_eod_reports,
    get_my_reports,
    get_report_stats,
    get_team_reports,
    submit_eod_report,
    submit_hourly_report,
)

logger = logging.getLogger(__name__)

router    = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory="app/templates")

# Role groups
_REPORTER_ROLES = ("employee", "team_lead")
_TEAM_ROLES     = ("team_lead", "manager", "admin")
_MANAGER_ROLES  = ("manager", "admin")

# Allowed filter values — anything else treated as "all"
_VALID_FILTERS = {"today", "week", "all"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_reporter(current_user: dict) -> None:
    if current_user.get("role") not in _REPORTER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only employees and team leads can submit hourly reports.",
        )


def _require_team_lead(current_user: dict) -> None:
    if current_user.get("role") != "team_lead":
        raise HTTPException(
            status_code=403,
            detail="Only team leads can submit EOD reports.",
        )


def _clean_filter(f: str | None) -> str:
    """Normalise a filter param; default to 'all'."""
    return f if f in _VALID_FILTERS else "all"


# ---------------------------------------------------------------------------
# GET /reports/  — role-aware dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def reports_home(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
    filter: str | None = Query(default=None),   # noqa: A002
    tab: str | None    = Query(default=None),
):
    role          = current_user.get("role")
    uid           = current_user["user_id"]
    flash         = request.query_params.get("flash")
    error         = request.query_params.get("error")
    active_filter = _clean_filter(filter)

    stats        = get_report_stats(db, uid)
    my_reports   = get_my_reports(db, uid, date_filter=active_filter)
    team_reports: list = []
    eod_reports:  list = []
    all_reports:  list = []
    all_eod:      list = []

    if role == "team_lead":
        team_raw     = get_team_reports(db, uid, date_filter=active_filter)
        team_reports = enrich_reports_with_names(db, team_raw)
        eod_reports  = get_eod_reports(db, uid)

    elif role in _MANAGER_ROLES:
        all_raw     = get_all_reports(db, date_filter=active_filter)
        all_reports = enrich_reports_with_names(db, all_raw)
        raw_eod     = get_all_eod_reports(db)
        all_eod     = enrich_eod_with_names(db, raw_eod)

    return templates.TemplateResponse(
        "reports/index.html",
        {
            "request":       request,
            "current_user":  current_user,
            "stats":         stats,
            "my_reports":    my_reports,
            "team_reports":  team_reports,
            "eod_reports":   eod_reports,
            "all_reports":   all_reports,
            "all_eod":       all_eod,
            "active_filter": active_filter,
            "active_tab":    tab,
            "flash":         flash,
            "error":         error,
        },
    )


# ---------------------------------------------------------------------------
# POST /reports/hourly — submit hourly report (Employee / Team Lead)
# ---------------------------------------------------------------------------

@router.post("/hourly")
def submit_hourly(
    description: str = Form(...),
    hours_spent: float = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    _require_reporter(current_user)
    try:
        submit_hourly_report(
            db=db,
            user_id=current_user["user_id"],
            description=description,
            hours_spent=hours_spent,
        )
    except ReportError as exc:
        return RedirectResponse(
            f"/reports/?tab=submit&error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse("/reports/?tab=my&flash=hourly_ok", status_code=303)


# ---------------------------------------------------------------------------
# POST /reports/eod — submit EOD report (Team Lead ONLY)
# ---------------------------------------------------------------------------

@router.post("/eod")
def submit_eod(
    summary: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    _require_team_lead(current_user)
    try:
        submit_eod_report(
            db=db,
            team_lead_id=current_user["user_id"],
            summary=summary,
        )
    except ReportError as exc:
        return RedirectResponse(
            f"/reports/?tab=eod&error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse("/reports/?tab=eod&flash=eod_ok", status_code=303)


# ---------------------------------------------------------------------------
# Convenience redirects (keep old links working)
# ---------------------------------------------------------------------------

@router.get("/my", response_class=HTMLResponse)
def my_reports_page(
    _request: Request,
    _current_user: dict = Depends(login_required),
):
    return RedirectResponse("/reports/?tab=my", status_code=302)


@router.get("/team", response_class=HTMLResponse)
def team_reports_page(
    _request: Request,
    _current_user: dict = Depends(role_required(*_TEAM_ROLES)),
):
    return RedirectResponse("/reports/?tab=team", status_code=302)


@router.get("/all", response_class=HTMLResponse)
def all_reports_page(
    _request: Request,
    _current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    return RedirectResponse("/reports/?tab=all", status_code=302)
