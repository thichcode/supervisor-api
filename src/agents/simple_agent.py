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

    def _looks_like_support_request(self, payload: InputPayload, memory: MemoryContext) -> bool:
        text = payload.message.text.lower()
        message_mode = (memory.conversation_state or {}).get("last_user_message_mode", "").lower()
        support_keywords = [
            "support", "case", "ticket", "issue", "problem", "bug", "error", "crash",
            "not working", "broken", "help", "hỗ trợ", "vấn đề", "sự cố", "lỗi", "hỏng",
            "không được", "bị lỗi", "treo", "đơ", "cần giúp", "giúp tôi", "sửa", "fix",
            "login", "đăng nhập", "credential", "auth", "authentication", "publickey",
        ]
        return message_mode == "problem" or any(keyword in text for keyword in support_keywords)

    def _build_support_clarification(self, text: str) -> str:
        text_lower = (text or "").lower()
        if any(keyword in text_lower for keyword in ["git", "github", "gitlab", "bitbucket", "ssh", "https", "credential", "publickey", "auth", "đăng nhập", "login"]):
            return (
                "Mình cần 3 thông tin để chẩn đoán nhanh: bạn đang dùng GitHub/GitLab/Bitbucket, "
                "đang login bằng HTTPS hay SSH, và nguyên lỗi hiển thị là gì?"
            )
        return (
            "Bạn cho mình biết hệ thống/dịch vụ nào đang lỗi, bạn đang kẹt ở bước nào, và có mã lỗi hoặc ảnh chụp màn hình không?"
        )

    def _looks_generic_support_reply(self, answer: str) -> bool:
        text = (answer or "").lower()
        generic_phrases = [
            "bạn cần tôi hỗ trợ gì",
            "vui lòng cho tôi biết yêu cầu của bạn",
            "bạn cần hỗ trợ gì",
            "mình có thể giúp gì",
            "tôi là trợ lý",
            "cho mình biết vấn đề",
            "bạn xác nhận",
            "nếu cần mình hỗ trợ",
        ]
        return any(phrase in text for phrase in generic_phrases)

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
            fallback_answer = self._fallback_answer(payload, memory)
            fallback_confidence = 0.6 if self._looks_like_support_request(payload, memory) else 0.4
            return fallback_answer, fallback_confidence

        context = self._build_context(payload, memory)
        answer = await self._generate(context, payload, llm, memory)
        return answer, 0.45

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
            chat_platform = memory.conversation_state.get("platform") or payload.source
            chat_type = memory.conversation_state.get("chat_type")
            chat_scope = memory.conversation_state.get("chat_scope")
            group_chat = memory.conversation_state.get("group_chat")
            if chat_platform:
                parts.append(f"CHANNEL: {chat_platform}")
            if chat_type:
                parts.append(f"CHAT_TYPE: {chat_type}")
            if chat_scope:
                parts.append(f"CHAT_SCOPE: {chat_scope}")
            if group_chat is not None:
                parts.append(f"GROUP_CHAT: {group_chat}")
            topic = memory.conversation_state.get("active_topic_title")
            mode = memory.conversation_state.get("conversation_mode")
            message_mode = memory.conversation_state.get("last_user_message_mode")
            if topic:
                parts.append(f"TOPIC: {topic}")
            if mode:
                parts.append(f"MODE: {mode}")
            if message_mode:
                parts.append(f"MESSAGE_MODE: {message_mode}")
                if message_mode == "problem":
                    parts.append("INTENT: user is reporting a problem; ask for missing details only if needed.")
                elif message_mode == "question":
                    parts.append("INTENT: user is asking a question; answer directly if possible.")
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
        memory: MemoryContext,
    ) -> str:
        """Generate answer - replaces PolicyAgent + KnowledgeAgent + DraftAgent"""
        support_request = self._looks_like_support_request(payload, memory)
        system_prompt = """Bạn là trợ lý IT Support thân thiện.
Trả lời ngắn gọn, hữu ích, bằng tiếng Việt.
Nếu người dùng đang báo lỗi/sự cố, không được trả lời chung chung kiểu 'bạn cần tôi hỗ trợ gì'.
Nếu thiếu thông tin, chỉ hỏi đúng 1 câu ngắn về dữ kiện còn thiếu quan trọng nhất.
Với lỗi login Git, ưu tiên hỏi: đang dùng GitHub/GitLab/Bitbucket, login bằng HTTPS hay SSH, và lỗi cụ thể là gì.
Nếu không biết, nói thẳng "Tôi không biết"."""

        if support_request:
            system_prompt += "\nNgười dùng đang báo một vấn đề kỹ thuật; ưu tiên triage và hỏi đúng thông tin thiếu, không mở đầu xã giao dài dòng."

        user_prompt = f"""{context}

QUESTION: {payload.message.text}

Trả lời câu hỏi trên."""

        response: LLMResponse = await llm.complete(system_prompt, user_prompt)
        answer = response.content
        if support_request and self._looks_generic_support_reply(answer):
            return self._build_support_clarification(payload.message.text)
        return answer

    def _fallback_answer(self, payload: InputPayload, memory: MemoryContext) -> str:
        """Fallback when no LLM available"""
        text_lower = payload.message.text.lower()

        if self._looks_like_support_request(payload, memory):
            return self._build_support_clarification(payload.message.text)

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
