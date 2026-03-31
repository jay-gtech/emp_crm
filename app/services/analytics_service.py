"""
Analytics Service
=================
Read-only analytics layer that queries existing tables.

Rules:
  * NEVER writes to the database
  * NEVER imports from other services (avoids circular deps)
  * Every public function is independently guarded by try/except
  * Returns typed safe-defaults on any failure
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy model imports — keep them inside functions to avoid circular-import
# issues if models are ever restructured.
# ---------------------------------------------------------------------------


def _models():
    """Return a namespace of the models we need."""
    from app.models.attendance import Attendance
    from app.models.task import Task, TaskStatus
    from app.models.leave import Leave, LeaveStatus
    from app.models.audit_log import AuditLog
    from app.models.user import User
    return Attendance, Task, TaskStatus, Leave, LeaveStatus, AuditLog, User


# ---------------------------------------------------------------------------
# 1. Attendance Trends  (last 30 days, daily check-in count)
# ---------------------------------------------------------------------------

def get_attendance_trends(db: Session, days: int = 30) -> dict:
    """
    Returns daily attendance counts for the last *days* days.

    Shape:
        {
          "labels": ["2024-03-01", ...],
          "present": [12, 14, ...],
          "remote":  [3, 2, ...],
        }
    """
    try:
        Attendance, *_ = _models()
        since = date.today() - timedelta(days=days)

        rows = (
            db.query(Attendance)
            .filter(Attendance.date >= since)
            .all()
        )

        office_counts: dict[str, int] = defaultdict(int)
        remote_counts: dict[str, int] = defaultdict(int)

        for r in rows:
            day = r.date.isoformat() if r.date else None
            if not day:
                continue
            if r.work_mode and r.work_mode.value == "remote":
                remote_counts[day] += 1
            else:
                office_counts[day] += 1

        # Build a continuous date range so the chart has no gaps
        labels: list[str] = []
        present: list[int] = []
        remote: list[int] = []
        cursor = since
        while cursor <= date.today():
            key = cursor.isoformat()
            labels.append(key)
            present.append(office_counts.get(key, 0))
            remote.append(remote_counts.get(key, 0))
            cursor += timedelta(days=1)

        return {"labels": labels, "present": present, "remote": remote}

    except Exception as exc:
        logger.error("analytics.get_attendance_trends failed: %s", exc)
        return {"labels": [], "present": [], "remote": []}


# ---------------------------------------------------------------------------
# 2. Task Trends  (created vs completed per week, last 8 weeks)
# ---------------------------------------------------------------------------

def get_task_trends(db: Session, weeks: int = 8) -> dict:
    """
    Returns weekly task creation and completion counts.

    Shape:
        {
          "labels":    ["W1", "W2", ...],
          "created":   [5, 3, ...],
          "completed": [2, 4, ...],
        }
    """
    try:
        _, Task, TaskStatus, *_ = _models()
        since = date.today() - timedelta(weeks=weeks)

        all_tasks = (
            db.query(Task)
            .filter(Task.created_at >= since)
            .all()
        )

        created_by_week: dict[int, int] = defaultdict(int)
        completed_by_week: dict[int, int] = defaultdict(int)

        for t in all_tasks:
            if not t.created_at:
                continue
            task_date = t.created_at.date() if hasattr(t.created_at, "date") else t.created_at
            delta_days = (date.today() - task_date).days
            week_idx = min(delta_days // 7, weeks - 1)  # 0 = most recent week
            created_by_week[week_idx] += 1
            if t.status == TaskStatus.completed:
                completed_by_week[week_idx] += 1

        labels = [f"W-{weeks - i}" for i in range(weeks)]
        created = [created_by_week.get(i, 0) for i in range(weeks - 1, -1, -1)]
        completed = [completed_by_week.get(i, 0) for i in range(weeks - 1, -1, -1)]

        return {"labels": labels, "created": created, "completed": completed}

    except Exception as exc:
        logger.error("analytics.get_task_trends failed: %s", exc)
        return {"labels": [], "created": [], "completed": []}


# ---------------------------------------------------------------------------
# 3. Leave Trends  (monthly leave days taken, last 6 months)
# ---------------------------------------------------------------------------

def get_leave_trends(db: Session, months: int = 6) -> dict:
    """
    Returns per-month totals of approved leave days for the last *months* months.

    Shape:
        {
          "labels":  ["Oct 2024", ...],
          "days":    [12, 8, ...],
          "by_type": {"casual": [...], "sick": [...], ...},
        }
    """
    try:
        _, _, _, Leave, LeaveStatus, *_ = _models()
        today = date.today()

        # Build month boundaries
        month_labels: list[str] = []
        month_keys: list[tuple[int, int]] = []   # (year, month)
        for offset in range(months - 1, -1, -1):
            # Step back offset months
            m = (today.month - 1 - offset) % 12 + 1
            y = today.year + ((today.month - 1 - offset) // 12)
            month_labels.append(date(y, m, 1).strftime("%b %Y"))
            month_keys.append((y, m))

        approved = (
            db.query(Leave)
            .filter(Leave.status == LeaveStatus.approved)
            .all()
        )

        leave_types = ["casual", "sick", "annual", "unpaid"]
        totals: dict[tuple[int, int], int] = defaultdict(int)
        by_type: dict[str, dict[tuple[int, int], int]] = {
            lt: defaultdict(int) for lt in leave_types
        }

        for lv in approved:
            if not lv.start_date:
                continue
            key = (lv.start_date.year, lv.start_date.month)
            if key not in month_keys:
                continue
            days = lv.total_days or 0
            totals[key] += days
            lt_val = lv.leave_type.value if hasattr(lv.leave_type, "value") else str(lv.leave_type)
            if lt_val in by_type:
                by_type[lt_val][key] += days

        return {
            "labels": month_labels,
            "days": [totals.get(k, 0) for k in month_keys],
            "by_type": {
                lt: [by_type[lt].get(k, 0) for k in month_keys]
                for lt in leave_types
            },
        }

    except Exception as exc:
        logger.error("analytics.get_leave_trends failed: %s", exc)
        return {"labels": [], "days": [], "by_type": {}}


# ---------------------------------------------------------------------------
# 4. Employee Performance  (per-user stats)
# ---------------------------------------------------------------------------

def get_employee_performance(db: Session, user_id: int) -> dict:
    """
    Returns performance metrics for a single employee.

    Shape:
        {
          "task_completion_rate": 75.0,   # %
          "tasks_total": 20,
          "tasks_completed": 15,
          "tasks_overdue": 2,
          "attendance_days": 18,
          "avg_hours": 7.5,
          "leave_days_taken": 3,
        }
    """
    try:
        Attendance, Task, TaskStatus, Leave, LeaveStatus, *_ = _models()

        # Tasks
        tasks = db.query(Task).filter(Task.assigned_to == user_id).all()
        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
        today = date.today()
        overdue = sum(
            1 for t in tasks
            if t.status != TaskStatus.completed
            and t.due_date
            and t.due_date < today
        )
        rate = round((completed / total) * 100, 1) if total else 0.0

        # Attendance (current month)
        first_of_month = today.replace(day=1)
        att_rows = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == user_id,
                Attendance.date >= first_of_month,
            )
            .all()
        )
        att_days = len(att_rows)
        hours_list = [a.total_hours for a in att_rows if a.total_hours is not None]
        avg_hours = round(sum(hours_list) / len(hours_list), 2) if hours_list else 0.0

        # Leave (current year)
        approved_leave = (
            db.query(Leave)
            .filter(
                Leave.employee_id == user_id,
                Leave.status == LeaveStatus.approved,
                Leave.start_date >= date(today.year, 1, 1),
            )
            .all()
        )
        leave_days = sum(lv.total_days or 0 for lv in approved_leave)

        return {
            "task_completion_rate": rate,
            "tasks_total": total,
            "tasks_completed": completed,
            "tasks_overdue": overdue,
            "attendance_days": att_days,
            "avg_hours": avg_hours,
            "leave_days_taken": leave_days,
        }

    except Exception as exc:
        logger.error("analytics.get_employee_performance(%s) failed: %s", user_id, exc)
        return {
            "task_completion_rate": 0.0,
            "tasks_total": 0,
            "tasks_completed": 0,
            "tasks_overdue": 0,
            "attendance_days": 0,
            "avg_hours": 0.0,
            "leave_days_taken": 0,
        }


# ---------------------------------------------------------------------------
# 5. Team Comparison  (all active employees, this month's stats)
# ---------------------------------------------------------------------------

def get_team_comparison(db: Session) -> list[dict]:
    """
    Returns a list of per-employee summary rows, sorted by task completion rate desc.

    Shape:
        [
          {
            "name": "Alice",
            "role": "employee",
            "task_completion_rate": 80.0,
            "attendance_days": 15,
            "leave_days": 2,
          },
          ...
        ]
    """
    try:
        Attendance, Task, TaskStatus, Leave, LeaveStatus, _, User = _models()

        users = db.query(User).filter(User.is_active == 1).all()
        today = date.today()
        first_of_month = today.replace(day=1)

        result = []
        for u in users:
            try:
                tasks = db.query(Task).filter(Task.assigned_to == u.id).all()
                total = len(tasks)
                completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
                rate = round((completed / total) * 100, 1) if total else 0.0

                att_days = (
                    db.query(Attendance)
                    .filter(
                        Attendance.employee_id == u.id,
                        Attendance.date >= first_of_month,
                    )
                    .count()
                )

                leave_days = sum(
                    lv.total_days or 0
                    for lv in db.query(Leave).filter(
                        Leave.employee_id == u.id,
                        Leave.status == LeaveStatus.approved,
                        Leave.start_date >= date(today.year, 1, 1),
                    ).all()
                )

                result.append({
                    "name": u.name,
                    "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                    "task_completion_rate": rate,
                    "attendance_days": att_days,
                    "leave_days": leave_days,
                })
            except Exception:
                continue  # skip one bad user; continue with the rest

        result.sort(key=lambda x: x["task_completion_rate"], reverse=True)
        return result

    except Exception as exc:
        logger.error("analytics.get_team_comparison failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 6. Summary KPIs  (single dict for the top-of-page cards)
# ---------------------------------------------------------------------------

def get_summary_kpis(db: Session) -> dict:
    """
    Returns org-wide headline numbers for the KPI cards.

    Shape:
        {
          "total_employees": 42,
          "present_today":   38,
          "open_tasks":      17,
          "pending_leaves":   5,
          "avg_task_completion": 68.4,
        }
    """
    try:
        Attendance, Task, TaskStatus, Leave, LeaveStatus, _, User = _models()

        total_employees = db.query(User).filter(User.is_active == 1).count()

        present_today = (
            db.query(Attendance)
            .filter(Attendance.date == date.today())
            .count()
        )

        open_tasks = (
            db.query(Task)
            .filter(Task.status != TaskStatus.completed)
            .count()
        )

        total_tasks = db.query(Task).count()
        completed_tasks = (
            db.query(Task)
            .filter(Task.status == TaskStatus.completed)
            .count()
        )
        avg_completion = round((completed_tasks / total_tasks) * 100, 1) if total_tasks else 0.0

        pending_leaves = (
            db.query(Leave)
            .filter(Leave.status == LeaveStatus.pending)
            .count()
        )

        attendance_rate = (
            round((present_today / total_employees) * 100, 1)
            if total_employees else 0.0
        )

        return {
            "total_employees": total_employees,
            "present_today": present_today,
            "open_tasks": open_tasks,
            "pending_leaves": pending_leaves,
            "avg_task_completion": avg_completion,
            "attendance_rate": attendance_rate,
        }

    except Exception as exc:
        logger.error("analytics.get_summary_kpis failed: %s", exc)
        return {
            "total_employees": 0,
            "present_today": 0,
            "open_tasks": 0,
            "pending_leaves": 0,
            "avg_task_completion": 0.0,
            "attendance_rate": 0.0,
        }
