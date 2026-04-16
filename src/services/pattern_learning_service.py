"""
Pattern Learning Service
Stores approved Q&A patterns and matches new questions against them using
semantic similarity with an optional transformer backend.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.db.models import ResponsePattern
from src.services.semantic_text import SemanticTextEncoder, cosine_similarity

logger = structlog.get_logger()


class PatternLearningService:
    """Learn approved Q&A pairs and retrieve the closest semantic match."""

    SIMILARITY_THRESHOLD = 0.78

    def __init__(self, session: AsyncSession, encoder: Optional[SemanticTextEncoder] = None):
        self.session = session
        self.encoder = encoder or SemanticTextEncoder()

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def _compute_hash(self, text: str) -> str:
        normalized = self._normalize_text(text)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

    def _token_set(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        return {token for token in normalized.split() if len(token) > 1}

    def _lexical_similarity(self, text_a: str, text_b: str) -> float:
        tokens_a = self._token_set(text_a)
        tokens_b = self._token_set(text_b)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    def _semantic_similarity(self, text_a: str, text_b: str) -> float:
        embedding_a = self.encoder.encode(text_a)
        embedding_b = self.encoder.encode(text_b)
        return cosine_similarity(embedding_a, embedding_b)

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
        """Store or update an approved Q&A pattern."""

        question_hash = self._compute_hash(question)
        question_embedding = self.encoder.encode(question)

        result = await self.session.execute(
            select(ResponsePattern).where(ResponsePattern.question_hash == question_hash)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.answer_text = answer
            existing.confidence_score = 1.0
            existing.approved_by = approved_by
            existing.source_request_id = source_request_id
            existing.embedding = question_embedding
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
                embedding=question_embedding,
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
        """Find the closest stored pattern for the given question."""

        normalized_question = self._normalize_text(question)
        if not normalized_question:
            return None

        query = select(ResponsePattern).where(ResponsePattern.is_active.is_(True))
        if team_id:
            query = query.where(or_(ResponsePattern.team_id == team_id, ResponsePattern.team_id.is_(None)))
        if intent:
            query = query.where(or_(ResponsePattern.intent == intent, ResponsePattern.intent.is_(None)))

        result = await self.session.execute(query)
        patterns = list(result.scalars().all())

        best_match: Optional[ResponsePattern] = None
        best_similarity = 0.0

        query_embedding = self.encoder.encode(question)
        for pattern in patterns:
            pattern_embedding = pattern.embedding or self.encoder.encode(pattern.question_text)
            semantic_score = cosine_similarity(query_embedding, pattern_embedding)
            lexical_score = self._lexical_similarity(question, pattern.question_text)
            similarity = (semantic_score * 0.8) + (lexical_score * 0.2)

            if user_id and pattern.user_id and pattern.user_id != user_id:
                similarity *= 0.85
            if team_id and pattern.team_id == team_id:
                similarity *= 1.05
            if intent and pattern.intent == intent:
                similarity *= 1.05

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = pattern

        if best_match and best_similarity >= self.SIMILARITY_THRESHOLD:
            logger.info(
                "pattern_found",
                similarity=best_similarity,
                threshold=self.SIMILARITY_THRESHOLD,
                question=question[:50],
            )
            return best_match, best_similarity

        return None

    async def increment_usage(self, pattern_id: int) -> None:
        """Increment usage count for a pattern."""

        await self.session.execute(
            update(ResponsePattern)
            .where(ResponsePattern.id == pattern_id)
            .values(
                usage_count=ResponsePattern.usage_count + 1,
                last_used_at=func.now(),
            )
        )

    async def get_top_patterns(self, limit: int = 10) -> list[ResponsePattern]:
        """Get the most used active patterns."""

        result = await self.session.execute(
            select(ResponsePattern)
            .where(ResponsePattern.is_active.is_(True))
            .order_by(ResponsePattern.usage_count.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
