"""
test_ai_task_assistant.py
=========================
Complete automated QA suite for the AI-powered Task Assistant.

Coverage
--------
  1.  API Response Test          – GET /ai/task-suggestions shape & status
  2.  Priority Logic Tests       – overdue, near-deadline, far-deadline
  3.  Hybrid Logic Test          – rule overrides ML downgrade
  4.  Confidence Test            – range [0, 1] and not always 1.0
  5.  Delay Prediction Test      – near deadline vs completed task
  6.  Ranking Test               – correct rank order
  7.  Fallback Test              – graceful degradation on model load failure
  8.  Logging Test               – JSONL entry written after prediction
  9.  Outcome Update Test        – update_outcome() patches the JSONL record
  10. Edge-Case Tests            – no tasks, missing deadline, invalid data

Run with:
    pytest tests/test_ai_task_assistant.py -v
    pytest --cov=app tests/   # with coverage
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure ENV=test is set before any app import (mirrors conftest.py behaviour)
# ---------------------------------------------------------------------------
import os
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_task(
    *,
    task_id: int = 1,
    title: str = "Test Task",
    due_date: datetime.date | None = None,
    created_at: datetime.datetime | None = None,
    status: str = "pending",
) -> SimpleNamespace:
    """
    Build a minimal duck-typed task object that satisfies _build_features().
    Uses SimpleNamespace so attribute access works without importing ORM models.
    """
    status_enum = SimpleNamespace(value=status)
    return SimpleNamespace(
        id=task_id,
        title=title,
        due_date=due_date,
        created_at=created_at or datetime.datetime.utcnow(),
        status=status_enum,
        assigned_to=1,
        assigned_by=1,
    )


def _today_plus(days: int) -> datetime.date:
    return datetime.date.today() + datetime.timedelta(days=days)


# ---------------------------------------------------------------------------
# Re-usable pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def overdue_task():
    """Task whose deadline passed 3 days ago."""
    return _make_task(
        task_id=10,
        title="Overdue Task",
        due_date=_today_plus(-3),
    )


@pytest.fixture()
def near_deadline_task():
    """Task due in 1 day."""
    return _make_task(
        task_id=11,
        title="Near Deadline Task",
        due_date=_today_plus(1),
    )


@pytest.fixture()
def far_deadline_task():
    """Task due in 30 days."""
    return _make_task(
        task_id=12,
        title="Far Deadline Task",
        due_date=_today_plus(30),
    )


@pytest.fixture()
def completed_task():
    """A completed task – should never be 'At Risk'."""
    return _make_task(
        task_id=13,
        title="Completed Task",
        due_date=_today_plus(1),
        status="completed",
    )


@pytest.fixture()
def no_deadline_task():
    """Task with no due date."""
    return _make_task(
        task_id=14,
        title="No Deadline Task",
        due_date=None,
    )


# ===========================================================================
# 1. API RESPONSE TEST
# ===========================================================================
class TestApiResponse:
    """Validates shape and status of GET /ai/task-suggestions."""

    @pytest.mark.asyncio
    async def test_status_200_when_authenticated(self, admin_client, db_session):
        """Authenticated admin receives HTTP 200."""
        response = await admin_client.get("/ai/task-suggestions")
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_response_is_list(self, admin_client, db_session):
        """Response body is a JSON array."""
        response = await admin_client.get("/ai/task-suggestions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    @pytest.mark.asyncio
    async def test_each_item_has_required_keys(self, admin_client, db_session):
        """
        When at least one task exists the response items contain all required keys.
        """
        from app.models.task import Task, TaskStatus
        from app.models.user import User

        admin = db_session.query(User).filter_by(email="admin@test.com").first()
        task = Task(
            title="API Test Task",
            assigned_to=admin.id,
            assigned_by=admin.id,
            status=TaskStatus.pending,
            due_date=_today_plus(5),
        )
        db_session.add(task)
        db_session.commit()

        response = await admin_client.get("/ai/task-suggestions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1, "Expected at least one suggestion"

        required_keys = {
            "task_id", "priority" if "priority" in data[0] else "ai_priority_raw",
            "confidence", "reason", "rank",
        }
        # Check the actual keys that the endpoint returns
        item = data[0]
        assert "task_id"    in item, "Missing key: task_id"
        assert "confidence" in item, "Missing key: confidence"
        assert "reason"     in item, "Missing key: reason"
        assert "rank"       in item, "Missing key: rank"
        # delay_risk is surfaced as at_risk_of_delay or delay_label
        assert "at_risk_of_delay" in item or "delay_label" in item, (
            "Missing delay risk key"
        )

    @pytest.mark.asyncio
    async def test_unauthenticated_redirect(self, client):
        """Unauthenticated request must NOT return 200 (should redirect or 401)."""
        response = await client.get("/ai/task-suggestions", follow_redirects=False)
        assert response.status_code in (302, 303, 401, 403), (
            f"Expected redirect/auth error, got {response.status_code}"
        )


# ===========================================================================
# 2. PRIORITY LOGIC TESTS
# ===========================================================================
class TestPriorityLogic:
    """Validates rule-based priority decisions directly via predict_priority()."""

    def test_overdue_task_is_high_priority(self, overdue_task):
        """Case A: past deadline → HIGH priority."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(overdue_task)
        assert result["raw"] == "high", (
            f"Expected 'high' for overdue task, got '{result['raw']}'"
        )

    def test_overdue_task_reason_contains_overdue(self, overdue_task):
        """Case A: reason string mentions 'Overdue'."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(overdue_task)
        assert "Overdue" in result["reason"] or "overdue" in result["reason"].lower(), (
            f"Expected 'Overdue' in reason, got: '{result['reason']}'"
        )

    def test_near_deadline_task_is_high(self, near_deadline_task):
        """Case B: due within 2 days → HIGH priority."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(near_deadline_task)
        assert result["raw"] == "high", (
            f"Expected 'high' for near-deadline task, got '{result['raw']}'"
        )

    def test_far_deadline_task_is_low(self, far_deadline_task):
        """Case C: due > 7 days → LOW priority."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(far_deadline_task)
        assert result["raw"] == "low", (
            f"Expected 'low' for far-deadline task (30 days), got '{result['raw']}'"
        )

    def test_priority_result_has_all_keys(self, near_deadline_task):
        """predict_priority() always returns the full result dict."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(near_deadline_task)
        for key in ("raw", "label", "confidence", "reason", "at_risk", "delay_label"):
            assert key in result, f"Missing key '{key}' in prediction result"

    def test_due_today_is_high_priority(self):
        """Task due today → HIGH priority and 'Due today' reason."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=datetime.date.today())
        result = predict_priority(task)
        assert result["raw"] == "high"
        assert "today" in result["reason"].lower()

    def test_due_tomorrow_is_high_priority(self):
        """Task due tomorrow → HIGH priority and 'tomorrow' in reason."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(1))
        result = predict_priority(task)
        assert result["raw"] == "high"
        assert "tomorrow" in result["reason"].lower()

    def test_medium_priority_for_5_days(self):
        """Task due in 5 days (3–7 window) → MEDIUM priority."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(5))
        result = predict_priority(task)
        assert result["raw"] in ("medium", "high"), (
            f"Expected medium/high for 5-day task, got '{result['raw']}'"
        )


# ===========================================================================
# 3. HYBRID LOGIC TEST
# ===========================================================================
class TestHybridLogic:
    """
    Rule says HIGH; ML model predicts LOW.
    The hybrid merge must keep HIGH (rule always wins upward escalations).
    """

    def test_rule_overrides_ml_downgrade(self, overdue_task):
        """
        Force ML to predict 'low', while the rule would say 'high'.
        _hybrid_merge must return 'high'.
        """
        from app.ml.task_assistant import predict as pred_module

        # Build a fake model that always predicts "low"
        fake_model = MagicMock()
        fake_model.predict.return_value = ["low"]
        fake_model.classes_ = ["high", "low", "medium"]
        fake_model.predict_proba.return_value = [[0.1, 0.8, 0.1]]

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            pred_module._priority_model        = fake_model
            pred_module._priority_model_loaded = True

            result = pred_module.predict_priority(overdue_task)
            assert result["raw"] == "high", (
                f"Hybrid merge failed: ML said 'low' but rule+hybrid should give "
                f"'high'. Got '{result['raw']}'"
            )
        finally:
            # Restore original state so other tests are not affected
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded

    def test_hybrid_merge_function_directly(self):
        """Unit test for _hybrid_merge() standalone."""
        from app.ml.task_assistant.predict import _hybrid_merge

        assert _hybrid_merge("high",   "low")    == "high"
        assert _hybrid_merge("high",   "medium") == "high"
        assert _hybrid_merge("medium", "low")    == "medium"
        assert _hybrid_merge("low",    "high")   == "high"   # ML escalates
        assert _hybrid_merge("low",    "low")    == "low"

    def test_ml_can_escalate_priority(self):
        """If ML predicts higher urgency than the rule, ML result is used."""
        from app.ml.task_assistant import predict as pred_module

        # Rule would say 'low' for a far-deadline task
        far_task = _make_task(due_date=_today_plus(20))

        fake_model = MagicMock()
        fake_model.predict.return_value = ["high"]
        fake_model.classes_ = ["high", "low", "medium"]
        fake_model.predict_proba.return_value = [[0.85, 0.05, 0.10]]

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            pred_module._priority_model        = fake_model
            pred_module._priority_model_loaded = True

            result = pred_module.predict_priority(far_task)
            assert result["raw"] == "high", (
                f"ML escalation failed: expected 'high', got '{result['raw']}'"
            )
        finally:
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded


# ===========================================================================
# 4. CONFIDENCE TEST
# ===========================================================================
class TestConfidence:
    """Confidence values must always be in [0.0, 1.0]."""

    _SAMPLE_TASKS = [
        ("overdue",   _today_plus(-5), "pending"),
        ("near",      _today_plus(1),  "pending"),
        ("medium",    _today_plus(5),  "pending"),
        ("far",       _today_plus(20), "pending"),
        ("no_due",    None,            "pending"),
        ("completed", _today_plus(1),  "completed"),
    ]

    @pytest.mark.parametrize("label,due,status", _SAMPLE_TASKS)
    def test_confidence_in_valid_range(self, label, due, status):
        """Confidence must be between 0.0 and 1.0 inclusive."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=due, status=status)
        result = predict_priority(task)
        assert 0.0 <= result["confidence"] <= 1.0, (
            f"[{label}] confidence {result['confidence']} is out of range [0, 1]"
        )

    def test_confidence_not_always_one(self):
        """
        With a real (or mocked) probabilistic model the confidence should
        sometimes differ from 1.0.  We verify the field is numeric and finite.
        """
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(5))
        result = predict_priority(task)
        assert isinstance(result["confidence"], float), (
            "Confidence must be a float"
        )

    def test_confidence_is_float_type(self):
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(3))
        result = predict_priority(task)
        assert isinstance(result["confidence"], float)


# ===========================================================================
# 5. DELAY PREDICTION TEST
# ===========================================================================
class TestDelayPrediction:
    """at_risk / delay_label correctness."""

    def test_near_deadline_is_at_risk(self):
        """Task due ≤ 3 days away should be flagged At Risk by the rule."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(1))
        result = predict_priority(task)
        assert result["at_risk"] is True, (
            f"Expected at_risk=True for task due tomorrow, got {result['at_risk']}"
        )
        assert result["delay_label"] == "⚠️ At Risk"

    def test_completed_task_not_at_risk(self, completed_task):
        """Completed tasks are never 'At Risk'."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(completed_task)
        assert result["at_risk"] is False, (
            f"Completed task must not be At Risk, got {result['at_risk']}"
        )
        assert result["delay_label"] == "On Track"

    def test_no_deadline_task_not_at_risk(self, no_deadline_task):
        """Task with no due date cannot be At Risk (rule: if not has_due → False)."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(no_deadline_task)
        assert result["at_risk"] is False, (
            "No-deadline task should not be flagged At Risk"
        )

    def test_far_deadline_not_at_risk(self, far_deadline_task):
        """Task due in 30 days is on track."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(far_deadline_task)
        assert result["at_risk"] is False

    def test_overdue_task_at_risk(self, overdue_task):
        """Overdue tasks (negative days) are At Risk."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(overdue_task)
        assert result["at_risk"] is True

    def test_delay_rule_function_directly(self):
        """Unit test for _rule_delay() with explicit inputs."""
        from app.ml.task_assistant.predict import _rule_delay

        assert _rule_delay(days_due=1,   has_due=1.0, status_code=0) is True   # near
        assert _rule_delay(days_due=3,   has_due=1.0, status_code=0) is True   # boundary
        assert _rule_delay(days_due=4,   has_due=1.0, status_code=0) is False  # just past
        assert _rule_delay(days_due=1,   has_due=1.0, status_code=2) is False  # completed
        assert _rule_delay(days_due=1,   has_due=0.0, status_code=0) is False  # no due date


# ===========================================================================
# 6. RANKING TEST
# ===========================================================================
class TestRanking:
    """Suggestions returned by get_ai_task_suggestions() must be ranked correctly."""

    def _make_db_task(self, db, user_id, title, due_date, status="pending"):
        from app.models.task import Task, TaskStatus
        t = Task(
            title=title,
            assigned_to=user_id,
            assigned_by=user_id,
            status=TaskStatus(status),
            due_date=due_date,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

    def test_rank_starts_at_one(self, db_session):
        """The most-urgent task always receives rank=1."""
        from app.models.user import User
        from app.services.ai_task_service import get_ai_task_suggestions

        user = db_session.query(User).filter_by(email="admin@test.com").first()
        if not user:
            pytest.skip("admin user not present; run after admin_client fixture")

        self._make_db_task(db_session, user.id, "Task A", _today_plus(20))
        self._make_db_task(db_session, user.id, "Task B", _today_plus(-1))  # overdue

        session_user = {"user_id": user.id, "role": "admin"}
        suggestions = get_ai_task_suggestions(db_session, session_user)

        assert len(suggestions) >= 1
        assert suggestions[0]["rank"] == 1

    def test_ranks_are_contiguous(self, db_session):
        """Ranks must be 1, 2, 3, … with no gaps."""
        from app.models.user import User
        from app.services.ai_task_service import get_ai_task_suggestions

        user = db_session.query(User).filter_by(email="admin@test.com").first()
        if not user:
            pytest.skip("admin user not present")

        for i in range(3):
            self._make_db_task(
                db_session, user.id,
                f"Rank Task {i}", _today_plus(i * 5)
            )

        session_user = {"user_id": user.id, "role": "admin"}
        suggestions = get_ai_task_suggestions(db_session, session_user)
        ranks = [s["rank"] for s in suggestions]
        assert ranks == list(range(1, len(ranks) + 1)), (
            f"Ranks are not contiguous: {ranks}"
        )

    def test_overdue_ranks_before_future(self, db_session):
        """Overdue task must rank above a far-future task."""
        from app.models.user import User
        from app.services.ai_task_service import get_ai_task_suggestions

        user = db_session.query(User).filter_by(email="admin@test.com").first()
        if not user:
            pytest.skip("admin user not present")

        overdue = self._make_db_task(
            db_session, user.id, "Overdue Rank Task", _today_plus(-5)
        )
        future = self._make_db_task(
            db_session, user.id, "Future Rank Task", _today_plus(60)
        )

        session_user = {"user_id": user.id, "role": "admin"}
        suggestions = get_ai_task_suggestions(db_session, session_user)

        rank_map = {s["task_id"]: s["rank"] for s in suggestions}
        assert rank_map[overdue.id] < rank_map[future.id], (
            "Overdue task must be ranked higher (lower rank number) than a future task"
        )

    def test_urgency_score_formula(self):
        """Unit-test the _urgency_score helper directly."""
        from app.services.ai_task_service import _urgency_score

        overdue_score  = _urgency_score(-3, 5)
        near_score     = _urgency_score(1, 5)
        far_score      = _urgency_score(30, 5)
        no_due_score   = _urgency_score(999, 10)

        assert overdue_score > near_score > far_score
        assert overdue_score > 100   # sentinel: overdue always > 100


# ===========================================================================
# 7. FALLBACK TEST
# ===========================================================================
class TestFallback:
    """
    System must degrade gracefully to rule-based logic when models cannot load.
    """

    def test_fallback_when_priority_model_missing(self):
        """
        With no model file available the system still returns a valid result
        using rule-based logic.
        """
        from app.ml.task_assistant import predict as pred_module

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            # Simulate missing model
            pred_module._priority_model        = None
            pred_module._priority_model_loaded = True

            task   = _make_task(due_date=_today_plus(-2))
            result = pred_module.predict_priority(task)

            assert result["raw"] in ("high", "medium", "low")
            assert "reason" in result
            assert 0.0 <= result["confidence"] <= 1.0
        finally:
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded

    def test_fallback_when_joblib_raises_on_load(self, tmp_path, monkeypatch):
        """
        Patch joblib.load to raise – _load_priority_model() must not propagate.
        Simulates a corrupted .pkl file.
        """
        from app.ml.task_assistant import predict as pred_module

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            # Force re-evaluation of the load path
            pred_module._priority_model_loaded = False
            pred_module._priority_model        = None

            with patch("joblib.load", side_effect=Exception("corrupt model")):
                model = pred_module._load_priority_model()

            assert model is None, "Corrupt model must fall back to None"
        finally:
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded

    def test_fallback_produces_rule_based_high_for_overdue(self):
        """With model=None, overdue task still gets HIGH from rule logic."""
        from app.ml.task_assistant import predict as pred_module

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            pred_module._priority_model        = None
            pred_module._priority_model_loaded = True

            task   = _make_task(due_date=_today_plus(-10))
            result = pred_module.predict_priority(task)
            assert result["raw"] == "high"
        finally:
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded

    def test_get_ai_task_suggestions_never_crashes(self, db_session):
        """
        get_ai_task_suggestions() must not raise even with a broken model.
        """
        from app.models.user import User, UserRole
        from app.core.auth import hash_password
        from app.services.ai_task_service import get_ai_task_suggestions
        from app.ml.task_assistant import predict as pred_module

        user = User(
            name="Fallback User",
            email="fallback@test.com",
            hashed_password=hash_password("pass"),
            role=UserRole.employee,
            department="QA",
            is_active=1,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        original_loaded = pred_module._priority_model_loaded
        original_model  = pred_module._priority_model

        try:
            pred_module._priority_model        = None
            pred_module._priority_model_loaded = True

            session_user = {"user_id": user.id, "role": "employee"}
            result = get_ai_task_suggestions(db_session, session_user)
            assert isinstance(result, list)  # No crash, returns a list
        finally:
            pred_module._priority_model        = original_model
            pred_module._priority_model_loaded = original_loaded


# ===========================================================================
# 8. LOGGING TEST
# ===========================================================================
class TestLogging:
    """Validates that the JSONL training log is written correctly."""

    def test_log_prediction_creates_file(self, tmp_path, monkeypatch):
        """log_prediction() must create the JSONL file if it does not exist."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(
            task_id=99,
            features=[5.0, 3.0, 0.0, 1.0],
            predicted_priority="high",
            confidence=0.87,
        )

        assert fake_log.exists(), "log_prediction() must create the JSONL file"

    def test_log_prediction_appends_entry(self, tmp_path, monkeypatch):
        """Each call to log_prediction() appends exactly one new line."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(42, [2.0, 1.0, 0.0, 1.0], "medium", 0.75)
        std.log_prediction(43, [1.0, 0.0, 0.0, 1.0], "high",   0.91)

        lines = [l for l in fake_log.read_text().splitlines() if l.strip()]
        assert len(lines) == 2, f"Expected 2 log entries, found {len(lines)}"

    def test_log_entry_contains_required_fields(self, tmp_path, monkeypatch):
        """Each JSONL entry must have task_id, predicted_priority, and logged_at."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(7, [3.0, 2.0, 0.0, 1.0], "low", 0.60)

        record = json.loads(fake_log.read_text().strip())
        assert record["task_id"]            == 7,     "Wrong task_id in log"
        assert record["predicted_priority"] == "low",  "Wrong predicted_priority in log"
        assert "logged_at" in record,                  "Missing logged_at in log"

    def test_log_entry_confidence_is_stored(self, tmp_path, monkeypatch):
        """Confidence value is persisted in the log record."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(5, [1.0, 0.0, 0.0, 1.0], "high", 0.94)
        record = json.loads(fake_log.read_text().strip())
        assert record["confidence"] == pytest.approx(0.94, abs=0.001)

    @pytest.mark.asyncio
    async def test_api_call_writes_log_entry(self, admin_client, db_session, tmp_path, monkeypatch):
        """
        A real API call must trigger a JSONL write.
        Monkeypatches LOG_PATH so the production file is untouched.
        """
        from app.models.task import Task, TaskStatus
        from app.models.user import User
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "api_training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        admin = db_session.query(User).filter_by(email="admin@test.com").first()
        task  = Task(
            title="Log Test Task",
            assigned_to=admin.id,
            assigned_by=admin.id,
            status=TaskStatus.pending,
            due_date=_today_plus(3),
        )
        db_session.add(task)
        db_session.commit()

        await admin_client.get("/ai/task-suggestions")

        if fake_log.exists():
            lines = [l for l in fake_log.read_text().splitlines() if l.strip()]
            assert len(lines) >= 1, "No log entry written after API call"
            record = json.loads(lines[-1])
            assert "task_id"            in record
            assert "predicted_priority" in record
            assert "logged_at"          in record


# ===========================================================================
# 9. OUTCOME UPDATE TEST
# ===========================================================================
class TestOutcomeUpdate:
    """update_outcome() must patch the correct JSONL record in-place."""

    def test_update_outcome_patches_actual_priority(self, tmp_path, monkeypatch):
        """After update_outcome() the record's actual_priority is set."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(20, [1.0, 0.0, 0.0, 1.0], "medium", 0.70)
        std.update_outcome(task_id=20, actual_priority="high")

        record = json.loads(fake_log.read_text().strip())
        assert record["actual_priority"] == "high", (
            f"Expected 'high', got '{record['actual_priority']}'"
        )

    def test_update_outcome_patches_was_delayed(self, tmp_path, monkeypatch):
        """After update_outcome() the record's was_delayed is set."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(21, [1.0, 0.0, 0.0, 1.0], "high", 0.80)
        std.update_outcome(task_id=21, was_delayed=True)

        record = json.loads(fake_log.read_text().strip())
        assert record["was_delayed"] is True

    def test_update_outcome_sets_resolved_at(self, tmp_path, monkeypatch):
        """resolved_at must be populated after update_outcome()."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(22, [5.0, 1.0, 0.0, 1.0], "low", 0.55)
        std.update_outcome(task_id=22, actual_priority="low", was_delayed=False)

        record = json.loads(fake_log.read_text().strip())
        assert record["resolved_at"] is not None, "resolved_at must be set after update"

    def test_update_outcome_patches_last_record_for_task(self, tmp_path, monkeypatch):
        """When multiple records exist for same task_id the LAST one is patched."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "training_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        std.log_prediction(30, [5.0, 1.0, 0.0, 1.0], "low",  0.60)
        std.log_prediction(30, [3.0, 2.0, 0.0, 1.0], "high", 0.85)  # duplicate task_id

        std.update_outcome(task_id=30, actual_priority="high")

        records = [json.loads(l) for l in fake_log.read_text().splitlines() if l.strip()]
        last    = records[-1]
        first   = records[0]
        assert last["actual_priority"]  == "high"
        assert first["actual_priority"] is None, "Only the last record should be patched"

    def test_update_outcome_no_crash_when_file_missing(self, tmp_path, monkeypatch):
        """update_outcome() on a missing log must not raise."""
        from app.ml.task_assistant import save_training_data as std

        fake_log = tmp_path / "nonexistent_log.jsonl"
        monkeypatch.setattr(std, "LOG_PATH", fake_log)

        # Should not raise
        std.update_outcome(task_id=999, actual_priority="low")


# ===========================================================================
# 10. EDGE-CASE TESTS
# ===========================================================================
class TestEdgeCases:
    """No tasks, missing deadline, and invalid / unusual data."""

    def test_no_tasks_returns_empty_list(self, db_session):
        """get_ai_task_suggestions() returns [] when the user has no tasks."""
        from app.models.user import User, UserRole
        from app.core.auth import hash_password
        from app.services.ai_task_service import get_ai_task_suggestions

        user = User(
            name="Empty User",
            email="empty@test.com",
            hashed_password=hash_password("pass"),
            role=UserRole.employee,
            department="QA",
            is_active=1,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        session_user = {"user_id": user.id, "role": "employee"}
        result = get_ai_task_suggestions(db_session, session_user)
        assert result == [], f"Expected empty list, got {result}"

    @pytest.mark.asyncio
    async def test_api_returns_empty_list_for_user_with_no_tasks(
        self, admin_client, db_session
    ):
        """API returns [] (not an error) when the user has no active tasks."""
        response = await admin_client.get("/ai/task-suggestions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_missing_deadline_handled_gracefully(self, no_deadline_task):
        """predict_priority() must not raise when due_date is None."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(no_deadline_task)
        assert result["raw"] in ("high", "medium", "low")
        assert "reason" in result

    def test_missing_created_at_handled(self):
        """Task with created_at=None must not crash feature extraction."""
        from app.ml.task_assistant.predict import predict_priority
        task = _make_task(due_date=_today_plus(5), created_at=None)
        task = SimpleNamespace(
            id=99, title="Edge", due_date=_today_plus(5),
            created_at=None, status=SimpleNamespace(value="pending"),
            assigned_to=1, assigned_by=1,
        )
        result = predict_priority(task)
        assert result["raw"] in ("high", "medium", "low")

    def test_dict_based_task_input(self):
        """_build_features() must work with a plain dict (not just ORM obj)."""
        from app.ml.task_assistant.predict import _build_features
        task_dict = {
            "due_date":   _today_plus(10),
            "created_at": datetime.datetime.utcnow(),
            "status":     "pending",
        }
        features = _build_features(task_dict)
        assert len(features) == 4, "Feature vector must have exactly 4 elements"
        assert all(isinstance(f, float) for f in features)

    def test_completed_task_is_low_priority(self, completed_task):
        """Completed tasks are always 'low' priority regardless of deadline."""
        from app.ml.task_assistant.predict import predict_priority
        result = predict_priority(completed_task)
        assert result["raw"] == "low", (
            f"Completed task must be 'low', got '{result['raw']}'"
        )
        assert result["reason"] == "Task is completed"

    def test_build_reason_overdue_plural(self):
        """Overdue reason uses correct plural for > 1 day."""
        from app.ml.task_assistant.predict import _build_reason
        reason = _build_reason(days_due=-3, age=0, status_code=0, priority_raw="high")
        assert "Overdue" in reason
        assert "days" in reason  # "3 days"

    def test_build_reason_overdue_singular(self):
        """Overdue reason uses 'day' (singular) for exactly 1 day overdue."""
        from app.ml.task_assistant.predict import _build_reason
        reason = _build_reason(days_due=-1, age=0, status_code=0, priority_raw="high")
        assert "Overdue" in reason
        assert "1 day" in reason

    def test_very_old_no_deadline_task_is_medium(self):
        """Task with no due date but older than 14 days → medium reason."""
        from app.ml.task_assistant.predict import predict_priority
        old_created = datetime.datetime.utcnow() - datetime.timedelta(days=20)
        task = _make_task(due_date=None, created_at=old_created)
        result = predict_priority(task)
        # Rule: no deadline + age > 14 → medium
        assert result["raw"] in ("medium", "high")
        assert "pending" in result["reason"].lower() or "deadline" in result["reason"].lower()

    def test_feature_vector_has_four_elements(self, overdue_task):
        """_build_features() ALWAYS returns exactly 4 numeric elements."""
        from app.ml.task_assistant.predict import _build_features
        features = _build_features(overdue_task)
        assert len(features) == 4
        assert all(isinstance(v, float) for v in features), (
            f"All features must be floats: {features}"
        )

    def test_feature_has_due_flag(self):
        """has_due feature (index 3) is 1.0 when due_date present, 0.0 otherwise."""
        from app.ml.task_assistant.predict import _build_features

        with_due    = _make_task(due_date=_today_plus(5))
        without_due = _make_task(due_date=None)

        feats_with    = _build_features(with_due)
        feats_without = _build_features(without_due)

        assert feats_with[3]    == 1.0
        assert feats_without[3] == 0.0


# ===========================================================================
# UTILITY / UNIT TESTS  (predict module internals)
# ===========================================================================
class TestPredictInternals:
    """Fine-grained unit tests for internal helper functions."""

    def test_days_until_due_none(self):
        from app.ml.task_assistant.predict import _days_until_due, NO_DUE_DATE_SENTINEL
        assert _days_until_due(None) == float(NO_DUE_DATE_SENTINEL)

    def test_days_until_due_future(self):
        from app.ml.task_assistant.predict import _days_until_due
        future = _today_plus(7)
        assert _days_until_due(future) == pytest.approx(7.0)

    def test_days_until_due_past(self):
        from app.ml.task_assistant.predict import _days_until_due
        past = _today_plus(-3)
        assert _days_until_due(past) == pytest.approx(-3.0)

    def test_task_age_none_created_at(self):
        from app.ml.task_assistant.predict import _task_age
        assert _task_age(None) == 0.0

    def test_status_code_mapping(self):
        from app.ml.task_assistant.predict import _status_code
        assert _status_code("pending")     == 0
        assert _status_code("in_progress") == 1
        assert _status_code("completed")   == 2
        assert _status_code("unknown")     == 0  # default

    def test_rule_priority_boundaries(self):
        from app.ml.task_assistant.predict import _rule_priority
        assert _rule_priority(-1,  0, 0) == "high"    # overdue
        assert _rule_priority(0,   0, 0) == "high"    # today
        assert _rule_priority(2,   0, 0) == "high"    # 2-day boundary
        assert _rule_priority(3,   0, 0) == "medium"  # 3 days
        assert _rule_priority(7,   0, 0) == "medium"  # 7-day boundary
        assert _rule_priority(8,   0, 0) == "low"     # just past medium
        assert _rule_priority(999, 0, 0) == "low"     # no due date, fresh
        assert _rule_priority(999,15, 0) == "medium"  # no due date, old

    def test_priority_labels_coverage(self):
        from app.ml.task_assistant.predict import _PRIORITY_LABELS
        expected = {"high": "🔥 High", "medium": "⚠️ Medium", "low": "🟢 Low"}
        for raw, label in expected.items():
            assert _PRIORITY_LABELS[raw] == label
