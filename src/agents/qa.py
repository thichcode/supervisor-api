from src.core import InputPayload
from src.llm import MultiProviderLLMClient, LLMResponse
from typing import Optional, List
import json
import re
import asyncio


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

    def _format_knowledge_sources(self, knowledge: dict) -> str:
        """Format KB sources with citation markers for the LLM prompt.
        
        This enables the LLM to reference sources in its response using [1], [2] etc.
        """
        sources = knowledge.get("knowledge_results", [])
        if not sources:
            return ""

        lines = []
        for i, src in enumerate(sources[:5]):
            title = src.get("title", src.get("name", f"Source {i+1}"))
            content = src.get("content", "")[:500]
            src_type = src.get("type", src.get("knowledge_type", "KB"))
            category = src.get("category", "")
            lines.append(
                f"[{i+1}] {title} (Loại: {src_type}, Danh mục: {category})\n"
                f"    Nội dung: {content}\n"
            )

        return "\n".join(lines)

    def _build_citation_instruction(self, knowledge: dict) -> str:
        """Build instruction for the LLM to include citations in responses."""
        sources = knowledge.get("knowledge_results", [])
        if not sources:
            return ""
        return (
            "\nQUAN TRỌNG - Hướng dẫn trích dẫn nguồn:\n"
            "Khi trả lời, nếu sử dụng thông tin từ KB, hãy kèm trích dẫn nguồn ở cuối câu.\n"
            "Ví dụ: 'Theo quy định công ty, thời gian nghỉ phép là 12 ngày/năm [1]'\n"
            "hoặc 'Bạn có thể tham khảo hướng dẫn chi tiết tại [2]'\n"
            "Chỉ trích dẫn khi thông tin thực sự có trong nguồn được cung cấp.\n"
            "Nếu câu trả lời dựa trên kiến thức chung (không có trong KB), không cần trích dẫn."
        )

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
        kb_sources_formatted = self._format_knowledge_sources(knowledge)
        citation_instruction = self._build_citation_instruction(knowledge)

        if llm:
            self_consistency_enabled = hasattr(self, '_self_consistency_enabled') and self._self_consistency_enabled
            num_samples = getattr(self, '_self_consistency_samples', 3)

            if self_consistency_enabled:
                # ===== SELF-CONSISTENCY: Gọi LLM nhiều lần, chọn kết quả nhất quán =====
                system_prompt = self._build_system_prompt(
                    style_instructions, citation_instruction, kb_sources_formatted
                )
                user_prompt = self._build_user_prompt(payload, context, policy, knowledge)

                # Gọi LLM N lần với temperature khác nhau
                tasks = []
                temperatures = [0.3, 0.5, 0.7]
                for i in range(num_samples):
                    temp = temperatures[i % len(temperatures)]
                    tasks.append(llm.complete(
                        system_prompt=system_prompt,
                        user_message=user_prompt,
                        temperature=temp,
                    ))

                responses = await asyncio.gather(*tasks, return_exceptions=True)

                # Lọc response thành công
                valid_responses = []
                for r in responses:
                    if isinstance(r, Exception):
                        continue
                    if r and r.content and len(r.content) > 20:
                        valid_responses.append(r.content)

                if not valid_responses:
                    # Fallback: gọi single
                    response = await llm.complete(
                        system_prompt=self._build_system_prompt(style_instructions, citation_instruction, kb_sources_formatted),
                        user_message=self._build_user_prompt(payload, context, policy, knowledge),
                        temperature=0.3,
                    )
                    return response.content

                if len(valid_responses) == 1:
                    return valid_responses[0]

                # Chọn response nhất quán nhất bằng cách:
                # 1. So sánh n-gram overlap giữa các response
                # 2. Chọn response có average similarity cao nhất với các response khác
                best_response = self._select_most_consistent(valid_responses)
                return best_response

            # ===== SINGLE GENERATION (default) =====
            system_prompt = f"""Bạn là trợ lý AI cho hệ thống IT Support.
Dựa trên ngữ cảnh, chính sách và kiến thức được cung cấp, tạo câu trả lời phù hợp.
Trả lời bằng tiếng Việt, ngắn gọn và chính xác.
{style_instructions}
{citation_instruction}"""

            user_prompt = f"""Câu hỏi của người dùng: {payload.message.text}

Ngữ cảnh hội thoại: {context.get('conversation_summary', 'Không có')}
Mạch hội thoại hiện tại: {context.get('conversation_state', {})}
Bối cảnh kênh chat: {context.get('chat_context', {})}
Tin nhắn gần đây: {context.get('conversation_history', [])}
Vai trò người dùng: {context.get('user_info', {}).get('role', 'employee')}
Phong cách người dùng: {context.get('user_info', {}).get('communication_style', 'balanced')}
Mục đích người dùng: {context.get('conversation_state', {}).get('last_user_message_mode', 'unknown')}

{context.get('url_context', '')}

Chính sách liên quan: {policy}
Kiến thức: {knowledge}

{kb_sources_formatted}"""

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

    def _build_system_prompt(self, style_instructions: str, citation_instruction: str, kb_sources_formatted: str) -> str:
        return f"""Bạn là trợ lý AI cho hệ thống IT Support.
Dựa trên ngữ cảnh, chính sách và kiến thức được cung cấp, tạo câu trả lời phù hợp.
Trả lời bằng tiếng Việt, ngắn gọn và chính xác.
{style_instructions}
{citation_instruction}"""

    def _build_user_prompt(self, payload: InputPayload, context: dict, policy: dict, knowledge: dict) -> str:
        kb_sources_formatted = self._format_knowledge_sources(knowledge)
        return f"""Câu hỏi của người dùng: {payload.message.text}

Ngữ cảnh hội thoại: {context.get('conversation_summary', 'Không có')}
Mạch hội thoại hiện tại: {context.get('conversation_state', {})}
Bối cảnh kênh chat: {context.get('chat_context', {})}
Tin nhắn gần đây: {context.get('conversation_history', [])}
Vai trò người dùng: {context.get('user_info', {}).get('role', 'employee')}
Phong cách người dùng: {context.get('user_info', {}).get('communication_style', 'balanced')}
Mục đích người dùng: {context.get('conversation_state', {}).get('last_user_message_mode', 'unknown')}

{context.get('url_context', '')}

Chính sách liên quan: {policy}
Kiến thức: {knowledge}

{kb_sources_formatted}"""

    def _select_most_consistent(self, responses: List[str]) -> str:
        """Select the most consistent response from multiple samples.
        
        Uses n-gram overlap (ROUGE-1 style) to measure consistency.
        Returns the response with highest average similarity to all others.
        """
        if len(responses) == 1:
            return responses[0]

        def tokenize(text: str) -> set:
            """Tokenize text into lowercase word set."""
            return set(re.findall(r'\w+', text.lower()))

        def compute_similarity(text_a: str, text_b: str) -> float:
            """Compute Jaccard similarity between two texts."""
            tokens_a = tokenize(text_a)
            tokens_b = tokenize(text_b)
            if not tokens_a or not tokens_b:
                return 0.0
            intersection = tokens_a.intersection(tokens_b)
            union = tokens_a.union(tokens_b)
            return len(intersection) / len(union) if union else 0.0

        # Compute average similarity for each response
        avg_similarities = []
        for i in range(len(responses)):
            similarities = []
            for j in range(len(responses)):
                if i != j:
                    sim = compute_similarity(responses[i], responses[j])
                    similarities.append(sim)
            avg_sim = sum(similarities) / len(similarities) if similarities else 0.0
            avg_similarities.append(avg_sim)

        # Return response with highest average similarity
        best_idx = max(range(len(responses)), key=lambda i: avg_similarities[i])
        return responses[best_idx]


class QAAgent:
    def __init__(self):
        self.confidence_threshold = 0.7

    async def validate(
        self,
        draft: str,
        payload: InputPayload,
        context: dict,
        llm: Optional[MultiProviderLLMClient] = None,
        knowledge: Optional[dict] = None,
    ) -> dict:
        issues = []
        confidence = 0.85
        hallucination_signals = []

        if len(draft) < 20:
            issues.append("Câu trả lời quá ngắn")
            confidence -= 0.2

        if not draft.strip():
            issues.append("Câu trả lời trống")
            confidence -= 0.5

        text_lower = (payload.message.text or "").lower()

        support_keywords = ["giúp", "help", "hỗ trợ", "support", "case", "vấn đề"]
        if any(kw in text_lower for kw in support_keywords):
            if "case" not in (draft or "").lower() and "support" not in (draft or "").lower() and "help" not in (draft or "").lower():
                issues.append("Yêu cầu hỗ trợ chưa được xử lý rõ ràng")
                confidence -= 0.1

        if context.get("case_info") and context["case_info"].get("status") == "urgent":
            if "urgent" not in (draft or "").lower() and "asap" not in (draft or "").lower():
                issues.append("Case khẩn cấp chưa được đánh dấu phù hợp")
                confidence -= 0.1

        if "?" in payload.message.text and "?" not in draft:
            issues.append("Câu hỏi chưa được trả lời trực tiếp")
            confidence -= 0.15

        # ===== HALLUCINATION DETECTION v2: Check facts against KB sources =====
        if knowledge:
            kb_results = knowledge.get("knowledge_results", [])
            if kb_results:
                # Build KB content for fact-checking
                kb_text = ""
                for src in kb_results[:3]:
                    title = src.get("title", "")
                    content = src.get("content", "")[:1000]
                    kb_text += f"Source '{title}': {content}\n"

                if kb_text and llm:
                    try:
                        hallucination_signals = await self._check_hallucinations(
                            draft=draft,
                            kb_text=kb_text,
                            llm=llm,
                        )
                    except Exception as e:
                        import structlog
                        logger = structlog.get_logger(__name__)
                        logger.warning("hallucination_check_failed", error=str(e))

                if hallucination_signals:
                    for signal in hallucination_signals:
                        issues.append(signal)
                        confidence -= 0.15
        # ==================================

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
            "hallucination_signals": hallucination_signals,
        }

    async def _check_hallucinations(
        self,
        draft: str,
        kb_text: str,
        llm: MultiProviderLLMClient,
    ) -> List[str]:
        """Check if the draft contains hallucinated facts not supported by KB.
        
        Uses LLM to compare each claim in the draft against KB sources.
        Returns list of hallucination signals (empty if none found).
        """
        hallucination_prompt = f"""Bạn là chuyên gia kiểm tra fact-checking cho hệ thống IT Support.
Nhiệm vụ: So sánh câu trả lời dự thảo với nguồn kiến thức (KB) để phát hiện thông tin sai lệch (hallucination).

Nguồn KB:
{kb_text[:2000]}

Câu trả lời dự thảo:
{draft}

Hướng dẫn:
1. Xác định các claims/quan trọng trong câu trả lời
2. Kiểm tra từng claim có được hỗ trợ bởi nguồn KB không
3. Nếu claim KHÔNG có trong KB → đó là hallucination

CHỈ trả về JSON (không thêm text):
{{"hallucinations": ["claim 1 không có trong KB", "claim 2 không có trong KB"], "is_safe": true/false}}
- "is_safe": true nếu KHÔNG có hallucination, false nếu có
- "hallucinations": danh sách các claims sai lệch (rỗng nếu is_safe=true)
"""

        try:
            response = await llm.complete(
                system_prompt="Bạn là fact-checker. Chỉ trả về JSON hợp lệ.",
                user_message=hallucination_prompt,
                temperature=0.1,
            )

            if not response or not response.content:
                return []

            match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                is_safe = parsed.get("is_safe", True)
                if not is_safe:
                    signals = parsed.get("hallucinations", [])
                    return [f"Nghi ngờ hallucination: {s}" for s in signals[:3]]
            return []
        except Exception:
            return []

    def refine(self, validation: dict, payload: InputPayload, context: Optional[dict] = None) -> str:
        draft = validation["draft"]
        issues = validation.get("issues", [])
        user_name = payload.user.display_name
        style = (context or {}).get("user_info", {}).get("communication_style", "")

        if not draft.strip():
            if style == "concise":
                return f"Hi {user_name}, mình đang kiểm tra lại."
            return f"Hi {user_name}, thank you for reaching out. I'm reviewing your request and will provide a detailed response shortly."

        # Add citation quality note if citations are missing but KB sources exist
        if validation.get("hallucination_signals"):
            draft += "\n\n*⚠️ Phát hiện thông tin chưa được xác thực, đề nghị review kỹ trước khi gửi.*"
        elif validation.get("needs_review"):
            draft += "\n\n*Note: This response may need further review.*"
        elif len(issues) > 2:
            draft += "\n\n*Note: This response may need further review.*"

        return draft