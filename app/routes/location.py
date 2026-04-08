"""
location.py — Live team-location tracking routes.

RBAC scope
──────────
  Admin    → all active users
  Manager  → their team leads + those team leads' employees
  TL       → their direct employees only
  Employee → 403 (no team to observe)

Endpoints
─────────
  GET /location/team-map           — HTML page with Leaflet map
  GET /location/team-locations     — JSON; consumed by the map page via fetch()
"""
import logging
from typing import List

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import login_required
from app.models.user import User
from app.models.location_log import LocationLog
from app.services.hierarchy_service import get_manager_team, get_team_lead_members

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/location", tags=["location"])
templates = Jinja2Templates(directory="app/templates")

# Roles allowed to view the live tracking map
_TRACKING_ROLES = {"admin", "manager", "team_lead"}


def _users_in_scope(db: Session, current_user: dict) -> List[User]:
    """
    Return the list of User ORM objects this principal may observe.
    Fails closed — returns [] on any error rather than leaking data.
    """
    try:
        role    = current_user.get("role", "")
        user_id = current_user.get("user_id")

        if role == "admin":
            return db.query(User).filter(User.is_active == 1).all()

        if role == "manager":
            return get_manager_team(db, user_id)

        if role == "team_lead":
            return get_team_lead_members(db, user_id)

        return []   # employee — no team scope
    except Exception as exc:
        logger.error("_users_in_scope error: %s", exc)
        return []


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/team-locations")
def team_locations(
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user.get("role") not in _TRACKING_ROLES:
        raise HTTPException(status_code=403, detail="Not authorised to view team locations.")

    users = _users_in_scope(db, current_user)
    results = []

    for user in users:
        try:
            latest: LocationLog | None = (
                db.query(LocationLog)
                .filter(LocationLog.user_id == user.id)
                .order_by(LocationLog.timestamp.desc())
                .first()
            )
            if latest and latest.latitude is not None and latest.longitude is not None:
                results.append({
                    "user_id":   user.id,
                    "name":      user.name,
                    "role":      user.role.value if hasattr(user.role, "value") else str(user.role),
                    "work_mode": getattr(user, "work_mode", "office") or "office",
                    "lat":       latest.latitude,
                    "lng":       latest.longitude,
                    "action":    latest.action,
                    "timestamp": latest.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                })
        except Exception as exc:
            logger.warning("Failed to fetch location for user_id=%s: %s", user.id, exc)
            continue

    return JSONResponse(content=results)


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/team-map", response_class=HTMLResponse)
def team_map_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    if current_user.get("role") not in _TRACKING_ROLES:
        raise HTTPException(status_code=403, detail="Not authorised to view team locations.")

    # Pass the office coordinates so the map can draw the office boundary
    from app.routes.admin import _get_office_config
    office = _get_office_config(db)

    return templates.TemplateResponse(
        "location/team_locations.html",
        {
            "request":       request,
            "current_user":  current_user,
            "office_lat":    office["lat"],
            "office_lng":    office["lng"],
            "office_radius": office["radius"],
        },
    )
