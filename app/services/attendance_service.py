from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from app.models.attendance import Attendance, WorkMode


class AttendanceError(Exception):
    pass


def _today() -> date:
    return datetime.now().date()


def _now() -> datetime:
    return datetime.now()


def get_today_record(db: Session, employee_id: int) -> Attendance | None:
    return (
        db.query(Attendance)
        .filter(Attendance.employee_id == employee_id, Attendance.date == _today())
        .first()
    )


def clock_in(db: Session, employee_id: int, work_mode: str = "office") -> Attendance:
    if _today().weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        raise AttendanceError("Clock-in is not allowed on weekends.")
    existing = get_today_record(db, employee_id)
    if existing and existing.clock_in_time:
        raise AttendanceError("Already clocked in today.")

    try:
        mode = WorkMode(work_mode)
    except ValueError:
        mode = WorkMode.office

    if existing:
        existing.clock_in_time = _now()
        existing.work_mode = mode
        db.commit()
        db.refresh(existing)
        return existing

    record = Attendance(
        employee_id=employee_id,
        date=_today(),
        clock_in_time=_now(),
        work_mode=mode,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def clock_out(db: Session, employee_id: int) -> Attendance:
    record = get_today_record(db, employee_id)
    if not record or not record.clock_in_time:
        raise AttendanceError("You have not clocked in today.")
    if record.clock_out_time:
        raise AttendanceError("Already clocked out today.")

    record.clock_out_time = _now()
    elapsed = (record.clock_out_time - record.clock_in_time).total_seconds() / 3600
    break_hours = record.total_break_hours or 0.0
    record.total_hours = round(max(elapsed - break_hours, 0), 2)
    db.commit()
    db.refresh(record)
    return record


def get_attendance_history(
    db: Session,
    employee_id: int,
    limit: int = 30,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Attendance]:
    q = db.query(Attendance).filter(Attendance.employee_id == employee_id)
    if date_from:
        q = q.filter(Attendance.date >= date_from)
    if date_to:
        q = q.filter(Attendance.date <= date_to)
    return q.order_by(Attendance.date.desc()).limit(limit).all()


def get_all_attendance_today(db: Session) -> list[Attendance]:
    return (
        db.query(Attendance)
        .filter(Attendance.date == _today())
        .all()
    )
