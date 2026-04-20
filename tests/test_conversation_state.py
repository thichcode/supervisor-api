from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.conversation_continuity import ConversationContinuityEvaluator
from src.core.schemas import ConversationInfo, InputPayload, MessageInfo, UserInfo
from src.memory.service import MemoryContext, MemoryService


class FakeCache:
    def __init__(self):
        self.store = {}

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ttl=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


class FakeStateRepo:
    def __init__(self):
        self.states = {}
        self.summaries = {}
        self.saved_messages = []
        self.thread_updates = []

    async def get_conversation_state(self, thread_id):
        return self.states.get(thread_id)

    async def upsert_conversation_state(self, **kwargs):
        thread_id = kwargs["thread_id"]
        state = self.states.get(thread_id)
        payload = dict(kwargs)
        if state is None:
            state = SimpleNamespace(
                thread_id=thread_id,
                active_topic_title=None,
                active_topic_summary=None,
                conversation_mode="continuation",
                continuity_score=0.5,
                last_user_intent=None,
                last_assistant_intent=None,
                open_loops=[],
                key_entities=[],
                recent_decisions=[],
                state_json={},
                last_message_at=None,
                turn_count=0,
            )
            self.states[thread_id] = state
        for key, value in payload.items():
            if key == "thread_id":
                continue
            setattr(state, key, value)
        return state

    async def patch_conversation_state(self, thread_id, patch):
        return await self.upsert_conversation_state(thread_id=thread_id, **patch)

    async def get_conversation_summary(self, conversation_id):
        return self.summaries.get(conversation_id)

    async def upsert_conversation_summary(self, conversation_id, summary_text, unresolved_points):
        state = SimpleNamespace(
            conversation_id=conversation_id,
            summary_text=summary_text,
            unresolved_points=unresolved_points,
        )
        self.summaries[conversation_id] = state
        return state

    async def save_message(self, **kwargs):
        self.saved_messages.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def upsert_conversation_thread(self, **kwargs):
        self.thread_updates.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def link_thread_ticket(self, *args, **kwargs):
        return None

    async def upsert_case_memory(self, *args, **kwargs):
        return None

    async def add_memory_item(self, *args, **kwargs):
        return None

    async def get_recent_user_messages(self, *args, **kwargs):
        return []

    async def upsert_user_profile(self, *args, **kwargs):
        return None


@pytest.fixture
def sample_payload():
    return InputPayload(
        request_id="req-1",
        source="telegram",
        timestamp="2026-04-20T09:16:00Z",
        user=UserInfo(id="u1", display_name="Thuong", role="employee", team="it"),
        conversation=ConversationInfo(thread_id="thread-1", message_id="msg-1"),
        message=MessageInfo(text="tiếp đi"),
    )


@pytest.mark.asyncio
async def test_continuity_evaluator_treats_short_followup_as_continuation():
    evaluator = ConversationContinuityEvaluator()
    result = evaluator.evaluate(
        current_message="tiếp đi",
        recent_messages=["Mình đang sửa lỗi KB import", "Thiếu cột extra_metadata"],
        conversation_summary="Đang xử lý lỗi import KB",
        active_topic_summary="Lỗi import KB schema",
        active_topic_title="KB import",
        open_loops=[{"key": "loop-1", "text": "Xác nhận migrate DB"}],
        key_entities=["kb", "import", "metadata"],
    )

    assert result["mode"] == "continuation"
    assert result["continuity_score"] >= 0.72


@pytest.mark.asyncio
async def test_continuity_evaluator_detects_topic_shift():
    evaluator = ConversationContinuityEvaluator()
    result = evaluator.evaluate(
        current_message="nhân tiện, chuyển qua vấn đề khác nhé",
        recent_messages=["Mình đang sửa lỗi KB import"],
        conversation_summary="Đang xử lý lỗi import KB",
        active_topic_summary="Lỗi import KB schema",
        active_topic_title="KB import",
    )

    assert result["mode"] == "new_topic"
    assert result["continuity_score"] <= 0.5


@pytest.mark.asyncio
async def test_memory_context_includes_conversation_state_text():
    context = MemoryContext(
        conversation_summary="summary",
        recent_messages=["a"],
        user_profile={"role": "employee"},
        conversation_state={
            "active_topic_title": "KB import",
            "conversation_mode": "continuation",
            "continuity_score": 0.83,
            "open_loops": [{"key": "loop-1", "text": "Check DB"}],
            "key_entities": ["kb", "db"],
        },
    )

    text = context.get_context_text()
    as_dict = context.to_dict()

    assert "Current Topic: KB import" in text
    assert "Conversation Mode: continuation" in text
    assert "Continuity Score: 0.83" in text
    assert "conversation_state" in as_dict


@pytest.mark.asyncio
async def test_memory_service_commit_updates_conversation_state(monkeypatch):
    monkeypatch.setattr("src.memory.service.settings.enable_user_style_learning", False, raising=False)
    service = MemoryService(session=None, cache=FakeCache())
    repo = FakeStateRepo()
    service.repo = repo

    payload = InputPayload(
        request_id="req-1",
        source="telegram",
        timestamp="2026-04-20T09:16:00Z",
        user=UserInfo(id="u1", display_name="Thuong", role="employee", team="it"),
        conversation=ConversationInfo(thread_id="thread-1", message_id="msg-1"),
        message=MessageInfo(text="tiếp đi"),
    )
    memory = MemoryContext(
        conversation_summary="Đang xử lý lỗi import KB",
        recent_messages=["Mình đang sửa lỗi KB import", "Thiếu cột extra_metadata"],
        user_profile={"role": "employee", "preferences": {}},
        conversation_state={
            "active_topic_title": "KB import",
            "active_topic_summary": "Lỗi import KB schema",
            "conversation_mode": "continuation",
            "continuity_score": 0.8,
            "open_loops": [{"key": "loop-1", "text": "Xác nhận migrate DB"}],
            "key_entities": ["kb", "import"],
            "recent_decisions": [],
            "turn_count": 2,
        },
    )

    await service.commit(
        payload,
        memory_snapshot=memory,
        assistant_text="Mình đã thêm migration cho extra_metadata rồi.",
        result_metadata={"intent": "faq", "risk_level": "low", "agents_used": ["draft"], "model_name": "test-model"},
    )

    state = repo.states[payload.conversation.thread_id]
    summary = repo.summaries[payload.conversation.thread_id]

    assert state.turn_count == 3
    assert state.active_topic_title == "KB import"
    assert state.conversation_mode in {"continuation", "new_topic", "clarify"}
    assert state.state_json["continuity_reason"]
    assert summary.summary_text
    assert summary.unresolved_points
