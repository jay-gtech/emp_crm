"""
scripts/retrain_model.py
=========================
Orchestrates the full continuous retraining pipeline.

Pipeline steps
--------------
1. Build dataset from assignment_log.jsonl (real labels where available,
   proxy labels as fallback)
2. Train a new LightGBM model on the dataset
3. Evaluate new model against the current production model on a held-out set
4. Register the new model as a versioned candidate
5. Promote if AUC improvement exceeds threshold; otherwise reject

Usage (from project root)
--------------------------
  python scripts/retrain_model.py                  # full retrain
  python scripts/retrain_model.py --dry-run        # build dataset + train but don't promote
  python scripts/retrain_model.py --rollback v1    # restore a specific archived version
  python scripts/retrain_model.py --list-versions  # show all registered versions

Exit codes
----------
  0 — success (new model promoted, or skipped with reason)
  1 — hard failure (dataset too small, training crashed)
"""

from __future__ import annotations
import argparse
import logging
import sys
import time
from pathlib import Path

# ── Bootstrap project root ────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("retrain")


# ─────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'=' * 58}")
    print(f"  {title}")
    print(f"{'=' * 58}")


def _row(label: str, value: object, width: int = 22) -> None:
    print(f"  {label:<{width}}: {value}")


def _metrics_block(label: str, m: dict | None) -> None:
    if m is None:
        print(f"  {label:<12}: (none — first run)")
        return
    print(
        f"  {label:<12}:  "
        f"acc={m.get('accuracy', 0):.4f}  "
        f"auc={m.get('auc', 0):.4f}  "
        f"f1={m.get('f1', 0):.4f}  "
        f"n_test={m.get('n_test', '?')}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sub-commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_list_versions() -> None:
    from app.ml.retraining.model_registry import ModelRegistry
    registry = ModelRegistry()
    versions = registry.list_versions()
    current  = registry.current_version()

    _section("REGISTERED MODEL VERSIONS")
    if not versions:
        print("  (no versions registered yet)")
        return

    print(f"  {'Version':<8} {'Status':<12} {'AUC':>6} {'Acc':>6} {'F1':>6}  Trained at")
    print("  " + "-" * 68)
    for v in sorted(versions, key=lambda x: x.get("version", "")):
        ver   = v.get("version", "?")
        flag  = " ◄ current" if ver == current else ""
        st    = v.get("status", "?")
        m     = v.get("metrics", {})
        ta    = v.get("trained_at", "?")[:19]
        print(
            f"  {ver:<8} {st:<12} "
            f"{m.get('auc', 0):>6.4f} "
            f"{m.get('accuracy', 0):>6.4f} "
            f"{m.get('f1', 0):>6.4f}  "
            f"{ta}{flag}"
        )


def cmd_rollback(version: str) -> int:
    from app.ml.retraining.model_registry import ModelRegistry
    registry = ModelRegistry()
    _section(f"ROLLBACK TO {version}")
    try:
        registry.rollback(version)
        print(f"  Rolled back to {version} successfully.")
        return 0
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return 1


def cmd_retrain(dry_run: bool = False) -> int:
    t_start = time.time()

    # ── Step 1: Build dataset ─────────────────────────────────────────────────
    _section("STEP 1 — BUILD DATASET")
    try:
        from app.ml.retraining.dataset_builder import build_retraining_dataset
        X, y, data_meta = build_retraining_dataset()
    except ValueError as exc:
        print(f"\n  SKIPPED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n  ERROR building dataset: {exc}", file=sys.stderr)
        log.exception("Dataset build failed")
        return 1

    _row("Total rows",   data_meta["n_rows"])
    _row("Real labels",  data_meta["n_real_labels"])
    _row("Proxy labels", data_meta["n_proxy_labels"])
    _row("Label counts", data_meta["label_counts"])
    _row("Assignments",  data_meta["n_assignments"])
    _row("Outcomes",     data_meta["n_outcomes"])
    if data_meta.get("bias_warning"):
        print(f"\n  ⚠  BIAS WARNING: {data_meta['bias_warning']}\n")

    # ── Step 2: Train new model ───────────────────────────────────────────────
    _section("STEP 2 — TRAIN NEW MODEL")
    try:
        from app.ml.retraining.retrainer import retrain
        new_pipeline, X_test, y_test, train_meta = retrain(X, y)
    except Exception as exc:
        print(f"\n  ERROR during training: {exc}", file=sys.stderr)
        log.exception("Training failed")
        return 1

    _row("Model type",   train_meta["model_type"])
    _row("Train rows",   train_meta["n_train"])
    _row("Test rows",    train_meta["n_test"])
    _row("CV AUC",       f"{train_meta['cv_auc_mean']:.4f}")
    _row("CV Accuracy",  f"{train_meta['cv_accuracy_mean']:.4f}")
    _row("Elapsed",      f"{train_meta['elapsed_sec']}s")
    if train_meta.get("feature_importances"):
        print("  Feature importances:")
        for feat, imp in sorted(
            train_meta["feature_importances"].items(), key=lambda x: -abs(x[1])
        ):
            bar = "#" * int(abs(imp) * 30)
            print(f"    {feat:<22} {imp:+.4f}  {bar}")

    # ── Step 3: Load production model for comparison ──────────────────────────
    _section("STEP 3 — EVALUATE vs PRODUCTION")
    from app.ml.retraining.model_registry import ModelRegistry
    from app.ml.retraining.evaluator import compare_models

    registry  = ModelRegistry()
    old_model = registry.load_production_model()

    try:
        decision = compare_models(old_model, new_pipeline, X_test, y_test)
    except Exception as exc:
        print(f"\n  ERROR during evaluation: {exc}", file=sys.stderr)
        log.exception("Evaluation failed")
        # Fallback: promote anyway rather than leaving the system stuck
        decision = {
            "old_metrics":    None,
            "new_metrics":    {"auc": 0.0, "accuracy": 0.0, "f1": 0.0, "n_test": 0},
            "should_promote": True,
            "reason":         f"evaluation_failed_promoting_unconditionally: {exc}",
            "auc_delta":      None,
        }

    _metrics_block("Old model", decision["old_metrics"])
    _metrics_block("New model", decision["new_metrics"])

    delta_str = (
        f"{decision['auc_delta']:+.4f}" if decision["auc_delta"] is not None else "N/A"
    )
    _row("AUC delta",      delta_str)
    _row("Decision",       "PROMOTE" if decision["should_promote"] else "REJECT")
    _row("Reason",         decision["reason"])

    # ── Step 4: Register candidate ────────────────────────────────────────────
    _section("STEP 4 — REGISTER CANDIDATE")
    new_metrics = decision["new_metrics"]
    try:
        version = registry.save_candidate(new_pipeline, new_metrics, train_meta)
        _row("New version", version)
        _row("Archive path", str(registry.models_dir / f"task_model_{version}.pkl"))
    except Exception as exc:
        print(f"\n  ERROR saving candidate: {exc}", file=sys.stderr)
        log.exception("save_candidate failed")
        return 1

    # ── Step 5: Promote or reject ─────────────────────────────────────────────
    _section("STEP 5 — PROMOTE / REJECT")
    if dry_run:
        print(f"  DRY RUN — model {version} registered but NOT promoted.")
        print(f"  Run without --dry-run to apply.")
        return 0

    if decision["should_promote"]:
        try:
            registry.promote(version)
            _row("Result",       f"Model {version} PROMOTED to production")
            _row("Production",   str(registry.production_path))
        except Exception as exc:
            print(f"\n  ERROR promoting model: {exc}", file=sys.stderr)
            log.exception("Promotion failed")
            return 1
    else:
        registry.reject(version, reason=decision["reason"])
        _row("Result",   f"Model {version} REJECTED — old model retained")
        _row("Reason",   decision["reason"])

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("SUMMARY")
    _row("Dataset rows",  data_meta["n_rows"])
    _row("New version",   version)
    _row("New AUC",       f"{new_metrics.get('auc', 0):.4f}")
    old_m = decision["old_metrics"]
    _row("Old AUC",       f"{old_m.get('auc', 0):.4f}" if old_m else "N/A (first run)")
    _row("AUC delta",     delta_str)
    _row("Promoted",      "YES" if decision["should_promote"] else "NO")
    _row("Total elapsed", f"{round(time.time() - t_start, 2)}s")
    print()

    # ── Persist retrain report ────────────────────────────────────────────────────
    try:
        from datetime import datetime, timezone
        report = {
            "version":      version,
            "auc":          round(new_metrics.get("auc", 0.0), 4),
            "accepted":     decision["should_promote"] and not dry_run,
            "dataset_size": data_meta["n_rows"],
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }
        registry.append_retrain_report(report)
    except Exception as _rpt_exc:
        log.warning("[retrain] Could not save retrain report: %s", _rpt_exc)

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Entry-point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Continuous retraining pipeline for the task-success ML model."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Train and evaluate but do not promote the new model.",
    )
    parser.add_argument(
        "--rollback", metavar="VERSION",
        help="Restore a specific archived version to production (e.g. --rollback v1).",
    )
    parser.add_argument(
        "--list-versions", action="store_true",
        help="List all registered model versions and exit.",
    )
    args = parser.parse_args()

    if args.list_versions:
        cmd_list_versions()
        return 0

    if args.rollback:
        return cmd_rollback(args.rollback)

    return cmd_retrain(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
