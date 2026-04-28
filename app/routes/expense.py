"""
Expense router — group expense tracking with equal split.

RBAC:
  Create group   → admin, manager, team_lead
  Add members    → group creator only
  View group     → members only
  Pay            → the member themselves OR group creator
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import login_required
from app.core.database import get_db
from app.services.expense_service import (
    ExpenseError,
    add_members,
    create_expense_group,
    get_group_detail,
    get_my_groups,
    mark_paid,
)

router    = APIRouter(prefix="/expense", tags=["expense"])
templates = Jinja2Templates(directory="app/templates")

_CREATOR_ROLES = ("admin", "manager", "team_lead")

# Notification helper — imported defensively
try:
    from app.services.notification_service import create_notification as _notif
except Exception:
    def _notif(*a, **kw): pass  # noqa


def _require_creator_role(current_user: dict) -> None:
    if current_user.get("role") not in _CREATOR_ROLES:
        raise HTTPException(status_code=403, detail="Only admin/manager/team lead can create expense groups.")


# ---------------------------------------------------------------------------
# GET /expense/  — my expense groups
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def expense_home(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    groups = get_my_groups(db, current_user["user_id"])

    # Fetch active users for the member-add picker (creator roles only)
    users_for_picker: list = []
    if current_user.get("role") in _CREATOR_ROLES:
        try:
            from app.models.user import User
            users_for_picker = [
                {"id": u.id, "name": u.name}
                for u in db.query(User).filter(User.is_active == 1).order_by(User.name).all()
            ]
        except Exception:
            pass

    return templates.TemplateResponse(
        "expense/index.html",
        {
            "request":          request,
            "current_user":     current_user,
            "groups":           groups,
            "users_for_picker": users_for_picker,
            "flash":            request.query_params.get("flash"),
            "error":            request.query_params.get("error"),
        },
    )


# ---------------------------------------------------------------------------
# POST /expense/create — create group (admin/manager/team_lead)
# ---------------------------------------------------------------------------

_MAX_AMOUNT = 1_000_000.00


@router.post("/create")
def create_group(
    title: str = Form(...),
    total_amount: float = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    _require_creator_role(current_user)

    # ── Backend validation (defence-in-depth, mirrors frontend + service) ──
    if total_amount <= 0:
        return RedirectResponse(
            f"/expense/?error={quote('Amount must be greater than zero.')}",
            status_code=303,
        )
    if total_amount > _MAX_AMOUNT:
        return RedirectResponse(
            f"/expense/?error={quote(f'Amount cannot exceed ₹{_MAX_AMOUNT:,.2f}. Enter a realistic value.')}",
            status_code=303,
        )

    try:
        group = create_expense_group(
            db=db,
            title=title,
            total_amount=total_amount,
            created_by=current_user["user_id"],
        )
    except ExpenseError as exc:
        return RedirectResponse(f"/expense/?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/expense/{group.id}?flash=created", status_code=303)


# ---------------------------------------------------------------------------
# GET /expense/{group_id} — group detail
# ---------------------------------------------------------------------------

@router.get("/{group_id}", response_class=HTMLResponse)
def group_detail(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    detail = get_group_detail(db, group_id, current_user["user_id"])
    if detail is None:
        raise HTTPException(status_code=404, detail="Expense group not found or access denied.")

    # Active users for adding members (only creator sees this)
    users_for_picker: list = []
    if detail["created_by"] == current_user["user_id"]:
        try:
            from app.models.user import User
            existing_ids = {m["user_id"] for m in detail["members"]}
            users_for_picker = [
                {"id": u.id, "name": u.name}
                for u in db.query(User).filter(User.is_active == 1).order_by(User.name).all()
                if u.id not in existing_ids
            ]
        except Exception:
            pass

    return templates.TemplateResponse(
        "expense/detail.html",
        {
            "request":          request,
            "current_user":     current_user,
            "group":            detail,
            "users_for_picker": users_for_picker,
            "flash":            request.query_params.get("flash"),
            "error":            request.query_params.get("error"),
        },
    )


# ---------------------------------------------------------------------------
# POST /expense/{group_id}/add-members
# ---------------------------------------------------------------------------

@router.post("/{group_id}/add-members")
def add_group_members(
    group_id: int,
    user_ids_raw: str = Form(...),   # comma-separated
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        ids = [int(x.strip()) for x in user_ids_raw.split(",") if x.strip()]
        if not ids:
            raise ExpenseError("No user IDs provided.")
        add_members(db, group_id, ids, current_user["user_id"])

        # ── Notify each new member ────────────────────────────────────────────
        try:
            detail = get_group_detail(db, group_id, current_user["user_id"])
            group_title = detail["title"] if detail else f"Group #{group_id}"
            for uid in ids:
                _notif(
                    db, uid, "expense",
                    f"💰 You have been added to expense group: {group_title}",
                    entity_id=group_id,
                    actor_id=current_user["user_id"],
                )
        except Exception:
            pass
    except (ExpenseError, ValueError) as exc:
        return RedirectResponse(
            f"/expense/{group_id}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/expense/{group_id}?flash=members_added", status_code=303)


# ---------------------------------------------------------------------------
# POST /expense/{group_id}/pay — mark own share as paid
# ---------------------------------------------------------------------------

@router.post("/{group_id}/pay")
def pay_share(
    group_id: int,
    target_user_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(login_required),
):
    try:
        mark_paid(db, group_id, target_user_id, current_user["user_id"])
    except ExpenseError as exc:
        return RedirectResponse(
            f"/expense/{group_id}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/expense/{group_id}?flash=paid", status_code=303)
