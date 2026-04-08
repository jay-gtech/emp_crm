"""
Report service — hourly reports + EOD reports.

All public functions are independently safe: each wraps its logic in
try/except and returns typed defaults so one failing call never crashes
the rest of the request pipeline.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.report import Report
from app.models.eod_report import EODReport

logger = logging.getLogger(__name__)


class ReportError(Exception):
    """Domain-level error for report operations."""


# ---------------------------------------------------------------------------
# 1. Submit hourly report (Employee / Team Lead)
# ---------------------------------------------------------------------------

def submit_hourly_report(
    db: Session,
    user_id: int,
    description: str,
    hours_spent: float,
) -> Report:
    """
    Validate and persist a new hourly report.
    Raises ReportError on validation failure.
    """
    description = description.strip()
    if not description:
        raise ReportError("Description cannot be empty.")
    if hours_spent <= 0:
        raise ReportError("Hours spent must be greater than zero.")
    if hours_spent > 24:
        raise ReportError("Hours spent cannot exceed 24.")

    report = Report(
        user_id=user_id,
        description=description,
        hours_spent=hours_spent,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


# ---------------------------------------------------------------------------
# 2. Fetch own reports
# ---------------------------------------------------------------------------

def get_my_reports(db: Session, user_id: int, limit: int = 50) -> list[Report]:
    """Return the most recent reports submitted by *user_id*."""
    try:
        return (
            db.query(Report)
            .filter(Report.user_id == user_id)
            .order_by(Report.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_my_reports failed for user_id=%s: %s", user_id, exc)
        return []


# ---------------------------------------------------------------------------
# 3. Fetch team reports (Team Lead / Manager)
# ---------------------------------------------------------------------------

def get_team_reports(db: Session, team_lead_id: int, limit: int = 100) -> list[Report]:
    """
    Return reports from all employees whose team_lead_id == *team_lead_id*.
    No N+1: fetches all matching user IDs in a single subquery.
    """
    try:
        from app.models.user import User

        member_ids = [
            uid
            for (uid,) in db.query(User.id)
            .filter(User.team_lead_id == team_lead_id, User.is_active == 1)
            .all()
        ]
        if not member_ids:
            return []

        return (
            db.query(Report)
            .filter(Report.user_id.in_(member_ids))
            .order_by(Report.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_team_reports failed for tl=%s: %s", team_lead_id, exc)
        return []


def get_all_reports(db: Session, limit: int = 200) -> list[Report]:
    """Return all reports across all users (Manager / Admin view)."""
    try:
        return (
            db.query(Report)
            .order_by(Report.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_all_reports failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 4. EOD report (Team Lead only, once per day)
# ---------------------------------------------------------------------------

def submit_eod_report(
    db: Session,
    team_lead_id: int,
    summary: str,
    report_date: date | None = None,
) -> EODReport:
    """
    Save a Team Lead's EOD report.
    Raises ReportError if:
      - summary is blank
      - an EOD for today already exists
    Notifies the Team Lead's manager on success (fire-and-forget).
    """
    summary = summary.strip()
    if not summary:
        raise ReportError("EOD summary cannot be empty.")

    today = report_date or date.today()

    existing = (
        db.query(EODReport)
        .filter(
            EODReport.team_lead_id == team_lead_id,
            EODReport.report_date == today,
        )
        .first()
    )
    if existing:
        raise ReportError("EOD report for today has already been submitted.")

    eod = EODReport(
        team_lead_id=team_lead_id,
        summary=summary,
        report_date=today,
    )
    db.add(eod)
    db.commit()
    db.refresh(eod)

    # Notify team lead's manager — fire-and-forget
    try:
        from app.models.user import User
        from app.services.notification_service import create_task_notification

        tl = db.query(User).filter(User.id == team_lead_id).first()
        if tl and tl.manager_id:
            msg = f"EOD report submitted by {tl.name} for {today.strftime('%d %b %Y')}."
            create_task_notification(db, tl.manager_id, msg)
    except Exception as exc:
        logger.warning("EOD notification failed for tl=%s: %s", team_lead_id, exc)

    return eod


# ---------------------------------------------------------------------------
# 5. EOD report history
# ---------------------------------------------------------------------------

def get_eod_reports(db: Session, team_lead_id: int, limit: int = 30) -> list[EODReport]:
    """Return EOD reports for a specific team lead, newest first."""
    try:
        return (
            db.query(EODReport)
            .filter(EODReport.team_lead_id == team_lead_id)
            .order_by(EODReport.report_date.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_eod_reports failed for tl=%s: %s", team_lead_id, exc)
        return []


def get_all_eod_reports(db: Session, limit: int = 100) -> list[EODReport]:
    """Return all EOD reports across all team leads (Manager / Admin)."""
    try:
        return (
            db.query(EODReport)
            .order_by(EODReport.report_date.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_all_eod_reports failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 6. Helper — resolve user display name for report listing
# ---------------------------------------------------------------------------

def enrich_reports_with_names(db: Session, reports: list[Report]) -> list[dict]:
    """
    Return report dicts with 'user_name' added.
    Single batch query — no N+1.
    """
    if not reports:
        return []
    try:
        from app.models.user import User

        uid_set = {r.user_id for r in reports}
        users   = db.query(User).filter(User.id.in_(uid_set)).all()
        id_name = {u.id: u.name for u in users}

        return [
            {
                "id":          r.id,
                "user_id":     r.user_id,
                "user_name":   id_name.get(r.user_id, "Unknown"),
                "description": r.description,
                "hours_spent": r.hours_spent,
                "created_at":  r.created_at,
            }
            for r in reports
        ]
    except Exception as exc:
        logger.error("enrich_reports_with_names failed: %s", exc)
        return []
