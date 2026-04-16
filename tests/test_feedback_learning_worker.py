from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.core.bayesian_confidence import BayesianConfidence


class FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakeSession:
    def __init__(self, events):
        self.events = events
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        return FakeScalarResult(self.events)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeLearningService:
    def __init__(self):
        self.add_signals = AsyncMock()
        self.recompute_profile = AsyncMock()


class FakeRedis:
    def __init__(self, initial=None):
        self.store = initial or {}
        self.saved = {}

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ttl=3600):
        self.saved[key] = value
        self.store[key] = value
        return True


class PendingEvent(SimpleNamespace):
    pass


@pytest.mark.asyncio
async def test_feedback_replay_worker_updates_bayesian_and_marks_events_processed(monkeypatch):
    from src.services.feedback_learning_worker import FeedbackReplayWorker

    approval_event = PendingEvent(
        id=1,
        request_id="req-1",
        user_id="thuong",
        thread_id="thread-1",
        ticket_id=None,
        ticket_system=None,
        event_type="approval_decision",
        event_payload={
            "approval_status": "approved",
            "reviewed_by": "thuong",
            "confidence_score": 0.38,
            "model_name": "llama3",
        },
        processed=False,
        created_at=datetime.now(timezone.utc),
        processed_at=None,
    )
    vote_event = PendingEvent(
        id=2,
        request_id="req-2",
        user_id="thuong",
        thread_id="thread-2",
        ticket_id=None,
        ticket_system=None,
        event_type="feedback_received",
        event_payload={
            "feedback_type": "approval",
            "feedback_label": "accepted",
            "feedback_text": "Looks good",
            "edited_output_text": None,
            "model_name": "llama3",
        },
        processed=False,
        created_at=datetime.now(timezone.utc),
        processed_at=None,
    )

    session = FakeSession([approval_event, vote_event])
    fake_learning = FakeLearningService()
    bayes = BayesianConfidence()
    supervisor = SimpleNamespace(
        bayesian_confidence=bayes,
        response_validator=SimpleNamespace(confidence_calculator=BayesianConfidence()),
        decision_engine=SimpleNamespace(router=SimpleNamespace(record_feedback=AsyncMock())),
    )

    fake_redis = FakeRedis()
    monkeypatch.setattr("src.services.feedback_learning_worker.redis_cache", fake_redis)

    worker = FeedbackReplayWorker(
        session_factory=lambda: FakeSessionContext(session),
        supervisor=supervisor,
        learning_service_factory=lambda _session: fake_learning,
        state_key="learning:bayesian_state",
    )

    processed = await worker.replay_once()

    assert processed == 2
    assert approval_event.processed is True
    assert vote_event.processed is True
    assert approval_event.processed_at is not None
    assert vote_event.processed_at is not None

    # Two positive signals should increment alpha twice in both calculators.
    assert supervisor.bayesian_confidence.model_performance["llama3"].alpha == 87
    assert supervisor.response_validator.confidence_calculator.model_performance["llama3"].alpha == 87
    assert fake_redis.saved["learning:bayesian_state"]["model_performance"]["llama3"]["alpha"] == 87


@pytest.mark.asyncio
async def test_feedback_replay_worker_maps_change_votes_to_negative_feedback(monkeypatch):
    from src.services.feedback_learning_worker import FeedbackReplayWorker

    event = PendingEvent(
        id=3,
        request_id="req-3",
        user_id="thuong",
        thread_id="thread-3",
        ticket_id=None,
        ticket_system=None,
        event_type="feedback_received",
        event_payload={
            "feedback_type": "approval",
            "feedback_label": "edited",
            "vote": "change",
            "feedback_text": "Please adjust the wording",
            "model_name": "llama3",
        },
        processed=False,
        created_at=datetime.now(timezone.utc),
        processed_at=None,
    )

    session = FakeSession([event])
    fake_learning = FakeLearningService()
    supervisor = SimpleNamespace(
        bayesian_confidence=BayesianConfidence(),
        response_validator=SimpleNamespace(confidence_calculator=BayesianConfidence()),
        decision_engine=SimpleNamespace(router=SimpleNamespace(record_feedback=AsyncMock())),
    )

    monkeypatch.setattr("src.services.feedback_learning_worker.redis_cache", FakeRedis())

    worker = FeedbackReplayWorker(
        session_factory=lambda: FakeSessionContext(session),
        supervisor=supervisor,
        learning_service_factory=lambda _session: fake_learning,
    )

    processed = await worker.replay_once()

    assert processed == 1
    assert supervisor.bayesian_confidence.model_performance["llama3"].beta == 16
    fake_learning.add_signals.assert_awaited_once()
    fake_learning.recompute_profile.assert_awaited_once_with("thuong")
