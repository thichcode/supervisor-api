import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
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
from src.core.metrics import (
    KB_SEARCHES,
    KB_RERANKS,
    KB_TEMPLATES,
    REASONING_LOOP_FALLBACKS,
    REASONING_LOOP_OUTCOMES,
    REASONING_LOOP_ROLLOUT,
)
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
            chat_type="private",
            chat_scope="dm",
            group_chat=False,
            platform="telegram",
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

    def test_classify_unknown_defaults_below_half(self, sample_payload, sample_context):
        classifier = IntentClassifier()
        sample_payload.message.text = "xyzqv random gibberish with no business meaning"
        result = classifier.classify(sample_payload, sample_context)
        assert result.confidence < 0.5

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
        assert "chat_context" in result
        assert result["chat_context"]["chat_type"] == "private"
        assert result["chat_context"]["chat_scope"] == "dm"
        assert result["chat_context"]["group_chat"] is False
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

    def test_response_route_confidence_rules(self):
        from src.core.supervisor import DecisionEngine

        engine = DecisionEngine()

        assert engine.response_route(confidence=0.49, kb_hit=False) == "skip"
        assert engine.response_route(confidence=0.75, kb_hit=False) == "approve"
        assert engine.response_route(confidence=0.91, kb_hit=True) == "send"
        assert engine.response_route(confidence=0.91, kb_hit=False) == "approve"


class TestSupervisor:
    def test_reasoning_loop_rollout_gating_by_percent(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_team_percent", 0)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_user_percent", 100)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_salt", "test-salt")

        supervisor = Supervisor()
        enabled, metadata = supervisor._should_run_reasoning_loop(sample_payload, settings)

        assert enabled is True
        assert metadata["enabled"] is True
        assert metadata["user_enabled"] is True
        assert metadata["team_enabled"] is False

    def test_reasoning_loop_rollout_gating_disabled(self, sample_payload, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_team_percent", 0)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_user_percent", 0)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_salt", "test-salt")

        supervisor = Supervisor()
        enabled, metadata = supervisor._should_run_reasoning_loop(sample_payload, settings)

        assert enabled is False
        assert metadata["enabled"] is False

    @pytest.mark.asyncio
    async def test_generate_direct_answer_uses_persona_hint(self, sample_payload):
        from src.core.supervisor import Supervisor

        captured = {}

        class FakeLLM:
            async def complete(self, system_prompt, user_message, context=None):
                captured["system_prompt"] = system_prompt
                captured["user_message"] = user_message
                captured["context"] = context
                from src.llm.provider import LLMResponse
                return LLMResponse(
                    content="Dynamic answer",
                    confidence=0.91,
                    usage={},
                    model="fake",
                    provider="fake",
                    finish_reason="stop"
                )

        supervisor = Supervisor()
        supervisor.set_llm(FakeLLM())
        memory = MemoryContext(
            conversation_summary="summary",
            recent_messages=["Hello"],
            user_profile={
                "role": "employee",
                "preferences": {
                    "response_persona_hint": "style=concise, tone=formal",
                    "style_profile": {
                        "response_persona_hint": "style=concise, tone=formal",
                    },
                },
            },
        )

        answer, confidence = await supervisor._generate_direct_answer(sample_payload, memory)

        assert answer == "Dynamic answer"
        assert confidence == 0.91
        assert "style=concise, tone=formal" in captured["system_prompt"]
    @pytest.mark.asyncio
    async def test_process_caps_confidence_without_kb(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor

        class FakeLLM:
            async def complete(self, system_prompt, user_message, context=None):
                from src.llm.provider import LLMResponse
                return LLMResponse(
                    content="Direct answer",
                    confidence=0.91,
                    usage={},
                    model="fake",
                    provider="fake",
                    finish_reason="stop"
                )

        async def fake_fetch_urls(self, payload):
            return ""

        async def fake_log_audit(*args, **kwargs):
            return None

        supervisor = Supervisor()
        supervisor.set_llm(FakeLLM())
        supervisor._fetch_urls = fake_fetch_urls.__get__(supervisor, Supervisor)
        supervisor._log_audit = fake_log_audit

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "skipped"
        assert result.confidence < 0.5
        assert result.confidence == 0.49

    @pytest.mark.asyncio
    async def test_process_uses_pattern_match_on_main_path(self, sample_payload, sample_context):
        from src.core.supervisor import Supervisor

        async def fake_fetch_urls(self, payload):
            return ""

        async def fake_log_audit(*args, **kwargs):
            return None

        supervisor = Supervisor()
        supervisor.set_llm(None)
        supervisor._fetch_urls = fake_fetch_urls.__get__(supervisor, Supervisor)
        supervisor._log_audit = fake_log_audit
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "facts": [],
            "patterns": [],
            "confidence": 0.4,
            "system_query_requested": False,
            "query_type": None,
            "knowledge_results": [],
            "knowledge_clarification_needed": False,
        })
        supervisor._check_patterns = AsyncMock(return_value=("Pattern answer", 0.92))
        supervisor.draft_agent.generate = AsyncMock(side_effect=AssertionError("draft should not run when pattern matches"))
        supervisor._enhanced_validate = AsyncMock(side_effect=AssertionError("qa should not run when pattern matches"))

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "completed"
        assert result.answer == "Pattern answer"
        assert result.confidence == 0.9
        assert result.metadata["pattern_hit"] is True
        assert result.metadata["kb_hit"] is True
        assert "pattern_match" in result.metadata["agents_used"]

    @pytest.mark.asyncio
    async def test_process_routes_cached_response_through_thresholds(self, sample_payload, sample_context):
        from src.core.supervisor import Supervisor

        async def fake_log_audit(*args, **kwargs):
            return None

        supervisor = Supervisor()
        supervisor._log_audit = fake_log_audit
        supervisor._check_cache = lambda payload: {"response": "Cached answer", "confidence": 0.91}

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "skipped"
        assert result.answer == ""
        assert result.confidence == 0.49
        assert result.metadata["cache_hit"] is True
        assert result.metadata["kb_hit"] is False

    @pytest.mark.asyncio
    async def test_process_promotes_kb_answer_to_point_nine_when_qa_is_stable(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor

        sample_payload.message.text = "VPN is not working"

        async def fake_fetch_urls(self, payload):
            return ""

        async def fake_log_audit(*args, **kwargs):
            return None

        supervisor = Supervisor()
        supervisor.set_llm(None)
        supervisor._fetch_urls = fake_fetch_urls.__get__(supervisor, Supervisor)
        supervisor._log_audit = fake_log_audit
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "facts": [],
            "patterns": [],
            "confidence": 0.87,
            "system_query_requested": False,
            "query_type": None,
            "knowledge_results": [
                {
                    "type": "faq",
                    "id": "faq-vpn-1",
                    "title": "VPN access issue",
                    "content": "Use the VPN portal to reset your VPN profile",
                    "category": "access",
                    "similarity": 0.82,
                    "metadata": {},
                }
            ],
        })
        supervisor.draft_agent.generate = AsyncMock(return_value="KB-backed draft")
        supervisor._enhanced_validate = AsyncMock(return_value={
            "draft": "KB-backed draft",
            "confidence": 0.87,
            "issues": [],
            "needs_review": False,
        })
        supervisor.qa_agent.refine = lambda validation, payload, context: validation["draft"]

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "completed"
        assert result.confidence == 0.9
        assert result.metadata["kb_hit"] is True


    @pytest.mark.asyncio
    async def test_process_returns_kb_clarification_when_context_missing(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor

        sample_payload.message.text = "VPN is not working"

        supervisor = Supervisor()
        supervisor.set_llm(None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "facts": [],
            "patterns": [],
            "confidence": 0.85,
            "system_query_requested": False,
            "query_type": None,
            "knowledge_results": [
                {
                    "type": "faq",
                    "id": "faq-vpn-1",
                    "title": "VPN access issue",
                    "content": "Use the VPN portal to reset your VPN profile",
                    "category": "access",
                    "similarity": 0.82,
                    "metadata": {},
                }
            ],
            "knowledge_clarification_needed": True,
            "knowledge_clarification_question": "Mình tìm thấy KB phù hợp về 'VPN access issue'. Để support đúng theo KB, bạn cho mình thêm: thiết bị đang dùng; hệ điều hành/phiên bản máy; mã lỗi.",
            "knowledge_missing_fields": ["device", "os", "error_code"],
            "knowledge_required_fields": ["device", "os", "error_code"],
        })

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "needs_clarification"
        assert "thiết bị" in result.answer.lower() or "mã lỗi" in result.answer.lower()
        assert result.metadata["kb_clarification_needed"] is True
        assert result.metadata["kb_missing_fields"] == ["device", "os", "error_code"]

    @pytest.mark.asyncio
    async def test_reasoning_loop_faq_simple_case(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: False
        supervisor._check_patterns = AsyncMock(return_value=("FAQ pattern answer", 0.9))

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "completed"
        assert result.answer == "FAQ pattern answer"
        assert result.metadata["reasoning_loop"] is True
        assert result.metadata["kb_hit"] is True
        assert "pattern_match" in result.metadata["agents_used"]

    @pytest.mark.asyncio
    async def test_reasoning_loop_tool_needed_case(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "knowledge_results": [],
            "knowledge_clarification_needed": False,
            "system_query_requested": True,
            "query_type": "n8n",
        })
        supervisor._handle_system_query = AsyncMock(return_value={"result": "tool-output", "confidence": 0.93})
        supervisor._format_system_query_response = lambda query_result: f"System query result: {query_result['result']}"

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "completed"
        assert "tool-output" in result.answer
        assert result.metadata["reasoning_loop"] is True
        assert "system_query" in result.metadata["agents_used"]
        assert result.metadata["reasoning_trace"]["max_iterations"] >= 1
        assert result.metadata["reasoning_trace"]["iterations_used"] >= 1
        assert any(step["stage"] == "plan" for step in result.metadata["reasoning_trace"]["steps"])
        assert any(step["stage"] == "observe" for step in result.metadata["reasoning_trace"]["steps"])

    @pytest.mark.asyncio
    async def test_reasoning_loop_clarification_case(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)

        sample_payload.message.text = "VPN is not working"
        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "knowledge_results": [
                {
                    "type": "faq",
                    "id": "faq-vpn-1",
                    "title": "VPN access issue",
                    "content": "Use the VPN portal to reset your VPN profile",
                    "category": "access",
                    "similarity": 0.82,
                    "metadata": {},
                }
            ],
            "knowledge_clarification_needed": True,
            "knowledge_clarification_question": "Mình tìm thấy KB phù hợp về 'VPN access issue'. Để support đúng theo KB, bạn cho mình thêm: thiết bị đang dùng; hệ điều hành/phiên bản máy; mã lỗi.",
            "knowledge_missing_fields": ["device", "os", "error_code"],
            "knowledge_required_fields": ["device", "os", "error_code"],
            "system_query_requested": False,
            "query_type": None,
            "confidence": 0.85,
        })

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "needs_clarification"
        assert result.metadata["reasoning_loop"] is True
        assert result.metadata["kb_clarification_needed"] is True
        assert result.metadata["kb_missing_fields"] == ["device", "os", "error_code"]
        assert "reasoning_trace" in result.metadata

    @pytest.mark.asyncio
    async def test_reasoning_loop_tool_retry_then_success(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_max_iterations", 5)
        monkeypatch.setattr(settings, "reasoning_loop_tool_retry", 2)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "knowledge_results": [],
            "knowledge_clarification_needed": False,
            "system_query_requested": True,
            "query_type": "n8n",
        })

        call_counter = {"count": 0}

        async def flaky_system_query(payload, memory, query_type):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise RuntimeError("temporary failure")
            return {"result": "tool-after-retry", "confidence": 0.91}

        supervisor._handle_system_query = flaky_system_query
        supervisor._format_system_query_response = lambda query_result: f"System query result: {query_result['result']}"

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "completed"
        assert "tool-after-retry" in result.answer
        assert call_counter["count"] == 2
        steps = result.metadata["reasoning_trace"]["steps"]
        assert any(step["event"] == "tool_attempt_failed" for step in steps)
        assert any(step["event"] == "tool_attempt_success" for step in steps)

    @pytest.mark.asyncio
    async def test_reasoning_loop_budget_exhausted_returns_needs_review(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_max_iterations", 1)
        monkeypatch.setattr(settings, "reasoning_loop_tool_retry", 2)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "knowledge_results": [],
            "knowledge_clarification_needed": False,
            "system_query_requested": True,
            "query_type": "n8n",
        })
        supervisor._handle_system_query = AsyncMock(side_effect=RuntimeError("always fail"))

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "needs_review"
        assert result.metadata["reasoning_trace"]["budget_exhausted"] is True
        assert any(step["event"] == "budget_exhausted" for step in result.metadata["reasoning_trace"]["steps"])

    @pytest.mark.asyncio
    async def test_reasoning_loop_tool_planner_requests_missing_query_type_clarification(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: True
        supervisor.context_agent.build = lambda payload, memory: {}
        supervisor.policy_agent.extract = AsyncMock(return_value={"guide_requested": False, "guide_id": None})
        supervisor.knowledge_agent.retrieve = AsyncMock(return_value={
            "knowledge_results": [],
            "knowledge_clarification_needed": False,
            "system_query_requested": True,
            "query_type": None,
        })

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "needs_clarification"
        assert "loại truy vấn" in result.answer.lower() or "truy vấn" in result.answer.lower()
        assert "tool_planner" in result.metadata["agents_used"]
        assert any(step["event"] == "tool_plan" for step in result.metadata["reasoning_trace"]["steps"])

    @pytest.mark.asyncio
    async def test_reasoning_loop_low_confidence_interrupts_for_clarification(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: False
        supervisor._check_patterns = AsyncMock(return_value=None)
        supervisor._generate_direct_answer = AsyncMock(return_value=("Câu trả lời nháp", 0.2))

        result = await supervisor.process(sample_payload, sample_context)

        assert result.status == "needs_clarification"
        assert result.metadata.get("interrupt_reason") == "low_confidence"
        assert "clarification" in result.metadata["agents_used"]
        assert any(step["event"] == "interrupt_clarification" for step in result.metadata["reasoning_trace"]["steps"])

    @pytest.mark.asyncio
    async def test_reasoning_loop_rollout_disabled_records_fallback_metric(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_team_percent", 0)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_user_percent", 0)

        before = REASONING_LOOP_FALLBACKS.labels(reason="rollout_disabled")._value.get()

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.decision_engine.should_use_subagents = lambda *args, **kwargs: False
        supervisor._check_patterns = AsyncMock(return_value=None)
        supervisor._generate_direct_answer = AsyncMock(return_value=("fallback answer", 0.2))

        result = await supervisor.process(sample_payload, sample_context)

        after = REASONING_LOOP_FALLBACKS.labels(reason="rollout_disabled")._value.get()
        assert after == before + 1
        assert result.metadata["reasoning_loop_rollout"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_reasoning_loop_enabled_records_outcome_and_rollout_metrics(self, sample_payload, sample_context, monkeypatch):
        from src.core.supervisor import Supervisor, get_settings
        from src.core import IntentClassification, RiskEvaluation

        settings = get_settings()
        monkeypatch.setattr(settings, "enable_reasoning_loop", True)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_team_percent", 0)
        monkeypatch.setattr(settings, "reasoning_loop_rollout_user_percent", 100)

        before_outcome = REASONING_LOOP_OUTCOMES.labels(status="completed")._value.get()
        before_rollout = REASONING_LOOP_ROLLOUT.labels(scope="user", outcome="enabled")._value.get()

        supervisor = Supervisor()
        supervisor._fetch_urls = AsyncMock(return_value="")
        supervisor._log_audit = AsyncMock(return_value=None)
        supervisor.reasoning_orchestrator.run = AsyncMock(
            return_value=supervisor._create_output(
                payload=sample_payload,
                answer="reasoning answer",
                confidence=0.9,
                intent=IntentClassification(intent=IntentType.FAQ, confidence=0.9),
                risk=RiskEvaluation(risk_level=RiskLevel.LOW, flags=[]),
                agents_used=["reasoning"],
                status="completed",
                processing_time=1.0,
                extra_metadata={"reasoning_loop": True},
            )
        )

        result = await supervisor.process(sample_payload, sample_context)

        after_outcome = REASONING_LOOP_OUTCOMES.labels(status="completed")._value.get()
        after_rollout = REASONING_LOOP_ROLLOUT.labels(scope="user", outcome="enabled")._value.get()

        assert after_outcome == before_outcome + 1
        assert after_rollout == before_rollout + 1
        assert result.metadata["reasoning_loop_rollout"]["enabled"] is True

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

    @pytest.mark.asyncio
    async def test_document_search_uses_keyword_arguments(self):
        service = KnowledgeRetrievalService(session=object())
        captured = []

        async def fake_search_documents(*args, **kwargs):
            captured.append(kwargs.copy())
            return []

        service.repo.search_documents = fake_search_documents  # type: ignore[method-assign]

        result = await service.search("vpn manual", search_type="document", category="docs", tags=["vpn"], limit=3)

        assert result.total == 0
        assert captured
        assert captured[0] == {
            "query": "vpn manual",
            "category": "docs",
            "tags": ["vpn"],
            "limit": 3,
        }
        assert all("query" in entry for entry in captured)

    @pytest.mark.asyncio
    async def test_template_mapper_prioritizes_vpn_access_search_types(self):
        service = KnowledgeRetrievalService(session=object())
        captured_types = []

        async def fake_search_knowledge_base(kb_type, query, category, tags, limit):
            captured_types.append(kb_type)
            return []

        service._search_knowledge_base = fake_search_knowledge_base  # type: ignore[method-assign]

        result = await service.search("VPN access issue")

        assert result.total == 0
        assert captured_types[:2] == ["faq", "guide"]

    def test_kb_template_mapper_detects_vpn_access(self):
        from src.core.kb_templates import KBCategoryTemplateMapper

        match = KBCategoryTemplateMapper.detect("VPN không vào được, remote access bị lỗi")

        assert match is not None
        assert match.template_id == "vpn_access"
        assert match.label == "VPN / Access"
        assert "vpn" in {term.lower() for term in match.matched_terms}

    def test_kb_template_mapper_detects_common_business_templates(self):
        from src.core.kb_templates import KBCategoryTemplateMapper

        cases = {
            "outlook mail không gửi được": "outlook_mail",
            "backup restore dữ liệu": "backup_restore",
            "export excel csv": "excel_csv",
            "jira confluence access": "jira_confluence",
            "sharepoint onedrive sync lỗi": "sharepoint_onedrive",
        }

        for query, template_id in cases.items():
            match = KBCategoryTemplateMapper.detect(query)
            assert match is not None
            assert match.template_id == template_id
            assert match.label

    def test_kb_response_formatter_includes_template_hint(self):
        from src.core.kb_presentation import format_kb_response
        from src.knowledge.schemas import KnowledgeSearchResult, KnowledgeType

        response = format_kb_response(
            [
                KnowledgeSearchResult(
                    knowledge_type=KnowledgeType.FAQ,
                    id="faq-1",
                    title="Reset VPN",
                    content="1. Open the VPN portal\n2. Click Reset Access\n3. Reconnect and verify login",
                    category="access",
                    tags=[],
                    similarity=0.93,
                    metadata={},
                )
            ],
            query="VPN access issue",
            max_results=1,
        )

        assert response["template_label"] == "VPN / Access"
        assert "Mẫu KB: VPN / Access" in response["text"]
        assert "Gợi ý:" in response["text"]
        assert "Làm theo:" in response["text"]


    def test_infer_clarification_for_vague_kb_match(self):
        from src.knowledge.schemas import KnowledgeSearchResult, KnowledgeType

        service = KnowledgeRetrievalService(session=object())
        result = KnowledgeSearchResult(
            knowledge_type=KnowledgeType.FAQ,
            id="faq-vpn-1",
            title="VPN access issue",
            content="Use the VPN portal to reset your VPN profile",
            category="access",
            tags=[],
            similarity=0.82,
            metadata={},
        )

        clarification = service.infer_clarification("VPN not working", [result])

        assert clarification["needs_clarification"] is True
        assert "device" in clarification["missing_fields"]
        assert clarification["clarification_question"]

    def test_detect_message_mode_question_vs_problem(self):
        from src.core.conversation_continuity import ConversationContinuityEvaluator

        evaluator = ConversationContinuityEvaluator()
        assert evaluator.detect_message_mode("Bạn có thể hướng dẫn mình không?") == "question"
        assert evaluator.detect_message_mode("VPN không vào được, báo lỗi 720") == "problem"
        assert evaluator.detect_message_mode("Mình đã làm xong rồi") == "statement"


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
                "preferences": {
                    "response_persona_hint": "style=concise, tone=formal",
                    "style_profile": {
                        "response_persona_hint": "style=concise, tone=formal",
                        "style_signals": {"has_bullets": True},
                    },
                },
            },
        }

        result = await agent.generate(payload, context, {}, {}, llm)

        assert result == "ok"
        assert llm.calls
        system_prompt, user_prompt = llm.calls[0]
        assert "Trả lời rất ngắn gọn" in system_prompt
        assert "Ưu tiên định dạng gạch đầu dòng" in system_prompt
        assert "style=concise, tone=formal" in system_prompt
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


class TestKnowledgeMetrics:
    @pytest.mark.asyncio
    async def test_kb_search_records_hit_metric(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        before = KB_SEARCHES.labels(search_type="faq", outcome="hit")._value.get()
        session = MagicMock()
        service = KnowledgeRetrievalService(session)
        service.repo.search_faqs = AsyncMock(
            return_value=[
                SimpleNamespace(
                    question_id="faq-1",
                    question="password reset",
                    answer="password reset steps",
                    category="it",
                    tags=[],
                    keywords=[],
                    usage_count=0,
                )
            ]
        )
        service.repo.increment_faq_usage = AsyncMock(return_value=None)

        result = await service.search(query="password reset", search_type="faq", limit=5)

        after = KB_SEARCHES.labels(search_type="faq", outcome="hit")._value.get()
        assert result.total == 1
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_kb_search_records_fallback_metric(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        before = KB_SEARCHES.labels(search_type="faq", outcome="miss")._value.get()
        service = KnowledgeRetrievalService(MagicMock())
        service.repo.search_faqs = AsyncMock(
            return_value=[
                SimpleNamespace(
                    question_id="faq-1",
                    question="password reset",
                    answer="password reset steps",
                    category="it",
                    tags=[],
                    keywords=[],
                    usage_count=0,
                )
            ]
        )
        service.repo.increment_faq_usage = AsyncMock(return_value=None)

        result = await service.search(query="unrelated gibberish", search_type="faq", limit=5)

        after = KB_SEARCHES.labels(search_type="faq", outcome="miss")._value.get()
        assert result.total == 1
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_kb_template_detection_records_metric(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        before = KB_TEMPLATES.labels(template_id="outlook_mail", search_type="faq", outcome="detected")._value.get()
        service = KnowledgeRetrievalService(MagicMock())
        service.repo.search_faqs = AsyncMock(
            return_value=[
                SimpleNamespace(
                    question_id="faq-mail-1",
                    question="outlook mail không gửi được",
                    answer="1. kiểm tra inbox\n2. kiểm tra send/receive",
                    category="mail",
                    tags=[],
                    keywords=[],
                    usage_count=0,
                )
            ]
        )
        service.repo.increment_faq_usage = AsyncMock(return_value=None)

        result = await service.search(query="outlook mail không gửi được", search_type="faq", limit=5)

        after = KB_TEMPLATES.labels(template_id="outlook_mail", search_type="faq", outcome="detected")._value.get()
        assert result.template_id == "outlook_mail"
        assert result.template_label == "Outlook / Mail"
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_kb_rerank_records_success_metric(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        before = KB_RERANKS.labels(search_type="faq", status="success")._value.get()
        service = KnowledgeRetrievalService(MagicMock())
        service.repo.search_faqs = AsyncMock(
            return_value=[
                SimpleNamespace(
                    question_id="faq-1",
                    question="password reset",
                    answer="password reset steps",
                    category="it",
                    tags=[],
                    keywords=[],
                    usage_count=0,
                )
            ]
        )
        service.repo.increment_faq_usage = AsyncMock(return_value=None)

        class FakeLLM:
            async def complete(self, system_prompt, user_message):
                return type("Resp", (), {"content": '{"relevant_ids": ["faq-1"], "reason": "ok"}', "confidence": 0.9})()

        service.llm = FakeLLM()
        result = await service.search_with_llm_enhancement(
            query="password reset",
            search_type="faq",
            limit=5,
        )

        after = KB_RERANKS.labels(search_type="faq", status="success")._value.get()
        assert result.total == 1
        assert after == before + 1
