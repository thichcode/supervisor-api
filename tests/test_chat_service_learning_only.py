import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.schemas import ChatRequest, MessageType
from src.services.chat_service import ChatService


@pytest.mark.asyncio
async def test_style_learning_user_skips_answer_generation(monkeypatch):
    import src.api as api_module
    import src.services.chat_service as chat_service_module

    class DummySession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeMemoryService:
        def __init__(self, session, cache):
            self.session = session
            self.cache = cache
            self.commit = AsyncMock(return_value=None)
            self.retrieve = AsyncMock(return_value=MagicMock(recent_messages=[], conversation_summary=None))

    supervisor = MagicMock()
    supervisor.process = AsyncMock()

    monkeypatch.setattr(api_module, "async_session", lambda: DummySession())
    monkeypatch.setattr(api_module, "supervisor", supervisor)
    monkeypatch.setattr(chat_service_module, "MemoryService", FakeMemoryService)
    monkeypatch.setattr(chat_service_module.settings, "enable_user_style_learning", True, raising=False)
    monkeypatch.setattr(chat_service_module.settings, "user_style_learning_user_id", "style-user", raising=False)

    service = ChatService()
    response = await service.handle_chat(
        ChatRequest(
            user_id="style-user",
            display_name="Thuong",
            message="test style learning only",
            metadata={},
            message_type=MessageType.TEXT,
        )
    )

    assert response.status == "skipped"
    assert response.message == ""
    assert response.metadata["style_learning_only"] is True
    assert response.metadata["skipped"] is True
    supervisor.process.assert_not_called()
