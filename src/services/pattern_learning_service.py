"""
Pattern Learning Service
Stores approved Q&A patterns and matches new questions against them.
"""

import hashlib
import re
from typing import Optional

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.db.models import ResponsePattern

logger = structlog.get_logger()


class PatternLearningService:
    """
    Learns from approved responses and matches new questions against stored patterns.
    Uses simple text matching (can be enhanced with embeddings later).
    """

    SIMILARITY_THRESHOLD = 0.9  # 90% match = use stored answer

    def __init__(self, session: AsyncSession):
        self.session = session

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text

    def _compute_similarity(self, text1: str, text2: str) -> float:
        """
        Compute simple text similarity using word overlap.
        Returns 0.0 to 1.0
        """
        norm1 = self._normalize_text(text1)
        norm2 = self._normalize_text(text2)

        words1 = set(norm1.split())
        words2 = set(norm2.split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union) if union else 0.0

    def _compute_hash(self, text: str) -> str:
        """Compute hash for question deduplication"""
        normalized = self._normalize_text(text)
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    async def store_pattern(
        self,
        question: str,
        answer: str,
        user_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        team_id: Optional[str] = None,
        intent: Optional[str] = None,
        approved_by: Optional[str] = None,
        source_request_id: Optional[str] = None,
    ) -> ResponsePattern:
        """
        Store an approved Q&A pattern.
        If pattern for this question exists, update it.
        """
        question_hash = self._compute_hash(question)

        result = await self.session.execute(
            select(ResponsePattern).where(ResponsePattern.question_hash == question_hash)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.answer_text = answer
            existing.confidence_score = 1.0
            existing.approved_by = approved_by
            existing.updated_at = func.now()
            pattern = existing
            logger.info("pattern_updated", question_hash=question_hash, question=question[:50])
        else:
            pattern = ResponsePattern(
                question_hash=question_hash,
                question_text=question,
                answer_text=answer,
                user_id=user_id,
                thread_id=thread_id,
                team_id=team_id,
                intent=intent,
                confidence_score=1.0,
                approved_by=approved_by,
                source_request_id=source_request_id,
            )
            self.session.add(pattern)
            logger.info("pattern_stored", question_hash=question_hash, question=question[:50])

        await self.session.flush()
        return pattern

    async def find_similar_pattern(
        self,
        question: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> Optional[tuple[ResponsePattern, float]]:
        """
        Find a similar pattern for the given question.
        Returns (pattern, similarity_score) if found, None otherwise.
        """
        norm_question = self._normalize_text(question)
        if not norm_question:
            return None

        query = select(ResponsePattern).where(ResponsePattern.is_active == True)

        if team_id:
            query = query.where(
                or_(ResponsePattern.team_id == team_id, ResponsePattern.team_id == None)
            )

        if intent:
            query = query.where(
                or_(ResponsePattern.intent == intent, ResponsePattern.intent == None)
            )

        result = await self.session.execute(query)
        patterns = list(result.scalars().all())

        best_match = None
        best_similarity = 0.0

        for pattern in patterns:
            similarity = self._compute_similarity(question, pattern.question_text)

            if user_id and pattern.user_id and pattern.user_id != user_id:
                similarity *= 0.7

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = pattern

        if best_similarity >= self.SIMILARITY_THRESHOLD:
            logger.info(
                "pattern_found",
                similarity=best_similarity,
                threshold=self.SIMILARITY_THRESHOLD,
                question=question[:50],
            )
            return best_match, best_similarity

        return None

    async def increment_usage(self, pattern_id: int) -> None:
        """Increment usage count for a pattern"""
        await self.session.execute(
            update(ResponsePattern)
            .where(ResponsePattern.id == pattern_id)
            .values(
                usage_count=ResponsePattern.usage_count + 1,
                last_used_at=func.now(),
            )
        )

    async def get_top_patterns(self, limit: int = 10) -> list[ResponsePattern]:
        """Get most used patterns"""
        result = await self.session.execute(
            select(ResponsePattern)
            .where(ResponsePattern.is_active == True)
            .order_by(ResponsePattern.usage_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


from sqlalchemy.sql import func
