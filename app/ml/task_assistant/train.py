"""
AI Task Priority & Delay Training Script
==========================================
Trains two models:

  1. priority_model (RandomForestClassifier)
     Labels: high / medium / low
     Saved:  app/ml/task_assistant/model.pkl

  2. delay_model (LogisticRegression, binary)
     Labels: 1 = at risk of delay, 0 = on time
     Saved:  app/ml/task_assistant/delay_model.pkl

Features (4, shared by both models):
  - days_until_due  : days to due_date (negative = overdue). 999 when no due_date.
  - task_age_days   : days since created_at.
  - status_code     : 0=pending, 1=in_progress, 2=completed.
  - has_due_date    : 1 if due_date is set, else 0.

Usage (from project root):
    python -m app.ml.task_assistant.train
"""
from __future__ import annotations

import sys
import pathlib
from datetime import datetime, date
import random

MODEL_PATH       = pathlib.Path(__file__).parent / "model.pkl"
DELAY_MODEL_PATH = pathlib.Path(__file__).parent / "delay_model.pkl"

# Sentinel used when due_date is NULL — must match predict.py
NO_DUE_DATE_SENTINEL = 999


# ---------------------------------------------------------------------------
# Feature engineering helpers (kept in sync with predict.py)
# ---------------------------------------------------------------------------

STATUS_CODES = {"pending": 0, "in_progress": 1, "completed": 2}


def _days_until_due(due_date: date | None, ref: date | None = None) -> float:
    if due_date is None:
        return float(NO_DUE_DATE_SENTINEL)
    today = ref or date.today()
    return float((due_date - today).days)


def _task_age(created_at: datetime | None, ref: date | None = None) -> float:
    if created_at is None:
        return 0.0
    today = ref or date.today()
    return float((today - created_at.date()).days)


def _status_code(status_str: str) -> int:
    return STATUS_CODES.get(status_str, 0)


def task_to_features(task_dict: dict) -> list[float]:
    """Convert a task dict (or ORM-like dict) to a 4-element feature vector."""
    return [
        _days_until_due(task_dict.get("due_date")),
        _task_age(task_dict.get("created_at")),
        float(_status_code(task_dict.get("status", "pending"))),
        1.0 if task_dict.get("due_date") is not None else 0.0,
    ]


# ---------------------------------------------------------------------------
# Rule-based labeller — ground truth for synthetic priority labels
# ---------------------------------------------------------------------------

def rule_label(days_until_due: float, task_age: float, status_code: int) -> str:
    """
    High   : due within 2 days OR already overdue (non-completed)
    Medium : due in 3–7 days OR (no due date but age > 14 days)
    Low    : everything else
    """
    if status_code == 2:
        return "low"
    if days_until_due < 0 or days_until_due <= 2:
        return "high"
    if days_until_due <= 7:
        return "medium"
    if days_until_due == NO_DUE_DATE_SENTINEL and task_age > 14:
        return "medium"
    return "low"


def delay_label(days_until_due: float, status_code: int) -> int:
    """
    1 = at risk of delay (due within 1 day, or already overdue, and not completed)
    0 = on time
    """
    if status_code == 2:
        return 0
    if days_until_due == NO_DUE_DATE_SENTINEL:
        return 0
    return 1 if days_until_due <= 1 else 0


# ---------------------------------------------------------------------------
# Synthetic dataset generator
# ---------------------------------------------------------------------------

def _generate_synthetic(n: int = 2500, seed: int = 42) -> tuple[list, list, list]:
    """Return (X, y_priority, y_delay) lists."""
    rng = random.Random(seed)
    X, y_priority, y_delay = [], [], []

    for _ in range(n):
        has_due = rng.random() > 0.25
        if has_due:
            due_days = rng.randint(-10, 60)
        else:
            due_days = NO_DUE_DATE_SENTINEL

        age    = float(rng.randint(0, 90))
        status = rng.choice([0, 0, 0, 1, 1, 2])   # weighted toward pending

        features = [float(due_days), age, float(status), 1.0 if has_due else 0.0]
        X.append(features)
        y_priority.append(rule_label(float(due_days), age, status))
        y_delay.append(delay_label(float(due_days), status))

    return X, y_priority, y_delay


# ---------------------------------------------------------------------------
# DB data loader (merges real task rows as labelled samples)
# ---------------------------------------------------------------------------

def _load_db_samples() -> tuple[list, list, list]:
    """Returns (X, y_priority, y_delay) — may be empty on failure."""
    try:
        from app.core.database import SessionLocal
        from app.models.task import Task

        db = SessionLocal()
        try:
            tasks = db.query(Task).all()
        finally:
            db.close()

        X, y_priority, y_delay = [], [], []
        for t in tasks:
            if t.priority is None:
                continue
            features = task_to_features({
                "due_date":   t.due_date,
                "created_at": t.created_at,
                "status":     t.status.value if t.status else "pending",
            })
            X.append(features)
            y_priority.append(t.priority.value)
            y_delay.append(delay_label(features[0], int(features[2])))

        print(f"[train] Loaded {len(X)} real task samples from DB.")
        return X, y_priority, y_delay

    except Exception as exc:
        print(f"[train] DB load skipped: {exc}")
        return [], [], []


# ---------------------------------------------------------------------------
# Main training routine
# ---------------------------------------------------------------------------

def train_and_save() -> None:
    try:
        import pandas as pd
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
        import joblib
    except ImportError as exc:
        print(f"[train] Missing dependency: {exc}")
        print("       Install with: pip install scikit-learn pandas joblib")
        sys.exit(1)

    print("[train] Generating synthetic training data...")
    X_syn, yp_syn, yd_syn = _generate_synthetic(n=2500)

    print("[train] Loading real DB samples...")
    X_db, yp_db, yd_db = _load_db_samples()

    X        = X_syn + X_db
    y_prio   = yp_syn + yp_db
    y_delay  = yd_syn + yd_db

    print(f"[train] Total samples: {len(X)}  (synthetic: {len(X_syn)}, real: {len(X_db)})")

    df = pd.DataFrame(X, columns=["days_until_due", "task_age_days", "status_code", "has_due_date"])

    # ── Priority model ────────────────────────────────────────────────────────
    print("[train] Priority label distribution:")
    for label, count in sorted({l: y_prio.count(l) for l in set(y_prio)}.items()):
        print(f"         {label:8s} {count:5d}  ({count/len(y_prio)*100:.1f}%)")

    priority_clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    scores = cross_val_score(priority_clf, df.values, y_prio, cv=5, scoring="accuracy")
    print(f"[train] Priority 5-fold CV accuracy: {scores.mean():.3f} +/- {scores.std():.3f}")
    priority_clf.fit(df.values, y_prio)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(priority_clf, MODEL_PATH)
    print(f"[train] Priority model saved: {MODEL_PATH}")

    # ── Delay model (binary) ──────────────────────────────────────────────────
    delay_counts = {0: y_delay.count(0), 1: y_delay.count(1)}
    print(f"[train] Delay label distribution: {delay_counts}")

    delay_clf = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            class_weight="balanced",
            max_iter=500,
            random_state=42,
        )),
    ])
    d_scores = cross_val_score(delay_clf, df.values, y_delay, cv=5, scoring="f1")
    print(f"[train] Delay 5-fold CV F1:       {d_scores.mean():.3f} +/- {d_scores.std():.3f}")
    delay_clf.fit(df.values, y_delay)

    joblib.dump(delay_clf, DELAY_MODEL_PATH)
    print(f"[train] Delay model saved: {DELAY_MODEL_PATH}")


if __name__ == "__main__":
    train_and_save()
