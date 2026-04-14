from typing import Optional, List, Tuple
import re
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.knowledge.repository import KnowledgeBaseRepository
from src.knowledge.schemas import (
    KnowledgeSearchResult,
    KnowledgeSearchResponse,
    KnowledgeType,
)
from src.llm import MultiProviderLLMClient

logger = structlog.get_logger()


class KnowledgeRetrievalService:
    def __init__(self, session: AsyncSession, llm: Optional[MultiProviderLLMClient] = None):
        self.session = session
        self.repo = KnowledgeBaseRepository(session)
        self.llm = llm

    async def search(
        self,
        query: str,
        search_type: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> KnowledgeSearchResponse:
        results: List[KnowledgeSearchResult] = []

        normalized_query = re.sub(r"\s+", " ", query).strip()
        if len(normalized_query) > 512:
            normalized_query = normalized_query[:512]

        search_types = self._resolve_search_types(search_type)

        for kb_type in search_types:
            kb_results = await self._search_knowledge_base(
                kb_type, normalized_query, category, tags, limit
            )
            results.extend(kb_results)

        results = self._deduplicate_and_rank(results, normalized_query)

        return KnowledgeSearchResponse(
            results=results[:limit],
            total=len(results),
            search_type=search_type or "all",
            query=query,
        )

    def _resolve_search_types(self, search_type: Optional[str]) -> List[str]:
        if search_type:
            return [search_type]
        return ["policy", "faq", "guide", "document"]

    async def _search_knowledge_base(
        self,
        kb_type: str,
        query: str,
        category: Optional[str],
        tags: Optional[List[str]],
        limit: int,
    ) -> List[KnowledgeSearchResult]:
        results: List[KnowledgeSearchResult] = []

        if kb_type == "policy":
            policies = await self.repo.search_policies(query, category, tags, limit)
            for p in policies:
                results.append(KnowledgeSearchResult(
                    knowledge_type=KnowledgeType.POLICY,
                    id=p.policy_id,
                    title=p.title,
                    content=p.content,
                    category=p.category,
                    tags=p.tags or [],
                    similarity=self._calculate_text_similarity(query, p.title + " " + p.content),
                    metadata={"version": p.version},
                ))

        elif kb_type == "faq":
            faqs = await self.repo.search_faqs(query, category, tags, None, limit)
            for f in faqs:
                results.append(KnowledgeSearchResult(
                    knowledge_type=KnowledgeType.FAQ,
                    id=f.question_id,
                    title=f.question,
                    content=f.answer,
                    category=f.category,
                    tags=f.tags or [],
                    keywords=f.keywords or [],
                    similarity=self._calculate_text_similarity(query, f.question + " " + f.answer),
                    metadata={"usage_count": f.usage_count},
                ))
                await self.repo.increment_faq_usage(f.question_id)

        elif kb_type == "guide":
            guides = await self.repo.search_guides(query, None, category, tags, limit)
            for g in guides:
                results.append(KnowledgeSearchResult(
                    knowledge_type=KnowledgeType.GUIDE,
                    id=g.guide_id,
                    title=g.title,
                    content=g.content,
                    category=g.category,
                    tags=g.tags or [],
                    similarity=self._calculate_text_similarity(query, g.title + " " + g.content),
                    metadata={"guide_type": g.guide_type, "steps_count": len(g.steps or [])},
                ))

        elif kb_type == "document":
            docs = await self.repo.search_documents(query, None, category, tags, limit)
            for d in docs:
                results.append(KnowledgeSearchResult(
                    knowledge_type=KnowledgeType.DOCUMENT,
                    id=d.document_id,
                    title=d.title,
                    content=d.content,
                    category=d.category,
                    tags=d.tags or [],
                    similarity=self._calculate_text_similarity(query, d.title + " " + d.content),
                    metadata={"doc_type": d.document_type},
                ))

        return results

    def _calculate_text_similarity(self, query: str, text: str) -> float:
        query_words = set(query.lower().split())
        text_words = set(text.lower().split())

        if not query_words or not text_words:
            return 0.5

        intersection = query_words.intersection(text_words)
        return min(1.0, len(intersection) / len(query_words))

    def _deduplicate_and_rank(
        self,
        results: List[KnowledgeSearchResult],
        query: str,
    ) -> List[KnowledgeSearchResult]:
        seen_ids = set()
        unique_results = []

        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_results.append(r)

        boost_keywords = self._extract_keywords(query)
        for r in unique_results:
            for keyword in boost_keywords:
                if keyword.lower() in r.title.lower() or keyword.lower() in r.content.lower():
                    r.similarity = min(1.0, r.similarity + 0.1)

        return sorted(unique_results, key=lambda x: x.similarity, reverse=True)

    def _extract_keywords(self, text: str) -> List[str]:
        keywords = []
        text_lower = text.lower()

        keyword_patterns = {
            "policy": ["chính sách", "quy định", "policy", "rule"],
            "faq": ["câu hỏi", "hỏi đáp", "faq", "question"],
            "guide": ["hướng dẫn", "cách làm", "guide", "manual"],
            "case": ["case", "vấn đề", "support"],
            "leave": ["nghỉ", "leave", "phép"],
            "remote": ["remote", "làm việc từ xa"],
        }

        for category, patterns in keyword_patterns.items():
            if any(p in text_lower for p in patterns):
                keywords.extend(patterns)

        return keywords

    async def search_with_llm_enhancement(
        self,
        query: str,
        search_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> KnowledgeSearchResponse:
        base_results = await self.search(query, search_type, category, None, limit)

        if not self.llm or not base_results.results:
            return base_results

        try:
            context_parts = [
                f"Result {i+1}: {r.title}\n{r.content[:500]}"
                for i, r in enumerate(base_results.results[:3])
            ]
            context = "\n\n".join(context_parts)

            system_prompt = """Bạn là trợ lý tìm kiếm knowledge base. 
Dựa trên kết quả search và câu hỏi người dùng, chọn kết quả phù hợp nhất và re-rank.
Trả về JSON: {"relevant_ids": ["id1", "id2"], "reason": "..."}"""

            response = await self.llm.complete(
                system_prompt,
                f"Câu hỏi: {query}\n\nKết quả:\n{context}",
            )

            import json
            import re
            match = re.search(r'\{[^{}]*\}', response.content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                relevant_ids = parsed.get("relevant_ids", [])

                id_to_result = {r.id: r for r in base_results.results}
                ranked = [id_to_result[rid] for rid in relevant_ids if rid in id_to_result]

                for r in base_results.results:
                    if r.id not in relevant_ids:
                        ranked.append(r)

                base_results.results = ranked

        except Exception as e:
            logger.warning("LLM enhancement failed", error=str(e))

        return base_results

    async def get_knowledge_stats(self) -> dict:
        return {
            "policies_count": await self.repo.count_policies(),
            "faqs_count": await self.repo.count_faqs(),
            "guides_count": await self.repo.count_guides(),
            "documents_count": await self.repo.count_documents(),
            "categories": await self.repo.get_categories_stats(),
        }