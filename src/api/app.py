"""Canonical FastAPI application entrypoint.

This module is the long-term home of the application object. Existing import
paths are preserved via compatibility exports in ``src.api`` and ``src/api.py``.
"""

from contextlib import asynccontextmanager
import asyncio
import time
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.config import get_settings
from src.core import InputPayload, OutputPayload
from src.core.approval import approval_service
from src.core.logging_config import setup_logging, RequestLogger
from src.core.metrics import metrics
from src.core.sanitizer import sanitizer
from src.core.supervisor import Supervisor
from src.db import init_db, close_db, async_session
from src.harness import HarnessSupervisorBridge
from src.llm import llm_client
from src.memory import redis_cache
from src.memory.service import MemoryService
from src.services.interaction_service import InteractionService
from src.services.feedback_learning_worker import FeedbackReplayWorker
from src.api.routers.admin import router as admin_router
from src.api.routers.approvals import router as approvals_router
from src.api.routers.approvals import TG_ROUTER as tg_router
from src.api.routers.chat import router as chat_router
from src.api.routers.delivery import router as delivery_router
from src.api.routers.feedback import router as feedback_router
from src.api.routers.health import router as health_router
from src.api.routers.harness import router as harness_router
from src.api.routers.knowledge import router as knowledge_router
from src.api.routers.knowledge_files import router as knowledge_files_router
from src.api.routers.monitoring import router as monitoring_router
from src.api.routers.n8n import router as n8n_router
from src.api.routers.system import router as system_router

logger = structlog.get_logger()

settings = get_settings()
setup_logging()

limiter = Limiter(key_func=get_remote_address)
supervisor = Supervisor()
feedback_worker = FeedbackReplayWorker(session_factory=async_session, supervisor=supervisor)
feedback_worker_task: Optional[asyncio.Task] = None


def _chat_context_from_payload(payload: InputPayload) -> dict:
    conversation = payload.conversation
    chat_type = conversation.chat_type or ("group" if conversation.group_chat else "private")
    chat_scope = conversation.chat_scope or ("group" if conversation.group_chat else "dm")
    group_chat = conversation.group_chat if conversation.group_chat is not None else chat_type in {"group", "supergroup", "channel"}
    return {
        "platform": conversation.platform or payload.source,
        "chat_type": chat_type,
        "chat_scope": chat_scope,
        "group_chat": bool(group_chat),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global feedback_worker_task
    import structlog

    logger = structlog.get_logger()
    logger.info("Starting up Multi-Agent Supervisor System")
    await init_db()
    await redis_cache.connect()
    
    # LLM is optional - app works without it
    try:
        await llm_client.initialize()
        supervisor.set_llm(llm_client)
        logger.info("LLM initialized", model=settings.llm_model)
    except Exception as e:
        logger.warning("LLM initialization failed - running in fallback mode", error=str(e))
        # App continues without LLM - uses fallback responses
        supervisor.set_llm(None)
    
    # Initialize Agent Harness with Supervisor
    init_harness(supervisor)
    logger.info("Agent Harness initialized", harness_status="ready")
    
    # Replay any pending feedback and keep the learning loop warm
    await feedback_worker.replay_once()
    global feedback_worker_task
    feedback_worker_task = asyncio.create_task(feedback_worker.start(interval_seconds=60))
    logger.info("Feedback learning worker started", interval_seconds=60)
    
    metrics.record_memory("startup", "success")
    yield
    logger.info("Shutting down Multi-Agent Supervisor System")
    
    # Shutdown learning worker
    if feedback_worker_task:
        feedback_worker_task.cancel()
        try:
            await feedback_worker_task
        except asyncio.CancelledError:
            pass
        feedback_worker_task = None
    
    # Shutdown harness
    harness_bridge = get_harness_bridge()
    if harness_bridge:
        await harness_bridge.harness.shutdown()
    
    await llm_client.close()
    await redis_cache.close()
    await close_db()


app = FastAPI(
    title=settings.app_name,
    description="AI agent system with long-term memory for Microsoft Teams integration",
    version=settings.app_version,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration - never use "*" with credentials
cors_origins = settings.cors_allowed_origins
if settings.app_debug and "*" in cors_origins:
    import structlog
    logger = structlog.get_logger()
    logger.warning("CORS: debug mode with wildcard - restrict in production")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(approvals_router)
app.include_router(tg_router)
app.include_router(chat_router)
app.include_router(delivery_router)
app.include_router(feedback_router)
app.include_router(n8n_router)
app.include_router(knowledge_router)
app.include_router(knowledge_files_router)
app.include_router(monitoring_router)
app.include_router(harness_router)
app.include_router(system_router)


# Telegram callback endpoint (direct)
@app.post("/telegram-callback", tags=["telegram"])
async def telegram_callback(update: dict):
    """Handle Telegram callback queries directly."""
    from src.api.routers.approvals import handle_telegram_callback
    return await handle_telegram_callback(update)




@app.post("/webhook/n8n", response_model=OutputPayload)
@limiter.limit(f"{settings.rate_limit_requests}/{settings.rate_limit_window}seconds")
async def receive_webhook(
    request: Request,
    payload: InputPayload,
    x_webhook_secret: str = Header(None, alias="X-Webhook-Secret"),
):
    import src.api as api_module
    import structlog

    logger = structlog.get_logger()
    metrics.increment_active()
    request_logger = RequestLogger(payload.request_id)

    try:
        if x_webhook_secret and (
            not settings.webhook_input_secret or x_webhook_secret != settings.webhook_input_secret
        ):
            metrics.record_error("auth_failed", "webhook/n8n")
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

        original_text = payload.message.text
        is_valid, error_msg = sanitizer.validate_input(original_text)
        if not is_valid:
            metrics.record_error("input_validation", "webhook/n8n")
            raise HTTPException(status_code=400, detail=f"Invalid input: {error_msg}")

        payload.message.text = sanitizer.sanitize(original_text)
        start_time = time.time()
        chat_context = _chat_context_from_payload(payload)
        request_logger.log_request_received(
            {"user": {"id": payload.user.id}, "conversation": {"thread_id": payload.conversation.thread_id}}
        )

        async with api_module.async_session() as session:
            memory_service = MemoryService(session, api_module.redis_cache)
            interaction_service = InteractionService(session)
            memory = await memory_service.retrieve(payload)
            result = await api_module.supervisor.process(payload, memory)
            result.metadata = {**(result.metadata or {}), **chat_context}
            await memory_service.commit(
                payload,
                memory_snapshot=memory,
                assistant_text=result.answer,
                result_metadata=result.metadata or {},
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            metrics.record_request("POST", "/webhook/n8n", 200, elapsed_ms / 1000)
            metrics.record_decision(
                decision_type="subagents" if len(result.metadata.get("agents_used", [])) > 1 else "direct",
                intent=result.metadata.get("intent", "unknown"),
                risk_level=result.risk_level,
            )
            request_logger.log_response_sent(result.status, result.confidence, elapsed_ms)
            await interaction_service.log_interaction(
                request_id=payload.request_id,
                thread_id=payload.conversation.thread_id,
                user_id=payload.user.id,
                input_text=payload.message.text,
                output_text=result.answer,
                intent=result.metadata.get("intent") if result.metadata else None,
                risk_level=result.risk_level,
                confidence_score=result.confidence,
                model_provider=(result.metadata or {}).get("model_provider"),
                model_name=(result.metadata or {}).get("model_name") or settings.llm_model,
                kb_sources=(result.metadata or {}).get("kb_sources", []),
                approval_required=result.status == "needs_review",
                approval_status="pending" if result.status == "needs_review" else "not_needed",
                processing_latency_ms=result.metadata.get("processing_time_ms") if result.metadata else None,
                outcome_status=result.status,
                ticket_id=payload.case.ticket_id if payload.case else None,
                ticket_system=payload.case.ticket_system if payload.case else None,
                extra_metadata=result.metadata or {},
            )
            await session.commit()

            if result.status == "needs_review":
                approval = await approval_service.create_approval(
                    request_id=payload.request_id,
                    user_id=payload.user.id,
                    display_name=payload.user.display_name,
                    original_message=payload.message.text,
                    ai_response=result.answer,
                    confidence=result.confidence,
                    action_type="send_message",
                    metadata={
                        **(result.metadata or {}),
                        "thread_id": payload.conversation.thread_id,
                        "approval_required": True,
                        "threshold": 0.5,
                        **chat_context,
                    },
                )
                metrics.record_delivery_action("approval", "queued")
                result.status = "pending_approval"
                result.metadata = {
                    **(result.metadata or {}),
                    "approval_id": approval.id,
                    "approval_required": True,
                    "threshold": 0.5,
                }
            elif result.status == "completed" and settings.power_automate_webhook_url:
                await _auto_send_to_power_automate(result)

            return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Request processing failed", request_id=payload.request_id, error=str(e))
        metrics.record_error("processing", "webhook/n8n")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    finally:
        metrics.decrement_active()


@app.post("/output/power-automate")
async def send_to_power_automate(payload: OutputPayload):
    if not settings.power_automate_webhook_url:
        metrics.record_delivery_action("power_automate", "skipped")
        return {"status": "skipped", "message": "Power Automate webhook not configured"}

    import httpx
    from tenacity import retry, stop_after_attempt, wait_exponential

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=10))
    async def _send():
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.power_automate_webhook_url,
                json=payload.model_dump(),
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
        raise HTTPException(status_code=502, detail="Failed to reach Power Automate")
# NEW: Auto-send helper for integrated sending
async def _auto_send_to_power_automate(payload: OutputPayload) -> bool:
    """Auto-send response to Power Automate (called automatically after chat)"""
    if not settings.power_automate_webhook_url:
        metrics.record_delivery_action("power_automate", "skipped")
        return False

    import httpx
    from tenacity import retry, stop_after_attempt, wait_exponential

    # Format payload for Power Automate
    pa_payload = {
        "request_id": getattr(payload, 'request_id', ''),
        "message": payload.message.text if payload.message else "",
        "confidence": payload.confidence,
        "intent": payload.intent.intent.value if payload.intent else "unknown",
        "risk_level": payload.risk.risk_level.value if payload.risk else "unknown",
        "agents_used": payload.agents_used,
        "status": payload.status,
        "processing_time_ms": payload.processing_time_ms,
        "metadata": payload.metadata,
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
        logger.info("Auto-sent to Power Automate", 
                 request_id=getattr(payload, 'request_id', ''),
                 status_code=status_code)
        metrics.record_delivery_action("power_automate", "sent")
        return True
    except Exception as e:
        logger.error("Auto-send to Power Automate failed", 
                   request_id=getattr(payload, 'request_id', ''),
                   error=str(e))
        metrics.record_delivery_action("power_automate", "failed")
        return False
# =============================================================================
# Agent Harness Integration - Supervisor wrapped by Harness
# =============================================================================

# Global harness bridge (initialized in lifespan)
_harness_bridge: Optional[HarnessSupervisorBridge] = None


def init_harness(supervisor_instance):
    """Initialize harness with supervisor"""
    global _harness_bridge
    _harness_bridge = HarnessSupervisorBridge(supervisor_instance)
    logger.info("Harness initialized with Supervisor bridge")
    return _harness_bridge


def get_harness_bridge() -> HarnessSupervisorBridge:
    """Get the harness bridge instance"""
    return _harness_bridge
