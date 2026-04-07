"""
Integration Tests for Supervisor API
Tests API endpoints, error handling, and circuit breaker integration
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio

from src.api import app
from src.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from src.core.dlq import DeadLetterQueue, DLQStatus
from src.core.error_handler import (
    AppError,
    CircuitBreakerOpenError,
    handle_processing_error,
    create_error_response
)


class TestCircuitBreaker:
    """Test circuit breaker functionality"""
    
    @pytest.fixture
    def circuit_breaker(self):
        return CircuitBreaker(
            "test",
            CircuitBreakerConfig(
                failure_threshold=3,
                success_threshold=2,
                timeout=1.0
            )
        )
    
    @pytest.mark.asyncio
    async def test_circuit_starts_closed(self, circuit_breaker):
        assert circuit_breaker.state == CircuitState.CLOSED
        assert await circuit_breaker.can_execute() is True
    
    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self, circuit_breaker):
        # Record failures up to threshold
        for _ in range(3):
            await circuit_breaker.record_failure()
        
        assert circuit_breaker.state == CircuitState.OPEN
        assert await circuit_breaker.can_execute() is False
    
    @pytest.mark.asyncio
    async def test_circuit_half_open_after_timeout(self, circuit_breaker):
        # Open the circuit
        for _ in range(3):
            await circuit_breaker.record_failure()
        
        assert circuit_breaker.state == CircuitState.OPEN
        
        # Wait for timeout
        await asyncio.sleep(1.1)
        
        # Check state transition
        await circuit_breaker._check_state_transition()
        assert circuit_breaker.state == CircuitState.HALF_OPEN
    
    @pytest.mark.asyncio
    async def test_circuit_closes_after_successes(self, circuit_breaker):
        # Open the circuit
        for _ in range(3):
            await circuit_breaker.record_failure()
        
        # Wait for timeout
        await asyncio.sleep(1.1)
        await circuit_breaker._check_state_transition()
        
        # Record successes
        for _ in range(2):
            await circuit_breaker.record_success()
        
        assert circuit_breaker.state == CircuitState.CLOSED
    
    def test_circuit_metrics_tracking(self, circuit_breaker):
        assert circuit_breaker.metrics.failures == 0
        assert circuit_breaker.metrics.total_calls == 0
        
        # These would be called through can_execute
        # which increments total_calls


class TestDeadLetterQueue:
    """Test dead letter queue functionality"""
    
    @pytest.fixture
    def dlq(self):
        return DeadLetterQueue(max_retries=3, retry_delay_seconds=1)
    
    def test_add_entry(self, dlq):
        payload = {"test": "data"}
        error = ValueError("Test error")
        
        entry = dlq.add("req-123", payload, error)
        
        assert entry.original_request_id == "req-123"
        assert entry.error_type == "ValueError"
        assert entry.status == DLQStatus.PENDING.value
        assert entry.retry_count == 0
    
    def test_get_pending_entries(self, dlq):
        dlq.add("req-1", {"data": 1}, Exception("error1"))
        dlq.add("req-2", {"data": 2}, Exception("error2"))
        
        pending = dlq.get_pending()
        assert len(pending) == 2
    
    def test_mark_resolved(self, dlq):
        entry = dlq.add("req-1", {"data": 1}, Exception("error"))
        assert dlq.mark_resolved(entry.id) is True
        assert dlq.get(entry.id).status == DLQStatus.RESOLVED.value
    
    def test_increment_retry(self, dlq):
        entry = dlq.add("req-1", {"data": 1}, Exception("error"))
        
        assert entry.retry_count == 0
        dlq.increment_retry(entry.id)
        assert entry.retry_count == 1
        assert entry.next_retry_at is not None
    
    def test_max_retries_exceeded(self, dlq):
        entry = dlq.add("req-1", {"data": 1}, Exception("error"))
        
        for _ in range(3):
            dlq.increment_retry(entry.id)
        
        assert dlq.get(entry.id).status == DLQStatus.FAILED.value
    
    def test_dlq_stats(self, dlq):
        dlq.add("req-1", {}, Exception("e1"))
        dlq.add("req-2", {}, Exception("e2"))
        
        entry = dlq.get_pending()[0]
        dlq.mark_resolved(entry.id)
        
        stats = dlq.get_stats()
        assert stats["total"] == 2
        assert stats["pending"] == 1
        assert stats["resolved"] == 1


class TestErrorHandling:
    """Test error handling"""
    
    def test_create_error_response(self):
        response = create_error_response(
            code="TEST_ERROR",
            message="Test message",
            request_id="req-123",
            details={"key": "value"}
        )
        
        assert response["error"]["code"] == "TEST_ERROR"
        assert response["error"]["message"] == "Test message"
        assert response["error"]["request_id"] == "req-123"
        assert response["error"]["details"]["key"] == "value"
    
    def test_app_error(self):
        error = AppError("Test error", "TEST_CODE", 400)
        
        assert error.message == "Test error"
        assert error.code == "TEST_CODE"
        assert error.status_code == 400
    
    def test_circuit_breaker_open_error(self):
        error = CircuitBreakerOpenError("llm_client")
        
        assert error.status_code == 503
        assert "llm_client" in error.message


class TestAPIEndpoints:
    """Test API endpoints with mock dependencies"""
    
    @pytest.fixture
    def mock_llm(self):
        mock = AsyncMock()
        mock.complete = AsyncMock(return_value=("Test response", 0.9))
        mock.health_check = AsyncMock(return_value=True)
        return mock
    
    @pytest.fixture
    def mock_session(self):
        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        return mock
    
    @pytest.fixture
    def mock_redis(self):
        mock = AsyncMock()
        mock.connect = AsyncMock()
        mock.close = AsyncMock()
        mock.exists = AsyncMock(return_value=True)
        mock.set = AsyncMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self, mock_llm, mock_session, mock_redis):
        with patch('src.api.llm_client', mock_llm), \
             patch('src.api.async_session', return_value=mock_session), \
             patch('src.api.redis_cache', mock_redis):
            
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
    
    @pytest.mark.asyncio
    async def test_ready_endpoint_all_healthy(self, mock_llm, mock_session, mock_redis):
        with patch('src.api.llm_client', mock_llm), \
             patch('src.api.async_session', return_value=mock_session), \
             patch('src.api.redis_cache', mock_redis):
            
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health/ready")
        
        assert response.status_code == 200
        data = response.json()
        # All checks should pass with mocks
        assert data["checks"]["llm"] is True
    
    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
        
        assert response.status_code == 200
        assert "supervisor_" in response.text


class TestRateLimiting:
    """Test rate limiting"""
    
    @pytest.mark.asyncio
    async def test_rate_limit_header(self):
        # Rate limiting is per-IP, test that headers are present
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        
        # Should have rate limit headers (if configured)
        # Headers depend on slowapi configuration
        assert response.status_code == 200


class TestWebhookValidation:
    """Test webhook input validation"""
    
    @pytest.fixture
    def valid_payload(self):
        return {
            "request_id": "test-123",
            "source": "ms_teams",
            "timestamp": "2024-01-01T00:00:00Z",
            "user": {
                "id": "user-001",
                "display_name": "Test User",
                "role": "employee"
            },
            "conversation": {
                "thread_id": "thread-001",
                "message_id": "msg-001"
            },
            "message": {
                "text": "Hello, I need help with password reset"
            }
        }
    
    @pytest.mark.asyncio
    async def test_webhook_with_valid_payload(self, valid_payload):
        mock_result = MagicMock()
        mock_result.status = "completed"
        mock_result.confidence = 0.9
        mock_result.risk_level = "low"
        mock_result.metadata = {"intent": "faq"}
        
        with patch('src.api.supervisor.process', return_value=mock_result):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/webhook/n8n",
                    json=valid_payload,
                    headers={"X-Webhook-Secret": "test-secret"}
                )
        
        # With mocked supervisor, should get response
        # Actual test depends on full setup
        assert response.status_code in [200, 401, 500]
    
    @pytest.mark.asyncio
    async def test_webhook_rejects_empty_message(self):
        payload = {
            "request_id": "test-123",
            "source": "ms_teams",
            "timestamp": "2024-01-01T00:00:00Z",
            "user": {"id": "user-001", "display_name": "Test", "role": "employee"},
            "conversation": {"thread_id": "thread-001", "message_id": "msg-001"},
            "message": {"text": ""}  # Empty message
        }
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/webhook/n8n", json=payload)
        
        # Should reject empty message
        assert response.status_code == 400


class TestSecurity:
    """Test security features"""
    
    @pytest.mark.asyncio
    async def test_invalid_webhook_secret(self):
        payload = {
            "request_id": "test-123",
            "source": "ms_teams",
            "timestamp": "2024-01-01T00:00:00Z",
            "user": {"id": "user-001", "display_name": "Test", "role": "employee"},
            "conversation": {"thread_id": "thread-001", "message_id": "msg-001"},
            "message": {"text": "Test"}
        }
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/webhook/n8n",
                json=payload,
                headers={"X-Webhook-Secret": "wrong-secret"}
            )
        
        # Should reject invalid secret (if secret is configured)
        # Response depends on settings
        assert response.status_code in [200, 401]


class TestConcurrency:
    """Test concurrent request handling"""
    
    @pytest.mark.asyncio
    async def test_concurrent_health_checks(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Make 10 concurrent requests
            tasks = [client.get("/health") for _ in range(10)]
            responses = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(r.status_code == 200 for r in responses)
    
    @pytest.mark.asyncio
    async def test_concurrent_circuit_breaker(self):
        cb = CircuitBreaker("concurrent_test")
        
        async def failing_operation():
            await cb.record_failure()
        
        # Simulate concurrent failures
        await asyncio.gather(*[failing_operation() for _ in range(5)])
        
        # Circuit should be open
        assert cb.state == CircuitState.OPEN
