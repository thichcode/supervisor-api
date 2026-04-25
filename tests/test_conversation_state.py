from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.app import _chat_context_from_payload, _extract_attachment_evidence
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
        conversation=ConversationInfo(
            thread_id="thread-1",
            message_id="msg-1",
            chat_type="private",
            chat_scope="dm",
            group_chat=False,
            platform="telegram",
        ),
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
            "platform": "telegram",
            "chat_type": "private",
            "chat_scope": "dm",
            "group_chat": False,
            "active_topic_title": "KB import",
            "conversation_mode": "continuation",
            "continuity_score": 0.83,
            "open_loops": [{"key": "loop-1", "text": "Check DB"}],
            "key_entities": ["kb", "db"],
        },
    )

    text = context.get_context_text()
    as_dict = context.to_dict()

    assert "Platform: telegram" in text
    assert "Chat Type: private" in text
    assert "Chat Scope: dm" in text
    assert "Group Chat: False" in text
    assert "Current Topic: KB import" in text
    assert "Conversation Mode: continuation" in text
    assert "Continuity Score: 0.83" in text
    assert "conversation_state" in as_dict


@pytest.mark.asyncio
async def test_extract_attachment_evidence_uses_inline_ocr_text():
    payload = InputPayload(
        request_id="req-image-1",
        source="ms_teams",
        timestamp="2026-04-25T08:00:00Z",
        user=UserInfo(id="u1", display_name="Thuong"),
        conversation=ConversationInfo(thread_id="thread-image-1", message_id="msg-image-1"),
        message=MessageInfo(
            text="",
            attachments=[
                {
                    "type": "image",
                    "name": "error.png",
                    "content_type": "image/png",
                    "ocr_text": "Error code 720 - VPN connection failed",
                }
            ],
        ),
    )

    evidence = await _extract_attachment_evidence(payload)

    assert evidence["attachment_count"] == 1
    assert evidence["has_images"] is True
    assert "error.png" in evidence["attachment_summary"]
    assert "Error code 720" in evidence["attachment_text"]
    assert evidence["attachments"][0]["ocr_text"].startswith("Error code 720")
    assert evidence["issue_signature"]
    assert evidence["needs_clarification"] is False


@pytest.mark.asyncio
async def test_extract_attachment_evidence_flags_missing_image_context():
    payload = InputPayload(
        request_id="req-image-2",
        source="ms_teams",
        timestamp="2026-04-25T08:10:00Z",
        user=UserInfo(id="u1", display_name="Thuong"),
        conversation=ConversationInfo(thread_id="thread-image-2", message_id="msg-image-2"),
        message=MessageInfo(
            text="",
            attachments=[
                {
                    "type": "image",
                    "name": "blank.png",
                    "content_type": "image/png",
                }
            ],
        ),
    )

    evidence = await _extract_attachment_evidence(payload)

    assert evidence["attachment_count"] == 1
    assert evidence["has_images"] is True
    assert evidence["has_actionable_text"] is False
    assert evidence["needs_clarification"] is True
    assert evidence["clarification_hint"]
    assert evidence["issue_signature"] == "blank png" or evidence["issue_signature"]


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
        conversation=ConversationInfo(
            thread_id="thread-1",
            message_id="msg-1",
            chat_type="private",
            chat_scope="dm",
            group_chat=False,
            platform="telegram",
        ),
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


@pytest.mark.asyncio
async def test_memory_service_retrieve_merges_runtime_chat_context():
    service = MemoryService(session=None, cache=FakeCache())
    repo = FakeStateRepo()
    service.repo = repo
    service.cache.store["memory:thread-1"] = {
        "conversation_summary": "summary",
        "recent_messages": ["hello"],
        "user_profile": {"role": "employee"},
        "case_memory": {},
        "episodic_memory": [],
        "external_memory": [],
        "conversation_state": {"active_topic_title": "KB import"},
    }

    payload = InputPayload(
        request_id="req-2",
        source="telegram",
        timestamp="2026-04-20T09:16:00Z",
        user=UserInfo(id="u1", display_name="Thuong", role="employee", team="it"),
        conversation=ConversationInfo(
            thread_id="thread-1",
            message_id="msg-2",
            chat_type="private",
            chat_scope="dm",
            group_chat=False,
            platform="telegram",
        ),
        message=MessageInfo(text="xem tiếp"),
    )

    context = await service.retrieve(payload)

    assert context.conversation_state["chat_type"] == "private"
    assert context.conversation_state["chat_scope"] == "dm"
    assert context.conversation_state["group_chat"] is False
    assert context.conversation_state["platform"] == "telegram"
    assert context.conversation_state["active_topic_title"] == "KB import"


@pytest.mark.asyncio
async def test_chat_context_helper_normalizes_private_and_group():
    private_payload = InputPayload(
        request_id="req-3",
        source="telegram",
        timestamp="2026-04-20T09:16:00Z",
        user=UserInfo(id="u1", display_name="Thuong"),
        conversation=ConversationInfo(thread_id="thread-2", message_id="msg-3", platform="telegram"),
        message=MessageInfo(text="hello"),
    )
    group_payload = InputPayload(
        request_id="req-4",
        source="slack",
        timestamp="2026-04-20T09:16:00Z",
        user=UserInfo(id="u2", display_name="Thuong"),
        conversation=ConversationInfo(
            thread_id="thread-3",
            message_id="msg-4",
            chat_type="group",
            chat_scope="group",
            group_chat=True,
            platform="slack",
        ),
        message=MessageInfo(text="ping"),
    )

    private_context = _chat_context_from_payload(private_payload)
    group_context = _chat_context_from_payload(group_payload)

    assert private_context == {
        "platform": "telegram",
        "chat_type": "private",
        "chat_scope": "dm",
        "group_chat": False,
    }
    assert group_context == {
        "platform": "slack",
        "chat_type": "group",
        "chat_scope": "group",
        "group_chat": True,
    }
