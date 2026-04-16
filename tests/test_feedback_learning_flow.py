"""
Integration tests for Feedback → Learning flow

Tests the complete flow:
1. FeedbackService.create_feedback() creates feedback and learning event
2. LearningService.infer_style_signals() extracts style from feedback
3. BayesianConfidence updates based on feedback
"""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone


class TestFeedbackLearningFlow:
    """Integration test for complete feedback → learning flow"""
    
    @pytest.mark.asyncio
    async def test_bayesian_confidence_positive_feedback(self):
        """Test that positive feedback updates Bayesian confidence"""
        from src.core.bayesian_confidence import BayesianConfidence
        
        bayesian = BayesianConfidence()
        
        # Get initial model performance
        initial_alpha = bayesian.model_performance["llama3"].alpha

        # Simulate positive feedback (accepted)
        bayesian.update_with_feedback(
            user_id="user-001",
            response_id="resp-001",
            is_positive=True,
            model_name="llama3",
        )
        
        # Verify alpha increased (positive signal)
        assert bayesian.model_performance["llama3"].alpha > initial_alpha
        assert bayesian.model_performance["llama3"].alpha == initial_alpha + 1

    @pytest.mark.asyncio
    async def test_bayesian_confidence_negative_feedback(self):
        """Test that negative feedback updates Bayesian confidence"""
        from src.core.bayesian_confidence import BayesianConfidence
        
        bayesian = BayesianConfidence()
        
        # Get initial model performance
        initial_beta = bayesian.model_performance["llama3"].beta

        # Simulate negative feedback (rejected)
        bayesian.update_with_feedback(
            user_id="user-001",
            response_id="resp-001",
            is_positive=False,
            model_name="llama3",
        )
        
        # Verify beta increased (negative signal)
        assert bayesian.model_performance["llama3"].beta > initial_beta
        assert bayesian.model_performance["llama3"].beta == initial_beta + 1

    @pytest.mark.asyncio
    async def test_multiple_feedback_accumulates(self):
        """Test that multiple feedback accumulates correctly"""
        from src.core.bayesian_confidence import BayesianConfidence
        
        bayesian = BayesianConfidence()
        initial_alpha = bayesian.model_performance["llama3"].alpha
        
        # Send 3 positive feedbacks
        for i in range(3):
            bayesian.update_with_feedback(
                user_id="user-001",
                response_id=f"resp-{i}",
                is_positive=True,
                model_name="llama3",
            )
        
        # Alpha should increase by 3 (3 * 1)
        assert bayesian.model_performance["llama3"].alpha == initial_alpha + 3

    @pytest.mark.asyncio
    async def test_style_signals_formal_text(self):
        """Test style inference from formal feedback text"""
        from src.services.learning_service import LearningService
        
        # Mock session
        class FakeSession:
            async def execute(self, stmt):
                class Result:
                    def scalar_one_or_none(self):
                        return None
                return Result()
        
        session = FakeSession()
        learning = LearningService(session)
        
        # Infer from formal text with polite markers
        signals = learning.infer_style_signals(
            text="Please provide the complete documentation regarding employee benefits and retirement plans.",
            source="human_edit"
        )
        
        # Returns list of signal dicts
        assert isinstance(signals, list)
        signal_types = [s["signal_type"] for s in signals]
        assert "verbosity" in signal_types
        
        # Formal text with polite markers should have formal tone
        _ = next(s for s in signals if s["signal_type"] == "verbosity")
        tone_signal = next(s for s in signals if s["signal_type"] == "tone")
        assert tone_signal["signal_value"] == "formal"

    @pytest.mark.asyncio
    async def test_style_signals_casual_text(self):
        """Test style inference from casual feedback text"""
        from src.services.learning_service import LearningService
        
        class FakeSession:
            async def execute(self, stmt):
                class Result:
                    def scalar_one_or_none(self):
                        return None
                return Result()
        
        session = FakeSession()
        learning = LearningService(session)
        
        # Infer from casual text
        signals = learning.infer_style_signals(
            text="ok thanks!",
            source="feedback"
        )
        
        # Casual text should have concise verbosity
        assert isinstance(signals, list)
        verbosity_signal = next(s for s in signals if s["signal_type"] == "verbosity")
        assert verbosity_signal["signal_value"] == "concise"

    @pytest.mark.asyncio
    async def test_feedback_response_model(self):
        """Test FeedbackResponse model validation"""
        from src.core.schemas import FeedbackResponse, FeedbackType
        
        # Valid response
        response = FeedbackResponse(
            id=1,
            request_id="req-123",
            feedback_type=FeedbackType.APPROVAL,
            feedback_label="accepted",
            stored=True,
            learning_event_created=True,
        )
        
        assert response.id == 1
        assert response.request_id == "req-123"
        assert response.stored

    @pytest.mark.asyncio
    async def test_feedback_create_request_model(self):
        """Test FeedbackCreateRequest model"""
        from src.core.schemas import FeedbackCreateRequest, FeedbackType
        
        request = FeedbackCreateRequest(
            request_id="req-123",
            user_id="user-001",
            feedback_type=FeedbackType.APPROVAL,
            feedback_score=1.0,
            feedback_label="accepted",
            feedback_text="Looks good!",
            metadata={"vote": "agree"},
        )
        
        assert request.request_id == "req-123"
        assert request.feedback_type == FeedbackType.APPROVAL
        assert request.feedback_label == "accepted"


class TestApprovalDecisionFlow:
    """Test approval decision flow"""
    
    @pytest.mark.asyncio
    async def test_approval_positive_updates_bayesian(self):
        """Test that approved decisions are treated as positive feedback"""
        from src.core.bayesian_confidence import BayesianConfidence
        
        bayesian = BayesianConfidence()
        initial_alpha = bayesian.model_performance["llama3"].alpha
        
        # Approval with high confidence should be positive
        bayesian.update_with_feedback(
            user_id="manager-001",
            response_id="approval-001",
            is_positive=True,  # approved
            model_name="llama3",
        )
        
        # Alpha should increase
        assert bayesian.model_performance["llama3"].alpha == initial_alpha + 1

    @pytest.mark.asyncio
    async def test_approval_rejected_updates_bayesian(self):
        """Test that rejected approvals are treated as negative feedback"""
        from src.core.bayesian_confidence import BayesianConfidence
        
        bayesian = BayesianConfidence()
        initial_beta = bayesian.model_performance["llama3"].beta
        
        # Rejected approval should be negative
        bayesian.update_with_feedback(
            user_id="manager-001",
            response_id="approval-001",
            is_positive=False,  # rejected
            model_name="llama3",
        )
        
        # Beta should increase
        assert bayesian.model_performance["llama3"].beta == initial_beta + 1


class TestFeedbackReplayWorker:
    """Test FeedbackReplayWorker integration"""
    
    @pytest.mark.asyncio
    async def test_worker_processes_approval_event(self):
        """Test worker correctly processes approval decision events"""
        from src.services.feedback_learning_worker import FeedbackReplayWorker
        from src.core.bayesian_confidence import BayesianConfidence
        from types import SimpleNamespace
        
        # Create mock event
        mock_event = SimpleNamespace(
            id=1,
            request_id="approval-001",
            user_id="manager-001",
            thread_id="thread-001",
            event_type="approval_decision",
            event_payload={
                "approval_status": "approved",
                "confidence_score": 0.45,
                "model_name": "llama3",
            },
            processed=False,
            created_at=datetime.now(timezone.utc),
            processed_at=None
        )
        
        bayesian = BayesianConfidence()
        initial_alpha = bayesian.model_performance["llama3"].alpha
        
        # Create mock supervisor
        supervisor = SimpleNamespace(
            bayesian_confidence=bayesian,
            response_validator=SimpleNamespace(confidence_calculator=bayesian),
            decision_engine=SimpleNamespace(router=SimpleNamespace(record_feedback=AsyncMock())),
        )
        
        # Create mock session factory
        class FakeSessionContext:
            def __init__(self):
                pass
            async def __aenter__(self):
                return FakeSession(mock_event)
            async def __aexit__(self, exc_type, exc, tb):
                return False
        
        class FakeSession:
            def __init__(self, event):
                self.event = event
            
            async def execute(self, stmt):
                return FakeResult(self.event)
            
            def add(self, obj):
                pass
            
            async def commit(self):
                pass
        
        class FakeResult:
            def __init__(self, event):
                self._event = event
            
            def scalars(self):
                return self
            
            def all(self):
                return [self._event]
        
        class FakeRedis:
            async def get_json(self, key):
                return None
            async def set_json(self, key, value, ttl=3600):
                return True
        
        # Patch redis
        with patch("src.services.feedback_learning_worker.redis_cache", FakeRedis()):
            worker = FeedbackReplayWorker(
                session_factory=lambda: FakeSessionContext(),
                supervisor=supervisor,
            )
            
            processed = await worker.replay_once()
        
        # Event should be processed
        assert processed == 1
        
        # Bayesian should be updated (positive for approved)
        assert bayesian.model_performance["llama3"].alpha > initial_alpha
