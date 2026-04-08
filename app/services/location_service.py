"""
location_service.py — Geolocation helpers for office / WFH access control.

Design rules
────────────
• Never raises — callers receive (bool, str | None) so the system cannot
  crash if location data is absent or malformed.
• WFH users always pass validation; location is logged for audit purposes.
• Office users must be within office_radius metres of the configured office
  coordinates.  If the admin has NOT set office_lat/office_lng the check
  fails safe (denies access) with an actionable error message.
• Admin bootstrap exception: if office coords are not yet configured, admin
  users are allowed through so they can reach /admin/office-location to
  complete the initial setup.  Non-admin users remain blocked.
"""
import math
import logging
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Haversine distance ────────────────────────────────────────────────────────

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two WGS-84 coordinates."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Validation ────────────────────────────────────────────────────────────────

def validate_user_location(
    user, lat: Optional[float], lng: Optional[float]
) -> Tuple[bool, Optional[str]]:
    """
    Check whether `user` is allowed to act from (lat, lng).

    Returns
    ───────
    (True, None)          — allowed
    (False, error_msg)    — denied; error_msg is safe to surface to the user
    """
    try:
        mode = getattr(user, "work_mode", "office") or "office"

        if mode == "wfh":
            # WFH users may act from anywhere — no coordinates required
            return True, None

        # ── Office mode ───────────────────────────────────────────────────
        office_lat = getattr(user, "office_lat", None)
        office_lng = getattr(user, "office_lng", None)

        # Check office configuration BEFORE requiring coordinates.
        # Admins get a bootstrap bypass so they can log in to configure
        # the office location even before it has been set up.
        if office_lat is None or office_lng is None:
            role = str(getattr(user, "role", "")).lower()
            if "admin" in role:
                logger.warning(
                    "Admin bootstrap: user_id=%s logged in without office location configured — "
                    "please visit /admin/office-location to complete setup.",
                    getattr(user, "id", "?"),
                )
                return True, None
            logger.warning(
                "Office location not configured for user_id=%s", getattr(user, "id", "?")
            )
            return False, "Office location is not configured. Please contact your administrator."

        if lat is None or lng is None:
            return False, "Location is required for office mode login"

        radius = getattr(user, "office_radius", None) or 100
        distance = calculate_distance(lat, lng, office_lat, office_lng)

        logger.debug(
            "Location check user_id=%s distance=%.1fm radius=%.1fm",
            getattr(user, "id", "?"), distance, radius,
        )

        if distance > radius:
            return (
                False,
                f"You must be within the office premises ({distance:.0f}m away, limit {radius:.0f}m).",
            )

        return True, None

    except Exception as exc:
        logger.error("validate_user_location error: %s", exc)
        # Fail-safe: deny access but do not crash
        return False, "Location validation failed. Please try again."


# ── Location logger ───────────────────────────────────────────────────────────

def save_location_log(
    db: Session,
    user_id: int,
    lat: Optional[float],
    lng: Optional[float],
    action: str,
) -> None:
    """
    Persist a location event to location_logs.
    Silently skips if lat/lng are None (WFH or browser denied).
    Never raises — a logging failure must not abort the business transaction.
    """
    try:
        from app.models.location_log import LocationLog  # local import to avoid circular deps

        log = LocationLog(
            user_id=user_id,
            latitude=lat,
            longitude=lng,
            action=action,
            timestamp=datetime.utcnow(),
        )
        db.add(log)
        db.commit()
        logger.debug("LocationLog saved: user_id=%s action=%s", user_id, action)
    except Exception as exc:
        logger.error("save_location_log failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
