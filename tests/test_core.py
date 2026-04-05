import pytest
from datetime import datetime
from src.core import (
    InputPayload,
    UserInfo,
    ConversationInfo,
    MessageInfo,
    CaseInfo,
    IntentType,
    RiskLevel,
)
from src.core.intent_classifier import IntentClassifier
from src.core.risk_evaluator import RiskEvaluator
from src.memory import MemoryContext
from src.agents import ContextAgent, DraftAgent, QAAgent


@pytest.fixture
def sample_payload():
    return InputPayload(
        request_id="test-123",
        source="ms_teams",
        timestamp=datetime.utcnow().isoformat(),
        user=UserInfo(
            id="user-001",
            display_name="John Doe",
            role="employee",
        ),
        conversation=ConversationInfo(
            thread_id="thread-001",
            message_id="msg-001",
        ),
        message=MessageInfo(
            text="What is the company policy on remote work?",
        ),
    )


@pytest.fixture
def sample_context():
    return MemoryContext(
        conversation_summary="User is asking about company policies",
        recent_messages=["Hello", "I need help with remote work"],
        user_profile={"role": "employee", "vip_flag": False},
        case_memory=None,
        episodic_memory=[],
    )


class TestIntentClassifier:
    def test_classify_faq(self, sample_payload, sample_context):
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload, sample_context)
        assert result.intent == IntentType.FAQ

    def test_classify_policy(self, sample_payload, sample_context):
        sample_payload.message.text = "What is the policy for annual leave?"
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload, sample_context)
        assert result.intent == IntentType.POLICY

    def test_classify_support_case(self, sample_payload, sample_context):
        sample_payload.case = CaseInfo(case_id="CASE-001", priority="medium")
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload, sample_context)
        assert result.intent == IntentType.SUPPORT_CASE

    def test_classify_executive(self, sample_payload, sample_context):
        sample_payload.user.vip_flag = True
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload, sample_context)
        assert result.intent == IntentType.EXECUTIVE_REQUEST
        assert result.confidence >= 0.7


class TestRiskEvaluator:
    def test_evaluate_low_risk(self, sample_payload, sample_context):
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, sample_context)
        assert result.risk_level == RiskLevel.LOW
        assert len(result.flags) == 0

    def test_evaluate_high_risk_executive(self, sample_payload, sample_context):
        sample_payload.user.vip_flag = True
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, sample_context)
        assert RiskLevel.MEDIUM in [result.risk_level, RiskLevel.HIGH]
        assert "vip" in result.flags

    def test_evaluate_financial_risk(self, sample_payload, sample_context):
        sample_payload.message.text = "What is the quarterly financial report?"
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, sample_context)
        assert "financial" in result.flags


class TestContextAgent:
    def test_build_context(self, sample_payload, sample_context):
        agent = ContextAgent()
        result = agent.build(sample_payload, sample_context)
        assert "current_message" in result
        assert result["current_message"] == sample_payload.message.text
        assert "user_info" in result
        assert result["user_info"]["name"] == "John Doe"


class TestDraftAgent:
    def test_generate_draft(self, sample_payload, sample_context):
        agent = DraftAgent()
        context = {"conversation_history": [], "case_info": None}
        policy = {"guidelines_found": False, "relevant_policies": []}
        knowledge = {"facts": [], "patterns": []}
        result = agent.generate(sample_payload, context, policy, knowledge)
        assert isinstance(result, str)
        assert len(result) > 0


class TestQAAgent:
    def test_validate_good_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        draft = "This is a comprehensive answer to your question about the policy."
        result = agent.validate(draft, sample_payload, sample_context)
        assert result["confidence"] >= 0.7
        assert not result["needs_review"]

    def test_validate_short_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        draft = "Short answer."
        result = agent.validate(draft, sample_payload, sample_context)
        assert result["confidence"] < 0.7
        assert len(result["issues"]) > 0

    def test_refine_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        validation = {
            "draft": "Some answer.",
            "confidence": 0.6,
            "issues": ["Confidence below threshold"],
            "needs_review": True,
        }
        result = agent.refine(validation, sample_payload)
        assert "review" in result.lower()
