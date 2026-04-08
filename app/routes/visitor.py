from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required, role_required
from app.core.database import get_db
from app.services.visitor_service import (
    VisitorError,
    approve_visitor,
    get_my_visitors,
    list_all_visitors,
    list_pending_visitors,
    register_visitor,
    reject_visitor,
)

router = APIRouter(prefix="/visitor", tags=["visitor"])
templates = Jinja2Templates(directory="app/templates")

_MANAGER_ROLES   = ("admin", "manager")
_GUARD_ROLE      = ("security_guard",)


# ---------------------------------------------------------------------------
# Helper: hard 403 for wrong role (returns HTML error for browser clients)
# ---------------------------------------------------------------------------
def _guard_only(current_user: dict) -> None:
    """Raise 403 if caller is not a security guard."""
    if current_user.get("role") != "security_guard":
        raise HTTPException(
            status_code=403,
            detail="Only security guards can perform this action.",
        )


# ---------------------------------------------------------------------------
# Register visitor — ONLY security_guard
# ---------------------------------------------------------------------------

@router.get("/register", response_class=HTMLResponse)
def register_page(
    request: Request,
    current_user: dict = Depends(login_required),
):
    _guard_only(current_user)
    return templates.TemplateResponse(
        "visitor/register.html",
        {"request": request, "current_user": current_user, "error": None, "success": False},
    )


@router.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    purpose: str = Form(...),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    _guard_only(current_user)
    try:
        register_visitor(
            db=db,
            name=name,
            phone=phone,
            purpose=purpose,
            image=image,
            created_by=current_user["user_id"],
        )
        return templates.TemplateResponse(
            "visitor/register.html",
            {
                "request": request,
                "current_user": current_user,
                "error": None,
                "success": True,
            },
        )
    except VisitorError as exc:
        return templates.TemplateResponse(
            "visitor/register.html",
            {
                "request": request,
                "current_user": current_user,
                "error": str(exc),
                "success": False,
            },
            status_code=400,
        )


# ---------------------------------------------------------------------------
# My logs — ONLY security_guard (own registrations)
# ---------------------------------------------------------------------------

@router.get("/my-logs", response_class=HTMLResponse)
def my_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    _guard_only(current_user)
    visitors = get_my_visitors(db, current_user["user_id"])
    return templates.TemplateResponse(
        "visitor/my_logs.html",
        {
            "request": request,
            "current_user": current_user,
            "visitors": visitors,
        },
    )


# ---------------------------------------------------------------------------
# Pending visitors — Manager / Admin ONLY
# ---------------------------------------------------------------------------

@router.get("/pending", response_class=HTMLResponse)
def pending_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    visitors = list_pending_visitors(db)
    return templates.TemplateResponse(
        "visitor/pending.html",
        {
            "request": request,
            "current_user": current_user,
            "visitors": visitors,
            "flash": request.query_params.get("flash"),
        },
    )


# ---------------------------------------------------------------------------
# Approve — Manager / Admin ONLY
# ---------------------------------------------------------------------------

@router.post("/approve/{visitor_id}")
def approve(
    visitor_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    try:
        approve_visitor(db, visitor_id, current_user["user_id"])
    except VisitorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/visitor/pending?flash=approved", status_code=303)


# ---------------------------------------------------------------------------
# Reject — Manager / Admin ONLY
# ---------------------------------------------------------------------------

@router.post("/reject/{visitor_id}")
def reject(
    visitor_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    try:
        reject_visitor(db, visitor_id, current_user["user_id"])
    except VisitorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse("/visitor/pending?flash=rejected", status_code=303)


# ---------------------------------------------------------------------------
# All visitors log — Manager / Admin ONLY
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def all_visitors_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required(*_MANAGER_ROLES)),
):
    visitors = list_all_visitors(db)
    return templates.TemplateResponse(
        "visitor/all.html",
        {
            "request": request,
            "current_user": current_user,
            "visitors": visitors,
        },
    )
