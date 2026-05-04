from datetime import date
from sqlalchemy.orm import Session
from app.models.leave import Leave, LeaveType, LeaveStatus
from app.core.config import settings


class LeaveError(Exception):
    pass


def _count_days(start: date, end: date) -> int:
    if end < start:
        raise LeaveError("End date must be on or after start date.")
    return (end - start).days + 1


def apply_leave(
    db: Session,
    employee_id: int,
    leave_type: str,
    start_date: date,
    end_date: date,
    reason: str | None = None,
) -> Leave:
    try:
        lt = LeaveType(leave_type)
    except ValueError:
        raise LeaveError(f"Invalid leave type: {leave_type}")

    total_days = _count_days(start_date, end_date)

    # Enforce annual leave quota (pending + pending_manager + approved all count)
    current_year = start_date.year
    existing = (
        db.query(Leave)
        .filter(
            Leave.employee_id == employee_id,
            Leave.status.in_([
                LeaveStatus.pending,
                LeaveStatus.pending_manager,
                LeaveStatus.approved,
            ]),
            Leave.start_date >= date(current_year, 1, 1),
            Leave.end_date <= date(current_year, 12, 31),
        )
        .all()
    )
    used_days = sum(l.total_days for l in existing)
    quota = settings.LEAVE_ANNUAL_QUOTA
    if used_days + total_days > quota:
        remaining = max(quota - used_days, 0)
        raise LeaveError(
            f"Insufficient leave balance. You have {remaining} day(s) remaining "
            f"out of the annual quota of {quota}."
        )

    # Check for overlapping leaves in any active state
    overlap = (
        db.query(Leave)
        .filter(
            Leave.employee_id == employee_id,
            Leave.status.in_([
                LeaveStatus.pending,
                LeaveStatus.pending_manager,
                LeaveStatus.approved,
            ]),
            Leave.start_date <= end_date,
            Leave.end_date >= start_date,
        )
        .first()
    )
    if overlap:
        raise LeaveError("You already have a leave request overlapping these dates.")

    leave = Leave(
        employee_id=employee_id,
        leave_type=lt,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        reason=reason,
        status=LeaveStatus.pending,
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def get_leave_balance(db: Session, employee_id: int, year: int | None = None) -> dict:
    """Returns used/remaining days. Quota is a fixed annual constant."""
    current_year = year or date.today().year
    used = (
        db.query(Leave)
        .filter(
            Leave.employee_id == employee_id,
            Leave.status == LeaveStatus.approved,
            Leave.start_date >= date(current_year, 1, 1),
            Leave.end_date <= date(current_year, 12, 31),
        )
        .all()
    )
    used_days = sum(l.total_days for l in used)
    quota = settings.LEAVE_ANNUAL_QUOTA
    return {
        "quota": quota,
        "used": used_days,
        "remaining": max(quota - used_days, 0),
        "year": current_year,
    }


def review_leave(
    db: Session,
    leave_id: int,
    reviewer_id: int,
    action: str,
    note: str | None = None,
) -> Leave:
    leave = db.query(Leave).filter(Leave.id == leave_id).first()
    if not leave:
        raise LeaveError("Leave request not found.")

    if leave.status == LeaveStatus.pending:
        if action not in ("approved", "rejected", "forward"):
            raise LeaveError("Action must be 'approved', 'rejected', or 'forward'.")
        if action == "forward":
            leave.status = LeaveStatus.pending_manager
        else:
            leave.status = LeaveStatus(action)

    elif leave.status == LeaveStatus.pending_manager:
        if action not in ("approved", "rejected"):
            raise LeaveError("Manager action must be 'approved' or 'rejected'.")
        leave.status = LeaveStatus(action)

    else:
        raise LeaveError("Only pending or pending_manager leave requests can be reviewed.")

    leave.reviewed_by = reviewer_id
    leave.review_note = note
    db.commit()
    db.refresh(leave)
    return leave


def list_leaves_for_employee(
    db: Session,
    employee_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[Leave]:
    q = (
        db.query(Leave)
        .filter(Leave.employee_id == employee_id)
        .order_by(Leave.created_at.desc())
    )
    if offset > 0:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def list_pending_leaves(db: Session) -> list[Leave]:
    return (
        db.query(Leave)
        .filter(Leave.status == LeaveStatus.pending)
        .order_by(Leave.created_at.asc())
        .all()
    )


def list_all_leaves(
    db: Session,
    limit: int | None = None,
    offset: int = 0,
) -> list[Leave]:
    q = db.query(Leave).order_by(Leave.created_at.desc())
    if offset > 0:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    return q.all()
