"""
app/ml/training/trainer.py
===========================
Trains a LGBMClassifier on the task-success dataset.

Design decisions
----------------
* LightGBM over RandomForest because:
  - significantly faster training and inference (histogram-based boosting)
  - lower memory footprint at inference time
  - native support for feature importances
  - produces reliable probability estimates via predict_proba
  - excellent on small-to-medium datasets

* For very small datasets (< 80 rows) we fall back to LogisticRegression
  which generalises better with limited data.

* Cross-validation is used instead of a single train/test split so
  the small dataset size doesn't make metrics unreliable.

* The trained pipeline (scaler → model) is saved as a single compressed
  pickle (compress=3) so inference never needs to pre-process separately.

Run
---
  python -m app.ml.training.trainer
  python scripts/train_model.py      (convenience wrapper)
"""

from __future__ import annotations
import logging
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# ── Bootstrap project root ────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_ROOT))

from app.ml.training.dataset_builder import build_dataset
from app.ml.training.utils import FEATURE_COLUMNS

log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
MODEL_DIR  = Path(__file__).parent / "models"
MODEL_PATH = MODEL_DIR / "task_success_model.pkl"

# ── Hyper-parameters ──────────────────────────────────────────────────────────
LGBM_PARAMS = {
    "n_estimators":   100,
    "max_depth":       6,
    "learning_rate":   0.1,
    "class_weight":   "balanced",   # handles label imbalance
    "random_state":    42,
    "n_jobs":         -1,
    "verbose":        -1,           # suppress LightGBM training output
}

CV_FOLDS   = 5     # stratified k-fold folds
TEST_SIZE  = 0.20  # held-out test set fraction


# ─────────────────────────────────────────────────────────────────────────────
# Model factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_pipeline(small_dataset: bool = False) -> Pipeline:
    """
    Build a sklearn Pipeline.

    For very small datasets (< 80 rows) we fall back to LogisticRegression
    which generalises better with limited data.
    For normal datasets, LightGBM is used for fast, low-latency inference.
    """
    if small_dataset:
        log.info("[trainer] Small dataset detected — using LogisticRegression fallback")
        clf = LogisticRegression(
            C=1.0, class_weight="balanced", max_iter=500, random_state=42
        )
    else:
        clf = LGBMClassifier(**LGBM_PARAMS)

    return Pipeline([
        ("scaler", StandardScaler()),   # needed for LogReg; harmless for LGBM
        ("clf",    clf),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Training
# ─────────────────────────────────────────────────────────────────────────────

def train(
    use_log:   bool = True,
    use_db:    bool = True,
    save:      bool = True,
) -> dict:
    """
    Full train → evaluate → save cycle.

    Returns
    -------
    dict with keys: accuracy, auc, cv_accuracy_mean, cv_accuracy_std,
                    model_path, n_samples, feature_importances
    """
    t0 = time.time()
    log.info("=" * 60)
    log.info("Task-Success Model Training  —  %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # ── 1. Load dataset ───────────────────────────────────────────────────────
    X, y, df = build_dataset(use_log=use_log, use_db=use_db)
    n = len(X)
    log.info("Dataset: %d samples | %d features | label balance: %s",
             n, len(FEATURE_COLUMNS), y.value_counts().to_dict())

    # ── 2. Train / test split ─────────────────────────────────────────────────
    # If dataset is tiny, skip split and do pure CV
    do_split = n >= 50
    if do_split:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=TEST_SIZE, stratify=y, random_state=42
        )
        log.info("Train: %d  |  Test: %d", len(X_train), len(X_test))
    else:
        X_train, y_train = X, y
        log.warning("Dataset too small for held-out split — using full set for CV only.")

    # ── 3. Build pipeline ─────────────────────────────────────────────────────
    pipeline = _build_pipeline(small_dataset=(n < 80))

    # ── 4. Cross-validation ───────────────────────────────────────────────────
    n_folds = min(CV_FOLDS, y_train.value_counts().min())  # can't have more folds than minority class
    n_folds = max(2, n_folds)
    cv = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
    cv_results = cross_validate(
        pipeline, X_train, y_train,
        cv=cv,
        scoring=["accuracy", "roc_auc"],
        return_train_score=False,
    )
    cv_acc_mean = cv_results["test_accuracy"].mean()
    cv_acc_std  = cv_results["test_accuracy"].std()
    cv_auc_mean = cv_results["test_roc_auc"].mean()
    log.info("CV (%d-fold)  accuracy: %.3f ± %.3f  |  AUC: %.3f",
             n_folds, cv_acc_mean, cv_acc_std, cv_auc_mean)

    # ── 5. Final fit on full training set ────────────────────────────────────
    pipeline.fit(X_train, y_train)

    # ── 6. Evaluate on held-out test set ─────────────────────────────────────
    result: dict = {}
    if do_split:
        y_pred = pipeline.predict(X_test)
        y_prob = pipeline.predict_proba(X_test)[:, 1]
        acc    = accuracy_score(y_test, y_pred)
        auc    = roc_auc_score(y_test, y_prob)
        log.info("Test  accuracy: %.3f  |  AUC: %.3f", acc, auc)
        log.info("Classification report:\n%s", classification_report(y_test, y_pred))
        result["accuracy"] = round(acc,  4)
        result["auc"]      = round(auc,  4)
    else:
        result["accuracy"] = round(cv_acc_mean, 4)
        result["auc"]      = round(cv_auc_mean,  4)

    # ── 7. Feature importance (RF only) ──────────────────────────────────────
    clf_ = pipeline.named_steps["clf"]
    if hasattr(clf_, "feature_importances_"):
        importances = dict(zip(FEATURE_COLUMNS, clf_.feature_importances_.round(4)))
        log.info("Feature importances: %s", importances)
        result["feature_importances"] = importances
    else:
        coef = clf_.coef_[0] if hasattr(clf_, "coef_") else []
        result["feature_importances"] = dict(zip(FEATURE_COLUMNS, [round(float(c), 4) for c in coef]))

    # ── 8. Save ───────────────────────────────────────────────────────────────
    model_path_str = ""
    if save:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(pipeline, MODEL_PATH, compress=3)
        model_path_str = str(MODEL_PATH)
        log.info("Model saved → %s", MODEL_PATH)

    elapsed = time.time() - t0
    log.info("Training complete in %.2fs", elapsed)
    log.info("=" * 60)

    result.update({
        "cv_accuracy_mean": round(cv_acc_mean, 4),
        "cv_accuracy_std":  round(cv_acc_std,  4),
        "cv_auc_mean":      round(cv_auc_mean,  4),
        "n_samples":        n,
        "n_features":       len(FEATURE_COLUMNS),
        "model_path":       model_path_str,
        "elapsed_sec":      round(elapsed, 2),
    })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    result = train()
    print()
    print("=" * 50)
    print("  TRAINING SUMMARY")
    print("=" * 50)
    print(f"  Samples        : {result['n_samples']}")
    print(f"  Features       : {result['n_features']}")
    print(f"  CV Accuracy    : {result['cv_accuracy_mean']:.3f} ± {result['cv_accuracy_std']:.3f}")
    print(f"  CV AUC         : {result['cv_auc_mean']:.3f}")
    print(f"  Test Accuracy  : {result['accuracy']:.3f}")
    print(f"  Test AUC       : {result['auc']:.3f}")
    print(f"  Elapsed        : {result['elapsed_sec']}s")
    print(f"  Model saved    : {result['model_path']}")
    if result.get("feature_importances"):
        print("  Feature importances:")
        for feat, imp in sorted(result["feature_importances"].items(), key=lambda x: -x[1]):
            bar = "#" * int(imp * 40)
            print(f"    {feat:<22} {imp:.4f}  {bar}")
    print("=" * 50)
