from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config import get_settings
from src.core.approval import approval_service
from src.core.kb_presentation import format_kb_response
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

        tool_execution_result = None
        is_tool_approval = bool((approval.metadata or {}).get("tool_name"))
        if is_tool_approval:
            from src.harness import get_tool_registry

            tool_registry = get_tool_registry()
            tool_name = approval.metadata.get("tool_name")
            tool_arguments = approval.metadata.get("tool_arguments") or {}
            tool_execution_result = await tool_registry.execute(
                tool_name,
                tool_arguments,
                approved=True,
                approval_context={
                    "request_id": approval.request_id,
                    "user_id": approval.user_id,
                    "display_name": approval.display_name,
                    "requested_via": approval.metadata.get("requested_via", "telegram_approval"),
                    "metadata": approval.metadata,
                },
            )

        # Send final response to user via Power Automate webhook
        # Only send if confidence >= 0.9, otherwise Telegram only
        from src.api.app import _auto_send_to_power_automate
        from src.core.schemas import OutputPayload

        output_payload = OutputPayload(
            answer=approval.ai_response,
            confidence=approval.confidence,
            status="approved",
            metadata={**approval.metadata, "approved_by": action.reviewed_by}
        )

        # Only auto-send when confidence >= 0.9 and this is not a tool approval
        if approval.confidence >= 0.9 and not is_tool_approval:
            try:
                await _auto_send_to_power_automate(output_payload)
            except Exception as e:
                logger.warning("Failed to send to Power Automate", error=str(e))

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
                extra_metadata={**(approval.metadata or {}), "approval_id": approval.id, "approved_by": action.reviewed_by, "tool_execution_result": tool_execution_result},
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
                    "model_name": approval.metadata.get("model_name", settings.primary_llm_model),
                    "approval_id": approval.id,
                    "request_id": approval.request_id,
                    "tool_execution_result": tool_execution_result,
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

        if settings.power_automate_webhook_url and not is_tool_approval:
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
            "message": "Action executed successfully" if not is_tool_approval else "Tool approval executed successfully",
            "tool_execution_result": tool_execution_result,
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
                    "model_name": approval.metadata.get("model_name", settings.primary_llm_model),
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
                "model_name": approval.metadata.get("model_name", settings.primary_llm_model),
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
    from src.knowledge.service import KnowledgeRetrievalService
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

            kb_response = format_kb_response(search_results.results if search_results else [], query=request.keywords, max_results=3)
            kb_context = kb_response["text"]

            if llm_client and llm_client.is_initialized:
                system_prompt = """Bạn là trợ lý IT Support.
Dựa vào kết quả tìm kiếm Knowledge Base, hãy viết câu trả lời thật dễ hiểu, theo checklist, có thể làm theo ngay.
BẮT BUỘC:
- tiếng Việt rõ ràng
- có phần 'Tóm tắt'
- có phần 'Làm theo'
- không bịa thêm thông tin ngoài KB
- nếu KB chưa đủ thì nói rõ cần bổ sung gì"""

                user_prompt = f"""Câu hỏi gốc: {approval.original_message}
Từ khóa tìm kiếm: {request.keywords}

KB đã chuẩn hoá:
{kb_context}

Hãy trả lời lại theo format dễ đọc, ngắn gọn, actionable."""

                response = await llm_client.complete(system_prompt, user_prompt)
                polished = (response.content or "").strip()
                new_response = polished or kb_context
                confidence = response.confidence if response.confidence else (0.45 if not search_results.results else 0.75)
            else:
                new_response = kb_context
                confidence = 0.45 if not search_results.results else 0.75

            return {
                "status": "success",
                "approval_id": approval_id,
                "keywords": request.keywords,
                "new_response": new_response,
                "kb_summary": kb_response["summary"],
                "kb_action_items": kb_response["action_items"],
                "kb_sources": kb_response["sources"],
                "kb_template_id": getattr(search_results, "template_id", ""),
                "kb_template_label": kb_response.get("template_label", "") or getattr(search_results, "template_label", ""),
                "kb_template_hint": kb_response.get("template_hint", ""),
                "confidence": round(confidence * 100, 1),
                "kb_results_count": len(search_results.results) if search_results else 0,
            }

    except Exception as e:
        logger.error("KB search retry failed", error=str(e), approval_id=approval_id)
        raise HTTPException(status_code=500, detail=f"KB search failed: {str(e)}")


# Telegram Webhook for callback queries
TG_ROUTER = APIRouter(prefix="/telegram", tags=["telegram"])


class TelegramCallbackUpdate(BaseModel):
    """Telegram callback query payload"""
    callback_query: dict


@TG_ROUTER.post("/webhook", response_model=dict, include_in_schema=True)
async def telegram_webhook(update: TelegramCallbackUpdate):
    """Handle Telegram callback queries from inline keyboard buttons."""
    return await handle_telegram_callback(update.callback_query)


async def handle_telegram_callback(callback: dict) -> dict:
    """Handle Telegram callback queries - extracted for direct calling."""
    import httpx
    
    # callback is now the direct parameter
    data = callback.get("data", "")
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    message_id = message.get("message_id")
    actor = callback.get("from", {}).get("first_name") or callback.get("from", {}).get("username") or "Unknown"
    
    # Parse callback data (format: "approval:action:approval_id")
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "approval":
        await _answer_callback_query(callback_id, "Invalid callback data")
        return {"status": "error"}
    
    action = parts[1]
    approval_id = parts[2]
    if action not in ("approve", "reject", "search_kb"):
        await _answer_callback_query(callback_id, "Unknown action")
        return {"status": "error"}
    
    # Call approval action via API
    try:
        import src.api as api_module
        async with api_module.async_session() as session:
            from src.api.routers.approvals import approve_or_reject, ApprovalActionRequest
            
            approval = await approval_service.get_approval(approval_id)
            if not approval:
                await _answer_callback_query(callback_id, "Approval not found", show_alert=True)
                return {"status": "error", "message": "Approval not found"}
            
            if action in ("approve", "reject"):
                action_req = ApprovalActionRequest(
                    action=action,
                    reviewed_by=actor,
                    comment=f"Via Telegram callback by {actor}"
                )
                await approve_or_reject(approval_id, action_req)
                
                status_text = "✅ Approved" if action == "approve" else "🚫 Rejected"
                await _answer_callback_query(callback_id, f"{status_text} successfully")
                await _edit_message(chat_id, message_id, f"{status_text} by {actor}\nApproval: {approval_id}")
                return {"status": "ok", "action": action, "approval_id": approval_id}
            
            elif action == "search_kb":
                import src.api as api_module
                
                # Extract keywords from the approval payload
                keywords = (
                    (approval.original_message or "")[:100]
                    or (approval.ai_response or "")[:100]
                    or approval.metadata.get("title", "")[:100]
                    or approval.metadata.get("description", "")[:100]
                    or "IT service"
                )
                
                # Search KB using the active supervisor instance
                supervisor = api_module.supervisor
                results = supervisor._search_knowledge_bm25(keywords, kb_type="knowledge")
                results_text = "\n\n".join([
                    f"• {r.get('title', 'Untitled')}\n  {r.get('content', '')[:200]}..."
                    for r in results[:5]
                ]) if results else "Không tìm thấy kết quả"
                
                # Update message with search results
                await _answer_callback_query(callback_id, "Tìm thấy kết quả KB", show_alert=True)
                await _edit_message(chat_id, message_id, 
                    f"🔍 Kết quả tìm kiếm cho '{keywords}'\n\n{results_text}\n\nApproval: {approval_id}")
                return {"status": "ok", "action": "search_kb", "approval_id": approval_id, "results_count": len(results)}
                
    except Exception as e:
        logger.error("Telegram callback failed", error=str(e))
        await _answer_callback_query(callback_id, f"Error: {str(e)}", show_alert=True)
        return {"status": "error", "message": str(e)}


async def _answer_callback_query(callback_id: str, text: str, show_alert: bool = False):
    """Answer Telegram callback query."""
    import httpx
    import src.api as api_module
    
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    
    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(endpoint, json={
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert
            })
    except Exception as e:
        logger.warning("Failed to answer callback query", error=str(e))


async def _edit_message(chat_id: str, message_id: int, text: str):
    """Edit Telegram message text."""
    import httpx
    
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    
    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(endpoint, json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text
            })
    except Exception as e:
        logger.warning("Failed to edit message", error=str(e))


async def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send message to Telegram user via bot."""
    import httpx
    
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.warning("Telegram bot token not configured")
        return False
    
    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": settings.telegram_parse_mode
            })
            response.raise_for_status()
            return True
    except Exception as e:
        logger.warning("Failed to send Telegram message", chat_id=chat_id, error=str(e))
        return False
