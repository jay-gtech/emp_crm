"""
app/ml/retraining/utils.py
===========================
Shared constants and helpers for the continuous retraining pipeline.

Keep this module pure Python — no SQLAlchemy, no FastAPI imports.
"""

from __future__ import annotations
from pathlib import Path

# ── Paths (relative to project root) ─────────────────────────────────────────
_PACKAGE_DIR    = Path(__file__).parent
MODELS_DIR      = _PACKAGE_DIR / "models"
METADATA_FILE   = MODELS_DIR / "metadata.json"

# Production model path — this is what model.py (inference) loads.
PRODUCTION_MODEL_PATH = (
    Path(__file__).parent.parent / "training" / "models" / "task_success_model.pkl"
)

# Source of raw training events
LOG_FILE = Path(__file__).parent.parent / "auto_assignment" / "assignment_log.jsonl"

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_ROWS                  = 50     # skip retraining if dataset is smaller
AUC_IMPROVEMENT_THRESHOLD = 0.02   # new model must beat old by this margin
TEST_SPLIT_RATIO          = 0.20   # fraction of data held out for evaluation

# ── Feature schema (must stay in sync with app/ml/training/utils.py) ─────────
FEATURE_COLUMNS: list[str] = [
    "active_tasks",
    "overdue_tasks",
    "completed_tasks",
    "performance_score",
]

FEATURE_DEFAULTS: dict[str, float] = {
    "active_tasks":      0.0,
    "overdue_tasks":     0.0,
    "completed_tasks":   0.0,
    "performance_score": 50.0,
}

FEATURE_CLAMPS: dict[str, tuple[float, float]] = {
    "active_tasks":      (0.0,  50.0),
    "overdue_tasks":     (0.0,  50.0),
    "completed_tasks":   (0.0, 500.0),
    "performance_score": (0.0, 100.0),
}

# Legacy key aliases found in older log entries
_KEY_ALIASES: dict[str, str] = {
    "active_tasks_count":    "active_tasks",
    "overdue_tasks_count":   "overdue_tasks",
    "completed_tasks_count": "completed_tasks",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_float(value, default: float = 0.0) -> float:
    """Convert value to float, returning default on failure."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_canonical_features(raw_features: dict) -> dict[str, float] | None:
    """
    Pull only the 4 canonical FEATURE_COLUMNS from a raw features dict,
    applying key aliasing, default filling, and range clamping.

    Returns None if the dict is empty or completely unparseable.
    Intentionally strips internal ML fields (rule_score, ml_prob, …)
    to prevent feature leakage during training.
    """
    if not raw_features:
        return None

    # Rename legacy keys
    normalised: dict[str, object] = {}
    for k, v in raw_features.items():
        canonical = _KEY_ALIASES.get(k, k)
        normalised[canonical] = v

    row: dict[str, float] = {}
    for col in FEATURE_COLUMNS:
        raw_val  = normalised.get(col, FEATURE_DEFAULTS[col])
        value    = safe_float(raw_val, FEATURE_DEFAULTS[col])
        lo, hi   = FEATURE_CLAMPS[col]
        row[col] = max(lo, min(hi, value))

    return row
