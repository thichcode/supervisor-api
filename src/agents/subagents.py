from src.core import InputPayload
from src.memory import MemoryContext
from src.llm import MultiProviderLLMClient, LLMResponse
from src.db import async_session
from src.knowledge import KnowledgeRetrievalService
from typing import Optional
import structlog

logger = structlog.get_logger()


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
        text_lower = (payload.message.text or "").lower()
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
            # Enhanced search metadata
            "detected_domain": None,
            "system_context": None,
            "domain_context": None,
        }

        text_lower = (payload.message.text or "").lower()
        text = payload.message.text



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

        # Replace old KB search with enhanced_kb_search (search_type="all")
        try:
            from src.services.kb_enhanced_search import enhanced_kb_search
            
            enhanced_results = await enhanced_kb_search(
                query=payload.message.text,
                user_id=getattr(payload, 'user_id', None),
                use_context=True,
                use_domain=True,
                llm=llm,
            )
            
            # Map results to knowledge dict
            knowledge["detected_domain"] = enhanced_results.get("detected_domain")
            knowledge["system_context"] = enhanced_results.get("system_context")
            knowledge["domain_context"] = enhanced_results.get("domain_context")
            
            search_results = enhanced_results.get("search_results", [])
            knowledge["knowledge_results"] = search_results
            knowledge["search_performed"] = len(search_results) > 0
            
            clarification = enhanced_results.get("clarification", {})
            knowledge["knowledge_clarification_needed"] = clarification.get("needs_clarification", False)
            knowledge["knowledge_clarification_question"] = clarification.get("clarification_question", "")
            knowledge["knowledge_missing_fields"] = clarification.get("missing_fields", [])
            knowledge["knowledge_required_fields"] = clarification.get("required_fields", [])
            
            knowledge["knowledge_template"] = {
                "template_id": enhanced_results.get("template_id", ""),
                "template_label": enhanced_results.get("template_label", ""),
                "template_score": 0.0,
                "template_terms": [],
            }
            
            knowledge["confidence"] = 0.85 if search_results else 0.4
            knowledge["enrichment"] = enhanced_results.get("enrichment", {})
            
            logger.info(
                "Enhanced KB search completed in gather()",
                query=payload.message.text,
                results_count=len(search_results),
                domain=enhanced_results.get("detected_domain")
            )
            
        except ImportError:
            logger.warning("Enhanced KB search module not available, skipping KB search")
        except Exception as e:
            logger.error("Enhanced KB search failed in gather()", error=str(e))

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






