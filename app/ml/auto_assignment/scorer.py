"""
app/ml/auto_assignment/scorer.py
================================
Pure, stateless ML scoring layer.
No DB access — receives pre-computed feature dicts from the service layer.

Scoring formula
---------------
  score = (
      - active_tasks   * 2.5   # penalise current workload
      - overdue_tasks  * 4.0   # penalise missed deadlines heavily
      + completed_tasks * 1.2  # reward proven delivery history
      + perf_score     * 2.0   # reward high performers (0–100 scale)
  )

Higher score  → better candidate.
All weights are named constants so they are easy to tune / replace with a
trained model in a later iteration.
"""

from __future__ import annotations
import collections
import logging
import time

log = logging.getLogger(__name__)

# ── Inference timing ring-buffer (last 500 batch calls) ──────────────────────
_INFERENCE_TIMES: collections.deque = collections.deque(maxlen=500)

# ── Prediction / fallback counters ────────────────────────────────────────────
_TOTAL_PREDICTIONS: int    = 0   # total calls to select_best_employee()
_FALLBACK_PREDICTIONS: int = 0   # calls where ML failed and rule fallback was used


def get_fallback_stats() -> dict:
    """
    Return fallback-rate statistics.

    Shape::
        {
          "total_predictions":    120,
          "fallback_predictions":   3,
          "fallback_rate":        0.025,
          "ml_usage_rate":        0.975,
        }
    """
    total    = _TOTAL_PREDICTIONS
    fallback = _FALLBACK_PREDICTIONS
    if total == 0:
        return {
            "total_predictions":    0,
            "fallback_predictions": 0,
            "fallback_rate":        0.0,
            "ml_usage_rate":        0.0,
        }
    fb_rate  = round(fallback / total, 4)
    return {
        "total_predictions":    total,
        "fallback_predictions": fallback,
        "fallback_rate":        fb_rate,
        "ml_usage_rate":        round(1.0 - fb_rate, 4),
    }


def get_inference_stats() -> dict:
    """
    Return latency statistics for recent batch ML prediction calls.

    Shape:
        {
          "n_samples":  120,
          "avg_ms":     2.3,
          "p50_ms":     1.9,
          "p95_ms":     6.1,
          "max_ms":     12.4,
        }
    Returns all-zero dict if no calls have been recorded yet.
    """
    if not _INFERENCE_TIMES:
        return {"n_samples": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}

    import statistics
    data = sorted(_INFERENCE_TIMES)
    n    = len(data)
    return {
        "n_samples": n,
        "avg_ms":    round(sum(data) / n, 3),
        "p50_ms":    round(statistics.median(data), 3),
        "p95_ms":    round(data[int(n * 0.95)], 3),
        "max_ms":    round(data[-1], 3),
    }

# ── Tunable scoring weights ───────────────────────────────────────────────────
W_ACTIVE    = 2.5    # penalty per active (pending / in-progress) task
W_OVERDUE   = 4.0    # penalty per overdue task
W_COMPLETED = 1.2    # reward per completed task
W_PERF      = 2.0    # reward per performance-score point (score is 0–100)

DEFAULT_PERF_SCORE = 50.0   # neutral mid-point if field is missing


# ─────────────────────────────────────────────────────────────────────────────
# Core scoring
# ─────────────────────────────────────────────────────────────────────────────

USE_ML      = True
SHADOW_MODE = True   # run candidate model in parallel (predictions logged, never used for decisions)

def compute_hybrid_score(
    rule_score: float,
    features: dict,
    precomputed_prob: float | None = None,
) -> tuple:
    """
    Combine the rule-based score with the ML model's success probability.
    Returns (final_score, ml_prob, normalized_rule_score).

    Parameters
    ----------
    precomputed_prob : if provided (from a batch call), skips the per-employee
                       model call entirely — the hot path for bulk scoring.
    """
    normalized_rule_score = (min(rule_score, 200.0) / 200.0) * 100.0

    if not USE_ML:
        return round(normalized_rule_score, 4), 0.0, round(normalized_rule_score, 4)

    try:
        if precomputed_prob is not None:
            prob = float(precomputed_prob)
        else:
            from app.ml.training.model import predict_success_proba
            prob = predict_success_proba(features)
        ml_score    = prob * 100.0
        final_score = 0.6 * normalized_rule_score + 0.4 * ml_score
        return round(final_score, 4), prob, round(normalized_rule_score, 4)
    except Exception as exc:
        log.warning("[scorer] ML prediction failed (%s) — falling back to rule score.", exc)
        return round(normalized_rule_score, 4), 0.0, round(normalized_rule_score, 4)


def calculate_employee_score(features: dict) -> float:
    """
    Compute assignment-fitness score for one employee.

    Parameters
    ----------
    features : dict with keys
        active_tasks      – int, pending + in_progress tasks
        overdue_tasks     – int, tasks past their due_date
        completed_tasks   – int, total completed tasks
        performance_score – float 0–100 (defaults to 50 if absent)

    Returns
    -------
    float – higher is better
    """
    active    = float(features.get("active_tasks",      0))
    overdue   = float(features.get("overdue_tasks",     0))
    completed = float(features.get("completed_tasks",   0))
    perf      = float(features.get("performance_score", DEFAULT_PERF_SCORE))

    # Clamp to valid ranges — guard against bad data
    active    = max(0.0, active)
    overdue   = max(0.0, overdue)
    completed = max(0.0, completed)
    perf      = max(0.0, min(100.0, perf))

    score = (
        - active    * W_ACTIVE
        - overdue   * W_OVERDUE
        + completed * W_COMPLETED
        + perf      * W_PERF
    )
    return round(score, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Explainability
# ─────────────────────────────────────────────────────────────────────────────

def generate_reason(features: dict, score: float) -> dict:
    """
    Produce a structured explanation for the frontend with tags.
    """
    parts: list[str] = []
    tags: list[str] = []

    active    = features.get("active_tasks",      0)
    overdue   = features.get("overdue_tasks",     0)
    completed = features.get("completed_tasks",   0)
    perf      = features.get("performance_score", DEFAULT_PERF_SCORE)

    if active == 0:
        parts.append("no active tasks (fully available)")
        tags.append("low_workload")
    elif active <= 2:
        parts.append(f"low workload ({active} active task{'s' if active > 1 else ''})")
        tags.append("low_workload")
    else:
        tags.append("high_workload")

    if overdue == 0:
        parts.append("no overdue tasks")
        tags.append("no_overdue")
    else:
        parts.append(f"{overdue} overdue task{'s' if overdue > 1 else ''} (still best available)")
        tags.append("high_overdue")

    if perf >= 80:
        parts.append(f"high performance score ({perf:.1f})")
        tags.append("high_performance")
    elif perf >= 60:
        parts.append(f"good performance score ({perf:.1f})")

    if completed >= 10:
        parts.append(f"strong delivery history ({completed} completed)")
    elif completed > 0:
        parts.append(f"{completed} task{'s' if completed > 1 else ''} completed previously")

    ml_prob = features.get("ml_prob", 0.0)
    if ml_prob > 0.7:
        parts.append(f"high ML success probability ({ml_prob:.0%})")
        tags.append("high_ml_confidence")

    if not parts:
        parts.append("best available match based on current workload and scoring")

    return {
        "reason_text": "; ".join(parts) + f" [score: {score:.2f}]",
        "reason_tags": tags
    }


# ─────────────────────────────────────────────────────────────────────────────
# Selection
# ─────────────────────────────────────────────────────────────────────────────

def select_best_employee(
    employee_features: list[dict],
) -> tuple[dict, list[dict]]:
    """
    Rank all employees and return the best candidate plus the full ranked list.

    Parameters
    ----------
    employee_features : list of dicts, each containing:
        {
            "employee_id":   int,
            "employee_name": str,
            "features":      dict   (keys: active_tasks, overdue_tasks, …)
        }

    Returns
    -------
    (best: dict, ranked: list[dict])

    best   – highest-scoring candidate dict with 'score' and 'reason' added
    ranked – all candidates sorted score descending, with 'score' added
    """
    if not employee_features:
        raise ValueError("No eligible employees provided for scoring.")

    # ── Track prediction call ─────────────────────────────────────────────────
    global _TOTAL_PREDICTIONS, _FALLBACK_PREDICTIONS
    _TOTAL_PREDICTIONS += 1

    # ── Batch ML prediction (single model call for all employees) ─────────────
    ml_probs: list[float] = []
    ml_used:          bool  = False
    inference_time_ms: float = 0.0
    if USE_ML:
        try:
            from app.ml.training.model import predict_batch_proba
            raw_features = [item["features"] for item in employee_features]
            _t0 = time.perf_counter()
            batch_probs  = predict_batch_proba(raw_features)
            inference_time_ms = (time.perf_counter() - _t0) * 1000  # ms
            _INFERENCE_TIMES.append(inference_time_ms)
            ml_probs = list(batch_probs)
            ml_used  = True
        except Exception as exc:
            log.warning("[scorer] Batch ML prediction failed (%s) — will use rule score only.", exc)
            _FALLBACK_PREDICTIONS += 1
            ml_probs = [None] * len(employee_features)  # type: ignore[list-item]
    else:
        _FALLBACK_PREDICTIONS += 1
        ml_probs = [None] * len(employee_features)  # type: ignore[list-item]

    scored: list[dict] = []
    for idx, item in enumerate(employee_features):
        try:
            rule_score  = calculate_employee_score(item["features"])
            precomputed = ml_probs[idx] if ml_probs else None
            final_score, prob, norm_rule = compute_hybrid_score(
                rule_score, item["features"], precomputed_prob=precomputed
            )
        except Exception as exc:
            log.warning(
                "[scorer] Failed to score employee %s: %s — defaulting to -9999",
                item.get("employee_id"), exc
            )
            rule_score = -9999.0
            prob = 0.0
            norm_rule = -9999.0
            final_score = -9999.0

        item["features"]["rule_score"] = rule_score
        item["features"]["ml_prob"] = prob
        item["features"]["normalized_rule_score"] = norm_rule
        item["features"]["final_score"] = final_score

        scored.append({
            "employee_id":   item["employee_id"],
            "employee_name": item["employee_name"],
            "features":      item["features"],
            "score":         final_score,
            "reason":        "",   # filled in after ranking
            "reason_tags":   [],
        })

    # Sort descending by score
    ranked = sorted(scored, key=lambda x: x["score"], reverse=True)

    # Generate reasons for top 5 (the rest rarely matter for UX)
    for entry in ranked[:5]:
        reason_data = generate_reason(entry["features"], entry["score"])
        entry["reason"] = reason_data["reason_text"]
        entry["reason_tags"] = reason_data["reason_tags"]

    best = ranked[0]
    best["ml_used"]           = ml_used
    best["inference_time_ms"] = round(inference_time_ms, 3)

    # ── Shadow mode: run candidate model without affecting decisions ──────────
    if SHADOW_MODE:
        try:
            from app.ml.retraining.shadow import shadow_predict_batch
            raw_features  = [item["features"] for item in employee_features]
            shadow_probs  = shadow_predict_batch(raw_features)
            # Attach shadow prob for the best employee (same index as ranked[0])
            best_idx = next(
                (i for i, item in enumerate(employee_features)
                 if item["employee_id"] == best["employee_id"]),
                0,
            )
            best["shadow_ml_prob"] = shadow_probs[best_idx] if shadow_probs else None
        except Exception as exc:
            log.debug("[scorer] Shadow mode failed: %s", exc)
            best["shadow_ml_prob"] = None

    return best, ranked


# ─────────────────────────────────────────────────────────────────────────────
# Fallback
# ─────────────────────────────────────────────────────────────────────────────

def fallback_least_workload(employee_features: list[dict]) -> dict:
    """
    Emergency fallback: pick the employee with the fewest active tasks.
    Used when normal scoring raises an unexpected exception.
    """
    return min(
        employee_features,
        key=lambda x: x["features"].get("active_tasks", 0),
        default=employee_features[0],
    )
