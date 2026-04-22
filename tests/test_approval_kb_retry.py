import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from src.api import app


@pytest.mark.asyncio
async def test_retry_with_kb_returns_actionable_kb_response(monkeypatch):
    approval = SimpleNamespace(
        id="approval-1",
        request_id="req-1",
        original_message="VPN của tôi không vào được",
    )

    class FakeKnowledgeService:
        def __init__(self, session, llm):
            self.search_with_llm_enhancement = AsyncMock(
                return_value=SimpleNamespace(
                    results=[
                        {
                            "knowledge_type": "faq",
                            "id": "faq-1",
                            "title": "Reset VPN",
                            "content": "1. Open the VPN portal\n2. Click Reset Access\n3. Reconnect and verify login",
                            "category": "access",
                            "similarity": 0.93,
                        }
                    ]
                )
            )

    @asynccontextmanager
    async def fake_async_session():
        session = MagicMock()
        session.commit = AsyncMock()
        yield session

    monkeypatch.setattr("src.api.routers.approvals.approval_service.get_approval", AsyncMock(return_value=approval))
    monkeypatch.setattr("src.knowledge.service.KnowledgeRetrievalService", FakeKnowledgeService)
    monkeypatch.setattr("src.api.async_session", fake_async_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/approvals/approval-1/retry-with-kb",
            json={"keywords": "vpn lỗi", "requested_by": "thuong"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["kb_results_count"] == 1
    assert body["kb_template_label"] == "VPN / Access"
    assert "Mẫu KB: VPN / Access" in body["new_response"]
    assert "Tóm tắt:" in body["new_response"]
    assert "Làm theo:" in body["new_response"]
    assert "Reset VPN" in body["new_response"]
    assert body["kb_action_items"]
