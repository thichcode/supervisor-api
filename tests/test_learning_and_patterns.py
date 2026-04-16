import pytest

from src.db.models import ResponseLearningEvent, ResponsePattern
from src.services.learning_events import build_learning_event_key, record_learning_event
from src.services.pattern_learning_service import PatternLearningService
from src.services.semantic_text import SemanticTextEncoder


class FakeScalarResult:
    def __init__(self, item):
        self._item = item

    def scalar_one_or_none(self):
        return self._item


class FakeNestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeLearningEventSession:
    def __init__(self):
        self.events_by_key: dict[str, ResponseLearningEvent] = {}
        self.added: list[ResponseLearningEvent] = []
        self.flushed = 0

    async def execute(self, stmt):
        # The helper queries by dedupe key; this fake session simply returns the
        # first stored event when one exists.
        item = next(iter(self.events_by_key.values()), None)
        return FakeScalarResult(item)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "dedupe_key", None):
            self.events_by_key[obj.dedupe_key] = obj

    async def flush(self):
        self.flushed += 1

    def begin_nested(self):
        return FakeNestedTransaction()


class FakePatternResult:
    def __init__(self, items):
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakePatternSession:
    def __init__(self, patterns):
        self.patterns = patterns
        self.added = []
        self.flushed = 0

    async def execute(self, stmt):
        return FakePatternResult(self.patterns)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


class FakeSemanticEncoder(SemanticTextEncoder):
    def __init__(self):
        super().__init__(dimension=8)

    def encode(self, text: str) -> list[float]:
        normalized = text.lower().strip()
        if "password" in normalized:
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "backup" in normalized:
            return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [0.0] * 8


@pytest.mark.asyncio
async def test_record_learning_event_is_idempotent():
    session = FakeLearningEventSession()
    payload = {"approval_status": "approved", "request_id": "req-1"}

    event_one = await record_learning_event(
        session,
        request_id="req-1",
        user_id="user-1",
        thread_id="thread-1",
        ticket_id="ticket-1",
        ticket_system="teams",
        event_type="approval_decision",
        event_payload=payload,
    )
    event_two = await record_learning_event(
        session,
        request_id="req-1",
        user_id="user-1",
        thread_id="thread-1",
        ticket_id="ticket-1",
        ticket_system="teams",
        event_type="approval_decision",
        event_payload=payload,
    )

    assert event_one.dedupe_key == build_learning_event_key("req-1", "approval_decision", payload)
    assert event_one is event_two
    assert len(session.added) == 1
    assert session.flushed == 1


@pytest.mark.asyncio
async def test_pattern_learning_service_matches_semantic_paraphrase():
    pattern = ResponsePattern(
        question_hash="abc123",
        question_text="How do I reset my password?",
        answer_text="Use the self-service portal to reset your password.",
        team_id="it",
        intent="access",
        confidence_score=1.0,
        is_active=True,
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )
    session = FakePatternSession([pattern])
    service = PatternLearningService(session=session, encoder=FakeSemanticEncoder())

    result = await service.find_similar_pattern(
        "What is the process to change my login password?",
        team_id="it",
        intent="access",
    )

    assert result is not None
    matched_pattern, similarity = result
    assert matched_pattern.question_hash == "abc123"
    assert similarity >= service.SIMILARITY_THRESHOLD


@pytest.mark.asyncio
async def test_pattern_learning_service_stores_embedding_on_approval():
    session = FakePatternSession([])
    service = PatternLearningService(session=session, encoder=FakeSemanticEncoder())

    pattern = await service.store_pattern(
        question="How do I reset my password?",
        answer="Use the portal.",
        user_id="user-1",
        team_id="it",
        intent="access",
        approved_by="thuong",
        source_request_id="req-1",
    )

    assert pattern.embedding == [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert session.flushed == 1
    assert len(session.added) == 1
