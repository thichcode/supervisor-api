"""
Power Automate webhook service.
Sends responses to Power Automate for Teams delivery.
"""
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.core.metrics import metrics

logger = structlog.get_logger(__name__)
settings = get_settings()


async def send_to_power_automate(payload) -> dict:
    """Send a response payload to Power Automate webhook."""
    from src.core.schemas import OutputPayload

    if not settings.power_automate_webhook_url:
        metrics.record_delivery_action("power_automate", "skipped")
        return {"status": "skipped", "message": "Power Automate webhook not configured"}

    if isinstance(payload, OutputPayload):
        request_data = payload.model_dump()
    else:
        request_data = payload

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.power_automate_webhook_url,
                json=request_data,
                timeout=settings.webhook_timeout,
            )
            response.raise_for_status()
            return response.status_code

    try:
        status_code = await _send()
        metrics.record_request("POST", "/output/power-automate", status_code, 0)
        metrics.record_delivery_action("power_automate", "sent")
        return {"status": "sent", "response_code": status_code}
    except httpx.HTTPError:
        metrics.record_error("power_automate", "output/power-automate")
        metrics.record_delivery_action("power_automate", "failed")
        raise


async def auto_send_to_power_automate(payload) -> bool:
    """Auto-send response to Power Automate (called automatically after chat).

    Builds a richer payload with expanded metadata for Power Automate workflows.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not settings.power_automate_webhook_url:
        metrics.record_delivery_action("power_automate", "skipped")
        return False

    meta = payload.metadata or {}

    conversation_info = meta.get("conversation_summary") or meta.get("conversation_id", "")

    pa_payload = {
        "request_id": getattr(payload, "request_id", ""),
        "message": payload.message if payload.message else "",  # Now it's the text string directly
        "answer": payload.answer,
        "confidence": payload.confidence,
        "intent": meta.get("intent", "unknown"),
        "risk_level": payload.risk_level,
        "agents_used": meta.get("agents_used", []),
        "status": payload.status,
        "processing_time_ms": meta.get("processing_time_ms", 0),
        "conversation": {
            "thread_id": meta.get("conversation_id", ""),
            "message_id": meta.get("message_id", ""),
            "summary": meta.get("conversation_summary"),
            "unresolved_points": meta.get("unresolved_points", []),
        },
        "kb_hit": meta.get("kb_hit", False),
        "kb_guides": meta.get("kb_guides", []),
        "kb_sources": meta.get("kb_sources", []),
        "kb_template": meta.get("kb_template", {}),
        "knowledge_results": meta.get("knowledge_results", []),
        "itc_ticket": meta.get("itc_ticket", False),
        "itc_requestid": meta.get("itc_requestid"),
        "ticket_id": meta.get("ticket_id"),
        "approval_id": meta.get("approval_id"),
        "approval_required": meta.get("approval_required", False),
        "metadata": meta,
    }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.power_automate_webhook_url,
                json=pa_payload,
                timeout=settings.webhook_timeout,
            )
            response.raise_for_status()
            return response.status_code

    try:
        status_code = await _send()
        logger.info(
            "Auto-sent to Power Automate",
            request_id=getattr(payload, "request_id", ""),
            status_code=status_code,
        )
        metrics.record_delivery_action("power_automate", "sent")
        return True
    except httpx.HTTPError:
        logger.warning(
            "Failed to auto-send to Power Automate",
            request_id=getattr(payload, "request_id", ""),
        )
        metrics.record_error("power_automate", "auto-send")
        metrics.record_delivery_action("power_automate", "failed")
        return False
