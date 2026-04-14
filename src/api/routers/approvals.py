from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException

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
        await session.commit()

    return {
        "status": "success",
        "approval_id": approval_id,
        "vote": vote_request.vote,
        "message": "Vote recorded successfully",
    }
