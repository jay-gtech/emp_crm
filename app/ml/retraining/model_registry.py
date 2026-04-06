"""
app/ml/retraining/model_registry.py
=====================================
Versioned model store with safe promotion and rollback.

Registry contract
-----------------
* Versioned archives live in:
      app/ml/retraining/models/task_model_v{N}.pkl

* The production model (loaded by inference) always lives at:
      app/ml/training/models/task_success_model.pkl

* metadata.json tracks all versions and which is "current":
  {
    "current_version": "v2",
    "models": {
      "v1": {
        "version":      "v1",
        "path":         "…/task_model_v1.pkl",
        "model_type":   "LGBMClassifier",
        "metrics":      { "auc": 0.87, "accuracy": 0.91, "f1": 0.89 },
        "trained_at":   "2026-04-02T10:00:00",
        "n_train":      210,
        "promoted":     true,
        "status":       "active"   # active | archived | rejected
      },
      "v2": { … }
    }
  }

* Promotion copies the versioned pkl → production path and reloads the
  in-memory cache via load_model(force_reload=True).

* Old models are NEVER deleted — rollback copies any archived version back
  to the production path.

Usage
-----
  from app.ml.retraining.model_registry import ModelRegistry
  registry = ModelRegistry()
  version  = registry.save_candidate(pipeline, metrics, train_meta)
  registry.promote(version)
"""

from __future__ import annotations
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from sklearn.pipeline import Pipeline

from app.ml.retraining.utils import MODELS_DIR, METADATA_FILE, PRODUCTION_MODEL_PATH

# Retrain history file — one JSON record per retraining run
RETRAIN_HISTORY_FILE = MODELS_DIR / "retrain_history.jsonl"

log = logging.getLogger(__name__)


class ModelRegistry:
    """
    Manages versioned model files and the metadata.json index.
    Thread-safety: designed for single-process CLI use; not concurrent-safe.
    """

    def __init__(
        self,
        models_dir: Path = MODELS_DIR,
        metadata_file: Path = METADATA_FILE,
        production_path: Path = PRODUCTION_MODEL_PATH,
    ) -> None:
        self.models_dir      = models_dir
        self.metadata_file   = metadata_file
        self.production_path = production_path
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Metadata I/O
    # ─────────────────────────────────────────────────────────────────────────

    def _load_meta(self) -> dict:
        """Load metadata.json, returning empty structure if it doesn't exist."""
        if not self.metadata_file.exists():
            return {"current_version": None, "models": {}}
        try:
            return json.loads(self.metadata_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("[registry] Failed to read metadata: %s — starting fresh", exc)
            return {"current_version": None, "models": {}}

    def _save_meta(self, meta: dict) -> None:
        self.metadata_file.write_text(
            json.dumps(meta, indent=2, default=str), encoding="utf-8"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Version helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _next_version(self, meta: dict) -> str:
        """Return the next version tag (v1, v2, …)."""
        existing = list(meta.get("models", {}).keys())
        nums = [int(v[1:]) for v in existing if v.startswith("v") and v[1:].isdigit()]
        next_n = max(nums, default=0) + 1
        return f"v{next_n}"

    def current_version(self) -> str | None:
        return self._load_meta().get("current_version")

    def list_versions(self) -> list[dict]:
        meta = self._load_meta()
        return list(meta.get("models", {}).values())

    # ─────────────────────────────────────────────────────────────────────────
    # Save candidate
    # ─────────────────────────────────────────────────────────────────────────

    def save_candidate(
        self,
        pipeline: Pipeline,
        eval_metrics: dict[str, Any],
        train_meta:   dict[str, Any],
    ) -> str:
        """
        Persist a newly trained model as the next versioned candidate.

        Returns the new version string (e.g. "v3").
        Does NOT promote — use promote() after evaluating.
        """
        meta    = self._load_meta()
        version = self._next_version(meta)

        pkl_path = self.models_dir / f"task_model_{version}.pkl"
        joblib.dump(pipeline, pkl_path, compress=3)
        log.info("[registry] Saved candidate %s → %s", version, pkl_path)

        clf = pipeline.named_steps.get("clf", pipeline)
        entry: dict[str, Any] = {
            "version":    version,
            "path":       str(pkl_path),
            "model_type": type(clf).__name__,
            "metrics":    eval_metrics,
            "train_meta": {
                "n_train":           train_meta.get("n_train"),
                "n_test":            train_meta.get("n_test"),
                "cv_auc_mean":       train_meta.get("cv_auc_mean"),
                "cv_accuracy_mean":  train_meta.get("cv_accuracy_mean"),
                "feature_importances": train_meta.get("feature_importances", {}),
            },
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "promoted":   False,
            "status":     "candidate",
        }
        meta["models"][version] = entry
        self._save_meta(meta)
        return version

    # ─────────────────────────────────────────────────────────────────────────
    # Promote
    # ─────────────────────────────────────────────────────────────────────────

    def promote(self, version: str) -> None:
        """
        Promote a versioned candidate to production.

        1. Copy versioned pkl → production path (task_success_model.pkl)
        2. Mark old current version as "archived"
        3. Update current_version in metadata
        4. Reload in-memory model cache
        """
        meta  = self._load_meta()
        entry = meta["models"].get(version)
        if entry is None:
            raise ValueError(f"Version '{version}' not found in registry.")

        src = Path(entry["path"])
        if not src.exists():
            raise FileNotFoundError(f"Model file missing: {src}")

        # Archive the currently-active version
        old_version = meta.get("current_version")
        if old_version and old_version in meta["models"]:
            meta["models"][old_version]["status"] = "archived"

        # Copy to production
        self.production_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(self.production_path))
        log.info("[registry] Promoted %s → %s", version, self.production_path)

        # Update metadata
        entry["promoted"]   = True
        entry["status"]     = "active"
        entry["promoted_at"] = datetime.now(timezone.utc).isoformat()
        meta["current_version"] = version
        self._save_meta(meta)

        # Flush in-memory cache so next inference loads the new model
        try:
            from app.ml.training.model import load_model
            load_model(force_reload=True)
            log.info("[registry] Model cache reloaded.")
        except Exception as exc:
            log.warning("[registry] Cache reload skipped (app not running?): %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Reject
    # ─────────────────────────────────────────────────────────────────────────

    def reject(self, version: str, reason: str = "") -> None:
        """Mark a candidate as rejected without changing the production model."""
        meta  = self._load_meta()
        entry = meta["models"].get(version)
        if entry is None:
            log.warning("[registry] Tried to reject unknown version '%s'", version)
            return
        entry["status"]      = "rejected"
        entry["reject_reason"] = reason
        entry["rejected_at"] = datetime.now(timezone.utc).isoformat()
        self._save_meta(meta)
        log.info("[registry] Version %s rejected: %s", version, reason)

    # ─────────────────────────────────────────────────────────────────────────
    # Rollback
    # ─────────────────────────────────────────────────────────────────────────

    def rollback(self, version: str) -> None:
        """
        Restore a previously archived model to production.
        Safe: old production file is overwritten but archived versions are intact.
        """
        log.info("[registry] Rolling back to %s", version)
        self.promote(version)   # promote handles all the mechanics

    # ─────────────────────────────────────────────────────────────────────────
    # Load production model (helper for evaluator)
    # ─────────────────────────────────────────────────────────────────────────

    def load_production_model(self) -> Pipeline | None:
        """
        Load the current production model from disk for evaluation.
        Returns None if no production model exists yet.
        """
        if not self.production_path.exists():
            log.info("[registry] No production model found at %s", self.production_path)
            return None
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pipeline = joblib.load(self.production_path)
            log.info("[registry] Loaded production model from %s", self.production_path)
            return pipeline
        except Exception as exc:
            log.error("[registry] Failed to load production model: %s", exc)
            return None

    # ──────────────────────────────────────────────────────────────────────────────
    # Retrain history
    # ──────────────────────────────────────────────────────────────────────────────

    def append_retrain_report(self, report: dict) -> None:
        """
        Append a structured retrain report to retrain_history.jsonl.

        Expected shape::
            {
                "version":      "v2",
                "auc":          0.87,
                "accepted":     true,
                "dataset_size": 182,
                "timestamp":    "2026-04-02T12:00:00+00:00"
            }
        """
        try:
            RETRAIN_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(RETRAIN_HISTORY_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(report, default=str) + "\n")
            log.info("[registry] Retrain report saved: %s", report)
        except Exception as exc:
            log.error("[registry] Failed to save retrain report: %s", exc)

    def get_retrain_history(self, limit: int = 10) -> list[dict]:
        """
        Return the last `limit` retrain reports from retrain_history.jsonl.
        Returns [] if no history exists.
        """
        if not RETRAIN_HISTORY_FILE.exists():
            return []
        try:
            lines = RETRAIN_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
            recent = lines[-limit:]
            return [json.loads(line) for line in recent if line.strip()]
        except Exception as exc:
            log.error("[registry] Failed to read retrain history: %s", exc)
            return []
