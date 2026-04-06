"""
app/ml/retraining/evaluator.py
================================
Compares a candidate new model against the current production model.

Decision rule
-------------
The new model replaces the old ONLY IF:

    new_auc > old_auc + AUC_IMPROVEMENT_THRESHOLD   (default: 0.02)

This conservative gate prevents regressions from noisy small datasets.
When there is no existing production model (first run), the new model
is promoted unconditionally.

Usage
-----
  from app.ml.retraining.evaluator import evaluate_model, compare_models
  new_metrics = evaluate_model(new_pipeline, X_test, y_test)
  decision    = compare_models(old_pipeline, new_pipeline, X_test, y_test)
"""

from __future__ import annotations
import logging
import warnings
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    roc_auc_score,
    f1_score,
    classification_report,
)
from sklearn.pipeline import Pipeline

from app.ml.retraining.utils import AUC_IMPROVEMENT_THRESHOLD

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Per-model evaluation
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_model(
    model: Pipeline,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    label: str = "model",
) -> dict[str, Any]:
    """
    Compute Accuracy, AUC, and F1 for a fitted model on a held-out set.

    Parameters
    ----------
    model  : fitted sklearn Pipeline with predict_proba support
    X_test : test feature DataFrame
    y_test : test label Series
    label  : name used in log messages

    Returns
    -------
    dict with keys: accuracy, auc, f1, n_test, label
    Returns safe sentinel values if evaluation fails.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y_prob = model.predict_proba(X_test)[:, 1]
            y_pred = model.predict(X_test)

        acc = round(float(accuracy_score(y_test, y_pred)), 4)
        f1  = round(float(f1_score(y_test, y_pred, zero_division=0)), 4)

        # roc_auc_score requires at least 2 classes in y_test
        if len(np.unique(y_test)) < 2:
            log.warning("[evaluator] Only one class in y_test for %s — AUC set to 0.5", label)
            auc = 0.5
        else:
            auc = round(float(roc_auc_score(y_test, y_prob)), 4)

        log.info("[evaluator] %s  →  acc=%.3f  auc=%.3f  f1=%.3f", label, acc, auc, f1)
        return {
            "label":    label,
            "accuracy": acc,
            "auc":      auc,
            "f1":       f1,
            "n_test":   int(len(y_test)),
        }

    except Exception as exc:
        log.error("[evaluator] Evaluation failed for %s: %s", label, exc)
        return {
            "label":    label,
            "accuracy": 0.0,
            "auc":      0.0,
            "f1":       0.0,
            "n_test":   int(len(y_test)),
            "error":    str(exc),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Head-to-head comparison
# ─────────────────────────────────────────────────────────────────────────────

def compare_models(
    old_model: Pipeline | None,
    new_model: Pipeline,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    threshold: float = AUC_IMPROVEMENT_THRESHOLD,
) -> dict[str, Any]:
    """
    Evaluate both models on the same test set and decide whether to promote.

    Parameters
    ----------
    old_model : current production model — None on first run (promotes unconditionally)
    new_model : freshly trained candidate model
    X_test    : shared held-out features
    y_test    : shared held-out labels
    threshold : required AUC improvement margin for promotion

    Returns
    -------
    {
        "old_metrics":    dict | None,
        "new_metrics":    dict,
        "should_promote": bool,
        "reason":         str,
        "auc_delta":      float,
    }
    """
    new_metrics = evaluate_model(new_model, X_test, y_test, label="new_model")

    # ── First run: no existing production model ───────────────────────────────
    if old_model is None:
        log.info("[evaluator] No existing model — promoting new model unconditionally.")
        return {
            "old_metrics":    None,
            "new_metrics":    new_metrics,
            "should_promote": True,
            "reason":         "first_run_no_existing_model",
            "auc_delta":      None,
        }

    old_metrics = evaluate_model(old_model, X_test, y_test, label="old_model")

    auc_delta  = round(new_metrics["auc"] - old_metrics["auc"], 4)
    promote    = auc_delta > threshold

    if promote:
        reason = (
            f"new AUC ({new_metrics['auc']:.4f}) exceeds old "
            f"({old_metrics['auc']:.4f}) by {auc_delta:.4f} > threshold {threshold}"
        )
        log.info("[evaluator] PROMOTE — %s", reason)
    else:
        reason = (
            f"new AUC ({new_metrics['auc']:.4f}) does not exceed old "
            f"({old_metrics['auc']:.4f}) by required {threshold} (delta={auc_delta:.4f})"
        )
        log.info("[evaluator] REJECT  — %s", reason)

    return {
        "old_metrics":    old_metrics,
        "new_metrics":    new_metrics,
        "should_promote": promote,
        "reason":         reason,
        "auc_delta":      auc_delta,
    }
