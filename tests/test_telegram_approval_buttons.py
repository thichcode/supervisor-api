import pytest

from src.core.schemas import ApprovalRequest, ApprovalStatus
from src.gateway.platforms.telegram import (
    TelegramAdapter,
    build_approval_inline_keyboard,
    build_approval_message_text,
)


@pytest.fixture
def sample_approval():
    return ApprovalRequest(
        id="approval-123",
        request_id="req-123",
        user_id="thuong",
        display_name="Thuong",
        original_message="Cần duyệt phản hồi",
        ai_response="Xin chào",
        confidence=0.42,
        threshold=0.9,
        status=ApprovalStatus.PENDING,
        action_type="send_message",
        metadata={"thread_id": "thread-1", "risk_level": "low"},
    )


def test_build_approval_message_text_includes_buttons_context(sample_approval):
    text = build_approval_message_text(sample_approval)

    assert "Approval Required" in text
    assert sample_approval.id in text
    assert "Thuong" in text
    assert "42.0%" in text
    assert "Xin chào" in text


def test_build_approval_inline_keyboard_has_approve_and_reject_buttons(sample_approval):
    keyboard = build_approval_inline_keyboard(sample_approval.id)

    assert keyboard == {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approval:approve:{sample_approval.id}"},
                {"text": "🚫 Reject", "callback_data": f"approval:reject:{sample_approval.id}"},
            ]
        ]
    }


@pytest.mark.asyncio
async def test_handle_callback_query_approves_and_edits_message(monkeypatch, sample_approval):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key="api-key",
    )

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 999}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers, timeout))
            return FakeResponse()

    class FakeHttpx:
        AsyncClient = lambda self=None: FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    async def fake_answer_callback_query(callback_query_id, text=None, show_alert=False):
        calls.append(("answer", callback_query_id, text, show_alert))

    async def fake_edit_message_text(chat_id, message_id, text):
        calls.append(("edit", chat_id, message_id, text))

    monkeypatch.setattr(adapter, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(adapter, "_edit_message_text", fake_edit_message_text)

    result = await adapter.handle_callback_query(
        {
            "id": "cb-1",
            "from": {"id": 111, "first_name": "Admin"},
            "message": {"chat": {"id": -100123}, "message_id": 77},
            "data": f"approval:approve:{sample_approval.id}",
        }
    )

    assert result is True
    assert any(call[0] == "answer" for call in calls)
    assert any("/approvals/approval-123/action" in call[0] if isinstance(call[0], str) else False for call in calls)
    assert any(call[0] == "edit" for call in calls)


@pytest.mark.asyncio
async def test_handle_callback_query_rejects_with_default_comment(monkeypatch, sample_approval):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 999}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers, timeout))
            return FakeResponse()

    class FakeHttpx:
        AsyncClient = lambda self=None: FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    async def fake_answer_callback_query(callback_query_id, text=None, show_alert=False):
        calls.append(("answer", callback_query_id, text, show_alert))

    async def fake_edit_message_text(chat_id, message_id, text):
        calls.append(("edit", chat_id, message_id, text))

    monkeypatch.setattr(adapter, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(adapter, "_edit_message_text", fake_edit_message_text)

    result = await adapter.handle_callback_query(
        {
            "id": "cb-2",
            "from": {"id": 222, "first_name": "Reviewer"},
            "message": {"chat": {"id": -100123}, "message_id": 78},
            "data": f"approval:reject:{sample_approval.id}",
        }
    )

    assert result is True
    assert any(call[0] == "answer" for call in calls)
    assert any("Reject" in str(call[-1]) for call in calls if call[0] == "edit")
