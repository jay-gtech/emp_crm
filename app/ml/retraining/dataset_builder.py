"""
app/ml/retraining/dataset_builder.py
=====================================
Converts the raw assignment_log.jsonl into a clean, labelled training set
for the retraining pipeline.

Label hierarchy
---------------
1. Real outcome (preferred): from "outcome" or "outcome_update" events.
     success = 1  iff  completed == True  AND  was_delayed == False
2. Proxy label (fallback): when no outcome is recorded for a task.
     success = 1  iff  overdue_tasks == 0

Deduplication
-------------
A single task_id may appear many times in the log (user clicking auto-assign
repeatedly).  We keep only the **last** assignment event per task_id so the
feature vector reflects the state at final decision time.

Feature extraction
------------------
Only the 4 canonical FEATURE_COLUMNS are extracted from each event's
"features" dict.  Internal scoring fields (rule_score, ml_prob, …) are
intentionally dropped to prevent target leakage.

Usage
-----
  from app.ml.retraining.dataset_builder import build_retraining_dataset
  X, y, meta = build_retraining_dataset()
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.retraining.utils import (
    LOG_FILE,
    MIN_ROWS,
    FEATURE_COLUMNS,
    extract_canonical_features,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_log(log_file: Path) -> tuple[dict[int, dict], dict[int, dict]]:
    """
    Single-pass read of the JSONL log.

    Returns
    -------
    assignments : dict  task_id → latest assignment record (features + metadata)
    outcomes    : dict  task_id → resolved outcome {completed, was_delayed}
    """
    assignments: dict[int, dict] = {}
    outcomes:    dict[int, dict] = {}

    if not log_file.exists():
        log.warning("[dataset_builder] Log not found: %s", log_file)
        return assignments, outcomes

    with open(log_file, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning("[dataset_builder] Malformed JSON at line %d — skipped", lineno)
                continue

            etype   = entry.get("event_type", "")
            task_id = entry.get("task_id")
            if task_id is None:
                continue

            # ── Assignment event ───────────────────────────────────────────────
            if etype == "assignment":
                raw = entry.get("features", {})
                features = extract_canonical_features(raw)
                if features is None:
                    continue
                # Always overwrite so we keep the LAST entry per task_id
                assignments[task_id] = {
                    "features":    features,
                    "employee_id": entry.get("employee_id"),
                    "timestamp":   entry.get("timestamp"),
                }

            # ── Outcome event (old format from update_assignment_outcome) ──────
            elif etype == "outcome":
                success_val = entry.get("success")
                delay_days  = entry.get("delay_days", 0)
                if success_val is not None:
                    outcomes[task_id] = {
                        "completed":   bool(success_val),
                        "was_delayed": delay_days > 0,
                        "source":      "outcome",
                    }

            # ── Outcome update event (from outcome_tracking_service) ───────────
            elif etype == "outcome_update":
                outcome_obj = entry.get("outcome", {})
                completed   = outcome_obj.get("completed")
                was_delayed = outcome_obj.get("was_delayed")
                if completed is not None:
                    outcomes[task_id] = {
                        "completed":   bool(completed),
                        "was_delayed": bool(was_delayed) if was_delayed is not None else False,
                        "source":      "outcome_update",
                    }

    return assignments, outcomes


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point
# ─────────────────────────────────────────────────────────────────────────────

def build_retraining_dataset(
    log_file: Path | None = None,
    min_rows: int = MIN_ROWS,
) -> tuple[pd.DataFrame, pd.Series, dict]:
    """
    Build a clean training dataset from the assignment log.

    Parameters
    ----------
    log_file : override the default LOG_FILE path (useful in tests)
    min_rows : raise ValueError if the final dataset is smaller

    Returns
    -------
    X    : pd.DataFrame  — shape (n, 4), canonical FEATURE_COLUMNS
    y    : pd.Series     — binary labels (0 = at-risk, 1 = success)
    meta : dict          — dataset statistics for reporting
    """
    src = log_file or LOG_FILE

    assignments, outcomes = _parse_log(src)

    if not assignments:
        raise ValueError(
            f"No assignment events found in {src}. "
            "Run the auto-assign system first to generate data."
        )

    rows:           list[dict] = []
    real_label_cnt: int        = 0
    proxy_label_cnt: int       = 0

    for task_id, asgn in assignments.items():
        features = asgn["features"]
        row      = dict(features)  # copy so we don't mutate

        if task_id in outcomes:
            # ── Real supervised label ─────────────────────────────────────────
            o       = outcomes[task_id]
            label   = 1 if (o["completed"] and not o["was_delayed"]) else 0
            source  = f"real_{o['source']}"
            real_label_cnt += 1
        else:
            # ── Proxy label: no overdue tasks → expected success ──────────────
            label  = 1 if features["overdue_tasks"] == 0.0 else 0
            source = "proxy"
            proxy_label_cnt += 1

        row["success"] = label
        row["source"]  = source
        rows.append(row)

    if not rows:
        raise ValueError("Dataset is empty after parsing log.")

    df = pd.DataFrame(rows)

    # Fill any gaps (should not happen after extract_canonical_features, but be safe)
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            from app.ml.retraining.utils import FEATURE_DEFAULTS
            df[col] = FEATURE_DEFAULTS[col]

    df = df.dropna(subset=["success"])
    df["success"] = df["success"].astype(int)

    n_total = len(df)
    if n_total < min_rows:
        raise ValueError(
            f"Dataset too small for retraining: {n_total} rows < min_rows={min_rows}. "
            "Accumulate more assignment data or lower MIN_ROWS."
        )

    X = df[FEATURE_COLUMNS].copy()
    y = df["success"].copy()

    label_counts = y.value_counts().to_dict()

    # ── Class imbalance / bias guard ──────────────────────────────────────────
    bias_warning: str | None = None
    IMBALANCE_THRESHOLD = 0.10   # flag if minority class < 10 %
    n_pos = int(label_counts.get(1, 0))
    n_neg = int(label_counts.get(0, 0))
    if n_total > 0:
        minority_frac = min(n_pos, n_neg) / n_total
        if minority_frac < IMBALANCE_THRESHOLD:
            bias_warning = (
                f"Class imbalance detected: minority class is "
                f"{minority_frac:.1%} of {n_total} rows "
                f"(pos={n_pos}, neg={n_neg}). "
                "Model may be biased toward the majority class."
            )
            log.warning("[dataset_builder] %s", bias_warning)

    log.info(
        "[dataset_builder] Dataset ready: %d rows | real=%d proxy=%d | labels=%s",
        n_total, real_label_cnt, proxy_label_cnt, label_counts,
    )

    meta = {
        "n_rows":          n_total,
        "n_real_labels":   real_label_cnt,
        "n_proxy_labels":  proxy_label_cnt,
        "label_counts":    label_counts,
        "n_assignments":   len(assignments),
        "n_outcomes":      len(outcomes),
        "features":        FEATURE_COLUMNS,
        "bias_warning":    bias_warning,
    }
    return X, y, meta


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    try:
        X, y, meta = build_retraining_dataset()
        print(f"\nDataset built successfully:")
        print(f"  Total rows    : {meta['n_rows']}")
        print(f"  Real labels   : {meta['n_real_labels']}")
        print(f"  Proxy labels  : {meta['n_proxy_labels']}")
        print(f"  Label balance : {meta['label_counts']}")
        print(f"\n  Feature stats:")
        print(X.describe().to_string())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
