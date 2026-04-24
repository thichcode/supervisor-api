import pytest
from datetime import datetime, timedelta, timezone

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
        metadata={
            "thread_id": "thread-1",
            "platform": "telegram",
            "chat_type": "private",
            "chat_scope": "dm",
            "group_chat": False,
            "risk_level": "low",
            "kb_sources": [
                {"id": "kb-1", "title": "Reset VPN", "similarity": 0.91},
            ],
            "kb_evidence": [
                {"id": "kb-1", "title": "Reset VPN", "similarity": 0.91, "content": "Use the VPN portal..."},
            ],
        },
    )


@pytest.fixture
def group_approval():
    return ApprovalRequest(
        id="approval-456",
        request_id="req-456",
        user_id="thuong",
        display_name="Thuong",
        original_message="Xóa file log",
        ai_response="Sẽ xóa file log theo yêu cầu",
        confidence=0.71,
        threshold=0.9,
        status=ApprovalStatus.PENDING,
        action_type="send_message",
        metadata={
            "thread_id": "thread-group-1",
            "platform": "telegram",
            "chat_type": "group",
            "chat_scope": "group",
            "group_chat": True,
            "risk_level": "high",
        },
    )


def test_build_approval_message_text_includes_buttons_context(sample_approval):
    text = build_approval_message_text(sample_approval)

    assert "Direct Message Approval Required" in text
    assert "Chat Mode: Direct message" in text
    assert "This request came from a direct message." in text
    assert "Display Name:" in text
    assert "User ID:" in text
    assert "Thread ID:" in text
    assert "Platform: telegram" in text
    assert "Chat Type: private" in text
    assert "Chat Scope: dm" in text
    assert "Group Chat: False" in text
    assert sample_approval.id in text
    assert "Thuong" in text
    assert "42.0%" in text
    assert "Xin chào" in text
    assert "KB Sources:" in text
    assert "Reset VPN" in text


def test_build_approval_message_text_group_chat_has_group_specific_header(group_approval):
    text = build_approval_message_text(group_approval)

    assert "Group Chat Approval Required" in text
    assert "Chat Mode: Group chat" in text
    assert "This request came from a *group chat*." in text
    assert "Display Name:" in text
    assert "Thread ID: thread-group-1" in text
    assert "Group Chat: True" in text
    assert "Risk: high" in text
    assert "Original (preview):" in text
    assert "AI (preview):" in text
    assert "Tap *View full context*" in text
    assert "AI Response:" not in text


def test_build_approval_inline_keyboard_has_approve_and_reject_buttons(sample_approval):
    keyboard = build_approval_inline_keyboard(sample_approval.id)

    assert keyboard == {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approval:approve:{sample_approval.id}"},
                {"text": "🚫 Reject", "callback_data": f"approval:reject:{sample_approval.id}"},
            ],
            [
                {"text": "🔍 Search KB", "callback_data": f"approval:search_kb:{sample_approval.id}"},
            ]
        ]
    }


@pytest.mark.asyncio
async def test_build_approval_inline_keyboard_group_compact_has_view_full_context(group_approval):
    keyboard = build_approval_inline_keyboard(group_approval.id, compact=True, group_chat=True)

    assert keyboard == {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approval:approve:{group_approval.id}"},
                {"text": "🚫 Reject", "callback_data": f"approval:reject:{group_approval.id}"},
            ],
            [
                {"text": "🔍 Search KB", "callback_data": f"approval:search_kb:{group_approval.id}"},
            ],
            [
                {"text": "🔎 View full context", "callback_data": f"approval:view_full_context:{group_approval.id}"},
            ]
        ]
    }


@pytest.mark.asyncio
async def test_handle_callback_query_view_full_context_expands_group_card(monkeypatch, group_approval):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )

    answers = []
    edits = []

    async def fake_get_approval(approval_id):
        assert approval_id == group_approval.id
        return group_approval

    async def fake_answer_callback_query(callback_query_id, text, show_alert=False):
        answers.append((callback_query_id, text, show_alert))

    async def fake_edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode="Markdown"):
        edits.append((chat_id, message_id, text, reply_markup, parse_mode))

    import src.core.approval as approval_module

    monkeypatch.setattr(approval_module.approval_service, "get_approval", fake_get_approval)
    monkeypatch.setattr(adapter, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(adapter, "_edit_message_text", fake_edit_message_text)

    handled = await adapter.handle_callback_query(
        {
            "id": "cb-1",
            "data": f"approval:view_full_context:{group_approval.id}",
            "message": {"chat": {"id": "-100"}, "message_id": 77},
            "from": {"id": 11, "first_name": "Approver"},
        }
    )

    assert handled is True
    assert answers == [("cb-1", "Đã mở full context", False)]
    assert edits
    chat_id, message_id, text, reply_markup, parse_mode = edits[0]
    assert chat_id == "-100"
    assert message_id == 77
    assert "AI Response:" in text
    assert "Original:" in text
    assert "Group Chat Approval Required" in text
    assert reply_markup["inline_keyboard"][1][0]["callback_data"] == f"approval:search_kb:{group_approval.id}"
    assert parse_mode == "Markdown"


@pytest.mark.asyncio
async def test_handle_callback_query_search_kb_prompts_for_text_input(monkeypatch, group_approval):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )

    answers = []
    sent_messages = []

    async def fake_answer_callback_query(callback_query_id, text, show_alert=False):
        answers.append((callback_query_id, text, show_alert))

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
        sent_messages.append((chat_id, text, reply_markup, parse_mode))

    monkeypatch.setattr(adapter, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(adapter, "_send_message", fake_send_message)

    handled = await adapter.handle_callback_query(
        {
            "id": "cb-2",
            "data": f"approval:search_kb:{group_approval.id}",
            "message": {"chat": {"id": "-100"}, "message_id": 77},
            "from": {"id": 11, "first_name": "Approver"},
        }
    )

    assert handled is True
    assert answers == [("cb-2", "Nhập từ khóa để tìm KB...", True)]
    assert sent_messages
    chat_id, text, reply_markup, parse_mode = sent_messages[0]
    assert chat_id == "-100"
    assert "Search Knowledge Base" in text
    assert reply_markup["force_reply"] is True
    assert reply_markup["input_field_placeholder"]
    assert reply_markup["kb_approval_id"] == group_approval.id
    assert parse_mode is None


@pytest.mark.asyncio
async def test_flush_conversation_buffer_merges_messages_and_tracks_mode(monkeypatch):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )
    adapter._buffer_delay_seconds = 60
    adapter._conversation_buffers = {
        "telegram_123": {
            "thread_id": "telegram_123",
            "chat_id": "123",
            "user_id": "user-123",
            "display_name": "Thuong",
            "metadata": {"platform": "telegram"},
            "messages": [
                {"text": "VPN không vào được", "message_mode": "problem", "timestamp": datetime.now(timezone.utc).isoformat()},
                {"text": "Mã lỗi 720", "message_mode": "problem", "timestamp": datetime.now(timezone.utc).isoformat()},
            ],
            "created_at": (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat(),
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat(),
            "message_mode": "problem",
        }
    }

    replies = []
    supervisor_calls = []

    async def fake_call_supervisor(user_id, display_name, message, thread_id, metadata):
        supervisor_calls.append((user_id, display_name, message, thread_id, metadata))
        return "Đã nhận, mình kiểm tra ngay"

    async def fake_send_message(chat_id, text):
        replies.append((chat_id, text))

    monkeypatch.setattr(adapter, "_call_supervisor", fake_call_supervisor)
    monkeypatch.setattr(adapter, "_send_message", fake_send_message)

    await adapter._flush_conversation_buffer("telegram_123")

    assert supervisor_calls
    user_id, display_name, merged_message, thread_id, metadata = supervisor_calls[0]
    assert user_id == "user-123"
    assert display_name == "Thuong"
    assert merged_message == "VPN không vào được\nMã lỗi 720"
    assert thread_id == "telegram_123"
    assert metadata["thread_buffered"] is True
    assert metadata["buffer_message_count"] == 2
    assert metadata["message_mode"] == "problem"
    assert replies == [("123", "Đã nhận, mình kiểm tra ngay")]


@pytest.mark.asyncio
async def test_register_bot_commands_includes_health(monkeypatch):
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
            return {"ok": True, "result": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers, timeout))
            return FakeResponse()

        async def get(self, url, params=None, timeout=None):
            if url.endswith("/getMe"):
                return FakeResponse()
            raise AssertionError(f"Unexpected GET {url}")

    class FakeHttpx:
        def AsyncClient(self, self_arg=None):
            return FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    await adapter._register_bot_commands()

    assert calls, "Expected setMyCommands to be called"
    url, payload, headers, timeout = calls[0]
    assert url.endswith("/setMyCommands")
    assert any(cmd["command"] == "health" for cmd in payload["commands"])
    assert any(cmd["command"] == "help" for cmd in payload["commands"])
    assert any(cmd["command"] == "kb" for cmd in payload["commands"])
    assert any(cmd["command"] == "super_analytics" for cmd in payload["commands"])


@pytest.mark.asyncio
async def test_handle_kb_command_search_shows_paginated_results(monkeypatch):
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
            return {
                "results": [
                    {
                        "knowledge_type": "faq",
                        "id": "faq-1",
                        "title": "Reset VPN",
                        "content": "Use the VPN portal to reset access.",
                        "category": "access",
                        "similarity": 0.91,
                    }
                ],
                "total": 6,
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers, timeout))
            return FakeResponse()

    class FakeHttpx:
        def AsyncClient(self, self_arg=None):
            return FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    await adapter._handle_command("123", "user-1", "/kb search vpn")

    search_calls = [call for call in calls if call[0].endswith("/knowledge/search")]
    send_calls = [call for call in calls if call[0].endswith("/sendMessage")]
    assert search_calls
    assert send_calls
    payload = send_calls[0][1]
    assert "KB Search" in payload["text"]
    assert "Tóm tắt:" in payload["text"]
    assert "Làm nhanh:" in payload["text"]
    assert "Mẫu:" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("kb:page:")


@pytest.mark.asyncio
async def test_handle_kb_callback_pages_results(monkeypatch):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )
    adapter._kb_sessions = {
        "sess123": {
            "session_id": "sess123",
            "mode": "search",
            "query": "vpn",
            "search_type": "all",
            "category": None,
            "tags": [],
            "page_size": 5,
            "user_id": "user-1",
        }
    }

    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {
                        "knowledge_type": "faq",
                        "id": "faq-2",
                        "title": "VPN lỗi 720",
                        "content": "Kiểm tra credential và profile.",
                        "category": "access",
                        "similarity": 0.88,
                    }
                ],
                "total": 6,
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append((url, json, headers, timeout))
            return FakeResponse()

    class FakeHttpx:
        def AsyncClient(self, self_arg=None):
            return FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    edits = []
    answers = []

    async def fake_answer_callback_query(callback_query_id, text=None, show_alert=False):
        answers.append((callback_query_id, text, show_alert))

    async def fake_edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode=None):
        edits.append((chat_id, message_id, text, reply_markup, parse_mode))

    monkeypatch.setattr(adapter, "_answer_callback_query", fake_answer_callback_query)
    monkeypatch.setattr(adapter, "_edit_message_text", fake_edit_message_text)

    result = await adapter.handle_callback_query(
        {
            "id": "cb-kb-1",
            "from": {"id": 111, "first_name": "Admin"},
            "message": {"chat": {"id": 123}, "message_id": 77},
            "data": "kb:page:sess123:2",
        }
    )

    assert result is True
    assert answers and answers[0][1].startswith("Đang mở trang 2")
    assert edits
    assert "Page: 2/2" in edits[0][2]
    assert edits[0][3]["inline_keyboard"][0][0]["callback_data"] == "kb:page:sess123:1"
@pytest.mark.asyncio
async def test_handle_super_analytics_command_fetches_report(monkeypatch):
    adapter = TelegramAdapter(
        token="bot-token",
        session_store=object(),
        supervisor_url="http://localhost:8000",
        api_key=None,
    )

    calls = []

    class FakeResponse:
        status_code = 200
        text = "Supervisor boss report\nWindow: last 1 day(s)\n- KB hit rate: 60%"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, timeout=None):
            calls.append((url, params, timeout))
            return FakeResponse()

    class FakeHttpx:
        def AsyncClient(self, self_arg=None):
            return FakeClient()

    monkeypatch.setattr("src.gateway.platforms.telegram.httpx", FakeHttpx())

    sent = []

    async def fake_send_message(chat_id, text, reply_markup=None, parse_mode=None):
        sent.append((chat_id, text, reply_markup, parse_mode))

    monkeypatch.setattr(adapter, "_send_message", fake_send_message)

    await adapter._handle_super_analytics_command("123", "/super_analytics 1")

    assert calls
    assert calls[0][0].endswith("/metrics/dashboard/boss-report")
    assert calls[0][1] == {"days": 1}
    assert sent
    assert sent[0][0] == "123"
    assert "Super Analytics (1 ngày)" in sent[0][1]
    assert "Supervisor boss report" in sent[0][1]


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
        def AsyncClient(self, self_arg=None):
            return FakeClient()

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
        def AsyncClient(self, self_arg=None):
            return FakeClient()

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
