from typing import Optional, List
import re
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.core.kb_templates import KBCategoryTemplateMapper
from src.knowledge.repository import KnowledgeBaseRepository
from src.knowledge.schemas import (
    KnowledgeSearchResult,
    KnowledgeSearchResponse,
    KnowledgeType,
)
from src.core.metrics import metrics
from src.llm import MultiProviderLLMClient

logger = structlog.get_logger()


class KnowledgeRetrievalService:
    def __init__(self, session: AsyncSession, llm: Optional[MultiProviderLLMClient] = None):
        self.session = session
        self.repo = KnowledgeBaseRepository(session)
        self.llm = llm
        self.template_mapper = KBCategoryTemplateMapper()

    async def search(
        self,
        query: str,
        search_type: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
        offset: int = 0,
    ) -> KnowledgeSearchResponse:
        results: List[KnowledgeSearchResult] = []

        normalized_query = re.sub(r"\s+", " ", query).strip()
        if len(normalized_query) > 512:
            normalized_query = normalized_query[:512]

        template_match = KBCategoryTemplateMapper.detect(normalized_query)
        search_types = self._resolve_search_types(search_type, normalized_query)
        query_variants = self._build_query_variants(normalized_query, template_match)
        primary_search_type = search_type or "all"
        metrics.record_kb_search(primary_search_type, "started")
        if template_match:
            metrics.record_kb_template(template_match.template_id, primary_search_type, "detected")
        else:
            metrics.record_kb_template("none", primary_search_type, "not_detected")

        for kb_type in search_types:
            kb_results = await self._search_knowledge_base(kb_type, normalized_query, category, tags, limit)
            results.extend(kb_results)

        results = self._deduplicate_and_rank(results, normalized_query, template_match)

        if (not results or results[0].similarity < 0.5) and query_variants:
            for variant in query_variants:
                for kb_type in search_types:
                    kb_results = await self._search_knowledge_base(kb_type, variant, category, tags, limit)
                    results.extend(kb_results)
            results = self._deduplicate_and_rank(results, normalized_query, template_match)

        self._record_search_outcome(primary_search_type, results)

        total_results = len(results)
        page_results = results[max(0, offset):max(0, offset) + limit]

        return KnowledgeSearchResponse(
            results=page_results,
            total=total_results,
            search_type=search_type or "all",
            query=query,
            template_id=template_match.template_id if template_match else "",
            template_label=template_match.label if template_match else "",
            template_score=template_match.score if template_match else 0.0,
            template_terms=list(template_match.matched_terms) if template_match else [],
        )

    def infer_clarification(
        self,
        query: str,
        results: List[KnowledgeSearchResult],
    ) -> dict:
        """Infer whether the KB match is too vague and needs more user details."""
        if not results:
            return {
                "needs_clarification": False,
                "missing_fields": [],
                "clarification_question": "",
                "reason": "no_results",
            }

        top = results[0]
        if top.similarity < 0.5:
            return {
                "needs_clarification": False,
                "missing_fields": [],
                "clarification_question": "",
                "reason": "low_similarity",
            }

        required_fields = self._extract_required_fields(top)
        if not required_fields:
            required_fields = self._default_required_fields(top.knowledge_type.value, top.category, top.title)

        missing_fields = self._missing_fields(query, required_fields)
        if not missing_fields:
            return {
                "needs_clarification": False,
                "missing_fields": [],
                "clarification_question": "",
                "reason": "enough_context",
            }

        clarification_question = self._build_clarification_question(top, missing_fields)
        metrics.record_kb_clarification(top.knowledge_type.value, "missing_context")
        return {
            "needs_clarification": True,
            "missing_fields": missing_fields,
            "clarification_question": clarification_question,
            "reason": "missing_kb_context",
            "required_fields": required_fields,
        }

    def _extract_required_fields(self, result: KnowledgeSearchResult) -> List[str]:
        metadata = result.metadata or {}
        required_fields = metadata.get("required_fields") or []
        if isinstance(required_fields, str):
            required_fields = [required_fields]
        required_fields = [str(field).strip() for field in required_fields if str(field).strip()]

        placeholder_text = f"{result.title} {result.content}"
        placeholder_matches = re.findall(r"[<\[{]([^>\]}]+)[>\]}]", placeholder_text)
        for field in placeholder_matches:
            cleaned = field.strip().lower()
            if cleaned and cleaned not in required_fields:
                required_fields.append(cleaned)

        return required_fields

    def _default_required_fields(self, kb_type: str, category: str, title: str) -> List[str]:
        kb_type = (kb_type or "").lower()
        category = (category or "").lower()
        title = (title or "").lower()
        fields: List[str] = []

        if kb_type == "faq":
            fields.extend(["error_message", "system", "environment"])
        elif kb_type == "guide":
            fields.extend(["step", "environment", "error_message"])
        elif kb_type == "document":
            fields.extend(["document_scope", "use_case", "system"])
        elif kb_type == "policy":
            fields.extend(["policy_scope", "system", "user_role"])

        if any(keyword in title for keyword in ["vpn", "remote", "network"]):
            fields.extend(["device", "os", "error_code"])
        if any(keyword in category for keyword in ["access", "auth", "login"]):
            fields.extend(["user_id", "system", "error_message"])

        unique_fields: List[str] = []
        for field in fields:
            if field not in unique_fields:
                unique_fields.append(field)
        return unique_fields

    def _missing_fields(self, query: str, required_fields: List[str]) -> List[str]:
        normalized = (query or "").lower()
        synonyms = {
            "error_message": ["lỗi", "error", "message", "thông báo"],
            "error_code": ["code", "mã lỗi", "error code"],
            "system": ["hệ thống", "system", "app", "application", "dịch vụ", "service"],
            "environment": ["prod", "production", "dev", "staging", "môi trường", "env"],
            "device": ["device", "máy", "laptop", "pc", "desktop", "mobile", "android", "ios"],
            "os": ["windows", "mac", "linux", "ubuntu", "os"],
            "step": ["bước", "step", "đã làm", "đang kẹt", "kẹt ở"],
            "user_id": ["user", "user_id", "id người dùng", "tài khoản"],
            "policy_scope": ["phạm vi", "scope", "đối tượng", "áp dụng cho"],
            "document_scope": ["tài liệu", "document", "file", "báo cáo", "chứng từ"],
            "use_case": ["use case", "trường hợp", "tình huống", "mục đích"],
            "user_role": ["vai trò", "role", "chức danh", "phòng ban"],
        }

        missing = []
        for field in required_fields:
            field_key = field.lower().strip()
            field_synonyms = synonyms.get(field_key, [field_key.replace("_", " ")])
            if not any(token in normalized for token in field_synonyms):
                missing.append(field)
        return missing

    def _build_clarification_question(self, result: KnowledgeSearchResult, missing_fields: List[str]) -> str:
        labels = {
            "error_message": "thông báo lỗi chính xác",
            "error_code": "mã lỗi",
            "system": "tên hệ thống/dịch vụ liên quan",
            "environment": "môi trường (prod/dev/staging)",
            "device": "thiết bị đang dùng",
            "os": "hệ điều hành/phiên bản máy",
            "step": "bạn đang kẹt ở bước nào",
            "user_id": "user_id/tài khoản liên quan",
            "policy_scope": "phạm vi áp dụng",
            "document_scope": "phạm vi tài liệu cần tra",
            "use_case": "trường hợp sử dụng cụ thể",
            "user_role": "vai trò/phòng ban liên quan",
        }
        friendly_fields = [labels.get(field, field.replace("_", " ")) for field in missing_fields[:4]]
        fields_text = "; ".join(friendly_fields)
        return (
            f"Mình tìm thấy KB phù hợp về '{result.title}'. Để support đúng theo KB, "
            f"bạn cho mình thêm: {fields_text}."
        )

    def _resolve_search_types(self, search_type: Optional[str], query: str) -> List[str]:
        return KBCategoryTemplateMapper.search_types_for(query, search_type)

    def _build_query_variants(self, query: str, template_match=None) -> List[str]:
        return KBCategoryTemplateMapper.build_query_variants(query, template_match)

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
            docs = await self.repo.search_documents(query=query, category=category, tags=tags, limit=limit)
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
            return 0.4

        intersection = query_words.intersection(text_words)
        return min(1.0, len(intersection) / len(query_words))

    def _deduplicate_and_rank(
        self,
        results: List[KnowledgeSearchResult],
        query: str,
        template_match=None,
    ) -> List[KnowledgeSearchResult]:
        seen_ids = set()
        unique_results = []

        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                unique_results.append(r)

        if not query:
            return sorted(unique_results, key=lambda x: (x.knowledge_type.value, x.title.lower()))

        boost_keywords = self._extract_keywords(query)
        for r in unique_results:
            for keyword in boost_keywords:
                if keyword.lower() in r.title.lower() or keyword.lower() in r.content.lower():
                    r.similarity = min(1.0, r.similarity + 0.1)
            r.similarity = KBCategoryTemplateMapper.boost_similarity(
                template_match,
                r.title,
                r.category,
                r.content,
                r.similarity,
                r.knowledge_type.value,
            )

        return sorted(unique_results, key=lambda x: x.similarity, reverse=True)

    def _record_search_outcome(self, search_type: str, results: List[KnowledgeSearchResult]) -> None:
        if not results:
            metrics.record_kb_fallback(search_type, "no_results")
            metrics.record_kb_search(search_type, "miss")
            return

        top = results[0]
        if top.similarity >= 0.5:
            metrics.record_kb_search(search_type, "hit")
        else:
            metrics.record_kb_fallback(search_type, "low_similarity")
            metrics.record_kb_search(search_type, "miss")

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
        offset: int = 0,
    ) -> KnowledgeSearchResponse:
        base_results = await self.search(query, search_type, category, None, limit, offset)

        if not self.llm or not base_results.results:
            return base_results

        try:
            context_parts = [
                f"Result {i+1}: {r.title}\n{r.content[:500]}"
                for i, r in enumerate(base_results.results[:3])
            ]
            context = "\n\n".join(context_parts)
            metrics.record_kb_rerank(search_type or "all", "success")

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
            metrics.record_kb_rerank(search_type or "all", "failure")

        return base_results

    async def get_knowledge_stats(self) -> dict:
        return {
            "policies_count": await self.repo.count_policies(),
            "faqs_count": await self.repo.count_faqs(),
            "guides_count": await self.repo.count_guides(),
            "documents_count": await self.repo.count_documents(),
            "categories": await self.repo.get_categories_stats(),
        }