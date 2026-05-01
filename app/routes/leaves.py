from datetime import date
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.auth import login_required, role_required
from app.services.leave_service import (
    apply_leave, review_leave, list_leaves_for_employee,
    list_all_leaves, get_leave_balance, LeaveError,
)

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
        # Admin: single flat list, no split needed
        leaves     = list_all_leaves(db)
        my_leaves  = []
        team_leaves = []
    elif role in ("manager", "team_lead"):
        from app.services.hierarchy_service import safe_get_subordinate_ids
        from app.models.leave import Leave as LeaveModel
        subordinate_ids = safe_get_subordinate_ids(db, uid)
        # Own full history + full team history (all statuses).
        # The template already gates action buttons on status — no need to
        # filter here; managers need visibility of the complete team history.
        my_leaves = list_leaves_for_employee(db, uid)
        team_leaves = (
            db.query(LeaveModel)
            .filter(LeaveModel.employee_id.in_(subordinate_ids))
            .order_by(LeaveModel.created_at.desc())
            .all()
        ) if subordinate_ids else []
        leaves = []  # unused for non-admin path
    else:
        # Employee / security_guard: own data only
        my_leaves   = list_leaves_for_employee(db, uid)
        team_leaves = []
        leaves      = []  # unused for non-admin path

    balance = get_leave_balance(db, uid)
    return templates.TemplateResponse(
        "leaves/index.html",
        {
            "request":      request,
            "current_user": current_user,
            "leaves":       leaves,
            "my_leaves":    my_leaves,
            "team_leaves":  team_leaves,
            "balance":      balance,
            "error":        None,
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
        _uid = current_user["user_id"]
        balance = get_leave_balance(db, _uid)
        return templates.TemplateResponse(
            "leaves/index.html",
            {
                "request":      request,
                "current_user": current_user,
                "leaves":       [],
                "my_leaves":    list_leaves_for_employee(db, _uid),
                "team_leaves":  [],
                "balance":      balance,
                "error":        str(e),
            },
            status_code=400,
        )


@router.post("/{leave_id}/review")
def review_leave_post(
    leave_id: int,
    action: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(role_required("admin", "manager", "team_lead")),
):
    role = current_user["role"]
    uid  = current_user["user_id"]

    # Resolve the record first — security checks must always run, never be skipped
    from app.models.leave import Leave as LeaveModel
    leave_record = db.query(LeaveModel).filter(LeaveModel.id == leave_id).first()
    if not leave_record:
        raise HTTPException(status_code=404, detail="Leave request not found")

    # Non-admin: scope + hierarchy-level guard (guards are now unconditional)
    if role != "admin":
        from app.models.user import User as UserModel
        from app.services.hierarchy_service import safe_get_subordinate_ids
        from app.core.rbac import can_act_on_roles

        # Self-approval guard (global rule)
        if leave_record.employee_id == uid:
            raise HTTPException(status_code=403, detail="You cannot review your own leave request")

        # Scope: leave owner must be a subordinate
        subordinate_ids = safe_get_subordinate_ids(db, uid)
        if leave_record.employee_id not in subordinate_ids:
            raise HTTPException(status_code=403, detail="This leave request is outside your scope")

        # Hierarchy-level: reviewer must outrank the leave owner
        leave_user = db.query(UserModel).filter(UserModel.id == leave_record.employee_id).first()
        if leave_user and not can_act_on_roles(role, uid, leave_user.role.value, leave_user.id):
            raise HTTPException(status_code=403, detail="Insufficient hierarchy level to review this leave")

        # Workflow: team_lead cannot act on pending_manager leaves
        if role == "team_lead" and leave_record.status.value == "pending_manager":
            raise HTTPException(status_code=403, detail="This leave has been forwarded to the manager")

        # Workflow: forward action is only for team_lead
        if action == "forward" and role != "team_lead":
            raise HTTPException(status_code=403, detail="Only a team lead can forward a leave request")

    try:
        updated = review_leave(db, leave_id, uid, action, note or None)

        if _AUDIT_OK:
            try:
                _audit_action = (
                    "leave_approved"  if action == "approved" else
                    "leave_forwarded" if action == "forward"  else
                    "leave_rejected"
                )
                _audit(db, uid, _audit_action, "leave", leave_id, note or None)
            except Exception:
                pass

        # Notify the employee of approve/reject; notify manager of forward
        try:
            if action == "forward":
                from app.models.user import User as _User
                emp = db.query(_User).filter(_User.id == updated.employee_id).first()
                if emp and emp.manager_id and emp.manager_id != uid:
                    _notif(
                        db, emp.manager_id, "leave",
                        f"📋 A leave request from {emp.name} was forwarded to you by "
                        f"{current_user.get('name', 'team lead')}.",
                        entity_id=leave_id,
                        actor_id=uid,
                    )
            else:
                verb = "✅ approved" if action == "approved" else "❌ rejected"
                _notif(
                    db, updated.employee_id, "leave",
                    f"Your leave request was {verb} by {current_user.get('name', 'your manager')}.",
                    entity_id=leave_id,
                    actor_id=uid,
                )
        except Exception:
            pass

    except LeaveError:
        pass
    return RedirectResponse("/leaves/", status_code=302)
