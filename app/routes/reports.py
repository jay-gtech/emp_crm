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

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required, role_required
from app.core.database import get_db
from app.services.report_service import (
    ReportError,
    enrich_reports_with_names,
    get_all_eod_reports,
    get_all_reports,
    get_eod_reports,
    get_my_reports,
    get_team_reports,
    submit_eod_report,
    submit_hourly_report,
)

logger = logging.getLogger(__name__)

router    = APIRouter(prefix="/reports", tags=["reports"])
templates = Jinja2Templates(directory="app/templates")

# Role groups
_REPORTER_ROLES = ("employee", "team_lead")          # can submit hourly
_VIEWER_ROLES   = ("employee", "team_lead", "manager", "admin")
_TEAM_ROLES     = ("team_lead", "manager", "admin")
_MANAGER_ROLES  = ("manager", "admin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_reporter(current_user: dict) -> None:
    """Raise 403 if the user is not allowed to submit hourly reports."""
    if current_user.get("role") not in _REPORTER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only employees and team leads can submit hourly reports.",
        )


def _require_team_lead(current_user: dict) -> None:
    """Raise 403 if the user is not a team lead."""
    if current_user.get("role") != "team_lead":
        raise HTTPException(
            status_code=403,
            detail="Only team leads can perform this action.",
        )


# ---------------------------------------------------------------------------
# GET /reports/  — role-aware dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def reports_home(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    role    = current_user.get("role")
    uid     = current_user["user_id"]
    flash   = request.query_params.get("flash")
    error   = request.query_params.get("error")

    my_reports   = get_my_reports(db, uid)
    team_reports: list = []
    eod_reports:  list = []
    all_reports:  list = []
    all_eod:      list = []

    if role == "team_lead":
        team_raw     = get_team_reports(db, uid)
        team_reports = enrich_reports_with_names(db, team_raw)
        eod_reports  = get_eod_reports(db, uid)

    elif role in _MANAGER_ROLES:
        all_raw     = get_all_reports(db)
        all_reports = enrich_reports_with_names(db, all_raw)
        all_eod     = get_all_eod_reports(db)

    return templates.TemplateResponse(
        "reports/index.html",
        {
            "request":      request,
            "current_user": current_user,
            "my_reports":   my_reports,
            "team_reports": team_reports,
            "eod_reports":  eod_reports,
            "all_reports":  all_reports,
            "all_eod":      all_eod,
            "flash":        flash,
            "error":        error,
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
            f"/reports/?error={exc}", status_code=303
        )
    return RedirectResponse("/reports/?flash=hourly_ok", status_code=303)


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
            f"/reports/?error={exc}", status_code=303
        )
    return RedirectResponse("/reports/?flash=eod_ok", status_code=303)


# ---------------------------------------------------------------------------
# GET /reports/my — own reports (JSON-friendly fallback, used by nav link)
# ---------------------------------------------------------------------------

@router.get("/my", response_class=HTMLResponse)
def my_reports_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    """Redirect to dashboard with ?tab=my pre-selected (tab handled client-side)."""
    return RedirectResponse("/reports/?tab=my", status_code=302)


# ---------------------------------------------------------------------------
# GET /reports/team — team reports (Team Lead / Manager / Admin)
# ---------------------------------------------------------------------------

@router.get("/team", response_class=HTMLResponse)
def team_reports_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_TEAM_ROLES)),
):
    return RedirectResponse("/reports/?tab=team", status_code=302)


# ---------------------------------------------------------------------------
# GET /reports/all — all reports (Manager / Admin only)
# ---------------------------------------------------------------------------

@router.get("/all", response_class=HTMLResponse)
def all_reports_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    return RedirectResponse("/reports/?tab=all", status_code=302)
