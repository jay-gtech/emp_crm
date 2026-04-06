"""
scripts/train_model.py
=======================
Convenience entry-point for the task-success ML training pipeline.

Usage (from project root)
-------------------------
  python scripts/train_model.py               # train + save model
  python scripts/train_model.py --dataset     # only show dataset stats
  python scripts/train_model.py --predict     # test prediction after training

What it does
------------
1. Builds dataset from assignment_log.jsonl + live DB
2. Trains RandomForestClassifier (or LogisticRegression for small data)
3. Evaluates with cross-validation + held-out test set
4. Saves model to app/ml/training/models/task_success_model.pkl
5. Tests inference on a sample feature vector
"""

import sys
import os
import logging
import argparse
from pathlib import Path

# ── Bootstrap project root ────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

parser = argparse.ArgumentParser(description="Train the task-success ML model.")
parser.add_argument("--dataset", action="store_true", help="Print dataset stats only")
parser.add_argument("--predict", action="store_true", help="Run sample prediction after training")
parser.add_argument("--log-only",  action="store_true", help="Use only assignment_log (no DB)")
parser.add_argument("--db-only",   action="store_true", help="Use only DB data (no log)")
args = parser.parse_args()

use_log = not args.db_only
use_db  = not args.log_only

# ────────────────────────────────────────────────────────────────────────────
if args.dataset:
    from app.ml.training.dataset_builder import build_dataset
    X, y, df = build_dataset(use_log=use_log, use_db=use_db)
    print(f"\n{'='*50}")
    print("  DATASET SUMMARY")
    print(f"{'='*50}")
    print(f"  Total rows   : {len(df)}")
    print(f"  Features     : {list(X.columns)}")
    print(f"  Labels       : {y.value_counts().to_dict()}")
    print(f"  Sources      : {df['source'].value_counts().to_dict()}")
    print(f"\n  Feature stats:")
    print(X.describe().to_string())
    sys.exit(0)

# ── Train ─────────────────────────────────────────────────────────────────────
from app.ml.training.trainer import train

result = train(use_log=use_log, use_db=use_db, save=True)

print()
print("=" * 52)
print("  TRAINING SUMMARY")
print("=" * 52)
print(f"  Samples         : {result['n_samples']}")
print(f"  Features        : {result['n_features']}")
print(f"  CV Accuracy     : {result['cv_accuracy_mean']:.3f} ± {result['cv_accuracy_std']:.3f}")
print(f"  CV AUC          : {result['cv_auc_mean']:.3f}")
print(f"  Test Accuracy   : {result['accuracy']:.3f}")
print(f"  Test AUC        : {result['auc']:.3f}")
print(f"  Training time   : {result['elapsed_sec']}s")
print(f"  Model saved to  : {result['model_path']}")
if result.get("feature_importances"):
    print("  Feature importances:")
    for feat, imp in sorted(result["feature_importances"].items(), key=lambda x: -abs(x[1])):
        bar = "#" * int(abs(imp) * 40)
        print(f"    {feat:<22} {imp:+.4f}  {bar}")
print("=" * 52)

# ── Optional: test inference ──────────────────────────────────────────────────
if args.predict:
    from app.ml.training.model import predict_success, load_model, get_model_info

    print()
    print("  MODEL INFO")
    print("  " + "-" * 48)
    info = get_model_info()
    for k, v in info.items():
        if k != "model_params":
            print(f"    {k:<20}: {v}")

    test_cases = [
        ("Ideal (low load, high perf)",  {"active_tasks": 0,  "overdue_tasks": 0, "completed_tasks": 12, "performance_score": 88}),
        ("Average employee",             {"active_tasks": 4,  "overdue_tasks": 1, "completed_tasks":  6, "performance_score": 70}),
        ("Overloaded (heavy, overdue)",  {"active_tasks": 13, "overdue_tasks": 5, "completed_tasks":  2, "performance_score": 58}),
        ("New employee (no history)",    {"active_tasks": 0,  "overdue_tasks": 0, "completed_tasks":  0, "performance_score": 50}),
    ]
    print()
    print("  SAMPLE PREDICTIONS")
    print(f"  {'Scenario':<38} {'Prob':>6}  {'Class':>5}")
    print("  " + "-" * 55)
    for label, feats in test_cases:
        out = predict_success(feats)
        cls_label = "SUCCESS" if out["predicted_class"] == 1 else ("AT-RISK" if out["predicted_class"] == 0 else "heuristic")
        print(f"  {label:<38} {out['success_probability']:>6.3f}  {cls_label:>8}")
    print()
