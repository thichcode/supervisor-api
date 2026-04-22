"""Smoke tests for KB Templates analytics router."""
import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app


class TestKBTemplatesEndpoints:
    @pytest.mark.asyncio
    async def test_kb_templates_json_returns_expected_fields(self):
        """JSON endpoint returns summary with expected top-level fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert "window_days" in data
        assert "summary" in data
        assert "raw_metrics" in data

    @pytest.mark.asyncio
    async def test_kb_templates_summary_has_required_fields(self):
        """Summary section contains all required fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates")
        assert response.status_code == 200
        summary = response.json()["summary"]
        assert "total_searches" in summary
        assert "total_detected" in summary
        assert "total_not_detected" in summary
        assert "detection_rate" in summary
        assert "templates" in summary

    @pytest.mark.asyncio
    async def test_kb_templates_report_is_plaintext(self):
        """Report endpoint returns plain text with expected sections."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates/report")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        text = response.text
        assert "KB Template Analytics Report" in text
        assert "Total KB searches:" in text
        assert "Template detected:" in text
        assert "Template not detected:" in text
        assert "Template breakdown:" in text

    @pytest.mark.asyncio
    async def test_kb_templates_html_is_html(self):
        """HTML endpoint returns HTML with key labels."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates/html")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        text = response.text
        assert "KB Template Analytics" in text
        assert "Total Searches" in text
        assert "Detection Rate" in text
        assert "Top Templates" in text
        assert "Coverage Gaps" in text

    @pytest.mark.asyncio
    async def test_kb_templates_days_param_is_respected(self):
        """days param is accepted and passed through."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates?days=30")
        assert response.status_code == 200
        data = response.json()
        assert data["window_days"] == 30

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates/report?days=14")
        assert response.status_code == 200

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates/html?days=7")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_kb_templates_days_param_validation(self):
        """days param rejects out-of-range values."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates?days=0")
        assert response.status_code == 422
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates?days=100")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_kb_templates_detection_rate_is_percentage(self):
        """detection_rate is a valid percentage (0-100)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates")
        assert response.status_code == 200
        summary = response.json()["summary"]
        rate = summary["detection_rate"]
        assert 0 <= rate <= 100

    @pytest.mark.asyncio
    async def test_kb_templates_templates_list_structure(self):
        """Each template entry has required fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/metrics/kb-templates")
        assert response.status_code == 200
        templates = response.json()["summary"]["templates"]
        for t in templates:
            assert "template_id" in t
            assert "label" in t
            assert "total_count" in t
            assert "by_search_type" in t
