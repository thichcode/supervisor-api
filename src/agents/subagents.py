from src.core import InputPayload
from src.memory import MemoryContext
from src.llm import MultiProviderLLMClient, LLMResponse
from src.db import async_session
from src.knowledge import KnowledgeRetrievalService
from typing import Optional


class ContextAgent:
    def build(self, payload: InputPayload, memory: MemoryContext) -> dict:
        context = {
            "current_message": payload.message.text,
            "conversation_history": memory.recent_messages,
            "conversation_summary": memory.conversation_summary or "No prior context",
            "conversation_state": memory.conversation_state or {},
            "chat_context": {
                "platform": payload.conversation.platform or payload.source,
                "chat_type": payload.conversation.chat_type or (memory.conversation_state or {}).get("chat_type"),
                "chat_scope": payload.conversation.chat_scope or (memory.conversation_state or {}).get("chat_scope"),
                "group_chat": payload.conversation.group_chat if payload.conversation.group_chat is not None else (memory.conversation_state or {}).get("group_chat", False),
            },
            "user_info": {
                "name": payload.user.display_name,
                "id": payload.user.id,
                "role": memory.user_profile.get("role") if memory.user_profile else None,
                "vip": payload.user.vip_flag,
                "team": memory.user_profile.get("team") if memory.user_profile else None,
                "communication_style": memory.user_profile.get("communication_style") if memory.user_profile else None,
                "preferences": memory.user_profile.get("preferences") if memory.user_profile else {},
            },
            "case_info": None,
            "resolved_points": [],
            "unresolved_points": memory.conversation_state.get("open_loops", []) if memory.conversation_state else [],
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

        guide_keywords = [
            # English
            "guide", "guideline", "manual", "document", "doc", "documentation",
            "how to", "tutorial", "instruction", "step by step",
            # Vietnamese
            "hướng dẫn", "tài liệu", "cách làm", "cách sử dụng", "quy trình",
            "sách hướng dẫn", "chỉ dẫn", "chỉ thị", "hướng dẫn sử dụng",
            "cách cài đặt", "cách config", "cách setup",
        ]
        
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

        policy_keywords = [
            # English
            "policy", "guideline", "rule", "sop", "procedure",
            "regulation", "compliance", "requirement", "standard",
            # Vietnamese
            "quy định", "chính sách", "thể lệ", "nội quy",
            "tiêu chuẩn", "yêu cầu", "nguyên tắc",
            "quyền lợi", "phúc lợi", "phạt", "thưởng",
        ]
        if any(kw in text_lower for kw in policy_keywords) and not policy_info["guide_requested"]:
            policy_info["guidelines_found"] = True
            if not policy_info["relevant_policies"]:
                policy_info["relevant_policies"].append("Áp dụng các chính sách chung của công ty")

        support_keywords = [
            # English
            "support", "case", "ticket", "issue", "problem", "bug",
            "error", "crash", "not working", "broken", "help",
            # Vietnamese
            "hỗ trợ", "vấn đề", "sự cố", "lỗi", "hỏng",
            "không được", "bị lỗi", "treo", "đơ",
            "cần giúp", "giúp tôi", "sửa", "fix",
        ]
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
            "confidence": 0.4,
            "system_query_requested": False,
            "query_type": None,
            "knowledge_results": [],
            "search_performed": False,
        }

        text_lower = payload.message.text.lower()

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

        knowledge_base_query = self._detect_knowledge_query(text_lower)
        
        if knowledge_base_query:
            knowledge["search_performed"] = True
            search_results = await self._search_knowledge_base(
                payload.message.text,
                knowledge_base_query,
                llm,
            )
            knowledge["knowledge_results"] = search_results.get("results", []) if isinstance(search_results, dict) else search_results
            knowledge["knowledge_clarification_needed"] = bool(search_results.get("clarification", {}).get("needs_clarification")) if isinstance(search_results, dict) else False
            knowledge["knowledge_clarification_question"] = search_results.get("clarification", {}).get("clarification_question", "") if isinstance(search_results, dict) else ""
            knowledge["knowledge_missing_fields"] = search_results.get("clarification", {}).get("missing_fields", []) if isinstance(search_results, dict) else []
            knowledge["knowledge_required_fields"] = search_results.get("clarification", {}).get("required_fields", []) if isinstance(search_results, dict) else []
            knowledge["knowledge_template"] = search_results.get("template", {}) if isinstance(search_results, dict) else {}
            knowledge["confidence"] = 0.85 if knowledge["knowledge_results"] else 0.4

        if memory.episodic_memory:
            knowledge["patterns"] = [
                item["content"] for item in memory.episodic_memory[:3]
            ]
            if not knowledge["search_performed"]:
                knowledge["confidence"] = 0.4

        question_types = {
            "who": ["who", "ai là", "người nào"],
            "when": ["when", "khi nào", "thời gian", "lúc nào"],
            "where": ["where", "ở đâu", "địa điểm"],
            "what": ["what", "là gì", "cái gì", "gì"],
            "why": ["why", "tại sao", "vì sao"],
            "how": ["how", "như thế nào", "làm sao"],
        }

        for qtype, keywords in question_types.items():
            if any(kw in text_lower for kw in keywords) and not knowledge["search_performed"]:
                knowledge["facts"].append(f"Phát hiện câu hỏi {qtype}")

        if not knowledge["search_performed"] and llm and (knowledge["patterns"] or memory.conversation_summary):
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

    def _detect_knowledge_query(self, text_lower: str) -> Optional[str]:
        patterns = {
            "policy": ["chính sách", "policy", "quy định", "quy luật", "rule"],
            "faq": ["faq", "câu hỏi", "hỏi đáp", "question", "là gì", "cái gì", "như thế nào"],
            "guide": ["hướng dẫn", "cách làm", "manual", "cách sử dụng", "làm sao"],
            "document": ["tài liệu", "document", "file", "báo cáo"],
        }

        for kb_type, keywords in patterns.items():
            if any(kw in text_lower for kw in keywords):
                return kb_type

        return None

    async def _search_knowledge_base(
        self,
        query: str,
        search_type: str,
        llm: Optional[MultiProviderLLMClient],
    ) -> dict:
        async with async_session() as session:
            if llm:
                kb_service = KnowledgeRetrievalService(session, llm)
                results = await kb_service.search_with_llm_enhancement(query, search_type)
            else:
                kb_service = KnowledgeRetrievalService(session, None)
                results = await kb_service.search(query, search_type)

            formatted_results = [
                {
                    "type": r.knowledge_type.value,
                    "id": r.id,
                    "title": r.title,
                    "content": r.content[:500],
                    "category": r.category,
                    "similarity": r.similarity,
                    "metadata": r.metadata or {},
                }
                for r in results.results
            ]
            clarification = getattr(results, "clarification", {}) or {}
            return {
                "results": formatted_results,
                "clarification": clarification,
                "template": {
                    "template_id": results.template_id,
                    "template_label": results.template_label,
                    "template_score": results.template_score,
                    "template_terms": results.template_terms,
                },
            }
