"""
Dashboard analytics service.

All public functions are independently safe: each wraps its own logic in
try/except and returns a typed default so one failing metric never blocks
the others or crashes the dashboard.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.break_record import BreakRecord, BreakStatus
from app.models.task import Task, TaskStatus
from app.models.user import User

try:
    from app.services.hierarchy_service import apply_hierarchy_filter
except ImportError:
    apply_hierarchy_filter = None

# ---------------------------------------------------------------------------
# Thresholds — change here, nowhere else
# ---------------------------------------------------------------------------
LATE_THRESHOLD_HOUR: int = 9
LATE_THRESHOLD_MINUTE: int = 30
BREAK_ALERT_HOURS: float = 1.0       # >60 min of breaks in a day = alert
NOT_CLOCKED_IN_ALERT_HOUR: int = 10  # only fire "not clocked in" alert after 10 AM


# ---------------------------------------------------------------------------
# 1. Employee personal performance (current calendar week)
# ---------------------------------------------------------------------------

def get_employee_performance(db: Session, employee_id: int) -> dict:
    """
    Returns personal performance metrics for the current Mon–today window.
    Keys: week_hours, avg_daily_hours, days_worked,
          tasks_assigned, tasks_completed, task_completion_rate, break_percentage
    Returns {} on any exception.
    """
    try:
        today = date.today()
        now = datetime.now()
        week_start = today - timedelta(days=today.weekday())  # Monday

        records = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == employee_id,
                Attendance.date >= week_start,
                Attendance.date <= today,
            )
            .all()
        )

        week_hours = 0.0
        week_break_hours = 0.0
        days_worked = 0

        for rec in records:
            if not rec.clock_in_time:
                continue
            days_worked += 1
            brk = rec.total_break_hours or 0.0
            if rec.total_hours is not None:          # day is completed
                week_hours += rec.total_hours
                week_break_hours += brk
            else:                                    # day in progress (today)
                elapsed = (now - rec.clock_in_time).total_seconds() / 3600
                week_hours += max(elapsed - brk, 0.0)
                week_break_hours += brk

        avg_daily_hours = round(week_hours / days_worked, 1) if days_worked > 0 else 0.0

        # Personal task stats (tasks assigned *to* this user, all time)
        tasks = db.query(Task).filter(Task.assigned_to == employee_id).all()
        tasks_assigned = len(tasks)
        tasks_completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
        task_completion_rate = (
            round(tasks_completed / tasks_assigned * 100)
            if tasks_assigned > 0
            else 0
        )

        # Break % = break_hours / (active_hours + break_hours)
        total_with_break = week_hours + week_break_hours
        break_percentage = (
            round(week_break_hours / total_with_break * 100, 1)
            if total_with_break > 0
            else 0.0
        )

        return {
            "week_hours": round(week_hours, 1),
            "avg_daily_hours": avg_daily_hours,
            "days_worked": days_worked,
            "tasks_assigned": tasks_assigned,
            "tasks_completed": tasks_completed,
            "task_completion_rate": task_completion_rate,
            "break_percentage": break_percentage,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 2. Manager / admin team headcount snapshot (today)
# ---------------------------------------------------------------------------

def get_manager_insights(db: Session, request_user: dict | None = None) -> dict:
    """
    Returns team attendance snapshot for today.
    Keys: total_employees, present_today, absent_today, late_clock_ins, on_break
    Returns {} on any exception.
    """
    try:
        today = date.today()

        employees = db.query(User).filter(User.is_active == 1).all()
        if request_user and apply_hierarchy_filter:
            employees = apply_hierarchy_filter(db, request_user, employees)
        total_employees = len(employees)


        today_records = (
            db.query(Attendance)
            .filter(
                Attendance.date == today,
                Attendance.clock_in_time.isnot(None),
            )
            .all()
        )
        if request_user and apply_hierarchy_filter:
            today_records = apply_hierarchy_filter(db, request_user, today_records)

        present_today = len(today_records)
        absent_today = max(total_employees - present_today, 0)

        late_clock_ins = sum(
            1
            for r in today_records
            if r.clock_in_time is not None and (
                r.clock_in_time.hour > LATE_THRESHOLD_HOUR
                or (
                    r.clock_in_time.hour == LATE_THRESHOLD_HOUR
                    and r.clock_in_time.minute > LATE_THRESHOLD_MINUTE
                )
            )
        )

        # Currently on break = has an active break record today
        on_break = (
            db.query(BreakRecord)
            .filter(
                BreakRecord.status == BreakStatus.active,
            )
            .count()
        )

        return {
            "total_employees": total_employees,
            "present_today": present_today,
            "absent_today": absent_today,
            "late_clock_ins": late_clock_ins,
            "on_break": on_break,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 3. Overdue task count (additive to existing task_stats)
# ---------------------------------------------------------------------------

def get_overdue_count(db: Session) -> int:
    """Returns the number of non-completed tasks past their due date."""
    try:
        return (
            db.query(Task)
            .filter(
                Task.due_date.isnot(None),
                Task.due_date < date.today(),
                Task.status != TaskStatus.completed,
            )
            .count()
        )
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 4. Alert list
# ---------------------------------------------------------------------------

def get_alerts(db: Session, role: str, user_id: int) -> list[dict]:
    """
    Returns a list of alert dicts: {"type": "warning"|"danger", "message": str}
    Never raises; returns [] on failure.
    """
    alerts: list[dict] = []
    try:
        today = date.today()
        now = datetime.now()

        # ── Manager / admin alerts ──────────────────────────────────────────
        if role in ("admin", "manager"):

            # 1. Employees not clocked in (only relevant after 10 AM)
            if now.hour >= NOT_CLOCKED_IN_ALERT_HOUR:
                clocked_in_ids = {
                    r.employee_id
                    for r in db.query(Attendance)
                    .filter(
                        Attendance.date == today,
                        Attendance.clock_in_time.isnot(None),
                    )
                    .all()
                }
                missing = (
                    db.query(User)
                    .filter(
                        User.is_active == 1,
                        User.id.notin_(clocked_in_ids),
                    )
                    .order_by(User.name)
                    .limit(5)       # cap to avoid wall-of-text
                    .all()
                )
                for u in missing:
                    alerts.append({
                        "type": "warning",
                        "icon": "⏰",
                        "message": f"{u.name} has not clocked in today.",
                    })

            # 2. Overdue tasks
            overdue = (
                db.query(Task)
                .filter(
                    Task.due_date.isnot(None),
                    Task.due_date < today,
                    Task.status != TaskStatus.completed,
                )
                .count()
            )
            if overdue > 0:
                word = "tasks are" if overdue > 1 else "task is"
                alerts.append({
                    "type": "danger",
                    "icon": "🔴",
                    "message": f"{overdue} {word} overdue.",
                })

        # ── Personal alerts (all roles) ─────────────────────────────────────

        # 3. Excessive break time today
        today_att = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == user_id,
                Attendance.date == today,
            )
            .first()
        )
        if today_att and (today_att.total_break_hours or 0.0) > BREAK_ALERT_HOURS:
            minutes = round((today_att.total_break_hours or 0.0) * 60)
            alerts.append({
                "type": "warning",
                "icon": "☕",
                "message": f"Your break time today is {minutes} min — above the recommended limit.",
            })

    except Exception:
        pass  # return whatever was built before the error

    return alerts


# ---------------------------------------------------------------------------
# 5. Team performance — weekly hours + task stats per employee
# ---------------------------------------------------------------------------

def get_team_performance(db: Session, request_user: dict | None = None) -> list[dict]:
    """
    Returns a list of performance dicts for every active employee, covering
    the current Mon–today window.

    Each dict has:
      id, name, department, week_hours, completed_tasks,
      total_tasks, task_completion_rate, performance_score

    performance_score (0–100):
      60% weight on task_completion_rate + 40% weight on hours (capped at 40 h/week)

    Returns [] on any exception.
    """
    try:
        today = date.today()
        now = datetime.now()
        week_start = today - timedelta(days=today.weekday())  # Monday

        employees = (
            db.query(User)
            .filter(User.is_active == 1)
            .order_by(User.name)
            .all()
        )

        result: list[dict] = []

        for emp in employees:
            # ── hours this week ──────────────────────────────────────────
            att_records = (
                db.query(Attendance)
                .filter(
                    Attendance.employee_id == emp.id,
                    Attendance.date >= week_start,
                    Attendance.date <= today,
                )
                .all()
            )

            week_hours = 0.0
            for rec in att_records:
                if not rec.clock_in_time:
                    continue
                brk = rec.total_break_hours or 0.0
                if rec.total_hours is not None:
                    week_hours += rec.total_hours
                else:
                    elapsed = (now - rec.clock_in_time).total_seconds() / 3600
                    week_hours += max(elapsed - brk, 0.0)

            # ── task stats (all-time) ────────────────────────────────────
            tasks = db.query(Task).filter(Task.assigned_to == emp.id).all()
            total_tasks = len(tasks)
            completed_tasks = sum(1 for t in tasks if t.status == TaskStatus.completed)
            task_completion_rate = (
                round(completed_tasks / total_tasks * 100)
                if total_tasks > 0
                else 0
            )

            # ── composite performance score ──────────────────────────────
            # Hours component: % of a 40-h target (capped at 100)
            hours_score = min(round(week_hours / 40 * 100), 100)
            performance_score = round(
                task_completion_rate * 0.60 + hours_score * 0.40
            )

            result.append({
                "id": emp.id,
                "name": emp.name,
                "department": emp.department or "—",
                "week_hours": round(week_hours, 1),
                "completed_tasks": completed_tasks,
                "total_tasks": total_tasks,
                "task_completion_rate": task_completion_rate,
                "performance_score": performance_score,
            })

        if request_user and apply_hierarchy_filter:
            result = apply_hierarchy_filter(db, request_user, result)

        # Sort by performance_score descending
        result.sort(key=lambda x: x["performance_score"], reverse=True)
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 6. Low performers — employees with low hours OR low task completion
# ---------------------------------------------------------------------------

# Tuneable thresholds
_LOW_HOURS_THRESHOLD: float = 20.0   # < 20 h/week
_LOW_TASK_RATE: int = 40             # < 40 % task completion


def get_low_performers(db: Session, request_user: dict | None = None) -> list[dict]:
    """
    Returns a subset of get_team_performance() entries that meet at least one
    low-performance criterion.

    Each dict has the same keys as get_team_performance() plus:
      low_hours (bool), low_tasks (bool)

    Returns [] on any exception.
    """
    try:
        team = get_team_performance(db, request_user=request_user)
        low: list[dict] = []
        for member in team:
            low_hours = member["week_hours"] < _LOW_HOURS_THRESHOLD
            low_tasks = (
                member["total_tasks"] > 0
                and member["task_completion_rate"] < _LOW_TASK_RATE
            )
            if low_hours or low_tasks:
                low.append({
                    **member,
                    "low_hours": low_hours,
                    "low_tasks": low_tasks,
                })
        return low
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 7. Task distribution — org-wide breakdown
# ---------------------------------------------------------------------------

def get_task_distribution(db: Session, request_user: dict | None = None) -> dict:
    """
    Returns org-wide task status counts.

    Keys: total, completed, pending, in_progress, overdue
    Returns safe zeros dict on any exception.
    """
    _safe = {"total": 0, "completed": 0, "pending": 0, "in_progress": 0, "overdue": 0}
    try:
        all_tasks = db.query(Task).all()
        if request_user and apply_hierarchy_filter:
            all_tasks = apply_hierarchy_filter(db, request_user, all_tasks)
        today = date.today()

        total = len(all_tasks)
        completed = sum(1 for t in all_tasks if t.status == TaskStatus.completed)
        pending = sum(1 for t in all_tasks if t.status == TaskStatus.pending)
        in_progress = sum(1 for t in all_tasks if t.status == TaskStatus.in_progress)
        overdue = sum(
            1 for t in all_tasks
            if t.due_date is not None
            and t.due_date < today
            and t.status != TaskStatus.completed
        )

        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "in_progress": in_progress,
            "overdue": overdue,
        }
    except Exception:
        return _safe
