"""
Self-Learning Data Pipeline
============================
Appends a prediction record to a JSONL feedback log every time a task is
scored.  When tasks are later completed or become overdue, the actual
outcome can be written back to the same record.

The JSONL file serves as the retraining dataset for future model versions.

Usage
-----
Log a prediction (called from ai_task_service):

    from app.ml.task_assistant.save_training_data import log_prediction
    log_prediction(task_id=1, features=[2.0, 5.0, 0.0, 1.0],
                   predicted_priority="high", confidence=0.91)

Update outcome when a task completes / becomes overdue:

    from app.ml.task_assistant.save_training_data import update_outcome
    update_outcome(task_id=1, actual_priority="high", was_delayed=False)

File format (one JSON object per line):
    {
      "task_id":            int,
      "features":           [float, float, float, float],
      "predicted_priority": "high" | "medium" | "low",
      "confidence":         float,
      "actual_priority":    str | null,
      "was_delayed":        bool | null,
      "logged_at":          "2025-04-01T12:00:00",
      "resolved_at":        "2025-04-03T09:30:00" | null
    }
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime
from typing import Any

LOG_PATH = pathlib.Path(__file__).parent / "training_log.jsonl"

logger = logging.getLogger(__name__)


def log_prediction(
    task_id: int,
    features: list[float],
    predicted_priority: str,
    confidence: float,
) -> None:
    """Append a new prediction record to the JSONL log."""
    record = {
        "task_id":            task_id,
        "features":           features,
        "predicted_priority": predicted_priority,
        "confidence":         round(confidence, 4),
        "actual_priority":    None,
        "was_delayed":        None,
        "logged_at":          datetime.utcnow().isoformat(timespec="seconds"),
        "resolved_at":        None,
    }
    _append(record)


def update_outcome(
    task_id: int,
    actual_priority: str | None = None,
    was_delayed: bool | None = None,
) -> None:
    """
    Rewrite the most recent record for task_id with its actual outcome.
    Reads the whole file, patches in place, rewrites — acceptable for the
    log sizes typical of a CRM (thousands of tasks, not millions).
    """
    if not LOG_PATH.exists():
        return

    try:
        records: list[dict] = []
        with LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        # Patch the LAST record for this task_id
        for rec in reversed(records):
            if rec.get("task_id") == task_id:
                if actual_priority is not None:
                    rec["actual_priority"] = actual_priority
                if was_delayed is not None:
                    rec["was_delayed"] = was_delayed
                rec["resolved_at"] = datetime.utcnow().isoformat(timespec="seconds")
                break

        with LOG_PATH.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    except Exception as exc:
        logger.warning("save_training_data.update_outcome failed: %s", exc)


def _append(record: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("save_training_data.log_prediction failed: %s", exc)
