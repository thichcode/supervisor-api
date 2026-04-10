from src.core import InputPayload
from src.memory import MemoryContext
from src.llm import MultiProviderLLMClient, LLMResponse
from src.db import async_session
from src.knowledge import KnowledgeRetrievalService
from typing import Optional


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
            "knowledge_results": [],
            "search_performed": False,
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

        knowledge_base_query = self._detect_knowledge_query(text_lower)
        
        if knowledge_base_query:
            knowledge["search_performed"] = True
            search_results = await self._search_knowledge_base(
                payload.message.text,
                knowledge_base_query,
                llm,
            )
            knowledge["knowledge_results"] = search_results
            knowledge["confidence"] = 0.85 if search_results else 0.4

        if memory.episodic_memory:
            knowledge["patterns"] = [
                item["content"] for item in memory.episodic_memory[:3]
            ]
            if not knowledge["search_performed"]:
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
    ) -> list:
        async with async_session() as session:
            if llm:
                kb_service = KnowledgeRetrievalService(session, llm)
                results = await kb_service.search_with_llm_enhancement(query, search_type)
            else:
                kb_service = KnowledgeRetrievalService(session, None)
                results = await kb_service.search(query, search_type)

            return [
                {
                    "type": r.knowledge_type.value,
                    "id": r.id,
                    "title": r.title,
                    "content": r.content[:500],
                    "category": r.category,
                    "similarity": r.similarity,
                }
                for r in results.results
            ]