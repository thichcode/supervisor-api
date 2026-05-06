"""
KB Promotion Service

Automatically promotes high-quality successful responses into KB entries.

How it works:
1. When a response has high confidence (>= 0.85) + KB hit → increment usage_count
2. When usage_count hits threshold → auto-promote to KB with higher priority
3. When response has no KB hit but high confidence + positive feedback → create new KB draft

This ensures the KB grows organically from real user interactions.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4
import structlog

logger = structlog.get_logger(__name__)

# Usage count thresholds for KB promotion
PROMOTION_THRESHOLDS = {
    "faq": 5,       # FAQ: promote after 5 successful uses
    "policy": 3,    # Policy: promote after 3 uses
    "guide": 2,     # Guide: promote after 2 uses
    "document": 5,  # Document: promote after 5 uses
}


class KBPromotionService:
    """Promote successful response patterns to KB entries."""
    
    def __init__(self, session_factory, llm=None):
        self.session_factory = session_factory
        self.llm = llm
    
    async def record_kb_usage(self, kb_source: dict) -> dict:
        """Record KB usage and auto-promote if threshold reached.
        
        Args:
            kb_source: KB source dict with id, type, title
            
        Returns:
            Dict with promotion status
        """
        kb_type = kb_source.get("type", kb_source.get("knowledge_type", "faq"))
        kb_id = kb_source.get("id")
        
        if not kb_id:
            return {"promoted": False, "reason": "no_id"}
        
        threshold = PROMOTION_THRESHOLDS.get(kb_type, 5)
        
        async with self.session_factory() as session:
            try:
                from src.db.models import KnowledgeFAQ
                from sqlalchemy import select
                
                promoted = False
                
                if kb_type == "faq":
                    result = await session.execute(
                        select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == kb_id)
                    )
                    entry = result.scalar_one_or_none()
                    if entry:
                        entry.usage_count = (entry.usage_count or 0) + 1
                        if entry.usage_count >= threshold:
                            promoted = True
                        session.add(entry)
                
                # Other KB types currently do not have usage_count columns.
                
                await session.commit()
                
                if promoted:
                    logger.info(
                        "kb_promoted",
                        kb_type=kb_type,
                        kb_id=kb_id,
                        threshold=threshold,
                    )
                
                return {
                    "promoted": promoted,
                    "kb_type": kb_type,
                    "usage_count": getattr(entry, "usage_count", 0) if 'entry' in locals() else 0,
                    "threshold": threshold,
                }
                
            except Exception as e:
                logger.warning("kb_promotion_failed", kb_type=kb_type, kb_id=kb_id, error=str(e))
                return {"promoted": False, "reason": str(e)}
    
    async def promote_response_to_kb(
        self,
        question: str,
        answer: str,
        confidence: float,
        source: str = "successful_response",
        user_id: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Promote a successful response to a KB FAQ entry.
        
        Called when:
        - Response has high confidence (>= 0.85) and positive feedback
        - Response has no KB hit but solved user's problem
        
        Args:
            question: Original user question
            answer: Successful response text
            confidence: Response confidence score
            source: Source of the promotion
            user_id: User who received the response
            category: KB category (auto-detected if None)
            tags: KB tags (auto-detected if None)
            
        Returns:
            Dict with creation status
        """
        if confidence < 0.85:
            return {"created": False, "reason": "confidence_too_low"}
        
        if not question or not answer:
            return {"created": False, "reason": "empty_question_or_answer"}
        
        async with self.session_factory() as session:
            try:
                from src.db.models import KnowledgeFAQ
                from sqlalchemy import select
                
                normalized_question = question.strip()
                # Check for duplicate
                result = await session.execute(
                    select(KnowledgeFAQ).where(
                        KnowledgeFAQ.question.ilike(f"%{normalized_question[:100]}%"),
                        KnowledgeFAQ.is_active.is_(True),
                    ).limit(1)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    # Update existing with better answer
                    existing.answer = answer
                    existing.usage_count = (existing.usage_count or 0) + 1
                    session.add(existing)
                    await session.commit()
                    logger.info("faq_updated_from_response", question=question[:50])
                    return {"created": True, "updated": True, "id": existing.question_id}
                
                # Infer category if not provided
                if not category:
                    category = self._infer_category(question, answer)
                
                # Infer tags if not provided
                if not tags:
                    tags = self._infer_tags(question, answer)
                
                # Create new FAQ entry
                faq = KnowledgeFAQ(
                    question_id=f"auto-{uuid4().hex[:12]}",
                    question=question[:500],
                    answer=answer[:2000],
                    category=category or "general",
                    tags=tags or [],
                    keywords=self._extract_keywords(question),
                    is_active=True,
                    usage_count=1,
                )
                session.add(faq)
                await session.commit()
                
                logger.info(
                    "faq_created_from_response",
                    question=question[:50],
                    category=category,
                )
                
                return {"created": True, "updated": False, "id": faq.question_id}
                
            except Exception as e:
                logger.warning("faq_promotion_failed", error=str(e))
                return {"created": False, "reason": str(e)}
    
    def _infer_category(self, question: str, answer: str) -> str:
        """Infer KB category from question and answer."""
        text = (question + " " + answer).lower()
        
        category_map = [
            ("network", ["vpn", "network", "mạng", "wifi", "internet", "kết nối", "remote"]),
            ("software", ["software", "phần mềm", "app", "application", "cài đặt"]),
            ("hardware", ["hardware", "phần cứng", "máy in", "laptop"]),
            ("email", ["email", "mail", "outlook", "thư", "gmail"]),
            ("account", ["account", "tài khoản", "password", "mật khẩu", "login"]),
            ("security", ["security", "bảo mật", "virus", "firewall"]),
        ]
        
        for category, keywords in category_map:
            if any(kw in text for kw in keywords):
                return category
        
        return "general"
    
    def _infer_tags(self, question: str, answer: str) -> list[str]:
        """Infer tags from question and answer."""
        import re
        text = (question + " " + answer).lower()
        words = re.findall(r'\w+', text)
        stopwords = {"the", "a", "an", "cho", "của", "với", "một", "các", "được", "không", "là", "và", "có", "trong"}
        return list(set(w for w in words if w not in stopwords and len(w) > 3))[:5]
    
    def _extract_keywords(self, question: str) -> list[str]:
        """Extract keywords from question."""
        import re
        words = re.findall(r'\w+', question.lower())
        stopwords = {"the", "a", "an", "cho", "của", "với", "một", "các", "được", "không", "là", "và", "có", "trong", "giúp", "mình", "ơi", "ạ"}
        return [w for w in words if w not in stopwords and len(w) > 2][:10]


__all__ = ["KBPromotionService"]