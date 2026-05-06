from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import FeedbackCreateRequest, FeedbackType
from src.core.traffic_classification import classify_traffic_class
from src.db.models import ApprovalRequestRecord, InteractionLog
from src.services.feedback_service import FeedbackService


class InteractionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.feedback_service = FeedbackService(session)

    async def _auto_enqueue_kb_failure_suggestion(
        self,
        *,
        request_id: str,
        thread_id: str,
        user_id: str,
        input_text: str,
        output_text: str,
        confidence_score: float,
        kb_hit_count: int,
        traffic_class: str,
    ) -> None:
        """Auto-create a KB candidate when service-like request misses KB but answer confidence is high."""
        if kb_hit_count > 0:
            return
        if traffic_class != "service_like":
            return
        if confidence_score < 0.75:
            return
        if not input_text or not output_text:
            return

        from src.db.models import KnowledgeCandidate

        source_request_id = f"kb-search-failure:{request_id}"
        existing = await self.session.execute(
            select(KnowledgeCandidate).where(KnowledgeCandidate.source_request_id == source_request_id)
        )
        row = existing.scalar_one_or_none()
        review_note = (
            f"auto_from_interaction_miss confidence={confidence_score:.2f} "
            f"traffic_class={traffic_class}"
        )

        if row is None:
            row = KnowledgeCandidate(
                source_request_id=source_request_id,
                source_thread_id=thread_id,
                ticket_id=None,
                ticket_system="supervisor-api",
                extracted_title=input_text.strip()[:180],
                extracted_content=output_text.strip()[:4000],
                category="general",
                tags=["auto", "kb_miss", "service_like"],
                confidence_score=confidence_score,
                status="pending",
                reviewer_id=None,
                review_note=review_note,
            )
            self.session.add(row)
        else:
            row.extracted_title = input_text.strip()[:180]
            row.extracted_content = output_text.strip()[:4000]
            row.confidence_score = confidence_score
            row.status = "pending"
            row.review_note = review_note
            row.reviewed_at = None
            row.promoted_at = None

    async def _auto_record_kb_usage(
        self,
        *,
        kb_sources: list,
        confidence_score: float,
    ) -> None:
        """Record KB usage for confident answers to improve ranking signals."""
        if confidence_score < 0.75:
            return
        if not kb_sources:
            return

        from src.db.models import KnowledgeFAQ

        for source in kb_sources[:5]:
            if not isinstance(source, dict):
                continue
            kb_type = source.get("type", source.get("knowledge_type", ""))
            kb_id = source.get("id")
            if kb_type != "faq" or not kb_id:
                continue

            result = await self.session.execute(
                select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == kb_id)
            )
            faq = result.scalar_one_or_none()
            if faq:
                faq.usage_count = (faq.usage_count or 0) + 1
                self.session.add(faq)

    async def log_interaction(
        self,
        *,
        request_id: str,
        thread_id: str,
        user_id: str,
        input_text: str,
        output_text: str,
        intent: Optional[str] = None,
        risk_level: Optional[str] = None,
        confidence_score: float = 0.0,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        kb_sources: Optional[list] = None,
        approval_required: bool = False,
        approval_status: Optional[str] = None,
        processing_latency_ms: Optional[int] = None,
        outcome_status: Optional[str] = None,
        ticket_id: Optional[str] = None,
        ticket_system: Optional[str] = None,
        extra_metadata: Optional[dict] = None,
        traffic_class: Optional[str] = None,
    ) -> InteractionLog:
        normalized_traffic_class = traffic_class or classify_traffic_class(
            intent=intent,
            input_text=input_text,
            output_text=output_text,
            extra_metadata=extra_metadata,
        )
        metadata = dict(extra_metadata or {})
        metadata["traffic_class"] = normalized_traffic_class

        result = await self.session.execute(
            select(InteractionLog).where(InteractionLog.request_id == request_id)
        )
        log = result.scalar_one_or_none()
        if log is None:
            log = InteractionLog(request_id=request_id)
            self.session.add(log)

        log.thread_id = thread_id
        log.user_id = user_id
        log.ticket_id = ticket_id
        log.ticket_system = ticket_system
        log.input_text = input_text
        log.output_text = output_text
        log.intent = intent
        log.risk_level = risk_level
        log.confidence_score = confidence_score
        log.model_provider = model_provider
        log.model_name = model_name
        log.kb_sources = kb_sources or []
        log.kb_hit_count = len(log.kb_sources or [])
        log.traffic_class = normalized_traffic_class
        log.approval_required = approval_required
        log.approval_status = approval_status
        log.processing_latency_ms = processing_latency_ms
        log.outcome_status = outcome_status
        log.extra_metadata = metadata

        await self._auto_enqueue_kb_failure_suggestion(
            request_id=request_id,
            thread_id=thread_id,
            user_id=user_id,
            input_text=input_text,
            output_text=output_text,
            confidence_score=confidence_score,
            kb_hit_count=log.kb_hit_count or 0,
            traffic_class=normalized_traffic_class,
        )
        await self._auto_record_kb_usage(
            kb_sources=log.kb_sources or [],
            confidence_score=confidence_score,
        )

        await self.session.flush()
        return log

    async def create_approval_record(
        self,
        *,
        request_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        proposed_response: str,
        reason: Optional[str],
        risk_level: Optional[str],
        confidence_score: float,
        status: str = "pending",
        ticket_id: Optional[str] = None,
        ticket_system: Optional[str] = None,
    ) -> ApprovalRequestRecord:
        record = ApprovalRequestRecord(
            request_id=request_id,
            thread_id=thread_id,
            user_id=user_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system,
            proposed_response=proposed_response,
            reason=reason,
            risk_level=risk_level,
            confidence_score=confidence_score,
            status=status,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def update_approval_record(
        self,
        *,
        request_id: str,
        status: str,
        approver_id: Optional[str] = None,
        action_note: Optional[str] = None,
    ) -> None:
        result = await self.session.execute(
            select(ApprovalRequestRecord).where(ApprovalRequestRecord.request_id == request_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return
        record.status = status
        record.approver_id = approver_id
        record.action_note = action_note
        record.acted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def record_vote_feedback(
        self,
        *,
        request_id: str,
        thread_id: Optional[str],
        user_id: Optional[str],
        vote: str,
        feedback: Optional[str],
        ticket_id: Optional[str],
        ticket_system: Optional[str],
    ):
        label = {"agree": "accepted", "change": "edited", "skip": "skipped"}.get(vote, vote)
        payload = FeedbackCreateRequest(
            request_id=request_id,
            thread_id=thread_id,
            user_id=user_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system or "servicedesk_plus",
            feedback_type=FeedbackType.APPROVAL,
            feedback_label=label,
            feedback_text=feedback,
            reviewer_id=user_id,
            metadata={"vote": vote},
        )
        return await self.feedback_service.create_feedback(payload)
