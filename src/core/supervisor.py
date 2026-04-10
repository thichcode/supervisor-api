from src.core import (
    InputPayload,
    OutputPayload,
    IntentClassification,
    RiskEvaluation,
    IntentType,
    RiskLevel,
)
from src.memory import MemoryContext as MemoryContextModel
from src.agents import ContextAgent, PolicyAgent, KnowledgeAgent, DraftAgent, QAAgent
from src.db import AuditLog, async_session
from src.llm import MultiProviderLLMClient, LLMResponse
from typing import Optional
import time


class DecisionEngine:
    def should_use_subagents(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
    ) -> bool:
        if intent.intent in [IntentType.POLICY, IntentType.SUPPORT_CASE, IntentType.ANALYSIS]:
            return True

        if risk.risk_level in [RiskLevel.MEDIUM, RiskLevel.HIGH]:
            return True

        words = payload.message.text.split()
        if len(words) > 50:
            return True

        if intent.confidence < 0.7:
            return True

        return False

    def needs_human_review(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
        confidence: float,
    ) -> bool:
        if intent.intent == IntentType.EXECUTIVE_REQUEST:
            return True

        if risk.risk_level == RiskLevel.HIGH:
            return True

        if confidence < 0.7:
            return True

        text_lower = payload.message.text.lower()
        commitment_keywords = ["cam kết", "đảm bảo", "chắc chắn", "sẽ làm", "hứa", "commit"]
        if any(kw in text_lower for kw in commitment_keywords):
            return True

        return False


class Supervisor:
    def __init__(self):
        self.decision_engine = DecisionEngine()
        self.context_agent = ContextAgent()
        self.policy_agent = PolicyAgent()
        self.knowledge_agent = KnowledgeAgent()
        self.draft_agent = DraftAgent()
        self.qa_agent = QAAgent()
        self._llm: Optional[MultiProviderLLMClient] = None

    def set_llm(self, llm: MultiProviderLLMClient):
        self._llm = llm

    async def process(self, payload: InputPayload, memory: MemoryContextModel) -> OutputPayload:
        start_time = time.time()
        agents_used = []
        decision = "direct"
        final_confidence = 0.85

        intent = self._classify_intent(payload, memory)
        risk = self._evaluate_risk(payload, memory)

        if self.decision_engine.should_use_subagents(intent, risk, payload):
            decision = "subagents"
            agents_used = ["context", "policy", "knowledge", "draft", "qa"]

            context = self.context_agent.build(payload, memory)
            policy = await self.policy_agent.extract(payload, memory, self._llm)
            knowledge = await self.knowledge_agent.retrieve(payload, memory, self._llm)

            draft = await self.draft_agent.generate(payload, context, policy, knowledge, self._llm)

            validation = await self.qa_agent.validate(draft, payload, context, self._llm)

            if validation["needs_review"]:
                if self.decision_engine.needs_human_review(intent, risk, payload, validation["confidence"]):
                    return self._create_output(
                        payload=payload,
                        answer=validation["draft"],
                        confidence=validation["confidence"],
                        risk=risk,
                        intent=intent,
                        agents_used=agents_used,
                        status="needs_review",
                        processing_time=start_time,
                    )

            answer = self.qa_agent.refine(validation, payload)
            final_confidence = validation["confidence"]
        else:
            agents_used = ["draft"]
            answer, final_confidence = await self._generate_direct_answer(payload, memory)

        processing_time_ms = int((time.time() - start_time) * 1000)

        await self._log_audit(
            request_id=payload.request_id,
            decision=decision,
            risk_level=risk.risk_level.value,
            agents_used=agents_used,
            input_summary=payload.message.text[:200],
            output_summary=answer[:200],
            processing_time_ms=processing_time_ms,
        )

        return self._create_output(
            payload=payload,
            answer=answer,
            confidence=final_confidence,
            risk=risk,
            intent=intent,
            agents_used=agents_used,
            status="completed",
            processing_time=start_time,
        )

    def _classify_intent(self, payload: InputPayload, memory: MemoryContextModel) -> IntentClassification:
        from src.core.intent_classifier import IntentClassifier
        classifier = IntentClassifier()
        return classifier.classify(payload, memory)

    def _evaluate_risk(self, payload: InputPayload, memory: MemoryContextModel) -> RiskEvaluation:
        from src.core.risk_evaluator import RiskEvaluator
        evaluator = RiskEvaluator()
        return evaluator.evaluate(payload, memory)

    async def _generate_direct_answer(self, payload: InputPayload, memory: MemoryContextModel) -> tuple[str, float]:
        user_name = payload.user.display_name
        message = payload.message.text

        if self._llm:
            response: LLMResponse = await self._llm.complete(
                system_prompt="Bạn là một trợ lý AI hữu ích. Trả lời ngắn gọn, chính xác bằng tiếng Việt.",
                user_message=f"Người dùng {user_name} hỏi: {message}",
                context=memory.to_dict(),
            )
            return response.content, response.confidence

        return (
            f"Xin chào {user_name}, về câu hỏi của bạn \"{message[:100]}...\", tôi có thể giúp bạn. Bạn cần thêm thông tin gì không?",
            0.6,
        )

    def _create_output(
        self,
        payload: InputPayload,
        answer: str,
        confidence: float,
        risk: RiskEvaluation,
        intent: IntentClassification,
        agents_used: list[str],
        status: str,
        processing_time,
    ) -> OutputPayload:
        processing_time_ms = int((time.time() - processing_time) * 1000)

        return OutputPayload(
            request_id=payload.request_id,
            status=status,
            answer=answer,
            confidence=confidence,
            risk_level=risk.risk_level.value,
            metadata={
                "intent": intent.intent.value,
                "agents_used": agents_used,
                "processing_time_ms": processing_time_ms,
                "risk_flags": risk.flags,
            },
        )

    async def _log_audit(
        self,
        request_id: str,
        decision: str,
        risk_level: str,
        agents_used: list[str],
        input_summary: str,
        output_summary: str,
        processing_time_ms: int,
    ):
        async with async_session() as session:
            from src.db.models import AuditLog as AuditLogModel
            audit = AuditLogModel(
                request_id=request_id,
                decision=decision,
                risk_level=risk_level,
                agents_used=agents_used,
                input_summary=input_summary,
                output_summary=output_summary,
                processing_time_ms=processing_time_ms,
            )
            session.add(audit)
            await session.commit()
