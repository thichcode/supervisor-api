import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from src.api import app
from src.api.routers import monitoring


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
    async def test_harness_execute_dangerous_tool_requests_approval(self, client, monkeypatch):
        from src.api.routers import harness as harness_router
        from src.harness import tool_registry as tool_registry_module
        from src.harness.tool_registry import ToolCategory as HarnessToolCategory, ToolRegistry as HarnessToolRegistry

        registry = HarnessToolRegistry()
        handler_calls = {"count": 0}

        async def dangerous_handler(command: str, timeout: int = 30, workdir=None):
            handler_calls["count"] += 1
            return {"output": "should not run"}

        registry.register(
            name="terminal",
            description="Execute shell commands in terminal",
            handler=dangerous_handler,
            category=HarnessToolCategory.TERMINAL,
            dangerous=True,
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        )

        async def fake_create_approval(**kwargs):
            class Approval:
                id = "apr-1"

            return Approval()

        monkeypatch.setattr(tool_registry_module.approval_service, "create_approval", fake_create_approval)
        monkeypatch.setattr(harness_router, "get_tool_registry", lambda: registry)

        response = await client.post("/harness/tools/terminal/execute", json={"command": "rm -rf /"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending_approval"
        assert data["result"]["pending_approval"] is True
        assert handler_calls["count"] == 0

    @pytest.mark.asyncio
    async def test_monitoring_traffic_classifier_distinguishes_service_and_casual(self):
        service_row = SimpleNamespace(
            intent="unknown",
            input_text="Anh check giúp em ticket VPN đang lỗi nhé",
            output_text="",
            kb_hit_count=0,
            approval_required=False,
            outcome_status="skipped",
            confidence_score=0.45,
            processing_latency_ms=None,
            extra_metadata={},
        )
        casual_row = SimpleNamespace(
            intent="unknown",
            input_text="1-2 tháng nữa kỉ niệm lại về thôi bạn",
            output_text="",
            kb_hit_count=0,
            approval_required=False,
            outcome_status="skipped",
            confidence_score=0.45,
            processing_latency_ms=None,
            extra_metadata={},
        )

        service_class, service_reason = monitoring._classify_traffic(service_row)
        casual_class, casual_reason = monitoring._classify_traffic(casual_row)

        assert service_class == "service_like"
        assert service_reason in {"heuristic_fallback", "stored_traffic_class"}
        assert casual_class == "casual_unknown"
        assert casual_reason in {"heuristic_fallback", "stored_traffic_class"}

    @pytest.mark.asyncio
    async def test_monitoring_boss_report_uses_service_breakdown(self, client, monkeypatch):
        async def fake_snapshot(days=7):
            return {
                "timestamp": "2026-04-21T10:00:00",
                "window_days": days,
                "overview": {"total_interactions": 3, "kb_hits": 1, "auto_sent": 1, "need_manual_review": 0, "skipped": 1, "auto_send_rate": 33.3, "kb_hit_rate": 33.3, "approval_required_rate": 0.0, "skip_rate": 33.3, "needs_review_rate": 0.0, "clarification_rate": 0.0, "avg_confidence": 45.0, "avg_latency_ms": 100.0, "avg_latency_sec": 0.1, "top_intents": [{"intent": "faq", "count": 1}]},
                "raw_overview": {"total_interactions": 3},
                "service_overview": {"total_interactions": 1, "kb_hits": 1, "auto_sent": 1, "need_manual_review": 0, "skipped": 0, "needs_review": 0, "clarifications": 0, "kb_hit_rate": 100.0, "approval_required_rate": 0.0, "skip_rate": 0.0, "auto_send_rate": 100.0, "needs_review_rate": 0.0, "clarification_rate": 0.0, "avg_confidence": 55.0, "avg_latency_ms": 100.0, "avg_latency_sec": 0.1, "high_confidence_count": 0, "low_confidence_count": 1, "top_intents": [{"intent": "faq", "count": 1}]},
                "traffic_breakdown": {"raw_total": 3, "service_like": 1, "casual_unknown": 2, "service_like_rate": 33.3, "casual_unknown_rate": 66.7, "service_signal_reasons": {"keyword:ticket": 1}},
                "performance": {"total_interactions": 1, "avg_processing_time_ms": 100.0, "avg_processing_time_sec": 0.1},
                "ai_quality": {"avg_confidence": 55.0, "high_confidence_count": 0, "low_confidence_count": 1, "auto_send_count": 1, "approval_needed_count": 0, "needs_review_count": 0},
                "user_satisfaction": {"total_votes": 0, "agree": 0, "change": 0, "skip": 0, "satisfaction_rate": 0.0},
                "approvals": {"pending": 0, "approved": 0, "rejected": 0, "approve_rate": 0.0, "average_confidence": 0.0},
                "efficiency": {"kb_hit_rate": 100.0, "approval_required_rate": 0.0, "skip_rate": 0.0, "auto_send_rate": 100.0, "needs_review_rate": 0.0, "clarification_rate": 0.0, "avg_confidence": 55.0, "avg_latency_ms": 100.0},
                "top_intents": [{"intent": "faq", "count": 1}],
                "boss_summary": [
                    "Trong 1 ngày gần nhất có 3 interaction(s) raw, trong đó 1 service-like và 2 casual/unknown.",
                    "Service-like rate: 33.3% | Casual/unknown rate: 66.7%.",
                ],
                "recommendations": ["Traffic casual/unknown chiếm 66.7%: cân nhắc lọc chat đời thường khỏi boss report."],
            }

        monkeypatch.setattr("src.api.routers.monitoring._load_dashboard_snapshot", fake_snapshot)

        response = await client.get("/metrics/dashboard/boss-report?days=7")
        assert response.status_code == 200
        assert "service-like" in response.text
        assert "casual/unknown" in response.text
        assert "Traffic casual/unknown chiếm 66.7%" in response.text

    @pytest.mark.asyncio
    async def test_monitoring_dashboard_endpoint(self, client, monkeypatch):
        async def fake_snapshot(days=7):
            return {
                "timestamp": "2026-04-21T10:00:00",
                "window_days": days,
                "overview": {"total_interactions": 10, "kb_hits": 6, "auto_sent": 4, "need_manual_review": 3, "skipped": 3, "auto_send_rate": 40.0},
                "performance": {"total_interactions": 10, "avg_processing_time_ms": 1234.0, "avg_processing_time_sec": 1.23},
                "ai_quality": {"avg_confidence": 48.0, "high_confidence_count": 1, "low_confidence_count": 7, "auto_send_count": 4, "approval_needed_count": 3, "needs_review_count": 3},
                "user_satisfaction": {"total_votes": 5, "agree": 3, "change": 1, "skip": 1, "satisfaction_rate": 60.0},
                "approvals": {"pending": 2, "approved": 4, "rejected": 1, "approve_rate": 80.0, "average_confidence": 55.0},
                "efficiency": {"kb_hit_rate": 60.0, "approval_required_rate": 30.0, "skip_rate": 30.0, "auto_send_rate": 40.0, "needs_review_rate": 30.0, "clarification_rate": 10.0, "avg_confidence": 48.0, "avg_latency_ms": 1234.0},
                "top_intents": [{"intent": "faq", "count": 6}],
                "boss_summary": ["summary one"],
                "recommendations": ["recommendation one"],
            }

        monkeypatch.setattr("src.api.routers.monitoring._load_dashboard_snapshot", fake_snapshot)

        response = await client.get("/metrics/dashboard?days=7")
        assert response.status_code == 200
        body = response.json()
        assert body["overview"]["kb_hits"] == 6
        assert body["efficiency"]["kb_hit_rate"] == 60.0
        assert body["approvals"]["approve_rate"] == 80.0

    @pytest.mark.asyncio
    async def test_monitoring_dashboard_html_endpoint(self, client, monkeypatch):
        async def fake_snapshot(days=7):
            return {
                "timestamp": "2026-04-21T10:00:00",
                "window_days": days,
                "overview": {"total_interactions": 10, "kb_hits": 6, "auto_sent": 4, "need_manual_review": 3, "skipped": 3, "auto_send_rate": 40.0},
                "performance": {"total_interactions": 10, "avg_processing_time_ms": 1234.0, "avg_processing_time_sec": 1.23},
                "ai_quality": {"avg_confidence": 48.0, "high_confidence_count": 1, "low_confidence_count": 7, "auto_send_count": 4, "approval_needed_count": 3, "needs_review_count": 3},
                "user_satisfaction": {"total_votes": 5, "agree": 3, "change": 1, "skip": 1, "satisfaction_rate": 60.0},
                "approvals": {"pending": 2, "approved": 4, "rejected": 1, "approve_rate": 80.0, "average_confidence": 55.0},
                "efficiency": {"kb_hit_rate": 60.0, "approval_required_rate": 30.0, "skip_rate": 30.0, "auto_send_rate": 40.0, "needs_review_rate": 30.0, "clarification_rate": 10.0, "avg_confidence": 48.0, "avg_latency_ms": 1234.0},
                "top_intents": [{"intent": "faq", "count": 6}],
                "boss_summary": ["summary one"],
                "recommendations": ["recommendation one"],
            }

        monkeypatch.setattr("src.api.routers.monitoring._load_dashboard_snapshot", fake_snapshot)

        response = await client.get("/metrics/dashboard/html?days=7")
        assert response.status_code == 200
        assert "Executive Summary" in response.text
        assert "KB Hit Rate" in response.text
        assert "recommendation one" in response.text

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
