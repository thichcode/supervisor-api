from src.core import InputPayload
from src.llm import MultiProviderLLMClient, LLMResponse
from typing import Optional


class DraftAgent:
    def _style_instructions(self, context: dict) -> str:
        user_info = context.get("user_info", {}) or {}
        style = (user_info.get("communication_style") or "").lower()
        preferences = user_info.get("preferences", {}) or {}
        style_profile = preferences.get("style_profile", {}) if isinstance(preferences, dict) else {}
        response_persona_hint = (
            preferences.get("response_persona_hint")
            or style_profile.get("response_persona_hint")
            or user_info.get("response_persona_hint")
            if isinstance(preferences, dict)
            else user_info.get("response_persona_hint")
        )
        parts = []

        if style == "structured":
            parts.append("Trả lời theo cấu trúc rõ ràng, ưu tiên gạch đầu dòng và các bước.")
        elif style == "detailed":
            parts.append("Trả lời chi tiết, giải thích ngắn gọn các bước quan trọng.")
        elif style == "formal":
            parts.append("Giữ giọng trang trọng, lịch sự, ngắn gọn vừa đủ.")
        elif style == "casual":
            parts.append("Giữ giọng tự nhiên, thân thiện.")
        elif style == "concise":
            parts.append("Trả lời rất ngắn gọn, đi thẳng vào ý chính.")
        else:
            parts.append("Trả lời tự nhiên, cân bằng giữa ngắn gọn và đầy đủ.")

        signals = style_profile.get("style_signals", {}) if isinstance(style_profile, dict) else {}
        if signals.get("has_numbered_steps"):
            parts.append("Nếu phù hợp, trình bày theo danh sách bước.")
        if signals.get("has_bullets"):
            parts.append("Ưu tiên định dạng gạch đầu dòng.")
        if response_persona_hint:
            parts.append(f"Persona học được: {response_persona_hint}")

        return " ".join(parts)

    async def generate(
        self,
        payload: InputPayload,
        context: dict,
        policy: dict,
        knowledge: dict,
        llm: Optional[MultiProviderLLMClient] = None,
    ) -> str:
        user_name = payload.user.display_name
        style_instructions = self._style_instructions(context)

        if llm:
            system_prompt = f"""Bạn là trợ lý AI cho hệ thống IT Support.
Dựa trên ngữ cảnh, chính sách và kiến thức được cung cấp, tạo câu trả lời phù hợp.
Trả lời bằng tiếng Việt, ngắn gọn và chính xác.
{style_instructions}"""

            user_prompt = f"""Câu hỏi của người dùng: {payload.message.text}

Ngữ cảnh hội thoại: {context.get('conversation_summary', 'Không có')}
Mạch hội thoại hiện tại: {context.get('conversation_state', {})}
Tin nhắn gần đây: {context.get('conversation_history', [])}
Vai trò người dùng: {context.get('user_info', {}).get('role', 'employee')}
Phong cách người dùng: {context.get('user_info', {}).get('communication_style', 'balanced')}
Mục đích người dùng: {context.get('conversation_state', {}).get('last_user_message_mode', 'unknown')}

{context.get('url_context', '')}

Chính sách liên quan: {policy}
Kiến thức: {knowledge}"""

            response: LLMResponse = await llm.complete(
                system_prompt=system_prompt,
                user_message=user_prompt,
            )
            return response.content

        parts = []

        if policy.get("guidelines_found"):
            parts.append("Based on our guidelines:\n")
            for policy_item in policy.get("relevant_policies", []):
                parts.append(f"- {policy_item}")
            if policy.get("sop_steps"):
                parts.append("\nRecommended steps:")
                for step in policy["sop_steps"]:
                    parts.append(f"  {step}")
            parts.append("")

        if knowledge.get("facts"):
            parts.append("Here is the information you requested:")
            for fact in knowledge.get("facts", []):
                parts.append(f"- {fact}")
            parts.append("")

        if knowledge.get("patterns"):
            parts.append("Based on similar cases:")
            for pattern in knowledge.get("patterns", []):
                parts.append(f"- {pattern}")
            parts.append("")

        if context.get("case_info"):
            case = context["case_info"]
            if case.get("open_items"):
                parts.append("Current open items:")
                for item in case.get("open_items", []):
                    parts.append(f"- {item}")
                parts.append("")

        if not parts:
            parts.append(f"Hi {user_name}, I've reviewed your request and I'm here to help.")

        return "\n".join(parts)


class QAAgent:
    def __init__(self):
        self.confidence_threshold = 0.7

    async def validate(
        self,
        draft: str,
        payload: InputPayload,
        context: dict,
        llm: Optional[MultiProviderLLMClient] = None,
    ) -> dict:
        issues = []
        confidence = 0.85

        if len(draft) < 20:
            issues.append("Câu trả lời quá ngắn")
            confidence -= 0.2

        if not draft.strip():
            issues.append("Câu trả lời trống")
            confidence -= 0.5

        text_lower = payload.message.text.lower()

        support_keywords = ["giúp", "help", "hỗ trợ", "support", "case", "vấn đề"]
        if any(kw in text_lower for kw in support_keywords):
            if "case" not in draft.lower() and "support" not in draft.lower() and "help" not in draft.lower():
                issues.append("Yêu cầu hỗ trợ chưa được xử lý rõ ràng")
                confidence -= 0.1

        if context.get("case_info") and context["case_info"].get("status") == "urgent":
            if "urgent" not in draft.lower() and "asap" not in draft.lower():
                issues.append("Case khẩn cấp chưa được đánh dấu phù hợp")
                confidence -= 0.1

        if "?" in payload.message.text and "?" not in draft:
            issues.append("Câu hỏi chưa được trả lời trực tiếp")
            confidence -= 0.15

        if llm and issues:
            validation_prompt = f"""Kiểm tra câu trả lời nháp cho câu hỏi của người dùng:
Câu hỏi: {payload.message.text}
Nháp: {draft}

Kiểm tra:
1. Có trả lời được câu hỏi không?
2. Có chi tiết phù hợp không?
3. Có thông tin sai không?

Trả về JSON:
{{"additional_issues": [], "confidence_adjustment": 0.0}}"""

            response: LLMResponse = await llm.complete(
                "Bạn là agent kiểm tra chất lượng QA. Trả lời bằng tiếng Việt.",
                validation_prompt
            )

            import json
            import re
            match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    issues.extend(parsed.get("additional_issues", []))
                    confidence += parsed.get("confidence_adjustment", 0)
                except json.JSONDecodeError:
                    pass

        if confidence < self.confidence_threshold:
            issues.append("Độ tin cậy dưới ngưỡng")

        needs_review = len(issues) > 1 or confidence < self.confidence_threshold

        return {
            "draft": draft,
            "confidence": max(0.0, min(1.0, confidence)),
            "issues": issues,
            "needs_review": needs_review,
        }

    def refine(self, validation: dict, payload: InputPayload, context: Optional[dict] = None) -> str:
        draft = validation["draft"]
        issues = validation.get("issues", [])
        user_name = payload.user.display_name
        style = (context or {}).get("user_info", {}).get("communication_style", "")

        if not draft.strip():
            if style == "concise":
                return f"Hi {user_name}, mình đang kiểm tra lại."
            return f"Hi {user_name}, thank you for reaching out. I'm reviewing your request and will provide a detailed response shortly."

        if validation.get("needs_review"):
            draft += "\n\n*Note: This response may need further review.*"
        elif len(issues) > 2:
            draft += "\n\n*Note: This response may need further review.*"

        return draft
