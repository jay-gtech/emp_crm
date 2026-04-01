"""
AI Task Priority Predictor  (v2 — Explainable, Hybrid, Confidence-scored)
===========================================================================
Public API
----------
  predict_priority(task) -> PredictionResult
      Returns priority label, confidence, human-readable reason, and whether
      the task is at risk of delay.

  PredictionResult keys:
      raw            : "high" | "medium" | "low"
      label          : "🔥 High" | "⚠️ Medium" | "🟢 Low"
      confidence     : float 0–1   (probability of predicted class)
      reason         : str          (one-line human explanation)
      at_risk        : bool         (delay model or rule fallback)
      delay_label    : "⚠️ At Risk" | "On Track"

Design notes
------------
- Both models are loaded ONCE per process (lazy, cached).
- Missing model files → silent rule-based fallback.  No log spam.
- Hybrid priority: take the more urgent of (rule result, model result).
  This means the ML model can only escalate, never silently downgrade.
- predict.py is DB-free.  Employee-level workload stats are computed
  upstream in ai_task_service.py.
"""
from __future__ import annotations

import pathlib
from datetime import date, datetime
from typing import Any

MODEL_PATH       = pathlib.Path(__file__).parent / "model.pkl"
DELAY_MODEL_PATH = pathlib.Path(__file__).parent / "delay_model.pkl"

# Sentinel must match train.py
NO_DUE_DATE_SENTINEL = 999

_PRIORITY_LABELS = {
    "high":   "🔥 High",
    "medium": "⚠️ Medium",
    "low":    "🟢 Low",
}

# More urgent = lower rank number
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

# ── Module-level model cache ──────────────────────────────────────────────────
_priority_model: Any = None
_priority_model_loaded: bool = False

_delay_model: Any = None
_delay_model_loaded: bool = False


def _load_priority_model() -> Any:
    global _priority_model, _priority_model_loaded
    if _priority_model_loaded:
        return _priority_model
    _priority_model_loaded = True
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib
        _priority_model = joblib.load(MODEL_PATH)
    except Exception:
        _priority_model = None
    return _priority_model


def _load_delay_model() -> Any:
    global _delay_model, _delay_model_loaded
    if _delay_model_loaded:
        return _delay_model
    _delay_model_loaded = True
    if not DELAY_MODEL_PATH.exists():
        return None
    try:
        import joblib
        _delay_model = joblib.load(DELAY_MODEL_PATH)
    except Exception:
        _delay_model = None
    return _delay_model


# ---------------------------------------------------------------------------
# Feature extraction (must stay in sync with train.py)
# ---------------------------------------------------------------------------

def _days_until_due(due_date: date | None) -> float:
    if due_date is None:
        return float(NO_DUE_DATE_SENTINEL)
    return float((due_date - date.today()).days)


def _task_age(created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    return float((date.today() - created_at.date()).days)


def _status_code(status_str: str) -> int:
    return {"pending": 0, "in_progress": 1, "completed": 2}.get(status_str, 0)


def _build_features(task: Any) -> list[float]:
    """Accept an ORM Task object or a plain dict. Returns 4-element vector."""
    if isinstance(task, dict):
        due     = task.get("due_date")
        created = task.get("created_at")
        status  = task.get("status", "pending")
    else:
        due         = getattr(task, "due_date", None)
        created     = getattr(task, "created_at", None)
        status_val  = getattr(task, "status", None)
        status      = status_val.value if hasattr(status_val, "value") else str(status_val or "pending")

    d = _days_until_due(due)
    return [
        d,
        _task_age(created),
        float(_status_code(status)),
        1.0 if due is not None else 0.0,
    ]


# ---------------------------------------------------------------------------
# Rule-based logic (identical thresholds to train.py — single source of truth)
# ---------------------------------------------------------------------------

def _rule_priority(days_due: float, age: float, status_code: int) -> str:
    if status_code == 2:
        return "low"
    if days_due < 0 or days_due <= 2:
        return "high"
    if days_due <= 7:
        return "medium"
    if days_due == NO_DUE_DATE_SENTINEL and age > 14:
        return "medium"
    return "low"


def _rule_delay(days_due: float, has_due: float, status_code: int) -> bool:
    if status_code == 2 or not has_due:
        return False
    return days_due <= 3


def _build_reason(days_due: float, age: float, status_code: int, priority_raw: str) -> str:
    """One-line human explanation for the predicted priority."""
    if status_code == 2:
        return "Task is completed"
    if days_due < 0:
        overdue_days = int(abs(days_due))
        return f"Overdue by {overdue_days} day{'s' if overdue_days != 1 else ''}"
    if days_due == 0:
        return "Due today"
    if days_due == 1:
        return "Due tomorrow"
    if days_due <= 2:
        return f"Due in {int(days_due)} days"
    if days_due <= 7:
        return f"Due in {int(days_due)} days"
    if days_due == NO_DUE_DATE_SENTINEL:
        if age > 14:
            return f"No deadline - pending for {int(age)} days"
        return "No deadline set"
    if priority_raw == "low":
        return f"Due in {int(days_due)} days - low urgency"
    return f"Due in {int(days_due)} days"


# ---------------------------------------------------------------------------
# Hybrid merge: take the more urgent of rule and model predictions
# ---------------------------------------------------------------------------

def _hybrid_merge(rule_raw: str, model_raw: str) -> str:
    """Return whichever prediction is more urgent (lower rank = more urgent)."""
    if _PRIORITY_RANK[rule_raw] <= _PRIORITY_RANK[model_raw]:
        return rule_raw
    return model_raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_priority(task: Any) -> dict:
    """
    Returns:
        {
            "raw":       "high" | "medium" | "low",
            "label":     "🔥 High" | "⚠️ Medium" | "🟢 Low",
            "confidence": float,          # 0.0–1.0
            "reason":    str,             # human explanation
            "at_risk":   bool,
            "delay_label": "⚠️ At Risk" | "On Track",
        }
    """
    features = _build_features(task)
    days_due, age, status_code, has_due = features

    # ── Rule-based priority ──────────────────────────────────────────────────
    rule_raw = _rule_priority(days_due, age, int(status_code))

    # ── ML priority + confidence ─────────────────────────────────────────────
    model = _load_priority_model()
    confidence: float = 1.0   # rule-only fallback: deterministic = 100 %

    if model is not None:
        try:
            ml_raw   = model.predict([features])[0]
            proba    = dict(zip(model.classes_, model.predict_proba([features])[0]))
            ml_conf  = float(round(proba.get(ml_raw, 0.0), 3))

            # Hybrid: escalate if rule says higher urgency, accept ML otherwise
            final_raw  = _hybrid_merge(rule_raw, ml_raw)
            confidence = float(round(proba.get(final_raw, ml_conf), 3))
        except Exception:
            final_raw = rule_raw
    else:
        final_raw = rule_raw

    # ── Delay risk ───────────────────────────────────────────────────────────
    delay_model = _load_delay_model()
    at_risk: bool

    if delay_model is not None:
        try:
            at_risk = bool(delay_model.predict([features])[0] == 1)
        except Exception:
            at_risk = _rule_delay(days_due, has_due, int(status_code))
    else:
        at_risk = _rule_delay(days_due, has_due, int(status_code))

    reason = _build_reason(days_due, age, int(status_code), final_raw)

    return {
        "raw":         final_raw,
        "label":       _PRIORITY_LABELS.get(final_raw, "🟢 Low"),
        "confidence":  confidence,
        "reason":      reason,
        "at_risk":     at_risk,
        "delay_label": "⚠️ At Risk" if at_risk else "On Track",
    }
