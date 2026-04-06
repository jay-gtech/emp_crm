"""
app/ml/auto_assignment/logger.py
================================
ML data pipeline logging for the auto-assignment system.

Every call to log_assignment() writes one JSON-Lines record to
assignment_log.jsonl.  This file is the raw training dataset for
future supervised ML models (predict_best_employee).

Schema
------
Assignment event:
  {
    "event_type":   "assignment",
    "task_id":      int,
    "employee_id":  int,
    "score":        float,
    "features":     { active_tasks, overdue_tasks, completed_tasks, performance_score },
    "task_context": { priority, status },
    "timestamp":    ISO-8601 UTC string
  }

Outcome event (call after task resolution):
  {
    "event_type":  "outcome",
    "task_id":     int,
    "employee_id": int,
    "success":     bool,
    "delay_days":  int,           # negative = delivered early
    "timestamp":   ISO-8601 UTC string
  }
"""

from __future__ import annotations
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Log file lives alongside this module so it is self-contained
_LOG_DIR  = Path(__file__).parent
LOG_FILE  = _LOG_DIR / "assignment_log.jsonl"

# ── Log rotation settings ─────────────────────────────────────────────────────
LOG_MAX_BYTES      = 10 * 1024 * 1024  # rotate when file exceeds 10 MB
LOG_MAX_ARCHIVES   = 3                  # keep .1, .2, .3 — oldest is dropped

# ── Log sampling (high-volume protection) ─────────────────────────────────────
LOG_SAMPLE_THRESHOLD = 5 * 1024 * 1024  # start sampling above 5 MB
LOG_SAMPLE_RATE      = 0.30              # keep 30 % of events when above threshold


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rotate_log_if_needed() -> None:
    """
    Rotate assignment_log.jsonl when it exceeds LOG_MAX_BYTES.

    Rotation scheme (oldest first):
      .jsonl.3  → deleted
      .jsonl.2  → .jsonl.3
      .jsonl.1  → .jsonl.2
      .jsonl    → .jsonl.1
      (fresh)   → .jsonl
    """
    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size < LOG_MAX_BYTES:
            return
        # Shift existing archives
        for i in range(LOG_MAX_ARCHIVES, 0, -1):
            src  = Path(f"{LOG_FILE}.{i}")
            dest = Path(f"{LOG_FILE}.{i + 1}")
            if src.exists():
                if i == LOG_MAX_ARCHIVES:
                    src.unlink()          # drop the oldest archive
                else:
                    src.rename(dest)
        # Move the current log to .1
        LOG_FILE.rename(Path(f"{LOG_FILE}.1"))
        log.info("[assignment_logger] Log rotated — started fresh %s", LOG_FILE)
    except Exception as exc:
        log.error("[assignment_logger] Log rotation failed: %s", exc)


def _should_sample() -> bool:
    """
    Return True if this log entry should be written.
    When the log file exceeds LOG_SAMPLE_THRESHOLD, drop to LOG_SAMPLE_RATE
    to prevent unbounded disk usage on high-traffic systems.
    """
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_SAMPLE_THRESHOLD:
            return random.random() < LOG_SAMPLE_RATE
    except Exception:
        pass
    return True  # always write when file is small or stat fails


def _write(record: dict) -> None:
    """Append a single JSON record (one line) to the log file."""
    try:
        if not _should_sample():
            return   # sampled out — skip this event
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        # Never crash the main flow because of logging
        log.error("[assignment_logger] Failed to write log entry: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def log_assignment(
    task_id:           int,
    employee_id:       int,
    score:             float,
    features:          dict,
    task_context:      dict | None = None,
    reason_tags:       list[str] | None = None,
    ml_used:           bool | None = None,
    inference_time_ms: float | None = None,
    shadow_ml_prob:    float | None = None,
) -> None:
    """
    Log a completed auto-assignment decision.
    """
    record = {
        "event_type":   "assignment",
        "task_id":      task_id,
        "employee_id":  employee_id,
        "rule_score":   features.get("rule_score", 0.0),
        "normalized_rule_score": features.get("normalized_rule_score", 0.0),
        "ml_probability": features.get("ml_prob", 0.0),
        "final_score":  round(float(score), 4),
        "ml_used":           ml_used,
        "inference_time_ms": inference_time_ms,
        "shadow_ml_prob":    shadow_ml_prob,
        "features":     features,
        "reason_tags":  reason_tags or [],
        "task_context": task_context or {},
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }
    _write(record)
    log.debug("[assignment_logger] Logged assignment task_id=%d → employee_id=%d", task_id, employee_id)


def update_assignment_outcome(
    task_id:     int,
    employee_id: int,
    success:     bool,
    delay_days:  int = 0,
) -> None:
    """
    Log the real-world outcome of a previously auto-assigned task.
    Used as the label source for supervised ML training.

    Parameters
    ----------
    task_id     : matches assignment log entry
    employee_id : who was assigned (for easy join)
    success     : True if task was completed on or before due_date
    delay_days  : days late (positive) or early (negative)
    """
    record = {
        "event_type":  "outcome",
        "task_id":     task_id,
        "employee_id": employee_id,
        "success":     success,
        "delay_days":  delay_days,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    _write(record)
    log.debug("[assignment_logger] Logged outcome task_id=%d success=%s delay=%d", task_id, success, delay_days)


def read_log(limit: int = 100) -> list[dict]:
    """
    Read the last `limit` log entries — useful for debugging and inspection.
    """
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").strip().splitlines()
        recent = lines[-limit:]
        return [json.loads(line) for line in recent]
    except Exception as exc:
        log.error("[assignment_logger] Failed to read log: %s", exc)
        return []
