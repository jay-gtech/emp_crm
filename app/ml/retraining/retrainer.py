"""
app/ml/retraining/retrainer.py
================================
Trains a fresh LightGBM model from a pre-built dataset.

Returns the trained sklearn Pipeline + a held-out test split for evaluation.
Does NOT touch the production model path — that is the registry's job.

Usage
-----
  from app.ml.retraining.retrainer import retrain
  pipeline, X_test, y_test, train_meta = retrain(X, y)
"""

from __future__ import annotations
import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.ml.retraining.utils import TEST_SPLIT_RATIO, FEATURE_COLUMNS

log = logging.getLogger(__name__)

# ── Hyper-parameters ──────────────────────────────────────────────────────────
LGBM_PARAMS: dict[str, Any] = {
    "n_estimators":  120,
    "max_depth":       6,
    "learning_rate":   0.05,
    "class_weight":  "balanced",
    "random_state":   42,
    "n_jobs":         -1,
    "verbose":        -1,
}

SMALL_DATASET_THRESHOLD = 80   # rows — use LogisticRegression below this


# ─────────────────────────────────────────────────────────────────────────────
# Internal pipeline factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_pipeline(n_samples: int) -> Pipeline:
    """
    Returns a sklearn Pipeline appropriate for the dataset size.

    Small datasets (< 80 rows) use LogisticRegression which generalises
    better when training examples are few.  Larger datasets use LightGBM
    for fast, low-latency inference.
    """
    if n_samples < SMALL_DATASET_THRESHOLD:
        log.info("[retrainer] Small dataset (%d rows) — using LogisticRegression", n_samples)
        clf = LogisticRegression(
            C=1.0, class_weight="balanced", max_iter=500, random_state=42
        )
    else:
        clf = LGBMClassifier(**LGBM_PARAMS)

    return Pipeline([
        ("scaler", StandardScaler()),  # needed for LogReg; harmless for LGBM
        ("clf",    clf),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def retrain(
    X: pd.DataFrame,
    y: pd.Series,
) -> tuple[Pipeline, pd.DataFrame, pd.Series, dict]:
    """
    Train a new model on (X, y) and return the fitted pipeline along with
    a held-out test split for independent evaluation.

    Parameters
    ----------
    X : feature DataFrame with FEATURE_COLUMNS columns
    y : binary label Series (0/1)

    Returns
    -------
    pipeline  : fitted sklearn Pipeline (ready for predict_proba)
    X_test    : held-out test features
    y_test    : held-out test labels
    meta      : training metadata dict (model_type, n_train, n_test, cv_auc, …)
    """
    t0 = time.time()
    n  = len(X)
    log.info("[retrainer] Starting training — %d samples, %d features", n, len(FEATURE_COLUMNS))

    # ── Train / test split ────────────────────────────────────────────────────
    do_split = n >= 50
    if do_split:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=TEST_SPLIT_RATIO, stratify=y, random_state=42
        )
        log.info("[retrainer] Train: %d  |  Test: %d", len(X_train), len(X_test))
    else:
        X_train, y_train = X, y
        X_test  = X.copy()
        y_test  = y.copy()
        log.warning("[retrainer] Dataset too small for held-out split — using full set")

    # ── Build and cross-validate ──────────────────────────────────────────────
    pipeline = _build_pipeline(n)

    n_folds = min(5, y_train.value_counts().min())
    n_folds = max(2, n_folds)
    cv = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    cv_results = cross_validate(
        pipeline, X_train, y_train,
        cv=cv,
        scoring=["accuracy", "roc_auc"],
        return_train_score=False,
    )
    cv_auc_mean = float(cv_results["test_roc_auc"].mean())
    cv_acc_mean = float(cv_results["test_accuracy"].mean())
    log.info(
        "[retrainer] CV (%d-fold)  AUC: %.3f  |  Accuracy: %.3f",
        n_folds, cv_auc_mean, cv_acc_mean,
    )

    # ── Final fit on full training set ────────────────────────────────────────
    pipeline.fit(X_train, y_train)

    # ── Feature importances ───────────────────────────────────────────────────
    clf_ = pipeline.named_steps["clf"]
    if hasattr(clf_, "feature_importances_"):
        importances = dict(zip(FEATURE_COLUMNS, clf_.feature_importances_.round(4)))
    elif hasattr(clf_, "coef_"):
        importances = dict(zip(FEATURE_COLUMNS, [round(float(c), 4) for c in clf_.coef_[0]]))
    else:
        importances = {}

    elapsed = round(time.time() - t0, 2)
    log.info("[retrainer] Training complete in %.2fs", elapsed)

    meta = {
        "model_type":       type(clf_).__name__,
        "n_train":          len(X_train),
        "n_test":           len(X_test),
        "n_features":       len(FEATURE_COLUMNS),
        "cv_folds":         n_folds,
        "cv_auc_mean":      round(cv_auc_mean, 4),
        "cv_accuracy_mean": round(cv_acc_mean, 4),
        "feature_importances": importances,
        "elapsed_sec":      elapsed,
    }
    return pipeline, X_test, y_test, meta
