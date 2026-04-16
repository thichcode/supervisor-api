from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.approval import approval_service
from src.core.schemas import (
    ApprovalActionRequest,
    ApprovalListResponse,
    ApprovalRequest,
    ApprovalRequestResponse,
    ApprovalStatus,
    ApprovalVoteRequest,
)
from src.services.interaction_service import InteractionService
from src.services.learning_events import record_learning_event

logger = structlog.get_logger()

router = APIRouter(prefix="/approvals", tags=["approvals"])
settings = get_settings()


@router.get("", response_model=ApprovalListResponse)
async def list_approvals(status: Optional[str] = None):
    filter_status = ApprovalStatus(status) if status else None
    approvals = await approval_service.get_all_approvals(filter_status)
    pending_count = await approval_service.get_pending_count()

    return ApprovalListResponse(
        approvals=[
            ApprovalRequestResponse(
                approval_id=a.id,
                request_id=a.request_id,
                status=a.status,
                message=a.ai_response[:200] + "..." if len(a.ai_response) > 200 else a.ai_response,
                confidence=a.confidence,
                threshold=a.threshold,
                created_at=a.created_at,
            )
            for a in approvals
        ],
        total=len(approvals),
        pending_count=pending_count,
    )


@router.get("/{approval_id}", response_model=ApprovalRequest)
async def get_approval(approval_id: str):
    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


@router.post("/{approval_id}/action")
async def approve_or_reject(approval_id: str, action: ApprovalActionRequest):
    import httpx
    import src.api as api_module

    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Approval already {approval.status}")

    if action.action == "approve":
        await approval_service.approve(approval_id, action.reviewed_by, action.comment)

        async with api_module.async_session() as session:
            interaction_service = InteractionService(session)
            await interaction_service.update_approval_record(
                request_id=approval.request_id,
                status="approved",
                approver_id=action.reviewed_by,
                action_note=action.comment,
            )
            await interaction_service.log_interaction(
                request_id=approval.request_id,
                thread_id=approval.metadata.get("thread_id", ""),
                user_id=approval.user_id,
                input_text=approval.original_message,
                output_text=approval.ai_response,
                intent=approval.metadata.get("intent"),
                risk_level=approval.metadata.get("risk_level"),
                confidence_score=approval.confidence,
                approval_required=True,
                approval_status="approved",
                outcome_status="approved",
                ticket_id=approval.metadata.get("ticket_id"),
                ticket_system=approval.metadata.get("ticket_system"),
                extra_metadata={**(approval.metadata or {}), "approval_id": approval.id, "approved_by": action.reviewed_by},
            )
            await record_learning_event(
                session,
                request_id=approval.request_id,
                user_id=approval.user_id,
                thread_id=approval.metadata.get("thread_id"),
                ticket_id=approval.metadata.get("ticket_id"),
                ticket_system=approval.metadata.get("ticket_system"),
                event_type="approval_decision",
                event_payload={
                    "approval_status": "approved",
                    "reviewed_by": action.reviewed_by,
                    "question": approval.original_message,
                    "answer": approval.ai_response,
                    "intent": approval.metadata.get("intent"),
                    "team_id": approval.metadata.get("team_id"),
                    "confidence_score": approval.confidence,
                    "threshold": approval.threshold,
                    "model_name": approval.metadata.get("model_name", settings.llm_model),
                    "approval_id": approval.id,
                    "request_id": approval.request_id,
                },
            )

            from src.services.pattern_learning_service import PatternLearningService
            pattern_service = PatternLearningService(session)
            await pattern_service.store_pattern(
                question=approval.original_message,
                answer=approval.ai_response,
                user_id=approval.user_id,
                thread_id=approval.metadata.get("thread_id"),
                team_id=approval.metadata.get("team_id"),
                intent=approval.metadata.get("intent"),
                approved_by=action.reviewed_by,
                source_request_id=approval.request_id,
            )

            await session.commit()

        if settings.power_automate_webhook_url:
            payload = {
                "request_id": approval.request_id,
                "approval_id": approval_id,
                "user_id": approval.user_id,
                "display_name": approval.display_name,
                "thread_id": approval.metadata.get("thread_id", ""),
                "message": approval.ai_response,
                "message_type": approval.action_type,
                "approved_by": action.reviewed_by,
                "timestamp": datetime.now().isoformat(),
            }
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        settings.power_automate_webhook_url,
                        json=payload,
                        timeout=settings.webhook_timeout,
                    )
                    response.raise_for_status()
            except httpx.HTTPError:
                pass

        return {
            "status": "approved",
            "approval_id": approval_id,
            "reviewed_by": action.reviewed_by,
            "comment": action.comment,
            "message": "Action executed successfully",
        }

    if action.action == "reject":
        await approval_service.reject(approval_id, action.reviewed_by, action.comment)

        import src.api as api_module
        async with api_module.async_session() as session:
            interaction_service = InteractionService(session)
            await interaction_service.update_approval_record(
                request_id=approval.request_id,
                status="rejected",
                approver_id=action.reviewed_by,
                action_note=action.comment,
            )
            await interaction_service.log_interaction(
                request_id=approval.request_id,
                thread_id=approval.metadata.get("thread_id", ""),
                user_id=approval.user_id,
                input_text=approval.original_message,
                output_text=approval.ai_response,
                intent=approval.metadata.get("intent"),
                risk_level=approval.metadata.get("risk_level"),
                confidence_score=approval.confidence,
                approval_required=True,
                approval_status="rejected",
                outcome_status="rejected",
                ticket_id=approval.metadata.get("ticket_id"),
                ticket_system=approval.metadata.get("ticket_system"),
                extra_metadata={**(approval.metadata or {}), "approval_id": approval.id, "rejected_by": action.reviewed_by},
            )
            await record_learning_event(
                session,
                request_id=approval.request_id,
                user_id=approval.user_id,
                thread_id=approval.metadata.get("thread_id"),
                ticket_id=approval.metadata.get("ticket_id"),
                ticket_system=approval.metadata.get("ticket_system"),
                event_type="approval_decision",
                event_payload={
                    "approval_status": "rejected",
                    "reviewed_by": action.reviewed_by,
                    "review_comment": action.comment,
                    "confidence_score": approval.confidence,
                    "threshold": approval.threshold,
                    "model_name": approval.metadata.get("model_name", settings.llm_model),
                    "approval_id": approval.id,
                    "request_id": approval.request_id,
                },
            )
            await session.commit()

        return {
            "status": "rejected",
            "approval_id": approval_id,
            "reviewed_by": action.reviewed_by,
            "comment": action.comment,
            "message": "Action rejected",
        }

    raise HTTPException(status_code=400, detail="Invalid action. Use 'approve' or 'reject'")


@router.post("/{approval_id}/vote")
async def vote_on_approval(approval_id: str, vote_request: ApprovalVoteRequest):
    import src.api as api_module

    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.status != ApprovalStatus.APPROVED:
        raise HTTPException(status_code=400, detail=f"Cannot vote on approval with status: {approval.status}")

    await approval_service.record_vote(
        approval_id=approval_id,
        vote=vote_request.vote,
        user_id=vote_request.user_id,
        feedback=vote_request.feedback,
    )

    async with api_module.async_session() as session:
        interaction_service = InteractionService(session)
        await interaction_service.record_vote_feedback(
            request_id=approval.request_id,
            thread_id=approval.metadata.get("thread_id"),
            user_id=vote_request.user_id,
            vote=vote_request.vote,
            feedback=vote_request.feedback,
            ticket_id=approval.metadata.get("ticket_id"),
            ticket_system=approval.metadata.get("ticket_system"),
        )
        await interaction_service.log_interaction(
            request_id=approval.request_id,
            thread_id=approval.metadata.get("thread_id", ""),
            user_id=approval.user_id,
            input_text=approval.original_message,
            output_text=approval.ai_response,
            intent=approval.metadata.get("intent"),
            risk_level=approval.metadata.get("risk_level"),
            confidence_score=approval.confidence,
            approval_required=True,
            approval_status=approval.status.value if hasattr(approval.status, "value") else str(approval.status),
            outcome_status="feedback_received",
            ticket_id=approval.metadata.get("ticket_id"),
            ticket_system=approval.metadata.get("ticket_system"),
            extra_metadata={**(approval.metadata or {}), "vote": vote_request.vote, "vote_feedback": vote_request.feedback},
        )
        await record_learning_event(
            session,
            request_id=approval.request_id,
            user_id=vote_request.user_id,
            thread_id=approval.metadata.get("thread_id"),
            ticket_id=approval.metadata.get("ticket_id"),
            ticket_system=approval.metadata.get("ticket_system"),
            event_type="approval_vote",
            event_payload={
                "vote": vote_request.vote,
                "feedback": vote_request.feedback,
                "approval_status": approval.status.value if hasattr(approval.status, "value") else str(approval.status),
                "confidence_score": approval.confidence,
                "threshold": approval.threshold,
                "model_name": approval.metadata.get("model_name", settings.llm_model),
                "approval_id": approval.id,
                "request_id": approval.request_id,
            },
        )
        await session.commit()

    return {
        "status": "success",
        "approval_id": approval_id,
        "vote": vote_request.vote,
        "message": "Vote recorded successfully",
    }


class RetryWithKBSearchRequest(BaseModel):
    keywords: str = Field(..., min_length=1, max_length=500)
    requested_by: str


@router.post("/{approval_id}/retry-with-kb")
async def retry_with_kb_search(approval_id: str, request: RetryWithKBSearchRequest):
    """Retry generating response using KB search with given keywords."""
    import src.api as api_module
    from src.services.knowledge_service import KnowledgeRetrievalService
    from src.llm import llm_client

    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    try:
        async with api_module.async_session() as session:
            kb_service = KnowledgeRetrievalService(session, llm_client)
            
            search_results = await kb_service.search_with_llm_enhancement(
                query=request.keywords,
                search_type="all",
                limit=5,
            )

            kb_context = ""
            if search_results and search_results.results:
                kb_context = "## Knowledge Base Results:\n"
                for i, result in enumerate(search_results.results[:3], 1):
                    kb_context += f"\n{i}. {result.title}\n{result.content[:500]}\n"

            if llm_client and llm_client.is_initialized:
                system_prompt = """Bạn là trợ lý IT Support.
Dựa vào kết quả tìm kiếm Knowledge Base, tạo câu trả lời phù hợp.
Trả lời ngắn gọn, hữu ích, bằng tiếng Việt."""

                user_prompt = f"""Câu hỏi gốc: {approval.original_message}
Từ khóa tìm kiếm: {request.keywords}
{kb_context}

Tạo câu trả lời mới dựa trên thông tin KB."""

                response = await llm_client.complete(system_prompt, user_prompt)
                new_response = response.content
                confidence = response.confidence if response.confidence else 0.85
            else:
                new_response = kb_context or "Không tìm thấy kết quả phù hợp."
                confidence = 0.7

            return {
                "status": "success",
                "approval_id": approval_id,
                "keywords": request.keywords,
                "new_response": new_response,
                "confidence": round(confidence * 100, 1),
                "kb_results_count": len(search_results.results) if search_results else 0,
            }

    except Exception as e:
        logger.error("KB search retry failed", error=str(e), approval_id=approval_id)
        raise HTTPException(status_code=500, detail=f"KB search failed: {str(e)}")
