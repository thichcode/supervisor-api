import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock

from src.api import app


EXTRACTED_ROUTE_PATHS = {
    "/health",
    "/health/ready",
    "/health/detailed",
    "/approvals",
    "/approvals/{approval_id}",
    "/approvals/{approval_id}/action",
    "/approvals/{approval_id}/vote",
    "/chat",
    "/chat/harness",
    "/feedback",
    "/feedback/style/{user_id}",
    "/n8n/actions",
    "/n8n/query",
    "/n8n/action/request",
    "/n8n/approvals/pending",
    "/n8n/approvals/{request_id}",
    "/n8n/approvals/{request_id}/approve",
    "/n8n/approvals/{request_id}/reject",
    "/knowledge/stats",
    "/knowledge/search",
    "/knowledge/search/enhanced",
    "/knowledge/policies",
    "/knowledge/policies/{policy_id}",
    "/knowledge/faqs",
    "/knowledge/faqs/{question_id}",
    "/knowledge/guides",
    "/knowledge/guides/{guide_id}",
    "/knowledge/documents",
    "/knowledge/documents/{document_id}",
    "/knowledge/bulk-import",
    "/knowledge/file/process",
    "/knowledge/file/import",
    "/knowledge/file/batch",
    "/knowledge/file/formats",
    "/admin/users",
    "/admin/users/{user_id}",
    "/admin/config",
    "/admin/config/{key}",
    "/metrics/dashboard",
    "/metrics/dashboard/html",
    "/metrics",
    "/alerts",
    "/alerts/{alert_id}/acknowledge",
    "/alerts/{alert_id}",
    "/harness/status",
    "/harness/execute",
    "/harness/tools",
    "/harness/tools/{tool_name}/execute",
    "/harness/hooks",
    "/harness/evaluations",
    "/harness/benchmark",
    "/harness/compare",
    "/harness/reset",
    "/system/query",
    "/guide/deliver",
    "/callback/send",
}


@pytest.fixture
def route_paths():
    return {route.path for route in app.routes if getattr(route, "path", None)}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestExtractedRouteRegistry:
    def test_all_extracted_routes_are_registered(self, route_paths):
        missing = sorted(EXTRACTED_ROUTE_PATHS - route_paths)
        assert missing == []


class TestSmokeRequests:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, client):
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "supervisor_" in response.text

    @pytest.mark.asyncio
    async def test_knowledge_file_formats_endpoint(self, client):
        response = await client.get("/knowledge/file/formats")
        assert response.status_code == 200
        data = response.json()
        assert "formats" in data
        assert any(item["extension"] == ".pdf" for item in data["formats"])

    @pytest.mark.asyncio
    async def test_system_query_endpoint(self, client, monkeypatch):
        repo = MagicMock()
        repo.get_user_profile = AsyncMock(
            return_value=MagicMock(
                user_id="u1",
                display_name="Thuong",
                role="admin",
                team="core",
                vip_flag=True,
                communication_style="direct",
                preferences={"lang": "vi"},
            )
        )
        repo.get_recent_messages = AsyncMock(return_value=[MagicMock(thread_id="t1"), MagicMock(thread_id="t2")])

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return None

        fake_api_module = MagicMock()
        fake_api_module.async_session = lambda: DummySession()

        import src.api as api_module
        monkeypatch.setattr(api_module, "async_session", lambda: DummySession())
        monkeypatch.setattr("src.memory.repository.MemoryRepository", lambda session: repo)

        response = await client.post(
            "/system/query",
            json={"query": "who is u1", "query_type": "user_info", "user_id": "u1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["results"]["user"]["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_n8n_actions_endpoint(self, client, monkeypatch):
        from src.api.routers import n8n as n8n_router

        tool = MagicMock()
        tool.list_available_actions.return_value = '[{"name": "backup_status"}]'
        monkeypatch.setattr(n8n_router, "get_n8n_tool", lambda: tool)

        response = await client.get("/n8n/actions")
        assert response.status_code == 200
        assert response.json()["actions"][0]["name"] == "backup_status"

    @pytest.mark.asyncio
    async def test_harness_status_endpoint(self, client, monkeypatch):
        from src.api.routers import harness as harness_router

        harness = MagicMock()
        harness.config = MagicMock()
        harness.config.name = "test-harness"
        harness.config.max_iterations = 3
        harness.config.max_tool_calls = 5
        harness.config.timeout_seconds = 30
        harness.config.enable_planning = True
        harness.config.enable_evaluation = True
        harness.config.enable_context_compaction = True
        harness.status = MagicMock(value="ready")
        harness.execution_id = "exec-1"
        harness.lifecycle.get_registered_hooks.return_value = []
        harness.context_manager.get_stats.return_value = {"contexts": 1}
        harness.evaluator.get_stats.return_value = {"runs": 0}

        tool_registry = MagicMock()
        tool_registry.get_stats.return_value = {"total": 1}

        monkeypatch.setattr(harness_router, "get_harness", lambda: harness)
        monkeypatch.setattr(harness_router, "get_tool_registry", lambda: tool_registry)

        response = await client.get("/harness/status")
        assert response.status_code == 200
        assert response.json()["harness"]["name"] == "test-harness"

    @pytest.mark.asyncio
    async def test_monitoring_dashboard_endpoint(self, client, monkeypatch):
        from src.api.routers import monitoring as monitoring_router

        approval_service = MagicMock()
        approval_service.get_all_approvals = AsyncMock(return_value=[])
        monkeypatch.setattr("src.core.approval.approval_service", approval_service)

        response = await client.get("/metrics/dashboard")
        assert response.status_code == 200
        assert "overview" in response.json()

    @pytest.mark.asyncio
    async def test_delivery_callback_endpoint(self, client, monkeypatch):
        class DummyResponse:
            status_code = 200

            def raise_for_status(self):
                return None

        class DummyAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def request(self, **kwargs):
                return DummyResponse()

        monkeypatch.setattr("httpx.AsyncClient", DummyAsyncClient)

        response = await client.post(
            "/callback/send",
            json={
                "callback_url": "https://example.com/callback",
                "method": "POST",
                "original_request_id": "req-1",
                "user_id": "u1",
                "message": "ok",
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"
