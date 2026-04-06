"""
app/ml/training/model.py
=========================
Inference layer — loads the trained model and exposes a clean predict API.

Caching
-------
The model is loaded once into _MODEL_CACHE on first call and reused
on every subsequent request.  This avoids disk I/O on every prediction.

Future integration
------------------
Once this model matures, replace rule-based scoring in scorer.py with:

    from app.ml.training.model import predict_success_proba
    prob = predict_success_proba(features)   # 0.0 – 1.0

The caller can blend rule score + ML probability:
    final_score = 0.4 * rule_score + 0.6 * (prob * 100)
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
MODEL_PATH = Path(__file__).parent / "models" / "task_success_model.pkl"

# ── Module-level cache ────────────────────────────────────────────────────────
_MODEL_CACHE: Optional[object] = None


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

def load_model(force_reload: bool = False):
    """
    Load the trained sklearn Pipeline from disk (cached after first load).

    Parameters
    ----------
    force_reload : bypass cache and reload from disk (useful after retraining)

    Returns
    -------
    sklearn Pipeline  or  None if the model file does not exist yet.
    """
    global _MODEL_CACHE
    if _MODEL_CACHE is not None and not force_reload:
        return _MODEL_CACHE

    if not MODEL_PATH.exists():
        log.warning(
            "[model] Model file not found at %s. "
            "Run 'python -m app.ml.training.trainer' to train first.",
            MODEL_PATH,
        )
        return None

    try:
        import joblib
        _MODEL_CACHE = joblib.load(MODEL_PATH)
        log.info("[model] Model loaded from %s", MODEL_PATH)
        return _MODEL_CACHE
    except Exception as exc:
        log.error("[model] Failed to load model: %s", exc)
        return None


def is_model_available() -> bool:
    """Return True if a trained model file exists."""
    return MODEL_PATH.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────

def predict_success(features: dict) -> dict:
    """
    Predict task-success probability for one employee feature set.

    Parameters
    ----------
    features : dict with keys:
        active_tasks, overdue_tasks, completed_tasks, performance_score

    Returns
    -------
    {
        "success_probability": float  (0.0 – 1.0),
        "predicted_class":     int    (0 = at-risk, 1 = likely success),
        "model_available":     bool,
    }

    Falls back gracefully if the model is not yet trained.
    """
    # ── Import utils here to avoid circular at module load ────────────────────
    from app.ml.training.utils import build_feature_vector

    model = load_model()
    if model is None:
        return {
            "success_probability": _heuristic_fallback(features),
            "predicted_class":     -1,
            "model_available":     False,
        }

    try:
        import warnings
        vec_arr = build_feature_vector(features).reshape(1, -1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # suppress sklearn feature-name warning
            proba         = model.predict_proba(vec_arr)[0][1]
            predicted_cls = int(model.predict(vec_arr)[0])
        return {
            "success_probability": round(float(proba), 4),
            "predicted_class":     predicted_cls,
            "model_available":     True,
        }
    except Exception as exc:
        log.error("[model] Prediction failed: %s", exc)
        return {
            "success_probability": _heuristic_fallback(features),
            "predicted_class":     -1,
            "model_available":     False,
        }


def predict_success_proba(features: dict) -> float:
    """
    Convenience wrapper — returns just the success probability (0.0–1.0).
    Safe to use in scorer.py when ML is ready to replace rule-based logic.
    """
    return predict_success(features)["success_probability"]


def reload_model():
    """
    Force-reload the model from disk, replacing the in-memory cache.

    Call this after the retraining pipeline promotes a new model so the
    running FastAPI process picks it up without a server restart.

    Returns the freshly loaded model (or None if the file is missing).
    """
    return load_model(force_reload=True)


def predict_batch_proba(features_list: list[dict]) -> np.ndarray:
    """
    Batch inference — score all employees in a single model call.

    Loads the cached model (no disk I/O after first call) and returns
    an array of success probabilities in the same order as features_list.

    Parameters
    ----------
    features_list : list of feature dicts (active_tasks, overdue_tasks, …)

    Returns
    -------
    np.ndarray of shape (n,) with dtype float64, values in [0.0, 1.0].
    Falls back to heuristic values if the model is unavailable.
    """
    from app.ml.training.utils import build_feature_array

    model = load_model()
    if model is None:
        return np.array([_heuristic_fallback(f) for f in features_list])

    try:
        import warnings
        X = build_feature_array(features_list)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # suppress sklearn feature-name warning
            probs = model.predict_proba(X)[:, 1]
        return probs
    except Exception as exc:
        log.error("[model] Batch prediction failed: %s — using heuristic fallback", exc)
        return np.array([_heuristic_fallback(f) for f in features_list])


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic fallback (when model is not yet available)
# ─────────────────────────────────────────────────────────────────────────────

def _heuristic_fallback(features: dict) -> float:
    """
    Rule-based probability estimate used before the model is trained.
    Mirrors the scorer formula, normalised to [0, 1].
    """
    active  = float(features.get("active_tasks",      features.get("active_tasks_count",      0)))
    overdue = float(features.get("overdue_tasks",     features.get("overdue_tasks_count",     0)))
    perf    = float(features.get("performance_score", 50.0))

    # Simple penalty-based formula, clamped to [0.1, 0.95]
    raw = 1.0 - (active * 0.05) - (overdue * 0.15) + (perf - 50) * 0.003
    return round(max(0.05, min(0.95, raw)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Model metadata
# ─────────────────────────────────────────────────────────────────────────────

def get_model_info() -> dict:
    """
    Return metadata about the loaded model (type, params, path).
    Useful for admin dashboards and API health checks.
    """
    model = load_model()
    if model is None:
        return {"status": "not_trained", "model_path": str(MODEL_PATH)}

    clf = model.named_steps.get("clf", model)
    return {
        "status":       "loaded",
        "model_type":   type(clf).__name__,
        "model_path":   str(MODEL_PATH),
        "n_features":   len(model.named_steps.get("scaler").mean_) if hasattr(model.named_steps.get("scaler"), "mean_") else "unknown",
        "model_params": clf.get_params() if hasattr(clf, "get_params") else {},
    }
