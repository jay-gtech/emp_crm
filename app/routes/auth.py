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
    # Clear any stale session data on a new login attempt
    clear_session(request)
    
    clean_email = email.strip().lower()
    print(f"DEBUG: Login attempt for {clean_email}")
    
    try:
        user = authenticate_user(db, clean_email, password)
        print(f"DEBUG: User found and authenticated: {user.name}")
        
        set_session_user(request, user.id, user.role.value, user.name)
        return RedirectResponse("/dashboard", status_code=302)
    except AuthError as e:
        print(f"DEBUG: Auth failed for {clean_email}: {str(e)}")
        return templates.TemplateResponse(
            "auth/login.html", {"request": request, "error": str(e)}, status_code=400
        )


@router.get("/logout")
def logout(request: Request):
    clear_session(request)
    return RedirectResponse("/auth/login", status_code=302)
