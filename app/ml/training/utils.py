"""
app/ml/training/utils.py
========================
Shared constants, feature ordering, and safe data conversion utilities.

This file is the single source of truth for feature names and defaults
across the entire training pipeline.  Both dataset_builder.py (training)
and model.py (inference) import from here — so the feature vector is
always aligned.
"""

from __future__ import annotations
import logging
import numpy as np

log = logging.getLogger(__name__)

# ── Feature schema ────────────────────────────────────────────────────────────
# ORDER MATTERS — the model is trained on this exact column order.
# Never reorder without retraining the model.

FEATURE_COLUMNS: list[str] = [
    "active_tasks",        # pending + in_progress tasks currently assigned
    "overdue_tasks",       # active tasks past their due_date
    "completed_tasks",     # historical completed count (delivery history)
    "performance_score",   # employee rating 0–100
]

# Safe defaults used when a feature is missing or null
FEATURE_DEFAULTS: dict[str, float] = {
    "active_tasks":      0.0,
    "overdue_tasks":     0.0,
    "completed_tasks":   0.0,
    "performance_score": 50.0,   # neutral midpoint
}

# Reasonable clamp ranges to catch outliers / corrupt data
FEATURE_CLAMPS: dict[str, tuple[float, float]] = {
    "active_tasks":      (0.0, 50.0),
    "overdue_tasks":     (0.0, 50.0),
    "completed_tasks":   (0.0, 500.0),
    "performance_score": (0.0, 100.0),
}

# Legacy key aliases — old log entries used different names
_KEY_ALIASES: dict[str, str] = {
    "active_tasks_count":    "active_tasks",
    "overdue_tasks_count":   "overdue_tasks",
    "completed_tasks_count": "completed_tasks",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalise_feature_keys(raw: dict) -> dict:
    """
    Rename legacy / aliased keys to canonical names.
    Returns a *new* dict — never mutates the input.
    """
    out: dict[str, object] = {}
    for k, v in raw.items():
        canonical = _KEY_ALIASES.get(k, k)
        out[canonical] = v
    return out


def safe_float(value, default: float = 0.0) -> float:
    """Convert any value to float, returning `default` on failure."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_feature_vector(features: dict) -> np.ndarray:
    """
    Convert a feature dict → a 1-D numpy array in the canonical FEATURE_COLUMNS order.

    Handles:
      - missing keys         → FEATURE_DEFAULTS
      - legacy key names     → remapped via _KEY_ALIASES
      - out-of-range values  → clamped to FEATURE_CLAMPS
      - non-numeric values   → replaced with default

    Returns
    -------
    np.ndarray of shape (len(FEATURE_COLUMNS),) with dtype float32
    """
    normalised = normalise_feature_keys(features)
    row: list[float] = []
    for col in FEATURE_COLUMNS:
        raw_val  = normalised.get(col, FEATURE_DEFAULTS[col])
        value    = safe_float(raw_val, FEATURE_DEFAULTS[col])
        lo, hi   = FEATURE_CLAMPS[col]
        value    = max(lo, min(hi, value))
        row.append(value)
    return np.array(row, dtype=np.float32)


def build_feature_array(features_list: list[dict]) -> np.ndarray:
    """
    Convert a list of feature dicts → a 2-D numpy array in FEATURE_COLUMNS order.
    Used for fast batch inference (no pandas overhead).

    Parameters
    ----------
    features_list : list of feature dicts (same schema as build_feature_vector)

    Returns
    -------
    np.ndarray of shape (len(features_list), len(FEATURE_COLUMNS)), dtype float32
    """
    rows: list[list[float]] = []
    for features in features_list:
        normalised = normalise_feature_keys(features)
        row: list[float] = []
        for col in FEATURE_COLUMNS:
            raw_val = normalised.get(col, FEATURE_DEFAULTS[col])
            value   = safe_float(raw_val, FEATURE_DEFAULTS[col])
            lo, hi  = FEATURE_CLAMPS[col]
            row.append(max(lo, min(hi, value)))
        rows.append(row)
    return np.array(rows, dtype=np.float32)


def features_to_dataframe_row(features: dict) -> dict[str, float]:
    """
    Return a flat dict keyed by canonical feature names, ready for pd.DataFrame.
    Useful in dataset_builder.py.
    """
    normalised = normalise_feature_keys(features)
    row: dict[str, float] = {}
    for col in FEATURE_COLUMNS:
        raw_val  = normalised.get(col, FEATURE_DEFAULTS[col])
        value    = safe_float(raw_val, FEATURE_DEFAULTS[col])
        lo, hi   = FEATURE_CLAMPS[col]
        row[col] = max(lo, min(hi, value))
    return row
