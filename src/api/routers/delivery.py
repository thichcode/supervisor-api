from datetime import datetime
import uuid

from fastapi import APIRouter, HTTPException

from src.config import get_settings
from src.core.schemas import CallbackRequest, GuideDeliveryRequest, GuideDeliveryResponse

router = APIRouter(tags=["delivery"])
settings = get_settings()


@router.post("/guide/deliver", response_model=GuideDeliveryResponse)
async def deliver_guide(request: GuideDeliveryRequest):
    """Deliver a guideline to user."""
    from src.core.approval import approval_service
    import httpx

    guide_id = request.guide_id
    guide_message = f"""📖 **Hướng dẫn: {request.guide_title}**

{request.guide_content}

---
*Đây là hướng dẫn được gửi từ hệ thống. Bạn có câu hỏi nào không?*"""
    confidence = 0.95
    needs_approval = await approval_service.needs_approval(confidence)

    if needs_approval:
        approval = await approval_service.create_approval(
            request_id=str(uuid.uuid4()),
            user_id=request.user_id,
            display_name=request.display_name,
            original_message=f"Request guide: {request.guide_title}",
            ai_response=guide_message,
            confidence=confidence,
            action_type="deliver_guide",
            metadata={
                "guide_id": guide_id,
                "guide_title": request.guide_title,
                "thread_id": request.thread_id,
            },
        )
        return GuideDeliveryResponse(
            status="pending_approval",
            guide_id=guide_id,
            delivered=False,
            message=f"Guide delivery queued for approval (confidence: {confidence:.0%})",
            metadata={"approval_id": approval.id},
        )

    if settings.power_automate_webhook_url:
        payload = {
            "request_id": str(uuid.uuid4()),
            "user_id": request.user_id,
            "message": guide_message,
            "message_type": "guideline",
            "guide_id": guide_id,
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
                return GuideDeliveryResponse(
                    status="sent",
                    guide_id=guide_id,
                    delivered=True,
                    message="Guide sent to user via webhook",
                    metadata={"webhook_response": response.status_code},
                )
        except Exception as e:
            return GuideDeliveryResponse(
                status="failed",
                guide_id=guide_id,
                delivered=False,
                message=f"Webhook failed: {str(e)}",
            )

    return GuideDeliveryResponse(
        status="pending",
        guide_id=guide_id,
        delivered=False,
        message="No webhook configured for guide delivery",
    )


@router.post("/callback/send")
async def send_callback(request: CallbackRequest):
    """Send async response back to user via callback URL."""
    import httpx

    if not request.callback_url:
        raise HTTPException(status_code=400, detail="callback_url is required")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=request.method,
                url=request.callback_url,
                json={
                    "request_id": request.original_request_id,
                    "user_id": request.user_id,
                    "message": request.message,
                    "timestamp": datetime.now().isoformat(),
                },
                timeout=settings.webhook_timeout,
            )
            response.raise_for_status()
            return {"status": "sent", "callback_response": response.status_code}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Callback failed: {str(e)}")
