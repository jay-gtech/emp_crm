import logging

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import set_session_user, clear_session, get_session_user
from app.services.auth_service import authenticate_user, AuthError
from app.services.location_service import save_location_log

logger = logging.getLogger(__name__)

# Rate-limiter — imported defensively so the app starts even without slowapi
try:
    from app.core.limiter import limiter
    _LIMITER_OK = True
except Exception:
    _LIMITER_OK = False

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


def _login_handler(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    latitude: float = Form(None),
    longitude: float = Form(None),
    db: Session = Depends(get_db),
):
    """Core login logic — registered with or without rate limiting below."""
    # Clear any stale session data on a new login attempt
    clear_session(request)

    clean_email = email.strip().lower()
    logger.info("Login attempt: email=%s", clean_email)

    try:
        user = authenticate_user(db, clean_email, password)
    except AuthError as e:
        logger.warning("Login failed: email=%s reason=%s", clean_email, str(e))
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": str(e)}, status_code=400
        )

    logger.info("Login success: user_id=%s role=%s", user.id, user.role.value)
    set_session_user(request, user.id, user.role.value, user.name)

    # Opportunistic location log — no enforcement at login; validation is
    # done at clock-in / clock-out only.
    if latitude is not None and longitude is not None:
        save_location_log(db, user.id, latitude, longitude, "login")

    return RedirectResponse("/dashboard", status_code=302)


# Register the POST /login route — wrap with rate limiter when available
if _LIMITER_OK:
    login = router.post("/login")(limiter.limit("5/minute")(_login_handler))
else:
    login = router.post("/login")(_login_handler)


@router.get("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse("/auth/login", status_code=302)
