"""Canonical FastAPI application entrypoint.

This module is the long-term home of the application object. Existing import
paths are preserved via compatibility exports in ``src.api`` and ``src/api.py``.
"""

from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.config import get_settings
from src.core import InputPayload, OutputPayload
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
    ApprovalRequest,
    ApprovalRequestResponse,
    ApprovalListResponse,
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
from datetime import datetime
from typing import Optional
import structlog

logger = structlog.get_logger()

settings = get_settings()
setup_logging()

limiter = Limiter(key_func=get_remote_address)
supervisor = Supervisor()


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
    
    metrics.record_memory("startup", "success")
    yield
    logger.info("Shutting down Multi-Agent Supervisor System")
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


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "supervisor",
        "llm_model": settings.llm_model,
        "version": settings.app_version,
    }


@app.get("/health/ready")
async def readiness_check():
    import src.api as api_module
    from sqlalchemy import text
    from src.memory.mempalace_adapter import MemPalaceAdapter

    checks = {"database": False, "redis": False, "llm": False, "external_memory": False}

    try:
        async with api_module.async_session() as session:
            await session.execute(text("SELECT 1"))
            checks["database"] = True
    except Exception:
        metrics.record_error("database_health", "health/ready")

    try:
        if await api_module.redis_cache.exists("health_check"):
            checks["redis"] = True
        else:
            await api_module.redis_cache.set("health_check", "ok", ttl=10)
            checks["redis"] = True
    except Exception:
        metrics.record_error("redis_health", "health/ready")

    try:
        if await api_module.llm_client.health_check():
            checks["llm"] = True
    except Exception:
        metrics.record_error("llm_health", "health/ready")

    try:
        provider = MemPalaceAdapter(top_k=settings.mempalace_top_k)
        checks["external_memory"] = await provider.health_check() or not provider.enabled
    except Exception:
        metrics.record_error("external_memory_health", "health/ready")

    return {"status": "ready" if all(checks.values()) else "degraded", "checks": checks}


@app.get("/metrics")
async def metrics_endpoint():
    return get_metrics()


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


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Direct user chat endpoint for real-time messaging with users.
    
    If confidence < 90%, the response will be queued for approval.
    """
    import uuid
    from src.core.schemas import UserInfo, ConversationInfo, MessageInfo, InputPayload
    from src.core.approval import approval_service
    
    request_id = str(uuid.uuid4())
    thread_id = request.thread_id or f"chat-{request.user_id}-{int(time.time())}"
    
    payload = InputPayload(
        request_id=request_id,
        source="direct_chat",
        timestamp=datetime.now().isoformat(),
        user=UserInfo(
            id=request.user_id,
            display_name=request.display_name,
            role=request.metadata.get("role"),
            team=request.metadata.get("team"),
            vip_flag=request.metadata.get("vip_flag", False),
        ),
        conversation=ConversationInfo(
            thread_id=thread_id,
            message_id=f"msg-{request_id}",
        ),
        case=CaseInfo(case_id=request.case_id) if request.case_id else None,
        message=MessageInfo(text=request.message),
    )
    
    import src.api as api_module
    
    async with api_module.async_session() as session:
        memory_service = MemoryService(session, api_module.redis_cache)
        memory = await memory_service.retrieve(payload)
        result = await api_module.supervisor.process(payload, memory)
        await memory_service.commit(payload)
    
    needs_approval = await approval_service.needs_approval(result.confidence)
    
    if needs_approval:
        approval = await approval_service.create_approval(
            request_id=request_id,
            user_id=request.user_id,
            display_name=request.display_name,
            original_message=request.message,
            ai_response=result.answer,
            confidence=result.confidence,
            action_type="send_message",
            metadata={
                "thread_id": thread_id,
                "case_id": request.case_id,
                "agents_used": result.metadata.get("agents_used", []),
                "intent": result.metadata.get("intent"),
            },
        )
        
        return ChatResponse(
            request_id=request_id,
            status="pending_approval",
            message=f"⚠️ Phản hồi AI (confidence: {result.confidence:.0%}) cần được duyệt trước khi gửi cho user.",
            message_type=request.message_type,
            confidence=result.confidence,
            metadata={
                **result.metadata,
                "approval_id": approval.id,
                "approval_required": True,
                "threshold": 0.9,
            },
        )
    
    return ChatResponse(
        request_id=request_id,
        status=result.status,
        message=result.answer,
        message_type=request.message_type,
        confidence=result.confidence,
        metadata=result.metadata,
    )


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


@app.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(status: Optional[str] = None):
    """List all approvals, optionally filtered by status."""
    from src.core.schemas import ApprovalStatus
    from src.core.approval import approval_service
    
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


@app.get("/approvals/{approval_id}", response_model=ApprovalRequest)
async def get_approval(approval_id: str):
    """Get details of a specific approval."""
    from src.core.approval import approval_service
    
    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    
    return approval


@app.post("/approvals/{approval_id}/action")
async def approve_or_reject(approval_id: str, action: ApprovalActionRequest):
    """Approve or reject an approval request.
    
    If approved, the action (send message, deliver guide, etc.) will be executed.
    If rejected, no action will be taken.
    """
    import httpx
    from src.core.approval import approval_service
    
    approval = await approval_service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Approval already {approval.status}")
    
    if action.action == "approve":
        result = await approval_service.approve(approval_id, action.reviewed_by, action.comment)
        
        if settings.power_automate_webhook_url:
            payload = {
                "request_id": approval.request_id,
                "user_id": approval.user_id,
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
            except httpx.HTTPError as e:
                logger.warning("Failed to send approved message", error=str(e))
        
        return {
            "status": "approved",
            "approval_id": approval_id,
            "reviewed_by": action.reviewed_by,
            "comment": action.comment,
            "message": "Action executed successfully",
        }
    
    elif action.action == "reject":
        await approval_service.reject(approval_id, action.reviewed_by, action.comment)
        
        return {
            "status": "rejected",
            "approval_id": approval_id,
            "reviewed_by": action.reviewed_by,
            "comment": action.comment,
            "message": "Action rejected",
        }
    
    raise HTTPException(status_code=400, detail="Invalid action. Use 'approve' or 'reject'")