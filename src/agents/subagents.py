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
            "guide_requested": False,
            "guide_id": None,
            "guide_title": None,
        }

        guide_keywords = ["hướng dẫn", "guideline", "hướng dẫn", "manual", "tài liệu", "doc", "cách làm", "quy trình", "hướng dẫn"]
        
        if any(kw in text_lower for kw in guide_keywords):
            policy_info["guidelines_found"] = True
            policy_info["guide_requested"] = True
            
            if llm:
                system_prompt = """Bạn là chuyên gia về policy. Trích xuất ID và tiêu đề hướng dẫn phù hợp với câu hỏi người dùng.
Trả về JSON: {"guide_id": "...", "guide_title": "..."}"""

                response: LLMResponse = await llm.complete(system_prompt, payload.message.text)
                
                import json
                import re
                match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                        policy_info["guide_id"] = parsed.get("guide_id")
                        policy_info["guide_title"] = parsed.get("guide_title")
                        if parsed.get("guide_title"):
                            policy_info["relevant_policies"].append(parsed.get("guide_title"))
                    except json.JSONDecodeError:
                        pass

        policy_keywords = ["policy", "quy định", "chính sách", "rule", "sop"]
        if any(kw in text_lower for kw in policy_keywords) and not policy_info["guide_requested"]:
            policy_info["guidelines_found"] = True

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
            "system_query_requested": False,
            "query_type": None,
        }

        text_lower = payload.message.text.lower()
        
        system_query_keywords = [
            "thông tin người dùng", "user info", "tra cứu", 
            "kiểm tra thông tin", "check info", "tìm thông tin",
            "case của tôi", "my case", "trạng thái case", "case id",
            "ai đang xử lý", "who is handling", "assignee",
            "đang ở đâu", "status", "tình trạng",
            "cho tôi biết", "cho xem", "hiển thị",
        ]
        
        query_type_mapping = {
            "case của tôi": "case_info",
            "my case": "case_info",
            "trạng thái case": "case_info",
            "case id": "case_info",
            "ai đang xử lý": "user_info",
            "who is handling": "user_info",
        }
        
        for keyword, qtype in query_type_mapping.items():
            if keyword in text_lower:
                knowledge["system_query_requested"] = True
                knowledge["query_type"] = qtype
                break

        if memory.episodic_memory:
            knowledge["patterns"] = [
                item["content"] for item in memory.episodic_memory[:3]
            ]
            knowledge["confidence"] = 0.7

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
