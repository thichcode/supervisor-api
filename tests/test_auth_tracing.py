"""
Tests for Authentication and Tracing modules
"""
import pytest
import time
from unittest.mock import patch, MagicMock


class TestJWTAuth:
    """Test JWT authentication"""
    
    def test_create_and_verify_token(self):
        from src.core.auth import JWTAuth
        
        auth = JWTAuth(secret="test-secret-key")
        
        # Create token
        token = auth.create_token(
            user_id="user-123",
            role="admin",
            scopes=["read", "write"],
            expires_in=3600
        )
        
        assert token is not None
        assert len(token) > 0
        
        # Verify token
        result = auth.verify(token)
        
        assert result.authenticated is True
        assert result.user_id == "user-123"
        assert result.role.value == "admin"
        assert "read" in result.scopes
    
    def test_expired_token_rejected(self):
        from src.core.auth import JWTAuth
        
        auth = JWTAuth(secret="test-secret-key")
        
        # Create expired token
        token = auth.create_token(
            user_id="user-123",
            role="user",
            expires_in=-1  # Already expired
        )
        
        result = auth.verify(token)
        
        assert result.authenticated is False
        assert "expired" in result.error.lower()
    
    def test_invalid_token_rejected(self):
        from src.core.auth import JWTAuth
        
        auth = JWTAuth(secret="test-secret-key")
        
        result = auth.verify("invalid.token.here")
        
        assert result.authenticated is False
    
    def test_wrong_secret_rejected(self):
        from src.core.auth import JWTAuth
        
        auth1 = JWTAuth(secret="secret-1")
        auth2 = JWTAuth(secret="secret-2")
        
        token = auth1.create_token(user_id="user-1", role="user")
        
        # Verify with wrong secret
        result = auth2.verify(token)
        
        assert result.authenticated is False


class TestHMACAuth:
    """Test HMAC authentication"""
    
    def test_compute_and_verify_signature(self):
        from src.core.auth import HMACAuth
        
        auth = HMACAuth(secret="webhook-secret")
        payload = b'{"message": "test"}'
        
        # Compute signature
        signature = auth.compute_signature(payload)
        
        assert signature is not None
        assert len(signature) == 64  # SHA256 hex
        
        # Verify signature
        result = auth.verify(payload, signature)
        
        assert result.authenticated is True
    
    def test_invalid_signature_rejected(self):
        from src.core.auth import HMACAuth
        
        auth = HMACAuth(secret="webhook-secret")
        payload = b'{"message": "test"}'
        
        result = auth.verify(payload, "invalid-signature")
        
        assert result.authenticated is False
    
    def test_expired_signature_rejected(self):
        from src.core.auth import HMACAuth
        
        auth = HMACAuth(secret="webhook-secret")
        payload = b'{"message": "test"}'
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        
        signature = auth.compute_signature(payload, old_timestamp)
        
        result = auth.verify(payload, signature, timestamp=old_timestamp, max_age_seconds=300)
        
        assert result.authenticated is False
        assert "expired" in result.error.lower()


class TestAPIKeyAuth:
    """Test API key authentication"""
    
    def test_add_and_verify_key(self):
        from src.core.auth import APIKeyAuth
        
        auth = APIKeyAuth()
        
        # Add a key
        auth.add_key("my-api-key-12345", "service-1", "service", ["read"])
        
        # Verify key
        result = auth.verify("my-api-key-12345")
        
        assert result.authenticated is True
        assert result.user_id == "service-1"
        assert "read" in result.scopes
    
    def test_bearer_prefix(self):
        from src.core.auth import APIKeyAuth
        
        auth = APIKeyAuth()
        auth.add_key("api-key-12345", "service-1")
        
        # Verify with Bearer prefix
        result = auth.verify("Bearer api-key-12345")
        
        assert result.authenticated is True
    
    def test_invalid_key_rejected(self):
        from src.core.auth import APIKeyAuth
        
        auth = APIKeyAuth()
        
        result = auth.verify("invalid-key")
        
        assert result.authenticated is False


class TestTracing:
    """Test OpenTelemetry tracing"""
    
    def test_tracing_manager_initialization(self):
        from src.core.tracing import TracingManager, TracingConfig
        
        manager = TracingManager(TracingConfig(console_export=True))
        
        # Should not fail even without otel
        manager.initialize()
        
        assert True  # If we get here, initialization worked
    
    def test_traced_decorator_no_error(self):
        from src.core.tracing import traced
        
        @traced("test_function")
        async def test_func():
            return "success"
        
        # Should work without errors
        import asyncio
        result = asyncio.run(test_func())
        
        assert result == "success"
    
    def test_add_span_attribute(self):
        from src.core.tracing import add_span_attribute, get_current_span
        
        # Should not raise errors even without active span
        add_span_attribute("test_key", "test_value")
        
        span = get_current_span()
        # Span might be None without tracing setup


class TestTracingMetrics:
    """Test tracing metrics"""
    
    def test_tracing_metrics_initialization(self):
        from src.core.tracing import tracing_metrics, tracing
        
        # Initialize with mock meter
        mock_meter = MagicMock()
        tracing_metrics.initialize(mock_meter)
        
        # Should not raise errors
        tracing_metrics.record_request_duration(100)
        tracing_metrics.record_llm_duration(50, "gpt-4")
        tracing_metrics.record_db_duration(10, "SELECT", "users")
        
        assert True
