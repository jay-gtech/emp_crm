"""
app/services/auto_assignment_service.py
========================================
Service layer for the Smart Task Auto-Assignment system.

This module is the bridge between the database (SQLAlchemy) and the
pure ML scoring engine.  It:
  1. Fetches eligible employees respecting RBAC scope
  2. Extracts live features from the DB for each candidate
  3. Delegates scoring to the stateless ML layer
  4. Commits the assignment
  5. Logs the decision for ML training
  6. Returns a rich explainable result

Architecture note
-----------------
DB access is ONLY in this service layer.
The ML scorer (app/ml/auto_assignment/scorer.py) is pure Python
and must never import SQLAlchemy.
"""

from __future__ import annotations
import datetime
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException

from app.models.task import Task
from app.models.user import User, UserRole
from app.services.hierarchy_service import get_manager_team, get_team_lead_members
from app.ml.auto_assignment.scorer import (
    select_best_employee,
    fallback_least_workload,
)
from app.ml.auto_assignment.logger import log_assignment

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Employee selection (RBAC-scoped)
# ─────────────────────────────────────────────────────────────────────────────

def get_eligible_employees(db: Session, current_user: dict) -> list[User]:
    """
    Return the employees the current user is allowed to assign tasks to,
    filtered to active employees only.

    Admin   → all employees in the system
    Manager → their direct reports (via hierarchy_service)
    Team Lead → their team members
    Employee  → empty list (employees cannot self-assign)
    """
    role    = current_user.get("role", "")
    user_id = current_user.get("user_id")

    if role == "admin":
        # Admin can assign to managers or team leads
        return (
            db.query(User)
            .filter(User.role.in_([UserRole.manager, UserRole.team_lead]), User.is_active == 1)
            .all()
        )
    elif role == "manager":
        team = get_manager_team(db, user_id)
        # Manager assigns strictly to Team Leads
        return [u for u in team if u.role == UserRole.team_lead and u.is_active == 1]
    elif role == "team_lead":
        members = get_team_lead_members(db, user_id)
        # Team Lead assigns strictly to Employees
        return [u for u in members if u.role == UserRole.employee and u.is_active == 1]
    else:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction (DB layer — never called from scorer.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_employee_features(db: Session, employee: User) -> dict:
    """
    Compute the feature vector for one employee from live DB counts.

    Features
    --------
    active_tasks      – pending + in_progress tasks currently assigned
    overdue_tasks     – active tasks whose due_date has already passed
    completed_tasks   – total completed tasks (delivery history)
    performance_score – from the User record (0–100, or 50.0 if NULL)
    """
    today = datetime.date.today()

    from app.models.task import TaskAssignment as _TA
    active_tasks = (
        db.query(func.count(_TA.id))
        .join(Task, _TA.task_id == Task.id)
        .filter(
            _TA.user_id == employee.id,
            _TA.status.in_(["pending", "in_progress", "assigned"]),
        )
        .scalar() or 0
    )

    overdue_tasks = (
        db.query(func.count(_TA.id))
        .join(Task, _TA.task_id == Task.id)
        .filter(
            _TA.user_id == employee.id,
            _TA.status.in_(["pending", "in_progress", "assigned"]),
            Task.due_date < today,
        )
        .scalar() or 0
    )

    completed_tasks = (
        db.query(func.count(_TA.id))
        .join(Task, _TA.task_id == Task.id)
        .filter(
            _TA.user_id == employee.id,
            _TA.status == "completed",
        )
        .scalar() or 0
    )

    # Use the real DB value; fall back to neutral 50.0 if not set
    perf_score = float(employee.performance_score) if employee.performance_score is not None else 50.0

    return {
        "active_tasks":      active_tasks,
        "overdue_tasks":     overdue_tasks,
        "completed_tasks":   completed_tasks,
        "performance_score": perf_score,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core service function
# ─────────────────────────────────────────────────────────────────────────────

def auto_assign_task(
    db:           Session,
    task:         Task,
    current_user: dict,
) -> dict:
    """
    Assign *task* to the best eligible employee and return a rich result dict.

    Returns
    -------
    {
        "task_id":        int,
        "assigned_to":    str,         employee name
        "employee_id":    int,
        "score":          float,
        "reason":         str,         human-readable explanation
        "top_candidates": list[dict],  top-3 ranked alternatives
    }

    Raises
    ------
    HTTPException 400 — no eligible employees in scope
    HTTPException 500 — unexpected failure (rolls back DB)
    """
    try:
        # ── 1. Fetch eligible employees ───────────────────────────────────────
        employees = get_eligible_employees(db, current_user)
        if not employees:
            raise HTTPException(
                status_code=400,
                detail="No eligible employees found in your scope for auto-assignment.",
            )

        # ── 2. Build feature vectors ──────────────────────────────────────────
        employee_features: list[dict] = []
        for emp in employees:
            features = get_employee_features(db, emp)
            employee_features.append({
                "employee_id":   emp.id,
                "employee_name": emp.name,
                "features":      features,
            })

        # ── 3. Score and rank ─────────────────────────────────────────────────
        try:
            best, ranked = select_best_employee(employee_features)
        except Exception as scoring_exc:
            log.warning(
                "[auto_assign] Scoring failed (%s) — falling back to least-workload.",
                scoring_exc,
            )
            best = fallback_least_workload(employee_features)
            best["score"]  = -1.0
            best["reason"] = "Fallback: least active tasks (scoring error)"
            ranked = [best]

        # ── 4. Commit assignment ──────────────────────────────────────────────
        # assignment tracking is done exclusively via task_assignments
        db.commit()
        db.refresh(task)

        # ── 5. Log for ML training ────────────────────────────────────────────
        task_context = {
            "priority": task.priority.value if task.priority else None,
            "status":   task.status.value   if task.status   else None,
            "due_date": str(task.due_date)  if task.due_date else None,
        }
        log_assignment(
            task_id=task.id,
            employee_id=best["employee_id"],
            score=best["score"],
            features=best["features"],
            task_context=task_context,
            reason_tags=best.get("reason_tags", []),
            ml_used=best.get("ml_used"),
            inference_time_ms=best.get("inference_time_ms"),
            shadow_ml_prob=best.get("shadow_ml_prob"),
        )

        # ── 6. Build response ─────────────────────────────────────────────────
        _role = current_user.get("role", "")
        if _role == "admin":
            role_level = "Manager / Team Lead"
        elif _role == "manager":
            role_level = "Team Lead"
        elif _role == "team_lead":
            role_level = "Employee"
        else:
            role_level = "Employee"

        top_candidates = [
            {
                "employee_id":   r["employee_id"],
                "employee_name": r["employee_name"],
                "final_score":   r["score"],
                "rule_score":    r["features"].get("rule_score", r["score"]),
                "normalized_rule_score": r["features"].get("normalized_rule_score", 0.0),
                "ml_probability": r["features"].get("ml_prob", 0.0),
                "reason":        r.get("reason", ""),
                "reason_tags":   r.get("reason_tags", []),
            }
            for r in ranked[:3]
        ]

        return {
            "task_id":        task.id,
            "assigned_to":    best["employee_name"],
            "employee_id":    best["employee_id"],
            "role_level":     role_level,
            "final_score":    best["score"],
            "rule_score":     best["features"].get("rule_score", best["score"]),
            "normalized_rule_score": best["features"].get("normalized_rule_score", 0.0),
            "ml_probability": best["features"].get("ml_prob", 0.0),
            "reason":         best.get("reason", ""),
            "reason_tags":    best.get("reason_tags", []),
            "top_candidates": top_candidates,
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        log.exception("[auto_assign] Unexpected failure for task %s: %s", getattr(task, "id", "?"), exc)
        raise HTTPException(
            status_code=500,
            detail=f"Auto-assignment failed unexpectedly: {exc}",
        )
