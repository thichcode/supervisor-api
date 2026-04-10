from src.core import InputPayload
from src.memory import MemoryContext
from src.llm import MultiProviderLLMClient, LLMResponse
from typing import Optional


class ContextAgent:
    def build(self, payload: InputPayload, memory: MemoryContext) -> dict:
        context = {
            "current_message": payload.message.text,
            "conversation_history": memory.recent_messages,
            "conversation_summary": memory.conversation_summary or "No prior context",
            "user_info": {
                "name": payload.user.display_name,
                "id": payload.user.id,
                "role": memory.user_profile.get("role") if memory.user_profile else None,
                "vip": payload.user.vip_flag,
            },
            "case_info": None,
            "resolved_points": [],
            "unresolved_points": memory.to_dict().get("conversation_summary", ""),
        }

        if payload.case and memory.case_memory:
            context["case_info"] = {
                "case_id": payload.case.case_id,
                "status": memory.case_memory.get("status"),
                "owner": memory.case_memory.get("owner"),
                "priority": payload.case.priority,
                "summary": memory.case_memory.get("summary"),
                "open_items": memory.case_memory.get("open_items", []),
            }

        if len(memory.recent_messages) > 3:
            context["conversation_history"] = memory.recent_messages[-5:]

        return context


class PolicyAgent:
    async def extract(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        llm: Optional[MultiProviderLLMClient] = None,
    ) -> dict:
        text_lower = payload.message.text.lower()
        policy_info = {
            "relevant_policies": [],
            "guidelines_found": False,
            "sop_steps": [],
        }

        policy_keywords = ["policy", "quy định", "chính sách", "guideline", "rule", "sop"]
        if any(kw in text_lower for kw in policy_keywords):
            policy_info["guidelines_found"] = True

            if llm:
                system_prompt = """Bạn là chuyên gia về chính sách công ty. 
Trích xuất các chính sách và SOP liên quan từ câu hỏi của người dùng.
Trả về JSON format:
{"policies": ["policy1", "policy2"], "sop_steps": ["step1", "step2"]}"""

                response: LLMResponse = await llm.complete(
                    system_prompt, 
                    payload.message.text
                )
                
                import json
                import re
                match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                        policy_info["relevant_policies"] = parsed.get("policies", [])
                        policy_info["sop_steps"] = parsed.get("sop_steps", [])
                    except json.JSONDecodeError:
                        pass

            if not policy_info["relevant_policies"]:
                policy_info["relevant_policies"].append("Áp dụng các chính sách chung của công ty")

        support_keywords = ["support", "case", "hỗ trợ", "vấn đề", "ticket", "issue"]
        if any(kw in text_lower for kw in support_keywords):
            if memory.case_memory:
                policy_info["relevant_policies"].append("Áp dụng quy trình xử lý case")
            if not policy_info["sop_steps"]:
                policy_info["sop_steps"] = [
                    "Xác nhận case",
                    "Xem lịch sử case",
                    "Cung cấp giải pháp hoặc chuyển escalated",
                ]

        if any(kw in text_lower for kw in ["escalate", "chuyển", "forward"]):
            policy_info["relevant_policies"].append("Áp dụng chính sách escalation")
            policy_info["sop_steps"].append("Chuyển đến team phù hợp")

        return policy_info


class KnowledgeAgent:
    async def retrieve(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        llm: Optional[MultiProviderLLMClient] = None,
    ) -> dict:
        knowledge = {
            "facts": [],
            "patterns": [],
            "confidence": 0.5,
        }

        if memory.episodic_memory:
            knowledge["patterns"] = [
                item["content"] for item in memory.episodic_memory[:3]
            ]
            knowledge["confidence"] = 0.7

        text_lower = payload.message.text.lower()
        question_types = {
            "who": ["who", "ai là", "người nào"],
            "when": ["when", "khi nào", "thời gian", "lúc nào"],
            "where": ["where", "ở đâu", "địa điểm"],
            "what": ["what", "là gì", "cái gì", "gì"],
            "why": ["why", "tại sao", "vì sao"],
            "how": ["how", "như thế nào", "làm sao"],
        }

        for qtype, keywords in question_types.items():
            if any(kw in text_lower for kw in keywords):
                knowledge["facts"].append(f"Phát hiện câu hỏi {qtype}")

        if llm and (knowledge["patterns"] or memory.conversation_summary):
            system_prompt = """Bạn là trợ lý tìm kiếm kiến thức. 
Dựa trên ngữ cảnh được cung cấp, trích xuất các thông tin và patterns phù hợp để trả lời câu hỏi.
Trả về JSON format:
{"relevant_facts": ["fact1", "fact2"], "confidence": 0.0-1.0}"""

            context_str = f"Patterns: {knowledge['patterns']}\nSummary: {memory.conversation_summary}"
            response: LLMResponse = await llm.complete(
                system_prompt, 
                f"Câu hỏi: {payload.message.text}\nNgữ cảnh: {context_str}"
            )

            import json
            import re
            match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    knowledge["facts"] = parsed.get("relevant_facts", knowledge["facts"])
                    knowledge["confidence"] = parsed.get("confidence", response.confidence)
                except json.JSONDecodeError:
                    pass

        return knowledge
