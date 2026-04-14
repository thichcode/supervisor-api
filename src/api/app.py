"""Canonical FastAPI application entrypoint.

This module is the long-term home of the application object. Existing import
paths are preserved via compatibility exports in ``src.api`` and ``src/api.py``.
"""

from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from pydantic import BaseModel

from src.config import get_settings
from src.core import InputPayload, OutputPayload
from src.core.thread_targeting import GroupChatTargetResolver
from src.core.teams_targeting import TeamsTargetResolver, extract_teams_signal
from src.core.schemas import (
    ChatRequest,
    ChatResponse,
    SystemQueryRequest,
    SystemQueryResponse,
    GuideDeliveryRequest,
    GuideDeliveryResponse,
    CallbackRequest,
    CaseInfo,
    ApprovalStatus,
    ApprovalActionRequest,
    ApprovalVoteRequest,
    ApprovalRequest,
    ApprovalRequestResponse,
    ApprovalListResponse,
)
from src.knowledge.schemas import (
    PolicyCreate,
    FAQCreate,
    GuideCreate,
    KnowledgeSearchRequest,
    DocumentCreate,
    BulkImportRequest,
    BulkImportResponse,
    FileProcessRequest,
    FileProcessResponse,
    BatchFileRequest,
    BatchFileResponse,
)
from src.core.logging_config import setup_logging, RequestLogger
from src.core.metrics import get_metrics, metrics
from src.core.sanitizer import sanitizer
from src.core.supervisor import Supervisor
from src.core import approval
from src.db import init_db, close_db, async_session
from src.llm import llm_client
from src.memory import redis_cache
from src.memory.service import MemoryService
from src.api.routers.admin import router as admin_router
from src.api.routers.approvals import router as approvals_router
from src.api.routers.chat import router as chat_router
from src.api.routers.feedback import router as feedback_router
from src.api.routers.health import router as health_router
from src.api.routers.harness import router as harness_router
from src.api.routers.knowledge import router as knowledge_router
from src.api.routers.knowledge_files import router as knowledge_files_router
from src.api.routers.monitoring import router as monitoring_router
from src.api.routers.n8n import router as n8n_router
from src.services.interaction_service import InteractionService
from datetime import datetime
from typing import Optional
import structlog

logger = structlog.get_logger()

settings = get_settings()
setup_logging()

limiter = Limiter(key_func=get_remote_address)
supervisor = Supervisor()
group_chat_resolver = GroupChatTargetResolver()
teams_target_resolver = TeamsTargetResolver()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    
    metrics.record_memory("startup", "success")
    yield
    logger.info("Shutting down Multi-Agent Supervisor System")
    
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
app.include_router(chat_router)
app.include_router(feedback_router)
app.include_router(n8n_router)
app.include_router(knowledge_router)
app.include_router(knowledge_files_router)
app.include_router(monitoring_router)
app.include_router(harness_router)




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
        request_logger.log_request_received(
            {"user": {"id": payload.user.id}, "conversation": {"thread_id": payload.conversation.thread_id}}
        )

        async with api_module.async_session() as session:
            memory_service = MemoryService(session, api_module.redis_cache)
            memory = await memory_service.retrieve(payload)
            result = await api_module.supervisor.process(payload, memory)
            await memory_service.commit(payload)
            elapsed_ms = int((time.time() - start_time) * 1000)
            metrics.record_request("POST", "/webhook/n8n", 200, elapsed_ms / 1000)
            metrics.record_decision(
                decision_type="subagents" if len(result.metadata.get("agents_used", [])) > 1 else "direct",
                intent=result.metadata.get("intent", "unknown"),
                risk_level=result.risk_level,
            )
            request_logger.log_response_sent(result.status, result.confidence, elapsed_ms)
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
        return {"status": "sent", "response_code": status_code}
    except httpx.HTTPError:
        metrics.record_error("power_automate", "output/power-automate")
        raise HTTPException(status_code=502, detail="Failed to reach Power Automate")


# NEW: Auto-send helper for integrated sending
async def _auto_send_to_power_automate(payload: OutputPayload) -> bool:
    """Auto-send response to Power Automate (called automatically after chat)"""
    if not settings.power_automate_webhook_url:
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
        return True
    except Exception as e:
        logger.error("Auto-send to Power Automate failed", 
                   request_id=getattr(payload, 'request_id', ''),
                   error=str(e))
        return False


@app.post("/system/query", response_model=SystemQueryResponse)
async def system_query(request: SystemQueryRequest):
    """Query system information (user data, case data, etc.)."""
    import src.api as api_module
    from src.memory.repository import MemoryRepository
    
    results = {}
    metadata = {"query_type": request.query_type}
    
    async with api_module.async_session() as session:
        repo = MemoryRepository(session)
        
        if request.query_type == "user_info" and request.user_id:
            user_profile = await repo.get_user_profile(request.user_id)
            if user_profile:
                results["user"] = {
                    "user_id": user_profile.user_id,
                    "display_name": user_profile.display_name,
                    "role": user_profile.role,
                    "team": user_profile.team,
                    "vip_flag": user_profile.vip_flag,
                    "communication_style": user_profile.communication_style,
                    "preferences": user_profile.preferences,
                }
                
                messages = await repo.get_recent_messages(request.user_id, limit=20)
                results["recent_threads"] = list(set([m.thread_id for m in messages]))
        
        elif request.query_type == "case_info" and request.case_id:
            case = await repo.get_case_memory(request.case_id)
            if case:
                results["case"] = {
                    "case_id": case.case_id,
                    "status": case.status,
                    "owner": case.owner,
                    "summary": case.summary,
                    "priority": case.priority,
                    "open_items": case.open_items,
                }
    
    return SystemQueryResponse(
        results=results,
        confidence=0.9 if results else 0.3,
        metadata=metadata,
    )


@app.post("/guide/deliver", response_model=GuideDeliveryResponse)
async def deliver_guide(request: GuideDeliveryRequest):
    """Deliver a guideline to user.
    
    If confidence < 90%, the guide delivery will be queued for approval.
    """
    import uuid
    from src.core.approval import approval_service
    
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
        import httpx
        
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


@app.post("/callback/send")
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






# =============================================================================
# Agent Harness Integration - Supervisor wrapped by Harness
# =============================================================================

from src.harness import (
    AgentHarness, 
    get_harness, 
    HarnessConfig,
    ToolRegistry,
    get_tool_registry,
    LifecycleHooks,
    ContextManager,
    Planner,
    Evaluator,
    HookType,
    SupervisorAgent,
    SupervisorAgentConfig,
    HarnessSupervisorBridge,
)

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

