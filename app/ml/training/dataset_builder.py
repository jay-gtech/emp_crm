"""
app/ml/training/dataset_builder.py
====================================
Builds a training dataset for the task-success prediction model.

Two data sources (merged automatically)
-----------------------------------------
1. assignment_log.jsonl  — real decisions made by the auto-assignment engine
2. Live SQLAlchemy DB    — synthesised rows from the existing 250-task dataset

Using both sources is essential because the log is sparse (few real assignments
so far) while the DB contains rich, realistic workload data from seed_tasks.py.

Label definition
-----------------
  success = 1   if overdue_tasks == 0  (employee has no missed deadlines)
  success = 0   otherwise

This is a proxy label.  Once real outcomes are accumulated via
logger.update_assignment_outcome(), the outcome events can be joined in
and this label will be replaced with ground-truth data.

Usage
-----
  from app.ml.training.dataset_builder import build_dataset
  X, y, df = build_dataset()
"""

from __future__ import annotations
import json
import logging
import sys
import os
from pathlib import Path

import pandas as pd
import numpy as np

# ── Bootstrap project root onto path (needed if run directly) ─────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

from app.ml.training.utils import (
    FEATURE_COLUMNS,
    FEATURE_DEFAULTS,
    features_to_dataframe_row,
    safe_float,
)

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG_FILE = Path(__file__).parent.parent / "auto_assignment" / "assignment_log.jsonl"


# ─────────────────────────────────────────────────────────────────────────────
# Source 1: assignment_log.jsonl
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_log() -> list[dict]:
    """
    Parse assignment events from the JSONL log.
    Skips outcome events and malformed lines silently.
    Returns a list of flat feature-row dicts with a 'success' label.
    """
    rows: list[dict] = []
    if not LOG_FILE.exists():
        log.warning("[dataset_builder] Log file not found: %s", LOG_FILE)
        return rows

    with open(LOG_FILE, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning("[dataset_builder] Skipping malformed JSON at line %d", lineno)
                continue

            if entry.get("event_type") != "assignment":
                continue

            raw_features = entry.get("features", {})
            row = features_to_dataframe_row(raw_features)

            # Proxy label: no overdue tasks → predicted success
            row["success"] = 1 if row["overdue_tasks"] == 0 else 0
            row["source"]  = "log"
            rows.append(row)

    log.info("[dataset_builder] Loaded %d rows from assignment log", len(rows))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Source 2: Live DB synthesis
# ─────────────────────────────────────────────────────────────────────────────

def _load_from_db() -> list[dict]:
    """
    Synthesise training rows by computing live feature vectors for every
    (employee × recent_task) pair in the DB.

    This gives us 200–300 realistic rows with proper workload imbalance,
    which is critical when the log is still sparse.
    """
    rows: list[dict] = []
    try:
        import datetime
        from sqlalchemy import func
        from app.core.database import SessionLocal
        from app.models.user import User, UserRole
        from app.models.task import Task, TaskStatus

        db = SessionLocal()
        today = datetime.date.today()

        try:
            employees = (
                db.query(User)
                .filter(User.role == UserRole.employee, User.is_active == 1)
                .all()
            )

            for emp in employees:
                # Live feature counts
                active = db.query(func.count(Task.id)).filter(
                    Task.assigned_to == emp.id,
                    Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
                ).scalar() or 0

                overdue = db.query(func.count(Task.id)).filter(
                    Task.assigned_to == emp.id,
                    Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
                    Task.due_date < today,
                ).scalar() or 0

                completed = db.query(func.count(Task.id)).filter(
                    Task.assigned_to == emp.id,
                    Task.status == TaskStatus.completed,
                ).scalar() or 0

                perf = float(emp.performance_score) if emp.performance_score else FEATURE_DEFAULTS["performance_score"]

                row = {
                    "active_tasks":      float(active),
                    "overdue_tasks":     float(overdue),
                    "completed_tasks":   float(completed),
                    "performance_score": perf,
                    # Proxy label
                    "success": 1 if overdue == 0 else 0,
                    "source": "db",
                }
                rows.append(row)

                # ── Augmentation: add slight variations to reduce overfitting ──
                # For each real employee, generate 3 synthetic variants so the
                # model sees a wider range of workload combinations.
                for _ in range(3):
                    noise_active   = max(0, active   + np.random.randint(-2, 3))
                    noise_overdue  = max(0, overdue  + np.random.randint(-1, 2))
                    noise_complete = max(0, completed + np.random.randint(-3, 4))
                    noise_perf     = float(np.clip(perf + np.random.uniform(-5, 5), 0, 100))
                    synth = {
                        "active_tasks":      float(noise_active),
                        "overdue_tasks":     float(noise_overdue),
                        "completed_tasks":   float(noise_complete),
                        "performance_score": round(noise_perf, 1),
                        "success": 1 if noise_overdue == 0 else 0,
                        "source": "augmented",
                    }
                    rows.append(synth)

        finally:
            db.close()

    except Exception as exc:
        log.warning("[dataset_builder] DB synthesis skipped: %s", exc)

    log.info("[dataset_builder] Synthesised %d rows from DB (incl. augmentation)", len(rows))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset(
    use_log: bool = True,
    use_db: bool = True,
    min_rows: int = 30,
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """
    Build the complete training dataset.

    Parameters
    ----------
    use_log  : include rows from the assignment log
    use_db   : include synthesised rows from the live DB
    min_rows : raise ValueError if the final dataset is smaller than this

    Returns
    -------
    X   : pd.DataFrame  — feature columns only
    y   : pd.Series     — binary labels (0/1)
    df  : pd.DataFrame  — full dataset including meta (source, success)
    """
    all_rows: list[dict] = []

    if use_log:
        all_rows.extend(_load_from_log())
    if use_db:
        all_rows.extend(_load_from_db())

    if not all_rows:
        raise ValueError(
            "Dataset is empty. Ensure assignment_log.jsonl exists or DB is seeded."
        )

    df = pd.DataFrame(all_rows)

    # ── Fill any remaining missing values ─────────────────────────────────────
    for col, default in FEATURE_DEFAULTS.items():
        if col in df.columns:
            df[col] = df[col].fillna(default)

    # ── Ensure all feature columns are present ────────────────────────────────
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            log.warning("[dataset_builder] Column '%s' missing — filling with default", col)
            df[col] = FEATURE_DEFAULTS[col]

    # ── Drop rows with null labels ────────────────────────────────────────────
    df = df.dropna(subset=["success"])
    df["success"] = df["success"].astype(int)

    if len(df) < min_rows:
        raise ValueError(
            f"Dataset too small ({len(df)} rows < min_rows={min_rows}). "
            "Run seed_tasks.py to generate more data."
        )

    X = df[FEATURE_COLUMNS].copy()
    y = df["success"].copy()

    label_counts = y.value_counts().to_dict()
    log.info(
        "[dataset_builder] Final dataset: %d rows | features=%d | labels=%s",
        len(df), len(FEATURE_COLUMNS), label_counts,
    )
    return X, y, df


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    X, y, df = build_dataset()
    print(f"\nDataset built successfully:")
    print(f"  Rows         : {len(df)}")
    print(f"  Features     : {list(X.columns)}")
    print(f"  Label balance: {y.value_counts().to_dict()}")
    print(f"  Sources      : {df['source'].value_counts().to_dict()}")
    print()
    print(df[FEATURE_COLUMNS + ['success', 'source']].head(10).to_string(index=False))
