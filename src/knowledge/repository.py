from typing import Optional, List
import re

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import KnowledgeDocument, KnowledgeFAQ, KnowledgeGuide, KnowledgePolicy


_MAX_SEARCH_QUERY_LEN = 512


def _normalize_query(query: str) -> str:
    """Collapse whitespace and cap query length for SQL LIKE searches."""
    compact = re.sub(r"\s+", " ", query).strip()
    if len(compact) > _MAX_SEARCH_QUERY_LEN:
        return compact[:_MAX_SEARCH_QUERY_LEN]
    return compact


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_policy(self, policy: KnowledgePolicy) -> KnowledgePolicy:
        self.session.add(policy)
        await self.session.commit()
        await self.session.refresh(policy)
        return policy

    async def get_policy(self, policy_id: str) -> Optional[KnowledgePolicy]:
        result = await self.session.execute(
            select(KnowledgePolicy).where(
                KnowledgePolicy.policy_id == policy_id,
                KnowledgePolicy.is_active == True
            )
        )
        return result.scalar_one_or_none()

    async def search_policies(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[KnowledgePolicy]:
        stmt = select(KnowledgePolicy).where(KnowledgePolicy.is_active == True)

        if category:
            stmt = stmt.where(KnowledgePolicy.category == category)

        if tags:
            stmt = stmt.where(KnowledgePolicy.tags.contains(tags))

        if query:
            query = _normalize_query(query)
            stmt = stmt.where(
                or_(
                    KnowledgePolicy.title.ilike(f"%{query}%"),
                    KnowledgePolicy.content.ilike(f"%{query}%"),
                )
            )

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_policies(self, limit: int = 100, offset: int = 0) -> List[KnowledgePolicy]:
        result = await self.session.execute(
            select(KnowledgePolicy)
            .where(KnowledgePolicy.is_active == True)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_policies(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(KnowledgePolicy).where(KnowledgePolicy.is_active == True)
        )
        return result.scalar() or 0

    async def update_policy(self, policy_id: str, **kwargs) -> Optional[KnowledgePolicy]:
        policy = await self.get_policy(policy_id)
        if not policy:
            return None

        for key, value in kwargs.items():
            if hasattr(policy, key):
                setattr(policy, key, value)

        await self.session.commit()
        await self.session.refresh(policy)
        return policy

    async def delete_policy(self, policy_id: str) -> bool:
        policy = await self.get_policy(policy_id)
        if not policy:
            return False
        policy.is_active = False
        await self.session.commit()
        return True

    async def create_faq(self, faq: KnowledgeFAQ) -> KnowledgeFAQ:
        self.session.add(faq)
        await self.session.commit()
        await self.session.refresh(faq)
        return faq

    async def get_faq(self, question_id: str) -> Optional[KnowledgeFAQ]:
        result = await self.session.execute(
            select(KnowledgeFAQ).where(
                KnowledgeFAQ.question_id == question_id,
                KnowledgeFAQ.is_active == True
            )
        )
        return result.scalar_one_or_none()

    async def search_faqs(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[KnowledgeFAQ]:
        stmt = select(KnowledgeFAQ).where(KnowledgeFAQ.is_active == True)

        if category:
            stmt = stmt.where(KnowledgeFAQ.category == category)

        if tags:
            stmt = stmt.where(KnowledgeFAQ.tags.contains(tags))

        if keywords:
            stmt = stmt.where(KnowledgeFAQ.keywords.overlap(keywords))

        if query:
            query = _normalize_query(query)
            stmt = stmt.where(
                or_(
                    KnowledgeFAQ.question.ilike(f"%{query}%"),
                    KnowledgeFAQ.answer.ilike(f"%{query}%"),
                )
            )

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def increment_faq_usage(self, question_id: str):
        faq = await self.get_faq(question_id)
        if faq:
            faq.usage_count += 1
            await self.session.commit()

    async def count_faqs(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(KnowledgeFAQ).where(KnowledgeFAQ.is_active == True)
        )
        return result.scalar() or 0

    async def create_guide(self, guide: KnowledgeGuide) -> KnowledgeGuide:
        self.session.add(guide)
        await self.session.commit()
        await self.session.refresh(guide)
        return guide

    async def get_guide(self, guide_id: str) -> Optional[KnowledgeGuide]:
        result = await self.session.execute(
            select(KnowledgeGuide).where(
                KnowledgeGuide.guide_id == guide_id,
                KnowledgeGuide.is_active == True
            )
        )
        return result.scalar_one_or_none()

    async def search_guides(
        self,
        query: Optional[str] = None,
        guide_type: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[KnowledgeGuide]:
        stmt = select(KnowledgeGuide).where(KnowledgeGuide.is_active == True)

        if guide_type:
            stmt = stmt.where(KnowledgeGuide.guide_type == guide_type)

        if category:
            stmt = stmt.where(KnowledgeGuide.category == category)

        if tags:
            stmt = stmt.where(KnowledgeGuide.tags.contains(tags))

        if query:
            query = _normalize_query(query)
            stmt = stmt.where(
                or_(
                    KnowledgeGuide.title.ilike(f"%{query}%"),
                    KnowledgeGuide.content.ilike(f"%{query}%"),
                )
            )

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_guides(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(KnowledgeGuide).where(KnowledgeGuide.is_active == True)
        )
        return result.scalar() or 0

    async def create_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        self.session.add(doc)
        await self.session.commit()
        await self.session.refresh(doc)
        return doc

    async def search_documents(
        self,
        query: Optional[str] = None,
        doc_type: Optional[str] = None,
        document_type: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[KnowledgeDocument]:
        stmt = select(KnowledgeDocument).where(KnowledgeDocument.is_active == True)

        resolved_type = document_type or doc_type
        if resolved_type:
            stmt = stmt.where(KnowledgeDocument.document_type == resolved_type)

        if category:
            stmt = stmt.where(KnowledgeDocument.category == category)

        if tags:
            stmt = stmt.where(KnowledgeDocument.tags.contains(tags))

        if query:
            query = _normalize_query(query)
            stmt = stmt.where(
                or_(
                    KnowledgeDocument.title.ilike(f"%{query}%"),
                    KnowledgeDocument.content.ilike(f"%{query}%"),
                )
            )

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_documents(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.is_active == True)
        )
        return result.scalar() or 0

    async def get_categories_stats(self) -> List[dict]:
        categories = []

        policy_cats = await self.session.execute(
            select(KnowledgePolicy.category, func.count()).group_by(KnowledgePolicy.category)
        )
        for cat, count in policy_cats.all():
            categories.append({"type": "policy", "category": cat, "count": count})

        faq_cats = await self.session.execute(
            select(KnowledgeFAQ.category, func.count()).group_by(KnowledgeFAQ.category)
        )
        for cat, count in faq_cats.all():
            categories.append({"type": "faq", "category": cat, "count": count})

        guide_cats = await self.session.execute(
            select(KnowledgeGuide.category, func.count()).group_by(KnowledgeGuide.category)
        )
        for cat, count in guide_cats.all():
            categories.append({"type": "guide", "category": cat, "count": count})

        return categories