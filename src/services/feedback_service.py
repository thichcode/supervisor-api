from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.schemas import FeedbackCreateRequest, FeedbackResponse, UserStyleProfileResponse
from src.db.models import FeedbackLog, InteractionLog, ResponseLearningEvent, UserStyleProfile
from src.services.learning_service import LearningService


class FeedbackService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.learning = LearningService(session)

    async def create_feedback(self, payload: FeedbackCreateRequest) -> FeedbackResponse:
        interaction = None
        if payload.request_id:
            result = await self.session.execute(
                select(InteractionLog).where(InteractionLog.request_id == payload.request_id)
            )
            interaction = result.scalar_one_or_none()

        user_id = payload.user_id or (interaction.user_id if interaction else None)
        thread_id = payload.thread_id or (interaction.thread_id if interaction else None)
        ticket_id = payload.ticket_id or (interaction.ticket_id if interaction else None)
        ticket_system = payload.ticket_system or (interaction.ticket_system if interaction else None)

        feedback = FeedbackLog(
            request_id=payload.request_id,
            thread_id=thread_id,
            user_id=user_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system,
            feedback_type=payload.feedback_type.value,
            feedback_score=payload.feedback_score,
            feedback_label=payload.feedback_label,
            feedback_text=payload.feedback_text,
            edited_output_text=payload.edited_output_text,
            reviewer_id=payload.reviewer_id,
            extra_metadata=payload.metadata,
        )
        self.session.add(feedback)
        await self.session.flush()

        learning_event = ResponseLearningEvent(
            request_id=payload.request_id,
            user_id=user_id,
            thread_id=thread_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system,
            event_type="feedback_received",
            event_payload={
                "feedback_type": payload.feedback_type.value,
                "feedback_label": payload.feedback_label,
                "feedback_score": payload.feedback_score,
                "has_human_edit": bool(payload.edited_output_text),
                "feedback_text": payload.feedback_text,
                "edited_output_text": payload.edited_output_text,
                "reviewer_id": payload.reviewer_id,
                "user_id": user_id,
                "vote": (payload.metadata or {}).get("vote"),
            },
        )
        self.session.add(learning_event)

        source_text = payload.edited_output_text or payload.feedback_text
        if user_id and source_text:
            signals = self.learning.infer_style_signals(
                text=source_text,
                source="human_edit" if payload.edited_output_text else "feedback",
            )
            await self.learning.add_signals(
                user_id=user_id,
                request_id=payload.request_id,
                signals=signals,
                evidence={"feedback_id": feedback.id, "ticket_id": ticket_id},
            )
            await self.learning.recompute_profile(user_id)

        if interaction and payload.feedback_label:
            interaction.extra_metadata = {
                **(interaction.extra_metadata or {}),
                "latest_feedback_label": payload.feedback_label,
                "latest_feedback_type": payload.feedback_type.value,
            }

        await self.session.commit()

        return FeedbackResponse(
            id=feedback.id,
            request_id=payload.request_id,
            feedback_type=payload.feedback_type,
            feedback_label=payload.feedback_label,
            stored=True,
            learning_event_created=True,
        )

    async def get_user_style_profile(self, user_id: str) -> UserStyleProfileResponse | None:
        result = await self.session.execute(
            select(UserStyleProfile).where(UserStyleProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            return None
        return UserStyleProfileResponse(
            user_id=profile.user_id,
            preferred_tone=profile.preferred_tone,
            preferred_verbosity=profile.preferred_verbosity,
            preferred_format=profile.preferred_format,
            preferred_language=profile.preferred_language,
            response_persona_hint=profile.response_persona_hint,
            confidence_score=profile.confidence_score or 0.0,
            sample_count=profile.sample_count or 0,
            updated_at=profile.updated_at,
        )
