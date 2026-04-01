"""
AI Task Service  (v2 — Ranked, Explainable, Workload-aware)
=============================================================
Fetches tasks for a user, enriches each with AI priority + confidence +
human reason + delay risk, computes a numeric urgency score, and returns
the list ranked from most to least urgent.

Ranking score formula
---------------------
  overdue task          : 100 + |days_overdue|
  task with due date    : 1.0 / max(days_until_due, 0.5)
  no due date, old task : age_days * 0.01
  no due date, fresh    : 0

Higher score = higher rank = position 1 in the list.

Employee workload feature
-------------------------
current_workload (int): number of active tasks assigned to this employee.
Used to enrich each task row so the UI / future models can display it,
but NOT fed into the priority ML model (feature count must stay at 4 to
remain compatible with the saved model.pkl).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from sqlalchemy.orm import Session

from app.models.task import Task, TaskStatus

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_NO_DUE_SENTINEL = 999


def _urgency_score(days_until_due: float, task_age: float) -> float:
    """Higher = more urgent. Safe for overdue, no-deadline, and normal tasks."""
    if days_until_due < 0:                          # overdue
        return 100.0 + abs(days_until_due)
    if days_until_due < _NO_DUE_SENTINEL:           # has due date
        return 1.0 / max(days_until_due, 0.5)
    if task_age > 0:                                # no due date, use age
        return task_age * 0.01
    return 0.0


def _days_until_due(due_date: datetime.date | None) -> float:
    if due_date is None:
        return float(_NO_DUE_SENTINEL)
    return float((due_date - datetime.date.today()).days)


def _task_age(created_at: datetime.datetime | None) -> float:
    if created_at is None:
        return 0.0
    return float((datetime.date.today() - created_at.date()).days)


def get_ai_task_suggestions(db: Session, user: dict) -> list[dict]:
    """
    Return AI-enriched, ranked task suggestions for the given session user.

    Result keys per item:
        task_id          int
        title            str
        status           str
        due_date         date | None
        ai_priority      str    e.g. "🔥 High"
        ai_priority_raw  str    "high" | "medium" | "low"
        confidence       float  0–1
        confidence_pct   int    0–100  (for template display)
        reason           str    human explanation
        at_risk_of_delay bool
        delay_label      str    "⚠️ At Risk" | "On Track"
        rank             int    1 = most urgent
        workload         int    total active tasks for this employee
    """
    from app.ml.task_assistant.predict import predict_priority
    from app.ml.task_assistant.predict import _build_features as _feat
    try:
        from app.ml.task_assistant.save_training_data import log_prediction as _log_pred
        _logging_ok = True
    except Exception:
        _logging_ok = False

    uid  = user["user_id"]
    role = user.get("role", "employee")

    # ── Fetch active tasks scoped to role ────────────────────────────────────
    q = db.query(Task).filter(Task.status != TaskStatus.completed)
    if role == "admin":
        tasks = q.all()
    elif role in ("manager", "team_lead"):
        tasks = q.filter(
            (Task.assigned_by == uid) | (Task.assigned_to == uid)
        ).all()
    else:
        tasks = q.filter(Task.assigned_to == uid).all()

    # ── Per-employee active task counts (workload feature) ───────────────────
    workload: dict[int, int] = defaultdict(int)
    for t in tasks:
        workload[t.assigned_to] += 1

    # ── Build enriched suggestions ───────────────────────────────────────────
    suggestions = []
    for task in tasks:
        prediction = predict_priority(task)

        days  = _days_until_due(task.due_date)
        age   = _task_age(task.created_at)
        score = _urgency_score(days, age)

        # Fire-and-forget: log prediction for future retraining
        if _logging_ok:
            try:
                _log_pred(
                    task_id=task.id,
                    features=_feat(task),
                    predicted_priority=prediction["raw"],
                    confidence=prediction["confidence"],
                )
            except Exception:
                pass

        suggestions.append({
            "task_id":          task.id,
            "title":            task.title,
            "status":           task.status.value,
            "due_date":         task.due_date,
            "ai_priority":      prediction["label"],
            "ai_priority_raw":  prediction["raw"],
            "confidence":       prediction["confidence"],
            "confidence_pct":   int(round(prediction["confidence"] * 100)),
            "reason":           prediction["reason"],
            "at_risk_of_delay": prediction["at_risk"],
            "delay_label":      prediction["delay_label"],
            "_score":           score,
            "workload":         workload.get(task.assigned_to, 0),
        })

    # ── Rank by urgency score (desc) ─────────────────────────────────────────
    suggestions.sort(key=lambda s: -s["_score"])
    for i, s in enumerate(suggestions, start=1):
        s["rank"] = i
        del s["_score"]   # internal field — remove before returning

    return suggestions
