from datetime import date, datetime
from fastapi import APIRouter, Request, Form, Depends, HTTPException
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

# Notification helper — imported defensively
try:
    from app.services.notification_service import create_notification as _notif
    _NOTIF_OK = True
except Exception:
    _NOTIF_OK = False
    def _notif(*a, **kw): pass  # noqa


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
        from app.services.hierarchy_service import get_subordinate_ids
        from app.models.leave import Leave as LeaveModel, LeaveStatus
        subordinate_ids = get_subordinate_ids(db, uid)
        leaves = db.query(LeaveModel).filter(
            LeaveModel.employee_id.in_(subordinate_ids),
            LeaveModel.status == LeaveStatus.pending
        ).order_by(LeaveModel.created_at.asc()).all()
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
    if current_user["role"] == "admin":
        raise HTTPException(status_code=403, detail="Admin cannot apply leave")

    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)

        # Past-date guard — reject leaves that start before today
        if sd < date.today():
            raise LeaveError("Leave start date cannot be in the past.")

        leave = apply_leave(db, current_user["user_id"], leave_type, sd, ed, reason or None)
        
        if _AUDIT_OK:
            try:
                _audit(db, current_user["user_id"], "leave_applied", "leave", leave.id,
                       f"Applied for {leave.total_days} days")
            except Exception:
                pass

        # ── Notify manager that a leave request is pending ───────────────────────
        try:
            from app.models.user import User as _User
            employee = db.query(_User).filter(
                _User.id == current_user["user_id"]
            ).first()
            if employee:
                manager_id = employee.team_lead_id or employee.manager_id
                if manager_id and manager_id != current_user["user_id"]:
                    _notif(
                        db, manager_id, "leave",
                        f"📅 {employee.name} applied for {leave.total_days} day(s) of "
                        f"{leave.leave_type.value} leave.",
                        entity_id=leave.id,
                        actor_id=current_user["user_id"],
                    )
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
    # Scope check: non-admin reviewers can only act on leaves within their hierarchy
    if current_user["role"] != "admin":
        from app.models.leave import Leave as LeaveModel
        from app.services.hierarchy_service import get_subordinate_ids
        leave_record = db.query(LeaveModel).filter(LeaveModel.id == leave_id).first()
        if leave_record:
            if leave_record.employee_id == current_user["user_id"]:
                raise HTTPException(status_code=403, detail="You cannot review your own leave request")

            subordinate_ids = get_subordinate_ids(db, current_user["user_id"])
            if leave_record.employee_id not in subordinate_ids:
                raise HTTPException(status_code=403, detail="This leave request is outside your scope")
    try:
        updated = review_leave(db, leave_id, current_user["user_id"], action, note or None)

        if _AUDIT_OK:
            try:
                audit_action = "leave_approved" if action == "approved" else "leave_rejected"
                _audit(db, current_user["user_id"], audit_action, "leave", leave_id,
                       note or None)
            except Exception:
                pass

        # ── Notify the employee of the review decision ──────────────────────────
        try:
            verb = "✅ approved" if action == "approved" else "❌ rejected"
            _notif(
                db, updated.employee_id, "leave",
                f"Your leave request was {verb} by "
                f"{current_user.get('name', 'your manager')}.",
                entity_id=leave_id,
                actor_id=current_user["user_id"],
            )
        except Exception:
            pass
    except LeaveError:
        pass
    return RedirectResponse("/leaves/", status_code=302)
