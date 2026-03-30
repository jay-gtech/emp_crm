from datetime import datetime
from sqlalchemy.orm import Session
from app.models.break_record import BreakRecord, BreakStatus
from app.models.attendance import Attendance
from app.services.attendance_service import get_today_record


class BreakError(Exception):
    pass


def _now() -> datetime:
    return datetime.now()


def _get_active_break(db: Session, employee_id: int) -> BreakRecord | None:
    return (
        db.query(BreakRecord)
        .filter(
            BreakRecord.employee_id == employee_id,
            BreakRecord.status == BreakStatus.active,
        )
        .first()
    )


def start_break(db: Session, employee_id: int) -> BreakRecord:
    attendance = get_today_record(db, employee_id)
    if not attendance or not attendance.clock_in_time:
        raise BreakError("You must clock in before taking a break.")
    if attendance.clock_out_time:
        raise BreakError("You have already clocked out.")

    if _get_active_break(db, employee_id):
        raise BreakError("You already have an active break. End it before starting a new one.")

    record = BreakRecord(
        employee_id=employee_id,
        attendance_id=attendance.id,
        start_time=_now(),
        status=BreakStatus.active,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def end_break(db: Session, employee_id: int) -> BreakRecord:
    record = _get_active_break(db, employee_id)
    if not record:
        raise BreakError("No active break found.")

    record.end_time = _now()
    duration = (record.end_time - record.start_time).total_seconds() / 60
    record.duration_minutes = round(duration)
    record.status = BreakStatus.completed

    # Update cumulative break hours on the attendance record
    attendance = db.query(Attendance).filter(Attendance.id == record.attendance_id).first()
    if attendance:
        current = attendance.total_break_hours or 0.0
        attendance.total_break_hours = round(current + duration / 60, 4)

    db.commit()
    db.refresh(record)
    return record


def get_today_breaks(db: Session, employee_id: int) -> list[BreakRecord]:
    attendance = get_today_record(db, employee_id)
    if not attendance:
        return []
    return (
        db.query(BreakRecord)
        .filter(BreakRecord.attendance_id == attendance.id)
        .order_by(BreakRecord.start_time)
        .all()
    )


def get_active_break(db: Session, employee_id: int) -> BreakRecord | None:
    return _get_active_break(db, employee_id)
