"""
app/ml/retraining/shadow.py
============================
Shadow-mode inference: run the latest *candidate* model in parallel with
the production model without affecting any assignment decisions.

Shadow predictions are logged as an extra field in the assignment record
so they can be analysed offline to decide whether to promote the candidate.

Usage (called automatically from scorer.py when SHADOW_MODE = True)
-----
  from app.ml.retraining.shadow import shadow_predict_batch
  shadow_probs = shadow_predict_batch(features_list)
  # returns list[float | None] — None means shadow is unavailable or failed
"""

from __future__ import annotations
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level cache so we don't reload the candidate pkl on every request
_SHADOW_MODEL = None
_SHADOW_VERSION: str | None = None


def _load_candidate_model():
    """
    Load the latest model with status 'candidate' from the registry.
    Returns the loaded pipeline, or None if no candidate exists.
    Updates module-level cache.
    """
    global _SHADOW_MODEL, _SHADOW_VERSION

    try:
        from app.ml.retraining.model_registry import ModelRegistry
        registry = ModelRegistry()
        versions = registry.list_versions()

        # Find the most recently trained candidate
        candidates = [v for v in versions if v.get("status") == "candidate"]
        if not candidates:
            return None

        # Sort by version number desc to get the latest candidate
        def _ver_num(v: dict) -> int:
            ver = v.get("version", "v0")
            return int(ver[1:]) if ver.startswith("v") and ver[1:].isdigit() else 0

        latest = max(candidates, key=_ver_num)
        ver    = latest.get("version")

        if ver == _SHADOW_VERSION and _SHADOW_MODEL is not None:
            return _SHADOW_MODEL   # already cached

        model_path = Path(latest.get("path", ""))
        if not model_path.exists():
            # Try the standard naming convention as a fallback
            model_path = registry.models_dir / f"task_model_{ver}.pkl"

        if not model_path.exists():
            log.debug("[shadow] Candidate model file not found: %s", model_path)
            return None

        import joblib
        _SHADOW_MODEL   = joblib.load(model_path)
        _SHADOW_VERSION = ver
        log.info("[shadow] Loaded candidate model %s from %s", ver, model_path)
        return _SHADOW_MODEL

    except Exception as exc:
        log.debug("[shadow] Could not load candidate model: %s", exc)
        return None


def shadow_predict_batch(features_list: list[dict]) -> list[float | None]:
    """
    Run batch inference on the candidate model.

    Parameters
    ----------
    features_list : list of canonical feature dicts
        (active_tasks, overdue_tasks, completed_tasks, performance_score)

    Returns
    -------
    list[float | None]
        Predicted success probabilities (0–1), or None per entry on failure.
        Never raises — all errors are swallowed to protect the main flow.
    """
    try:
        model = _load_candidate_model()
        if model is None:
            return [None] * len(features_list)

        from app.ml.retraining.utils import FEATURE_COLUMNS, extract_canonical_features
        import pandas as pd

        rows = []
        for f in features_list:
            canonical = extract_canonical_features(f) or {}
            rows.append({col: canonical.get(col, 0.0) for col in FEATURE_COLUMNS})

        X = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
        probs = model.predict_proba(X)[:, 1]
        return [round(float(p), 4) for p in probs]

    except Exception as exc:
        log.debug("[shadow] Batch prediction failed: %s", exc)
        return [None] * len(features_list)
