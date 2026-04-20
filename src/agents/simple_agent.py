"""
SimpleAgent - Unified agent that handles everything in one call
Replaces: ContextAgent + PolicyAgent + KnowledgeAgent + DraftAgent + QAAgent

Steve Jobs philosophy: "Simplicity is the ultimate sophistication"
"""

from src.core import InputPayload
from src.memory import MemoryContext
from src.llm import MultiProviderLLMClient, LLMResponse
from src.db import async_session
import structlog
from typing import Optional

logger = structlog.get_logger()


class SimpleAgent:
    """
    One agent to rule them all.
    - Check learned patterns first (>90% match = use stored answer)
    - Fallback to LLM generation
    - Direct answer, no validation loop
    """

    async def answer(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        llm: Optional[MultiProviderLLMClient] = None,
    ) -> tuple[str, float]:
        """
        Generate answer in ONE step.
        Priority: Learned patterns > LLM generation > Fallback
        
        Returns:
            tuple: (answer, confidence)
        """
        pattern_match = await self._check_patterns(payload)
        if pattern_match:
            answer, similarity = pattern_match
            logger.info("pattern_matched", similarity=similarity, question=payload.message.text[:50])
            return answer, min(1.0, similarity + 0.05)

        if not llm:
            return self._fallback_answer(payload, memory), 0.5

        context = self._build_context(payload, memory)
        answer = await self._generate(context, payload, llm)
        return answer, 0.85

    async def _check_patterns(
        self,
        payload: InputPayload,
    ) -> Optional[tuple[str, float]]:
        """
        Check for matching learned patterns.
        Returns (answer, similarity) if match found.
        """
        try:
            from src.services.pattern_learning_service import PatternLearningService

            async with async_session() as session:
                pattern_service = PatternLearningService(session)
                result = await pattern_service.find_similar_pattern(
                    question=payload.message.text,
                    user_id=payload.user.id,
                    team_id=payload.user.team,
                )

                if result:
                    pattern, similarity = result
                    await pattern_service.increment_usage(pattern.id)
                    return pattern.answer_text, similarity

        except Exception as e:
            logger.warning("pattern_check_failed", error=str(e))

        return None

    def _build_context(self, payload: InputPayload, memory: MemoryContext) -> str:
        """Build context string - replaces ContextAgent"""
        parts = []

        parts.append(f"USER: {payload.user.display_name}")
        if payload.user.role:
            parts.append(f"ROLE: {payload.user.role}")
        if payload.user.vip_flag:
            parts.append("VIP: true")

        if memory.conversation_summary:
            parts.append(f"CONTEXT: {memory.conversation_summary}")

        if memory.conversation_state:
            topic = memory.conversation_state.get("active_topic_title")
            mode = memory.conversation_state.get("conversation_mode")
            if topic:
                parts.append(f"TOPIC: {topic}")
            if mode:
                parts.append(f"MODE: {mode}")
            if memory.conversation_state.get("open_loops"):
                parts.append(f"OPEN_LOOPS: {memory.conversation_state.get('open_loops', [])[:3]}")

        if memory.recent_messages:
            recent = memory.recent_messages[-3:]
            parts.append(f"RECENT: {' | '.join(recent)}")

        if payload.case and memory.case_memory:
            parts.append(f"CASE: {memory.case_memory.get('summary', 'Open case')}")

        return "\n".join(parts)

    async def _generate(
        self,
        context: str,
        payload: InputPayload,
        llm: MultiProviderLLMClient,
    ) -> str:
        """Generate answer - replaces PolicyAgent + KnowledgeAgent + DraftAgent"""
        system_prompt = """Bạn là trợ lý IT Support thân thiện.
Trả lời ngắn gọn, hữu ích, bằng tiếng Việt.
Nếu không biết, nói thẳng "Tôi không biết"."""

        user_prompt = f"""{context}

QUESTION: {payload.message.text}

Trả lời câu hỏi trên."""

        response: LLMResponse = await llm.complete(system_prompt, user_prompt)
        return response.content

    def _fallback_answer(self, payload: InputPayload, memory: MemoryContext) -> str:
        """Fallback when no LLM available"""
        text_lower = payload.message.text.lower()

        greetings = ["xin chào", "hello", "hi", "chào", "hey"]
        if any(g in text_lower for g in greetings):
            return f"Xin chào {payload.user.display_name}! Tôi có thể giúp gì cho bạn?"

        faq_patterns = {
            ("mật khẩu", "password"): "Để đặt lại mật khẩu, liên hệ IT: it-support@company.com",
            ("wifi",): "WiFi: SSID 'Company-Office', liên hệ IT nếu không kết nối được.",
            ("máy in", "in ấn"): "Máy in tầng 2. Liên hệ IT nếu gặp sự cố.",
        }

        for keywords, answer in faq_patterns.items():
            if any(kw in text_lower for kw in keywords):
                return answer

        return "Tôi đang xử lý yêu cầu của bạn. Bạn cần hỗ trợ gì thêm?"
