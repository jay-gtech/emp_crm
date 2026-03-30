from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import set_session_user, clear_session, get_session_user
from app.services.auth_service import authenticate_user, AuthError

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request, "error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = authenticate_user(db, email.strip().lower(), password)
        set_session_user(request, user.id, user.role.value, user.name)
        return RedirectResponse("/dashboard", status_code=302)
    except AuthError as e:
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": str(e)}, status_code=400
        )


@router.get("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse("/auth/login", status_code=302)
