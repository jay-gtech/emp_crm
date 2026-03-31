from datetime import date
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.leave_service import (
    apply_leave, review_leave, list_leaves_for_employee,
    list_pending_leaves, list_all_leaves, get_leave_balance, LeaveError,
)

try:
    from app.services.hierarchy_service import is_user_in_scope
except ImportError:
    is_user_in_scope = None

# Audit trigger — imported defensively

try:
    from app.services.audit_service import log_action as _audit
    _AUDIT_OK = True
except Exception:
    _AUDIT_OK = False


router = APIRouter(prefix="/leaves", tags=["leaves"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def leaves_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    uid = current_user["user_id"]
    role = current_user["role"]

    if role == "admin":
        leaves = list_all_leaves(db)
    elif role in ("manager", "team_lead"):
        all_pending = list_pending_leaves(db)
        if is_user_in_scope:
            leaves = [l for l in all_pending if is_user_in_scope(db, current_user, l.employee_id)]
        else:
            leaves = all_pending
    else:
        leaves = list_leaves_for_employee(db, uid)

    balance = get_leave_balance(db, uid)
    return templates.TemplateResponse(
        "leaves/index.html",
        {
            "request": request,
            "current_user": current_user,
            "leaves": leaves,
            "balance": balance,
            "error": None,
        },
    )


@router.post("/apply")
def apply_leave_post(
    request: Request,
    leave_type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        leave = apply_leave(db, current_user["user_id"], leave_type, sd, ed, reason or None)
        
        if _AUDIT_OK:
            try:
                _audit(db, current_user["user_id"], "leave_applied", "leave", leave.id,
                       f"Applied for {leave.total_days} days")
            except Exception:
                pass
                
        return RedirectResponse("/leaves/", status_code=302)
    except (LeaveError, ValueError) as e:
        balance = get_leave_balance(db, current_user["user_id"])
        leaves = list_leaves_for_employee(db, current_user["user_id"])
        return templates.TemplateResponse(
            "leaves/index.html",
            {
                "request": request,
                "current_user": current_user,
                "leaves": leaves,
                "balance": balance,
                "error": str(e),
            },
            status_code=400,
        )


@router.post("/{leave_id}/review")
def review_leave_post(
    leave_id: int,
    request: Request,
    action: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    try:
        updated = review_leave(db, leave_id, current_user["user_id"], action, note or None)

        if _AUDIT_OK:
            try:
                audit_action = "leave_approved" if action == "approved" else "leave_rejected"
                _audit(db, current_user["user_id"], audit_action, "leave", leave_id,
                       note or None)
            except Exception:
                pass
    except LeaveError:
        pass
    return RedirectResponse("/leaves/", status_code=302)
