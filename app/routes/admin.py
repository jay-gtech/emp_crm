"""
admin.py — Admin-only configuration routes.

Currently provides:
  GET  /admin/office-location  — render the office location config page
  POST /admin/office-location  — persist new office coordinates to all users

Design notes
────────────
• Uses the existing session-based auth guards (login_required / role_required).
• Office location is stored in the per-user columns (office_lat, office_lng,
  office_radius) introduced in the location feature.  When admin saves the
  config we cascade it to ALL users so that every employee's validation uses
  the same office point.
• Never crashes on missing values — all reads are guarded.
"""
import logging

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

_ADMIN_REQUIRED = role_required("admin")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_office_config(db: Session) -> dict:
    """
    Return the current office config by reading the first user that has
    office_lat set, falling back to safe defaults.
    """
    user_with_config = (
        db.query(User)
        .filter(User.office_lat.isnot(None))
        .first()
    )
    if user_with_config:
        return {
            "lat": user_with_config.office_lat,
            "lng": user_with_config.office_lng,
            "radius": user_with_config.office_radius or 100,
        }
    return {"lat": None, "lng": None, "radius": 100}


# ── GET /admin/office-location ────────────────────────────────────────────────

@router.get("/office-location", response_class=HTMLResponse)
def office_location_page(
    request: Request,
    success: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(_ADMIN_REQUIRED),
):
    config = _get_office_config(db)
    return templates.TemplateResponse(
        "admin/office_location.html",
        {
            "request": request,
            "current_user": current_user,
            "office_lat": config["lat"],
            "office_lng": config["lng"],
            "office_radius": config["radius"],
            "success": success == "1",
        },
    )


# ── POST /admin/office-location ───────────────────────────────────────────────

@router.post("/office-location")
def set_office_location(
    request: Request,
    latitude: float = Form(...),
    longitude: float = Form(...),
    radius: float = Form(100),
    db: Session = Depends(get_db),
    current_user: dict = Depends(_ADMIN_REQUIRED),
):
    # Basic sanity-check on coordinates
    if not (-90 <= latitude <= 90):
        raise HTTPException(status_code=422, detail="Latitude must be between -90 and 90.")
    if not (-180 <= longitude <= 180):
        raise HTTPException(status_code=422, detail="Longitude must be between -180 and 180.")
    if radius <= 0:
        raise HTTPException(status_code=422, detail="Radius must be a positive number.")

    try:
        # Cascade office coordinates to ALL users so validation works for everyone
        updated = (
            db.query(User)
            .update(
                {
                    User.office_lat: latitude,
                    User.office_lng: longitude,
                    User.office_radius: radius,
                },
                synchronize_session="fetch",
            )
        )
        db.commit()
        logger.info(
            "Office location updated by admin user_id=%s: lat=%s lng=%s radius=%sm (%d users affected)",
            current_user["user_id"], latitude, longitude, radius, updated,
        )
    except Exception as exc:
        db.rollback()
        logger.error("Failed to save office location: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save office location. Please try again.")

    return RedirectResponse("/admin/office-location?success=1", status_code=303)
