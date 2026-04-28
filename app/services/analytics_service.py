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

from sqlalchemy import func
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

        # Tasks — query via task_assignments (single source of truth)
        from app.models.task import TaskAssignment as _TA
        _assignments = db.query(_TA).filter(_TA.user_id == user_id).all()
        total = len(_assignments)
        completed = sum(1 for a in _assignments if a.status.value == "completed")
        today = date.today()
        # For overdue we still need the task's due_date — join to Task
        overdue = (
            db.query(_TA)
            .join(Task, _TA.task_id == Task.id)
            .filter(
                _TA.user_id == user_id,
                _TA.status != "completed",
                Task.due_date < today,
            )
            .count()
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
                from app.models.task import TaskAssignment as _TA
                _assignments = db.query(_TA).filter(_TA.user_id == u.id).all()
                total = len(_assignments)
                completed = sum(1 for a in _assignments if a.status.value == "completed")
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
                    "user_id": u.id,
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

        # Deduplicate by employee_id and require a clock-in so the count
        # never exceeds total_employees (guards against duplicate records
        # or attendance rows for inactive users).
        present_today = (
            db.query(func.count(func.distinct(Attendance.employee_id)))
            .filter(
                Attendance.date == date.today(),
                Attendance.clock_in_time.isnot(None),
            )
            .scalar() or 0
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


# ---------------------------------------------------------------------------
# AI / ML monitoring helpers
# ---------------------------------------------------------------------------

def _load_assignment_log() -> list[dict]:
    """
    Read assignment_log.jsonl and return all valid JSON lines.
    Returns [] on any I/O error.
    """
    try:
        from app.ml.retraining.utils import LOG_FILE
        if not LOG_FILE.exists():
            return []
        lines = []
        with LOG_FILE.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    import json
                    lines.append(json.loads(raw))
                except Exception:
                    continue
        return lines
    except Exception as exc:
        logger.error("analytics._load_assignment_log failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 7. AI System Metrics  (success rate, delay rate, avg ML probability, etc.)
# ---------------------------------------------------------------------------

def get_ai_system_metrics() -> dict:
    """
    Aggregate ML-assignment health KPIs from the assignment log.

    Deduplicates by task_id (takes last assignment event per task).
    Outcome labels come from 'outcome' and 'outcome_update' events.

    Shape:
        {
          "total_assignments": 42,
          "success_rate":      0.85,
          "delay_rate":        0.10,
          "avg_ml_prob":       0.74,
          "avg_final_score":   83.2,
          "model_available":   true,
          "alerts":            [],
        }
    """
    try:
        lines = _load_assignment_log()

        # Deduplicate: keep last assignment record per task_id
        assignments: dict[int, dict] = {}
        outcomes: dict[int, dict] = {}   # task_id → {success, was_delayed}

        for rec in lines:
            etype = rec.get("event_type", "")
            tid   = rec.get("task_id")
            if tid is None:
                continue

            if etype == "assignment":
                assignments[tid] = rec

            elif etype == "outcome":
                outcomes[tid] = {
                    "success":     bool(rec.get("success", False)),
                    "was_delayed": (rec.get("delay_days", -1) or 0) > 0,
                }

            elif etype == "outcome_update":
                out = rec.get("outcome", {})
                outcomes[tid] = {
                    "success":     bool(out.get("completed", False)) and not bool(out.get("was_delayed", False)),
                    "was_delayed": bool(out.get("was_delayed", False)),
                }

        total = len(assignments)
        if total == 0:
            return {
                "total_assignments": 0,
                "success_rate":      None,
                "delay_rate":        None,
                "avg_ml_prob":       None,
                "avg_final_score":   None,
                "model_available":   False,
                "alerts":            [],
            }

        # Outcome stats — only from tasks that have a recorded outcome
        successes  = sum(1 for o in outcomes.values() if o["success"])
        delayed    = sum(1 for o in outcomes.values() if o["was_delayed"])
        n_outcomes = len(outcomes)
        success_rate = round(successes / n_outcomes, 4) if n_outcomes else None
        delay_rate   = round(delayed   / n_outcomes, 4) if n_outcomes else None

        # ML probability and final score averages (from assignment records)
        ml_probs     = [r.get("ml_probability") for r in assignments.values() if r.get("ml_probability") is not None]
        final_scores = [r.get("final_score")    for r in assignments.values() if r.get("final_score")    is not None]
        avg_ml_prob    = round(sum(ml_probs)     / len(ml_probs),     4) if ml_probs     else None
        avg_final_score= round(sum(final_scores) / len(final_scores), 2) if final_scores else None

        # Model availability
        try:
            from app.ml.training.model import is_model_available
            model_available = is_model_available()
        except Exception:
            model_available = False

        # Alerts
        alerts: list[str] = []
        if success_rate is not None and success_rate < 0.6:
            alerts.append(f"Low success rate: {success_rate:.0%} (threshold 60%)")
        if delay_rate is not None and delay_rate > 0.3:
            alerts.append(f"High delay rate: {delay_rate:.0%} (threshold 30%)")
        if not model_available:
            alerts.append("ML model not trained — assignments using heuristic fallback")

        return {
            "total_assignments": total,
            "success_rate":      success_rate,
            "delay_rate":        delay_rate,
            "avg_ml_prob":       avg_ml_prob,
            "avg_final_score":   avg_final_score,
            "model_available":   model_available,
            "alerts":            alerts,
        }

    except Exception as exc:
        logger.error("analytics.get_ai_system_metrics failed: %s", exc)
        return {
            "total_assignments": 0,
            "success_rate":      None,
            "delay_rate":        None,
            "avg_ml_prob":       None,
            "avg_final_score":   None,
            "model_available":   False,
            "alerts":            [f"Error loading metrics: {exc}"],
        }


# ---------------------------------------------------------------------------
# 8. Workload Distribution  (per-employee active task count from log)
# ---------------------------------------------------------------------------

def get_workload_distribution(db) -> dict:
    """
    Return per-employee task counts from the DB (assigned, completed, overdue).

    Shape:
        {
          "employees": ["Alice", "Bob", ...],
          "active":    [3, 1, ...],
          "completed": [8, 5, ...],
          "overdue":   [0, 1, ...],
        }
    """
    try:
        Attendance, Task, TaskStatus, Leave, LeaveStatus, _, User = _models()
        today = date.today()

        users = db.query(User).filter(User.is_active == 1).all()
        names, active, completed, overdue = [], [], [], []

        for u in users:
            from app.models.task import TaskAssignment as _TA
            _assignments = db.query(_TA).filter(_TA.user_id == u.id).all()
            n_completed = sum(1 for a in _assignments if a.status.value == "completed")
            n_active    = sum(1 for a in _assignments if a.status.value != "completed")
            n_overdue   = (
                db.query(_TA)
                .join(Task, _TA.task_id == Task.id)
                .filter(_TA.user_id == u.id, _TA.status != "completed", Task.due_date < today)
                .count()
            )
            names.append(u.name)
            active.append(n_active)
            completed.append(n_completed)
            overdue.append(n_overdue)

        # Sort by active desc
        order = sorted(range(len(names)), key=lambda i: active[i], reverse=True)
        return {
            "employees": [names[i]     for i in order],
            "active":    [active[i]    for i in order],
            "completed": [completed[i] for i in order],
            "overdue":   [overdue[i]   for i in order],
        }

    except Exception as exc:
        logger.error("analytics.get_workload_distribution failed: %s", exc)
        return {"employees": [], "active": [], "completed": [], "overdue": []}


# ---------------------------------------------------------------------------
# 9. Model Registry Metrics  (from metadata.json)
# ---------------------------------------------------------------------------

def get_model_registry_metrics() -> dict:
    """
    Read metadata.json from the retraining package and return model history.

    Shape:
        {
          "current_version": "v2",
          "total_versions":  3,
          "versions": [
            {
              "version": "v2",
              "status": "active",
              "auc": 0.87,
              "accuracy": 0.91,
              "trained_at": "2026-04-02T10:00:00",
            },
            ...
          ],
        }
    """
    try:
        import json
        from app.ml.retraining.utils import METADATA_FILE
        if not METADATA_FILE.exists():
            return {"current_version": None, "total_versions": 0, "versions": []}

        meta = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        current = meta.get("current_version")
        models  = meta.get("models", {})

        versions = []
        for v, entry in sorted(models.items()):
            m = entry.get("metrics", {})
            versions.append({
                "version":    v,
                "status":     entry.get("status", "?"),
                "auc":        m.get("auc", 0.0),
                "accuracy":   m.get("accuracy", 0.0),
                "f1":         m.get("f1", 0.0),
                "trained_at": (entry.get("trained_at") or "")[:19],
                "n_train":    entry.get("train_meta", {}).get("n_train"),
            })

        return {
            "current_version": current,
            "total_versions":  len(models),
            "versions":        versions,
        }

    except Exception as exc:
        logger.error("analytics.get_model_registry_metrics failed: %s", exc)
        return {"current_version": None, "total_versions": 0, "versions": []}


# ---------------------------------------------------------------------------
# 10. Reason Tag Distribution  (from assignment log)
# ---------------------------------------------------------------------------

def get_reason_tag_distribution() -> dict:
    """
    Count how often each reason_tag appears across deduplicated assignment events.

    Shape:
        {
          "tags":   ["high_ml_confidence", "low_workload", ...],
          "counts": [34, 28, ...],
        }
    """
    try:
        lines = _load_assignment_log()

        # Deduplicate by task_id (last wins) then collect tags
        last_assignment: dict[int, dict] = {}
        for rec in lines:
            if rec.get("event_type") == "assignment":
                tid = rec.get("task_id")
                if tid is not None:
                    last_assignment[tid] = rec

        tag_counts: dict[str, int] = defaultdict(int)
        for rec in last_assignment.values():
            for tag in (rec.get("reason_tags") or []):
                tag_counts[str(tag)] += 1

        # Sort by count desc
        ordered = sorted(tag_counts.items(), key=lambda x: -x[1])
        return {
            "tags":   [t for t, _ in ordered],
            "counts": [c for _, c in ordered],
        }

    except Exception as exc:
        logger.error("analytics.get_reason_tag_distribution failed: %s", exc)
        return {"tags": [], "counts": []}


# ---------------------------------------------------------------------------
# 11. Recent AI Assignments  (last N deduplicated assignment records)
# ---------------------------------------------------------------------------

def get_recent_ai_assignments(limit: int = 20) -> list[dict]:
    """
    Return the most recent deduplicated assignment events for the activity table.

    Shape (list of dicts):
        [
          {
            "task_id":      53,
            "employee_id":  27,
            "final_score":  97.3,
            "ml_prob":      0.986,
            "reason_tags":  ["low_workload", ...],
            "timestamp":    "2026-04-02T09:44:16",
          },
          ...
        ]
    """
    try:
        lines = _load_assignment_log()

        # Keep last assignment per task_id
        last_assignment: dict[int, dict] = {}
        for rec in lines:
            if rec.get("event_type") == "assignment":
                tid = rec.get("task_id")
                if tid is not None:
                    last_assignment[tid] = rec

        # Sort by timestamp desc
        records = sorted(
            last_assignment.values(),
            key=lambda r: r.get("timestamp", ""),
            reverse=True,
        )[:limit]

        result = []
        for r in records:
            result.append({
                "task_id":     r.get("task_id"),
                "employee_id": r.get("employee_id"),
                "final_score": r.get("final_score") or r.get("score"),
                "ml_prob":     r.get("ml_probability"),
                "reason_tags": r.get("reason_tags") or [],
                "timestamp":   (r.get("timestamp") or "")[:19],
            })
        return result

    except Exception as exc:
        logger.error("analytics.get_recent_ai_assignments failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 12. Data Quality Check
# ---------------------------------------------------------------------------

def get_data_quality_check() -> dict:
    """
    Sanity checks on the assignment log for the monitoring dashboard.

    Shape:
        {
          "total_log_lines": 120,
          "assignment_events": 60,
          "outcome_events": 12,
          "duplicate_task_ids": 5,
          "missing_ml_prob": 3,
          "warnings": ["5 tasks have duplicate assignment entries"],
        }
    """
    try:
        lines = _load_assignment_log()

        assignment_lines   = [l for l in lines if l.get("event_type") == "assignment"]
        outcome_lines      = [l for l in lines if l.get("event_type") in ("outcome", "outcome_update")]

        # Count raw task_id occurrences
        from collections import Counter
        task_id_counts = Counter(r.get("task_id") for r in assignment_lines if r.get("task_id") is not None)
        duplicate_tids = sum(1 for c in task_id_counts.values() if c > 1)

        # Missing ml_probability in newer-format records
        missing_ml = sum(
            1 for r in assignment_lines
            if r.get("ml_probability") is None and r.get("rule_score") is not None
        )

        warnings: list[str] = []
        if duplicate_tids:
            warnings.append(f"{duplicate_tids} task(s) have duplicate assignment entries (last used)")
        if missing_ml:
            warnings.append(f"{missing_ml} assignment(s) missing ml_probability field")

        return {
            "total_log_lines":   len(lines),
            "assignment_events": len(assignment_lines),
            "outcome_events":    len(outcome_lines),
            "duplicate_task_ids": duplicate_tids,
            "missing_ml_prob":   missing_ml,
            "warnings":          warnings,
        }

    except Exception as exc:
        logger.error("analytics.get_data_quality_check failed: %s", exc)
        return {
            "total_log_lines":    0,
            "assignment_events":  0,
            "outcome_events":     0,
            "duplicate_task_ids": 0,
            "missing_ml_prob":    0,
            "warnings":           [f"Error: {exc}"],
        }


# ---------------------------------------------------------------------------
# 13. User Task Stats  (personal dashboard cards)
# ---------------------------------------------------------------------------

def get_user_task_stats(db: Session, user_id: int) -> dict:
    """
    Personal task KPIs for *user_id* — sourced entirely from task_assignments.
    """
    try:
        from app.models.task import TaskAssignment, AssignmentStatus

        assignments = db.query(TaskAssignment).filter(
            TaskAssignment.user_id == user_id
        ).all()

        total      = len(assignments)
        completed  = [a for a in assignments if a.status == AssignmentStatus.completed]
        in_prog    = [a for a in assignments if a.status == AssignmentStatus.in_progress]
        pending_ap = [a for a in assignments if a.status == AssignmentStatus.pending_approval]
        delayed    = [a for a in assignments if getattr(a, "is_delayed", False)]

        durations = [
            a.duration_seconds for a in completed
            if a.duration_seconds and a.duration_seconds > 0
        ]
        avg_dur_min = round(sum(durations) / len(durations) / 60, 1) if durations else 0.0
        rate        = round(len(completed) / total * 100, 1) if total else 0.0

        return {
            "total_tasks":           total,
            "completed_tasks":       len(completed),
            "pending_tasks":         len(pending_ap),
            "in_progress_tasks":     len(in_prog),
            "delayed_tasks":         len(delayed),
            "completion_rate":       rate,
            "avg_duration_minutes":  avg_dur_min,
        }
    except Exception as exc:
        logger.error("analytics.get_user_task_stats(%s) failed: %s", user_id, exc)
        return {
            "total_tasks": 0, "completed_tasks": 0, "pending_tasks": 0,
            "in_progress_tasks": 0, "delayed_tasks": 0,
            "completion_rate": 0.0, "avg_duration_minutes": 0.0,
        }


# ---------------------------------------------------------------------------
# 14. Manager Team Stats  (scoped to manager's direct team)
# ---------------------------------------------------------------------------

def get_manager_team_stats(db: Session, manager_id: int) -> dict:
    """
    Aggregated task KPIs for all assignments on tasks the manager created.
    Uses TaskAssignment for new-style tasks; falls back to Task.status for legacy.
    """
    try:
        from app.models.task import TaskAssignment, AssignmentStatus, Task, TaskStatus

        # New-style: count assignments on tasks assigned_by this manager
        assignments = (
            db.query(TaskAssignment)
            .join(Task, TaskAssignment.task_id == Task.id)
            .filter(Task.assigned_by == manager_id)
            .all()
        )

        # Legacy: tasks with no assignments
        task_ids_with_assignments = {a.task_id for a in assignments}
        legacy_tasks = (
            db.query(Task)
            .filter(
                Task.assigned_by == manager_id,
                Task.id.notin_(task_ids_with_assignments),
            )
            .all()
        ) if task_ids_with_assignments else db.query(Task).filter(Task.assigned_by == manager_id).all()

        total   = len(assignments) + len(legacy_tasks)
        completed = (
            [a for a in assignments if a.status == AssignmentStatus.completed]
            + [t for t in legacy_tasks if t.status == TaskStatus.completed]
        )
        delayed = (
            [a for a in assignments if getattr(a, "is_delayed", False)]
            + [t for t in legacy_tasks if getattr(t, "is_delayed", False)]
        )
        pend_ap = (
            [a for a in assignments if a.status == AssignmentStatus.pending_approval]
            + [t for t in legacy_tasks if t.status == TaskStatus.pending_approval]
        )

        durations = [
            x.duration_seconds for x in completed
            if getattr(x, "duration_seconds", None) and x.duration_seconds > 0
        ]
        avg_dur_min = round(sum(durations) / len(durations) / 60, 1) if durations else 0.0
        rate        = round(len(completed) / total * 100, 1) if total else 0.0

        return {
            "team_total":                total,
            "team_completed":            len(completed),
            "team_delayed":              len(delayed),
            "team_pending_approval":     len(pend_ap),
            "team_avg_duration_minutes": avg_dur_min,
            "team_completion_rate":      rate,
        }
    except Exception as exc:
        logger.error("analytics.get_manager_team_stats(%s) failed: %s", manager_id, exc)
        return {
            "team_total": 0, "team_completed": 0, "team_delayed": 0,
            "team_pending_approval": 0, "team_avg_duration_minutes": 0.0,
            "team_completion_rate": 0.0,
        }


# ---------------------------------------------------------------------------
# 15. System Task Stats  (org-wide KPI cards for admin/analytics page)
# ---------------------------------------------------------------------------

def get_system_task_stats(db: Session) -> dict:
    """
    Org-wide task health KPIs.
    Counts TaskAssignment rows for new tasks; legacy Task rows for old data.
    """
    try:
        from app.models.task import TaskAssignment, AssignmentStatus, Task, TaskStatus

        all_assignments = db.query(TaskAssignment).all()
        assigned_task_ids = {a.task_id for a in all_assignments}

        legacy_tasks = (
            db.query(Task)
            .filter(Task.id.notin_(assigned_task_ids))
            .all()
        ) if assigned_task_ids else db.query(Task).all()

        total     = len(all_assignments) + len(legacy_tasks)
        completed = (
            [a for a in all_assignments if a.status == AssignmentStatus.completed]
            + [t for t in legacy_tasks if t.status == TaskStatus.completed]
        )
        in_prog   = (
            [a for a in all_assignments if a.status == AssignmentStatus.in_progress]
            + [t for t in legacy_tasks if t.status == TaskStatus.in_progress]
        )
        pend_ap   = (
            [a for a in all_assignments if a.status == AssignmentStatus.pending_approval]
            + [t for t in legacy_tasks if t.status == TaskStatus.pending_approval]
        )
        assigned  = (
            [a for a in all_assignments if a.status == AssignmentStatus.assigned]
            + [t for t in legacy_tasks if t.status == TaskStatus.assigned]
        )
        delayed   = (
            [a for a in all_assignments if getattr(a, "is_delayed", False)]
            + [t for t in legacy_tasks if getattr(t, "is_delayed", False)]
        )

        durations = [
            x.duration_seconds for x in completed
            if getattr(x, "duration_seconds", None) and x.duration_seconds > 0
        ]
        avg_dur_min = round(sum(durations) / len(durations) / 60, 1) if durations else 0.0
        rate        = round(len(completed) / total * 100, 1) if total else 0.0

        return {
            "total":             total,
            "completed":         len(completed),
            "in_progress":       len(in_prog),
            "pending_approval":  len(pend_ap),
            "assigned":          len(assigned),
            "delayed":           len(delayed),
            "completion_rate":   rate,
            "avg_duration_minutes": avg_dur_min,
        }
    except Exception as exc:
        logger.error("analytics.get_system_task_stats failed: %s", exc)
        return {
            "total": 0, "completed": 0, "in_progress": 0,
            "pending_approval": 0, "assigned": 0, "delayed": 0,
            "completion_rate": 0.0, "avg_duration_minutes": 0.0,
        }
