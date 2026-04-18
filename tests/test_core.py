import pytest
from datetime import datetime, timezone
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
from src.llm.provider import MultiProviderLLMClient, LLMProvider
from src.knowledge.service import KnowledgeRetrievalService
from src.memory.service import MemoryService


@pytest.fixture
def sample_payload():
    return InputPayload(
        request_id="test-123",
        source="ms_teams",
        timestamp=datetime.now(timezone.utc).isoformat(),
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
            text="How do I reset my password?",
        ),
    )


@pytest.fixture
def sample_payload_vietnamese():
    return InputPayload(
        request_id="test-456",
        source="ms_teams",
        timestamp=datetime.now(timezone.utc).isoformat(),
        user=UserInfo(
            id="user-002",
            display_name="Nguyễn Văn A",
            role="employee",
        ),
        conversation=ConversationInfo(
            thread_id="thread-002",
            message_id="msg-002",
        ),
        message=MessageInfo(
            text="Chính sách nghỉ phép năm mới là gì?",
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


@pytest.fixture
def vip_context():
    return MemoryContext(
        conversation_summary="VIP user inquiry",
        recent_messages=["Hello CEO"],
        user_profile={"role": "manager", "vip_flag": True},
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

    def test_classify_executive(self, vip_context, sample_payload):
        sample_payload.user.vip_flag = True
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload, vip_context)
        assert result.intent == IntentType.EXECUTIVE_REQUEST
        assert result.confidence >= 0.7

    def test_classify_vietnamese_policy(self, sample_payload_vietnamese, sample_context):
        classifier = IntentClassifier()
        result = classifier.classify(sample_payload_vietnamese, sample_context)
        assert result.intent in [IntentType.POLICY, IntentType.FAQ]
        assert result.confidence > 0.5


class TestRiskEvaluator:
    def test_evaluate_low_risk(self, sample_payload, sample_context):
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, sample_context)
        assert result.risk_level == RiskLevel.LOW
        assert len(result.flags) == 0

    def test_evaluate_vip_risk(self, sample_payload, vip_context):
        sample_payload.user.vip_flag = True
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, vip_context)
        assert "vip" in result.flags

    def test_evaluate_high_priority_case(self, sample_payload, sample_context):
        sample_payload.case = CaseInfo(case_id="CASE-001", priority="high")
        evaluator = RiskEvaluator()
        result = evaluator.evaluate(sample_payload, sample_context)
        assert "high_priority_case" in result.flags

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

    def test_build_context_with_case(self, sample_payload, sample_context):
        sample_payload.case = CaseInfo(case_id="CASE-001", priority="high")
        sample_context.case_memory = {"status": "open", "owner": "agent-1", "summary": "Password reset issue"}
        agent = ContextAgent()
        result = agent.build(sample_payload, sample_context)
        assert result["case_info"] is not None
        assert result["case_info"]["case_id"] == "CASE-001"


class TestDraftAgent:
    @pytest.mark.asyncio
    async def test_generate_draft_fallback(self, sample_payload, sample_context):
        agent = DraftAgent()
        context = {"conversation_history": [], "case_info": None, "conversation_summary": ""}
        policy = {"guidelines_found": False, "relevant_policies": []}
        knowledge = {"facts": [], "patterns": []}
        result = await agent.generate(sample_payload, context, policy, knowledge, None)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "John Doe" in result

    @pytest.mark.asyncio
    async def test_generate_draft_with_policy(self, sample_payload, sample_context):
        agent = DraftAgent()
        context = {"conversation_history": [], "case_info": None, "conversation_summary": ""}
        policy = {
            "guidelines_found": True,
            "relevant_policies": ["Annual Leave Policy v2.0"],
            "sop_steps": ["Step 1: Check eligibility", "Step 2: Submit request"]
        }
        knowledge = {"facts": ["Users get 12 days/year"], "patterns": []}
        result = await agent.generate(sample_payload, context, policy, knowledge, None)
        assert isinstance(result, str)
        assert "Annual Leave Policy" in result


class TestQAAgent:
    @pytest.mark.asyncio
    async def test_validate_good_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        draft = "This is a comprehensive answer to your question about the policy."
        result = await agent.validate(draft, sample_payload, {}, None)
        assert result["confidence"] >= 0.7

    @pytest.mark.asyncio
    async def test_validate_short_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        draft = "Short answer."
        result = await agent.validate(draft, sample_payload, {}, None)
        assert result["confidence"] < 0.7
        assert len(result["issues"]) > 0

    @pytest.mark.asyncio
    async def test_validate_empty_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        draft = ""
        result = await agent.validate(draft, sample_payload, {}, None)
        assert result["needs_review"] is True

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

    def test_refine_empty_draft(self, sample_payload, sample_context):
        agent = QAAgent()
        validation = {
            "draft": "",
            "confidence": 0.0,
            "issues": ["Empty response"],
            "needs_review": True,
        }
        result = agent.refine(validation, sample_payload)
        assert "Người dùng" in result or "user" in result.lower() or "thank" in result.lower()


class TestDecisionEngine:
    def test_low_risk_faq_uses_fast_path(self, sample_payload):
        from src.core import IntentClassification, RiskEvaluation
        from src.core.supervisor import DecisionEngine

        engine = DecisionEngine()
        sample_payload.message.text = "What is the VPN service?"
        intent = IntentClassification(intent=IntentType.FAQ, confidence=0.42)
        risk = RiskEvaluation(risk_level=RiskLevel.LOW)

        assert engine.should_use_subagents(intent, risk, sample_payload) is False
        assert engine.needs_human_review(intent, risk, sample_payload, confidence=0.38) is False


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_direct_answer_path(self, sample_payload, sample_context):
        from src.core.supervisor import Supervisor

        class FakeLLM:
            async def complete(self, system_prompt, user_message, context=None):
                from src.llm.provider import LLMResponse
                return LLMResponse(
                    content="Dynamic answer",
                    confidence=0.91,
                    usage={},
                    model="fake",
                    provider="fake",
                    finish_reason="stop"
                )

        async def fake_log_audit(*args, **kwargs):
            return None

        supervisor = Supervisor()
        supervisor.set_llm(FakeLLM())
        supervisor._log_audit = fake_log_audit

        result = await supervisor.process(sample_payload, sample_context)
        assert result.status == "completed"
        assert result.confidence > 0.8

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Supervisor v2 has different architecture - test needs update")
    async def test_subagent_path_with_policy_intent(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor

        sample_payload.message.text = "What is the company policy on remote work?"
        
        class FakeLLM:
            async def complete(self, system_prompt, user_message, context=None):
                from src.llm.provider import LLMResponse
                return LLMResponse(
                    content="Policy response",
                    confidence=0.85,
                    usage={},
                    model="fake",
                    provider="fake",
                    finish_reason="stop"
                )

        async def fake_log_audit(*args, **kwargs):
            return None

        async def mock_search_knowledge_base(self, query, search_type, llm):
            return []

        async def mock_bm25_search(self, query, limit):
            return []

        async def mock_fetch_urls(self, payload):
            return ""

        monkeypatch.setattr("src.agents.subagents.KnowledgeAgent._search_knowledge_base", mock_search_knowledge_base)
        monkeypatch.setattr("src.knowledge.bm25_search.HybridSearch.search", mock_bm25_search)
        monkeypatch.setattr("src.core.supervisor.Supervisor._fetch_urls", mock_fetch_urls)

        supervisor = Supervisor()
        supervisor.set_llm(FakeLLM())
        supervisor._log_audit = fake_log_audit

        result = await supervisor.process(sample_payload, sample_context)
        assert "policy" in result.metadata["intent"]
        assert len(result.metadata["agents_used"]) > 1


class TestKnowledgeSearch:
    @pytest.mark.asyncio
    async def test_long_query_is_truncated_before_search(self):
        long_query = (
            "Running with gitlab-runner 17.4.0 (b92ee590) "
            "Preparing the docker executor " * 40
        )

        service = KnowledgeRetrievalService(session=object())
        captured_queries = []

        async def fake_search_knowledge_base(kb_type, query, category, tags, limit):
            captured_queries.append(query)
            return []

        service._search_knowledge_base = fake_search_knowledge_base  # type: ignore[method-assign]

        result = await service.search(long_query)

        assert result.query == long_query
        assert captured_queries
        assert all(len(query) <= 512 for query in captured_queries)
        assert all("\n" not in query for query in captured_queries)
        assert result.total == 0


class TestUserStyleLearning:
    def test_infer_structured_style(self):
        service = MemoryService.__new__(MemoryService)
        style, signals = service._infer_user_style("1. First step\n2. Second step\n- Final note")

        assert style == "structured"
        assert signals["has_numbered_steps"] is True
        assert signals["has_bullets"] is True

    @pytest.mark.asyncio
    async def test_draft_agent_uses_style_instructions(self, sample_payload):
        class FakeLLM:
            def __init__(self):
                self.calls = []

            async def complete(self, system_prompt, user_message):
                self.calls.append((system_prompt, user_message))
                return type("Resp", (), {"content": "ok", "confidence": 0.9})()

        llm = FakeLLM()
        agent = DraftAgent()
        payload = sample_payload
        context = {
            "conversation_summary": "",
            "conversation_history": [],
            "user_info": {
                "role": "employee",
                "communication_style": "concise",
                "preferences": {"style_profile": {"style_signals": {"has_bullets": True}}},
            },
        }

        result = await agent.generate(payload, context, {}, {}, llm)

        assert result == "ok"
        assert llm.calls
        system_prompt, user_prompt = llm.calls[0]
        assert "Trả lời rất ngắn gọn" in system_prompt
        assert "Ưu tiên định dạng gạch đầu dòng" in system_prompt
        assert "Phong cách người dùng: concise" in user_prompt


class TestLLMProvider:
    def test_provider_detection(self, monkeypatch):
        monkeypatch.setattr("src.llm.provider.settings.llm_provider", "", raising=False)
        client = MultiProviderLLMClient()
        assert client.get_provider("gpt-4o") == LLMProvider.OPENAI
        assert client.get_provider("llama3") == LLMProvider.OLLAMA
        assert client.get_provider("mistral") == LLMProvider.OLLAMA

    def test_explicit_provider_override(self, monkeypatch):
        monkeypatch.setattr("src.llm.provider.settings.llm_provider", "openai")
        client = MultiProviderLLMClient()
        assert client._explicit_provider == LLMProvider.OPENAI

    @pytest.mark.asyncio
    async def test_llm_client_init_without_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "")
        client = MultiProviderLLMClient()
        await client.initialize()
        assert client.is_initialized
