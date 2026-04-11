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
from pydantic import BaseModel

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


@app.get("/health/detailed")
async def health_detailed():
    """Detailed system health with comprehensive stats."""
    import src.api as api_module
    from sqlalchemy import text, select, func
    from src.db.models import Message, UserProfile, CaseMemory, ConversationSummary
    from src.knowledge import KnowledgeRetrievalService

    health = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {},
        "statistics": {},
    }

    # Database stats
    try:
        async with api_module.async_session() as session:
            msg_count = await session.scalar(select(func.count(Message.id)))
            user_count = await session.scalar(select(func.count(UserProfile.id)))
            case_count = await session.scalar(select(func.count(CaseMemory.id)))
            conv_count = await session.scalar(select(func.count(ConversationSummary.id)))

            health["components"]["database"] = {"status": "healthy", "connected": True}
            health["statistics"]["database"] = {
                "total_messages": msg_count or 0,
                "total_users": user_count or 0,
                "total_cases": case_count or 0,
                "total_conversations": conv_count or 0,
            }
    except Exception as e:
        health["components"]["database"] = {"status": "error", "error": str(e)}

    # Redis stats
    try:
        info = await api_module.redis_cache.get_info()
        keys = await api_module.redis_cache.get_keys_pattern("*")
        health["components"]["redis"] = {"status": "healthy", "connected": True}
        health["statistics"]["redis"] = {
            "total_keys": len(keys) if keys else 0,
            "info": info,
        }
    except Exception as e:
        health["components"]["redis"] = {"status": "error", "error": str(e)}

    # LLM stats
    try:
        llm = api_module.llm_client
        if llm.is_initialized:
            health["components"]["llm"] = {
                "status": "healthy",
                "model": llm.active_model,
                "provider": llm.active_provider,
                "cost_stats": llm.get_cost_stats(),
            }
        else:
            health["components"]["llm"] = {"status": "degraded", "reason": "not_initialized"}
    except Exception as e:
        health["components"]["llm"] = {"status": "error", "error": str(e)}

    # Knowledge Base stats
    try:
        async with api_module.async_session() as session:
            kb_service = KnowledgeRetrievalService(session)
            stats = await kb_service.get_knowledge_stats()
            health["components"]["knowledge_base"] = {"status": "healthy"}
            health["statistics"]["knowledge_base"] = stats
    except Exception as e:
        health["components"]["knowledge_base"] = {"status": "error", "error": str(e)}

    # Overall status
    error_count = sum(1 for c in health["components"].values() if c.get("status") == "error")
    health["status"] = "healthy" if error_count == 0 else ("degraded" if error_count == 1 else "unhealthy")

    return health


@app.get("/metrics/dashboard")
async def dashboard_metrics():
    """Dashboard metrics for monitoring."""
    import src.api as api_module
    from sqlalchemy import select, func, text
    from src.db.models import Message, UserProfile, CaseMemory, AuditLog, ApprovalRequest

    stats = {
        "timestamp": datetime.now().isoformat(),
        "conversations": {},
        "users": {},
        "cases": {},
        "approvals": {},
        "performance": {},
    }

    try:
        async with api_module.async_session() as session:
            # Conversation stats
            total_msgs = await session.scalar(select(func.count(Message.id)))
            today_msgs = await session.scalar(
                select(func.count(Message.id)).where(text("created_at > NOW() - INTERVAL '24 hours'"))
            )

            stats["conversations"] = {
                "total_messages": total_msgs or 0,
                "messages_last_24h": today_msgs or 0,
            }

            # User stats
            total_users = await session.scalar(select(func.count(UserProfile.id)))
            vip_users = await session.scalar(
                select(func.count(UserProfile.id)).where(UserProfile.vip_flag == True)
            )

            stats["users"] = {
                "total": total_users or 0,
                "vip": vip_users or 0,
            }

            # Case stats
            total_cases = await session.scalar(select(func.count(CaseMemory.id)))
            open_cases = await session.scalar(
                select(func.count(CaseMemory.id)).where(CaseMemory.status == "open")
            )

            stats["cases"] = {
                "total": total_cases or 0,
                "open": open_cases or 0,
            }

            # Approval stats
            pending_approvals = await session.scalar(
                select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "pending")
            )
            approved_approvals = await session.scalar(
                select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "approved")
            )
            rejected_approvals = await session.scalar(
                select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "rejected")
            )

            stats["approvals"] = {
                "pending": pending_approvals or 0,
                "approved": approved_approvals or 0,
                "rejected": rejected_approvals or 0,
            }

    except Exception as e:
        stats["error"] = str(e)

    return stats


@app.post("/alerts")
async def create_alert(
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    metadata: dict = None
):
    """Create an alert."""
    from src.db.models import Alert

    alert_id = f"alert-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    async with async_session() as session:
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            metadata=metadata or {},
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)

    metrics.record_counter("alerts_created", 1, {"type": alert_type, "severity": severity})

    return {"status": "created", "alert_id": alert_id}


@app.get("/alerts")
async def list_alerts(
    severity: str = None,
    status: str = None,
    limit: int = 50
):
    """List alerts."""
    from src.db.models import Alert
    from sqlalchemy import select

    async with async_session() as session:
        query = select(Alert).order_by(Alert.created_at.desc()).limit(limit)

        if severity:
            query = query.where(Alert.severity == severity)
        if status:
            query = query.where(Alert.status == status)

        result = await session.execute(query)
        alerts = result.scalars().all()

        return {
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "message": a.message,
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in alerts
            ],
            "total": len(alerts),
        }


@app.put("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, acknowledged_by: str):
    """Acknowledge an alert."""
    from src.db.models import Alert
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(Alert.alert_id == alert_id)
        )
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        alert.status = "acknowledged"
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.now()

        await session.commit()

    return {"status": "acknowledged", "alert_id": alert_id}


@app.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    """Delete an alert."""
    from src.db.models import Alert
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(Alert.alert_id == alert_id)
        )
        alert = result.scalar_one_or_none()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        await session.delete(alert)
        await session.commit()

    return {"status": "deleted", "alert_id": alert_id}


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
    
    # Auto-send to Power Automate after successful response (if configured)
    if result.status == "completed" and settings.power_automate_webhook_url:
        try:
            await _auto_send_to_power_automate(result)
        except Exception as e:
            logger.warning("Auto-send to Power Automate failed", error=str(e))

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


# =============================================================================
# n8n Action Endpoints - Execute actions on internal systems via n8n webhooks
# =============================================================================

from src.tools.n8n_connector import get_n8n_connector, ActionType, RiskLevel
from src.tools.n8n_tool import get_n8n_tool


@app.get("/n8n/actions")
async def list_n8n_actions(action_type: Optional[str] = None):
    """
    List all available n8n actions that can be executed via webhooks.
    
    - action_type: Filter by type ('query' or 'action')
    """
    tool = get_n8n_tool()
    return {"actions": json.loads(tool.list_available_actions(action_type))}


@app.post("/n8n/query")
async def execute_n8n_query(
    action_name: str,
    parameters: dict = {},
    user_id: str = "system",
):
    """
    Execute a read-only query via n8n webhook (no approval needed).
    
    Examples:
    - backup_status: Get backup system status
    - monitor_status: Get monitoring system status
    - server_status: Get server status
    """
    tool = get_n8n_tool()
    result = json.loads(tool.execute_query(action_name, parameters, user_id))
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Query failed"))
    
    return result


@app.post("/n8n/action/request")
async def request_n8n_action(
    action_name: str,
    parameters: dict = {},
    user_id: str = "unknown",
    user_display_name: str = "Unknown User",
):
    """
    Request an action that requires approval before execution.
    
    Returns an approval request ID that can be used to approve/reject.
    """
    tool = get_n8n_tool()
    result = json.loads(tool.request_action(
        action_name, parameters, user_id, user_display_name
    ))
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to request action"))
    
    return result


@app.get("/n8n/approvals/pending")
async def list_pending_n8n_approvals(
    system: Optional[str] = None,
    risk_level: Optional[str] = None,
):
    """
    List all pending n8n action approvals.
    
    - system: Filter by system (backup, monitoring, itsm, infrastructure, iam)
    - risk_level: Filter by risk level (low, medium, high, critical)
    """
    tool = get_n8n_tool()
    return json.loads(tool.get_pending_approvals(system, risk_level))


@app.post("/n8n/approvals/{request_id}/approve")
async def approve_n8n_action(
    request_id: str,
    approver_name: str = "Admin",
):
    """
    Approve a pending n8n action request.
    
    After approval, the action will be executed via n8n webhook.
    """
    tool = get_n8n_tool()
    result = json.loads(tool.approve_action(request_id, approver_name))
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to approve"))
    
    # Execute the approved action
    exec_result = json.loads(tool.execute_approved_action(request_id))
    
    return {
        "approval": result,
        "execution": exec_result,
    }


@app.post("/n8n/approvals/{request_id}/reject")
async def reject_n8n_action(
    request_id: str,
    rejector_name: str = "Admin",
    reason: str = "",
):
    """
    Reject a pending n8n action request.
    """
    tool = get_n8n_tool()
    result = json.loads(tool.reject_action(request_id, rejector_name, reason))
    
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to reject"))
    
    return result


@app.get("/n8n/approvals/{request_id}")
async def get_n8n_approval_status(request_id: str):
    """
    Get the status of an n8n action approval request.
    """
    connector = get_n8n_connector()
    
    if request_id not in connector.approval_store:
        raise HTTPException(status_code=404, detail="Request not found")
    
    req = connector.approval_store[request_id]
    
    return {
        "request_id": req.request_id,
        "action": req.action,
        "system": req.system,
        "risk_level": req.risk_level.value,
        "status": req.status,
        "requested_by": req.requested_by,
        "requested_at": req.requested_at.isoformat(),
        "approved_by": req.approved_by,
        "approved_at": req.approved_at.isoformat() if req.approved_at else None,
        "parameters": req.parameters,
        "result": req.result,
    }


@app.get("/knowledge/stats")
async def get_knowledge_stats():
    """Get knowledge base statistics."""
    from src.knowledge import KnowledgeRetrievalService
    
    async with async_session() as session:
        kb_service = KnowledgeRetrievalService(session)
        stats = await kb_service.get_knowledge_stats()
        return stats


@app.post("/knowledge/search")
async def search_knowledge(request: KnowledgeSearchRequest):
    """Search knowledge base (policies, FAQs, guides, documents)."""
    from src.knowledge import KnowledgeRetrievalService
    
    async with async_session() as session:
        kb_service = KnowledgeRetrievalService(session, None)
        results = await kb_service.search(
            query=request.query,
            search_type=request.search_type,
            category=request.category,
            tags=request.tags,
            limit=request.limit,
        )
        return results


@app.post("/knowledge/policies")
async def create_policy(policy: PolicyCreate):
    """Create a new policy."""
    from src.db.models import KnowledgePolicy
    
    async with async_session() as session:
        kb_policy = KnowledgePolicy(
            policy_id=policy.policy_id,
            title=policy.title,
            content=policy.content,
            category=policy.category,
            tags=policy.tags,
            version=policy.version,
        )
        session.add(kb_policy)
        await session.commit()
        await session.refresh(kb_policy)
        return {"status": "created", "policy_id": kb_policy.policy_id}


@app.get("/knowledge/policies")
async def list_policies(category: str = None, limit: int = 20):
    """List all policies, optionally filtered by category."""
    from src.knowledge import KnowledgeBaseRepository
    
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        policies = await repo.search_policies(category=category, limit=limit)
        return {
            "policies": [
                {
                    "policy_id": p.policy_id,
                    "title": p.title,
                    "content": p.content,
                    "category": p.category,
                    "tags": p.tags,
                    "version": p.version,
                }
                for p in policies
            ],
            "total": len(policies),
        }


@app.post("/knowledge/faqs")
async def create_faq(faq: FAQCreate):
    """Create a new FAQ."""
    from src.db.models import KnowledgeFAQ
    
    async with async_session() as session:
        kb_faq = KnowledgeFAQ(
            question_id=faq.question_id,
            question=faq.question,
            answer=faq.answer,
            category=faq.category,
            tags=faq.tags,
            keywords=faq.keywords,
        )
        session.add(kb_faq)
        await session.commit()
        await session.refresh(kb_faq)
        return {"status": "created", "question_id": kb_faq.question_id}


@app.get("/knowledge/faqs")
async def list_faqs(category: str = None, limit: int = 20):
    """List all FAQs, optionally filtered by category."""
    from src.knowledge import KnowledgeBaseRepository
    
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        faqs = await repo.search_faqs(category=category, limit=limit)
        return {
            "faqs": [
                {
                    "question_id": f.question_id,
                    "question": f.question,
                    "answer": f.answer,
                    "category": f.category,
                    "tags": f.tags,
                    "usage_count": f.usage_count,
                }
                for f in faqs
            ],
            "total": len(faqs),
        }


@app.post("/knowledge/guides")
async def create_guide(guide: GuideCreate):
    """Create a new guide."""
    from src.db.models import KnowledgeGuide
    
    async with async_session() as session:
        kb_guide = KnowledgeGuide(
            guide_id=guide.guide_id,
            title=guide.title,
            content=guide.content,
            guide_type=guide.guide_type,
            category=guide.category,
            tags=guide.tags,
            steps=guide.steps,
        )
        session.add(kb_guide)
        await session.commit()
        await session.refresh(kb_guide)
        return {"status": "created", "guide_id": kb_guide.guide_id}


@app.get("/knowledge/guides")
async def list_guides(guide_type: str = None, category: str = None, limit: int = 20):
    """List all guides, optionally filtered by type or category."""
    from src.knowledge import KnowledgeBaseRepository
    
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        guides = await repo.search_guides(guide_type=guide_type, category=category, limit=limit)
        return {
            "guides": [
                {
                    "guide_id": g.guide_id,
                    "title": g.title,
                    "content": g.content,
                    "guide_type": g.guide_type,
                    "category": g.category,
                    "tags": g.tags,
                    "steps_count": len(g.steps or []),
                }
                for g in guides
            ],
            "total": len(guides),
        }


@app.get("/knowledge/policies/{policy_id}")
async def get_policy(policy_id: str):
    """Get a specific policy by ID."""
    from src.db.models import KnowledgePolicy
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id)
        )
        policy = result.scalar_one_or_none()
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return {
            "policy_id": policy.policy_id,
            "title": policy.title,
            "content": policy.content,
            "category": policy.category,
            "tags": policy.tags,
            "version": policy.version,
            "created_at": policy.created_at.isoformat() if policy.created_at else None,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
        }


@app.put("/knowledge/policies/{policy_id}")
async def update_policy(policy_id: str, policy: PolicyCreate):
    """Update a policy."""
    from src.db.models import KnowledgePolicy
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id)
        )
        kb_policy = result.scalar_one_or_none()
        if not kb_policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        
        kb_policy.title = policy.title
        kb_policy.content = policy.content
        kb_policy.category = policy.category
        kb_policy.tags = policy.tags
        kb_policy.version = policy.version
        
        await session.commit()
        await session.refresh(kb_policy)
        return {"status": "updated", "policy_id": kb_policy.policy_id}


@app.delete("/knowledge/policies/{policy_id}")
async def delete_policy(policy_id: str):
    """Delete a policy."""
    from src.db.models import KnowledgePolicy
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgePolicy).where(KnowledgePolicy.policy_id == policy_id)
        )
        kb_policy = result.scalar_one_or_none()
        if not kb_policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        
        await session.delete(kb_policy)
        await session.commit()
        return {"status": "deleted", "policy_id": policy_id}


@app.get("/knowledge/faqs/{question_id}")
async def get_faq(question_id: str):
    """Get a specific FAQ by ID."""
    from src.db.models import KnowledgeFAQ
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id)
        )
        faq = result.scalar_one_or_none()
        if not faq:
            raise HTTPException(status_code=404, detail="FAQ not found")
        return {
            "question_id": faq.question_id,
            "question": faq.question,
            "answer": faq.answer,
            "category": faq.category,
            "tags": faq.tags,
            "keywords": faq.keywords,
            "usage_count": faq.usage_count,
        }


@app.put("/knowledge/faqs/{question_id}")
async def update_faq(question_id: str, faq: FAQCreate):
    """Update an FAQ."""
    from src.db.models import KnowledgeFAQ
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id)
        )
        kb_faq = result.scalar_one_or_none()
        if not kb_faq:
            raise HTTPException(status_code=404, detail="FAQ not found")
        
        kb_faq.question = faq.question
        kb_faq.answer = faq.answer
        kb_faq.category = faq.category
        kb_faq.tags = faq.tags
        kb_faq.keywords = faq.keywords
        
        await session.commit()
        await session.refresh(kb_faq)
        return {"status": "updated", "question_id": kb_faq.question_id}


@app.delete("/knowledge/faqs/{question_id}")
async def delete_faq(question_id: str):
    """Delete an FAQ."""
    from src.db.models import KnowledgeFAQ
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeFAQ).where(KnowledgeFAQ.question_id == question_id)
        )
        kb_faq = result.scalar_one_or_none()
        if not kb_faq:
            raise HTTPException(status_code=404, detail="FAQ not found")
        
        await session.delete(kb_faq)
        await session.commit()
        return {"status": "deleted", "question_id": question_id}


@app.get("/knowledge/guides/{guide_id}")
async def get_guide(guide_id: str):
    """Get a specific guide by ID."""
    from src.db.models import KnowledgeGuide
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id)
        )
        guide = result.scalar_one_or_none()
        if not guide:
            raise HTTPException(status_code=404, detail="Guide not found")
        return {
            "guide_id": guide.guide_id,
            "title": guide.title,
            "content": guide.content,
            "guide_type": guide.guide_type,
            "category": guide.category,
            "tags": guide.tags,
            "steps": guide.steps,
        }


@app.put("/knowledge/guides/{guide_id}")
async def update_guide(guide_id: str, guide: GuideCreate):
    """Update a guide."""
    from src.db.models import KnowledgeGuide
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id)
        )
        kb_guide = result.scalar_one_or_none()
        if not kb_guide:
            raise HTTPException(status_code=404, detail="Guide not found")
        
        kb_guide.title = guide.title
        kb_guide.content = guide.content
        kb_guide.guide_type = guide.guide_type
        kb_guide.category = guide.category
        kb_guide.tags = guide.tags
        kb_guide.steps = guide.steps
        
        await session.commit()
        await session.refresh(kb_guide)
        return {"status": "updated", "guide_id": kb_guide.guide_id}


@app.delete("/knowledge/guides/{guide_id}")
async def delete_guide(guide_id: str):
    """Delete a guide."""
    from src.db.models import KnowledgeGuide
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeGuide).where(KnowledgeGuide.guide_id == guide_id)
        )
        kb_guide = result.scalar_one_or_none()
        if not kb_guide:
            raise HTTPException(status_code=404, detail="Guide not found")
        
        await session.delete(kb_guide)
        await session.commit()
        return {"status": "deleted", "guide_id": guide_id}


@app.post("/knowledge/bulk-import")
async def bulk_import_knowledge(request: BulkImportRequest):
    """Bulk import policies, FAQs, guides, and documents."""
    from src.db.models import KnowledgePolicy, KnowledgeFAQ, KnowledgeGuide, KnowledgeDocument
    
    imported = {"policies": 0, "faqs": 0, "guides": 0, "documents": 0}
    errors = []
    
    async with async_session() as session:
        for policy in request.policies:
            try:
                kb_policy = KnowledgePolicy(
                    policy_id=policy.policy_id,
                    title=policy.title,
                    content=policy.content,
                    category=policy.category,
                    tags=policy.tags,
                    version=policy.version,
                )
                session.add(kb_policy)
                imported["policies"] += 1
            except Exception as e:
                errors.append({"type": "policy", "id": policy.policy_id, "error": str(e)})
        
        for faq in request.faqs:
            try:
                kb_faq = KnowledgeFAQ(
                    question_id=faq.question_id,
                    question=faq.question,
                    answer=faq.answer,
                    category=faq.category,
                    tags=faq.tags,
                    keywords=faq.keywords,
                )
                session.add(kb_faq)
                imported["faqs"] += 1
            except Exception as e:
                errors.append({"type": "faq", "id": faq.question_id, "error": str(e)})
        
        for guide in request.guides:
            try:
                kb_guide = KnowledgeGuide(
                    guide_id=guide.guide_id,
                    title=guide.title,
                    content=guide.content,
                    guide_type=guide.guide_type,
                    category=guide.category,
                    tags=guide.tags,
                    steps=guide.steps,
                )
                session.add(kb_guide)
                imported["guides"] += 1
            except Exception as e:
                errors.append({"type": "guide", "id": guide.guide_id, "error": str(e)})
        
        for doc in request.documents:
            try:
                kb_doc = KnowledgeDocument(
                    document_id=doc.document_id,
                    title=doc.title,
                    content=doc.content,
                    document_type=doc.document_type,
                    category=doc.category,
                    tags=doc.tags,
                    file_url=doc.file_url,
                )
                session.add(kb_doc)
                imported["documents"] += 1
            except Exception as e:
                errors.append({"type": "document", "id": doc.document_id, "error": str(e)})
        
        await session.commit()
    
    return {"status": "completed", "imported": imported, "errors": errors}


@app.post("/knowledge/search/enhanced")
async def search_knowledge_enhanced(request: KnowledgeSearchRequest):
    """Search knowledge base with LLM enhancement."""
    from src.llm import llm_client
    from src.knowledge import KnowledgeRetrievalService
    
    async with async_session() as session:
        if llm_client and llm_client.is_initialized:
            kb_service = KnowledgeRetrievalService(session, llm_client)
            results = await kb_service.search_with_llm_enhancement(
                query=request.query,
                search_type=request.search_type or "all",
                category=request.category,
                tags=request.tags,
                limit=request.limit,
            )
        else:
            kb_service = KnowledgeRetrievalService(session, None)
            results = await kb_service.search(
                query=request.query,
                search_type=request.search_type,
                category=request.category,
                tags=request.tags,
                limit=request.limit,
            )
        return results


@app.post("/knowledge/documents")
async def create_document(document: DocumentCreate):
    """Create a new document."""
    from src.db.models import KnowledgeDocument
    
    async with async_session() as session:
        kb_doc = KnowledgeDocument(
            document_id=document.document_id,
            title=document.title,
            content=document.content,
            document_type=document.document_type,
            category=document.category,
            tags=document.tags,
            file_url=document.file_url,
        )
        session.add(kb_doc)
        await session.commit()
        await session.refresh(kb_doc)
        return {"status": "created", "document_id": kb_doc.document_id}


@app.get("/knowledge/documents")
async def list_documents(document_type: str = None, category: str = None, limit: int = 20):
    """List all documents, optionally filtered by type or category."""
    from src.knowledge import KnowledgeBaseRepository
    
    async with async_session() as session:
        repo = KnowledgeBaseRepository(session)
        docs = await repo.search_documents(document_type=document_type, category=category, limit=limit)
        return {
            "documents": [
                {
                    "document_id": d.document_id,
                    "title": d.title,
                    "content": d.content,
                    "document_type": d.document_type,
                    "category": d.category,
                    "tags": d.tags,
                    "file_url": d.file_url,
                }
                for d in docs
            ],
            "total": len(docs),
        }


@app.get("/knowledge/documents/{document_id}")
async def get_document(document_id: str):
    """Get a specific document by ID."""
    from src.db.models import KnowledgeDocument
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "document_id": doc.document_id,
            "title": doc.title,
            "content": doc.content,
            "document_type": doc.document_type,
            "category": doc.category,
            "tags": doc.tags,
            "file_url": doc.file_url,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }


@app.put("/knowledge/documents/{document_id}")
async def update_document(document_id: str, document: DocumentCreate):
    """Update a document."""
    from src.db.models import KnowledgeDocument
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id)
        )
        kb_doc = result.scalar_one_or_none()
        if not kb_doc:
            raise HTTPException(status_code=404, detail="Document not found")
        
        kb_doc.title = document.title
        kb_doc.content = document.content
        kb_doc.document_type = document.document_type
        kb_doc.category = document.category
        kb_doc.tags = document.tags
        kb_doc.file_url = document.file_url
        
        await session.commit()
        await session.refresh(kb_doc)
        return {"status": "updated", "document_id": kb_doc.document_id}


@app.delete("/knowledge/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document."""
    from src.db.models import KnowledgeDocument
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.document_id == document_id)
        )
        kb_doc = result.scalar_one_or_none()
        if not kb_doc:
            raise HTTPException(status_code=404, detail="Document not found")
        
        await session.delete(kb_doc)
        await session.commit()
        return {"status": "deleted", "document_id": document_id}


# ========== User Management ==========

class UserCreateRequest(BaseModel):
    user_id: str
    display_name: str
    role: str = "employee"
    team: Optional[str] = None
    vip_flag: bool = False
    preferences: dict = {}


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    vip_flag: Optional[bool] = None
    preferences: Optional[dict] = None


@app.post("/admin/users")
async def create_user(request: UserCreateRequest):
    """Create a new user profile."""
    from src.db.models import UserProfile
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == request.user_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="User already exists")
        
        user = UserProfile(
            user_id=request.user_id,
            display_name=request.display_name,
            role=request.role,
            team=request.team,
            vip_flag=request.vip_flag,
            preferences=request.preferences,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        return {"status": "created", "user_id": user.user_id}


@app.get("/admin/users")
async def list_users(role: str = None, team: str = None, vip: bool = None, limit: int = 50):
    """List all users with optional filters."""
    from src.db.models import UserProfile
    from sqlalchemy import select
    
    async with async_session() as session:
        query = select(UserProfile).limit(limit)
        
        if role:
            query = query.where(UserProfile.role == role)
        if team:
            query = query.where(UserProfile.team == team)
        if vip is not None:
            query = query.where(UserProfile.vip_flag == vip)
        
        result = await session.execute(query)
        users = result.scalars().all()
        
        return {
            "users": [
                {
                    "user_id": u.user_id,
                    "display_name": u.display_name,
                    "role": u.role,
                    "team": u.team,
                    "vip_flag": u.vip_flag,
                }
                for u in users
            ],
            "total": len(users),
        }


@app.get("/admin/users/{user_id}")
async def get_user(user_id: str):
    """Get a specific user by ID."""
    from src.db.models import UserProfile
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "role": user.role,
            "team": user.team,
            "vip_flag": user.vip_flag,
            "preferences": user.preferences,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }


@app.put("/admin/users/{user_id}")
async def update_user(user_id: str, request: UserUpdateRequest):
    """Update a user profile."""
    from src.db.models import UserProfile
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if request.display_name is not None:
            user.display_name = request.display_name
        if request.role is not None:
            user.role = request.role
        if request.team is not None:
            user.team = request.team
        if request.vip_flag is not None:
            user.vip_flag = request.vip_flag
        if request.preferences is not None:
            user.preferences = request.preferences
        
        await session.commit()
        await session.refresh(user)
        
        return {"status": "updated", "user_id": user.user_id}


@app.delete("/admin/users/{user_id}")
async def delete_user(user_id: str):
    """Delete a user profile."""
    from src.db.models import UserProfile
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        await session.delete(user)
        await session.commit()
        
        return {"status": "deleted", "user_id": user_id}


# ========== Config Management ==========

class ConfigCreateRequest(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    is_sensitive: bool = False


class ConfigUpdateRequest(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None
    is_sensitive: Optional[bool] = None


@app.post("/admin/config")
async def create_config(request: ConfigCreateRequest):
    """Create a new config entry."""
    from src.db.models import Config
    
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Config).where(Config.key == request.key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Config key already exists")
        
        config = Config(
            key=request.key,
            value=request.value,
            description=request.description,
            is_sensitive=request.is_sensitive,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        
        return {"status": "created", "key": config.key}


@app.get("/admin/config")
async def list_configs(category: str = None, limit: int = 50):
    """List all configs with optional filter."""
    from src.db.models import Config
    from sqlalchemy import select
    
    async with async_session() as session:
        query = select(Config).limit(limit)
        
        if category:
            query = query.where(Config.category == category)
        
        result = await session.execute(query)
        configs = result.scalars().all()
        
        return {
            "configs": [
                {
                    "key": c.key,
                    "value": "***" if c.is_sensitive else c.value,
                    "description": c.description,
                    "category": c.category,
                    "is_sensitive": c.is_sensitive,
                }
                for c in configs
            ],
            "total": len(configs),
        }


@app.get("/admin/config/{key}")
async def get_config(key: str):
    """Get a specific config by key."""
    from src.db.models import Config
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(Config).where(Config.key == key)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        
        return {
            "key": config.key,
            "value": "***" if config.is_sensitive else config.value,
            "description": config.description,
            "category": config.category,
            "is_sensitive": config.is_sensitive,
        }


@app.put("/admin/config/{key}")
async def update_config(key: str, request: ConfigUpdateRequest):
    """Update a config."""
    from src.db.models import Config
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(Config).where(Config.key == key)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        
        if request.value is not None:
            config.value = request.value
        if request.description is not None:
            config.description = request.description
        if request.is_sensitive is not None:
            config.is_sensitive = request.is_sensitive
        
        await session.commit()
        await session.refresh(config)
        
        return {"status": "updated", "key": config.key}


@app.delete("/admin/config/{key}")
async def delete_config(key: str):
    """Delete a config."""
    from src.db.models import Config
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(
            select(Config).where(Config.key == key)
        )
        config = result.scalar_one_or_none()
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        
        await session.delete(config)
        await session.commit()
        
        return {"status": "deleted", "key": key}
# ============ File Processing Endpoints ============

@app.post("/knowledge/file/process", response_model=FileProcessResponse)
async def process_file(request: FileProcessRequest):
    """Process a file and extract content for knowledge base.
    
    Supported formats: PDF, Excel (xlsx/xls), CSV, JSON, Text, Images (OCR)
    """
    import time
    from pathlib import Path
    from src.tools.file_processor import get_file_processor
    
    start_time = time.time()
    errors = []
    
    # Get file processor
    processor = get_file_processor()
    if not processor:
        raise HTTPException(
            status_code=503,
            detail="File processor not available. Set ENABLE_FILE_PROCESSOR=true"
        )
    
    # Handle URL download
    file_path = request.file_path
    if request.file_url and not Path(request.file_path).exists():
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(request.file_url)
                response.raise_for_status()
                # Save to temp file
                temp_path = Path("/tmp") / f"upload_{int(time.time())}"
                temp_path.write_bytes(response.content)
                file_path = str(temp_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    
    # Check file exists
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    file_size = Path(file_path).stat().st_size
    
    try:
        # Extract content - process_file returns FileContent object
        file_content = processor.process_file(file_path)
        if not file_content or not file_content.content:
            errors.append("No content extracted from file")
            extracted_content = ""
        else:
            extracted_content = file_content.content
        
        # Truncate if too long
        if len(extracted_content) > 50000:
            extracted_content = extracted_content[:50000]
            errors.append("Content truncated to 50K characters")
        
        # Auto-classify if enabled
        knowledge_type = request.knowledge_type
        suggested_tags = request.tags.copy()
        
        if request.auto_classify and extracted_content:
            try:
                from src.llm import llm_client
                if llm_client and llm_client.is_initialized:
                    # Use LLM to classify
                    classification_prompt = f"""Phân loại tài liệu sau và trả về JSON:
{{
    "type": "policy|faq|guide|document",
    "category": "một từ mô tả category",
    "tags": ["tag1", "tag2", "tag3"]
}}

Nội dung:
{extracted_content[:3000]}
"""
                    response = await llm_client.complete(
                        "Bạn là trợ lý phân loại tài liệu. Phân tích và trả về JSON.",
                        classification_prompt,
                    )
                    
                    import re, json
                    match = re.search(r'\{[^{}]*"type"[^{}]*"category"[^{}]*"tags"[^{}]*\}', response.content, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        knowledge_type = data.get("type", request.knowledge_type)
                        suggested_tags = data.get("tags", request.tags)
            except Exception as e:
                errors.append(f"Auto-classification failed: {str(e)}")
        
        # Get metadata from file_content
        extracted_fields = {}
        if request.extract_metadata and file_content:
            extracted_fields = {
                "filename": file_content.filename,
                "content_type": file_content.content_type,
                "metadata": file_content.metadata or {},
            }
        
        processing_time_ms = int((time.time() - start_time) * 1000)
        
        return FileProcessResponse(
            status="success",
            file_name=Path(file_path).name,
            file_size=file_size,
            extracted_content=extracted_content,
            knowledge_type=knowledge_type,
            category=request.category,
            suggested_tags=suggested_tags,
            extracted_fields=extracted_fields,
            chunks_count=max(1, len(extracted_content) // 1000),
            embeddings_generated=False,  # Will be generated when imported
            processing_time_ms=processing_time_ms,
            errors=errors
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@app.post("/knowledge/file/import")
async def import_file_to_knowledge(request: FileProcessRequest):
    """Process a file and import directly to knowledge base."""
    import time
    from pathlib import Path
    from src.tools.file_processor import get_file_processor
    from src.db.models import KnowledgePolicy, KnowledgeFAQ, KnowledgeGuide, KnowledgeDocument
    import hashlib
    
    start_time = time.time()
    processor = get_file_processor()
    
    if not processor:
        raise HTTPException(
            status_code=503,
            detail="File processor not available. Set ENABLE_FILE_PROCESSOR=true"
        )
    
    file_path = request.file_path
    
    # Handle URL download
    if request.file_url and not Path(file_path).exists():
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(request.file_url)
                response.raise_for_status()
                temp_path = Path("/tmp") / f"upload_{int(time.time())}"
                temp_path.write_bytes(response.content)
                file_path = str(temp_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to download file: {str(e)}")
    
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    # Process file
    file_content = processor.process_file(file_path)
    if not file_content or not file_content.content:
        raise HTTPException(status_code=400, detail="No content extracted")
    
    # Get content
    content = file_content.content[:50000]
    
    # Auto-classify
    knowledge_type = request.knowledge_type
    tags = request.tags.copy()
    
    if request.auto_classify:
        try:
            from src.llm import llm_client
            if llm_client and llm_client.is_initialized:
                classification_prompt = f"""Phân loại tài liệu:
{content[:3000]}
Trả về JSON: {{"type": "policy|faq|guide|document", "category": "...", "tags": [...]}}"""
                response = await llm_client.complete(
                    "Phân loại tài liệu",
                    classification_prompt,
                )
                import re, json
                match = re.search(r'\{[^{}]*"type"[^{}]*"category"[^{}]*"tags"[^{}]*\}', response.content, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    knowledge_type = data.get("type", knowledge_type)
                    tags = data.get("tags", tags)
        except:
            pass
    
    # Generate ID
    file_id = hashlib.md5(f"{Path(file_path).name}{time.time()}".encode()).hexdigest()[:12]
    file_name = Path(file_path).name
    
    # Import based on type
    async with async_session() as session:
        try:
            if knowledge_type == "policy":
                kb = KnowledgePolicy(
                    policy_id=f"policy_{file_id}",
                    title=file_name,
                    content=content,
                    category=request.category,
                    tags=tags,
                    version="1.0",
                )
            elif knowledge_type == "faq":
                kb = KnowledgeFAQ(
                    question_id=f"faq_{file_id}",
                    question=file_name,
                    answer=content,
                    category=request.category,
                    tags=tags,
                )
            elif knowledge_type == "guide":
                kb = KnowledgeGuide(
                    guide_id=f"guide_{file_id}",
                    title=file_name,
                    content=content,
                    guide_type="document",
                    category=request.category,
                    tags=tags,
                )
            else:  # document
                kb = KnowledgeDocument(
                    document_id=f"doc_{file_id}",
                    title=file_name,
                    content=content,
                    document_type=Path(file_path).suffix,
                    category=request.category,
                    tags=tags,
                    file_url=request.file_url or file_path,
                )
            
            session.add(kb)
            await session.commit()
            
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            return {
                "status": "imported",
                "file_name": file_name,
                "knowledge_type": knowledge_type,
                "knowledge_id": kb.policy_id if hasattr(kb, 'policy_id') else (kb.question_id if hasattr(kb, 'question_id') else (kb.guide_id if hasattr(kb, 'guide_id') else kb.document_id)),
                "processing_time_ms": processing_time_ms,
            }
            
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")


@app.post("/knowledge/file/batch")
async def batch_process_files(request: BatchFileRequest):
    """Process multiple files in batch."""
    results = []
    successful = 0
    failed = 0
    
    for file_req in request.files:
        try:
            # Process each file
            from src.tools.file_processor import get_file_processor
            processor = get_file_processor()
            
            if not processor:
                results.append({
                    "file": file_req.file_path,
                    "status": "error",
                    "error": "File processor not available"
                })
                failed += 1
                continue
            
            file_content = processor.process_file(file_req.file_path)
            content_length = file_content.content_length if file_content else 0
            
            if request.import_to_knowledge_base:
                # Import directly
                result = await import_file_to_knowledge(file_req)
                results.append({
                    "file": file_req.file_path,
                    "status": "imported",
                    "knowledge_id": result.get("knowledge_id")
                })
            else:
                results.append({
                    "file": file_req.file_path,
                    "status": "processed",
                    "content_length": content_length
                })
            
            successful += 1
            
        except Exception as e:
            results.append({
                "file": file_req.file_path,
                "status": "error",
                "error": str(e)
            })
            failed += 1
    
    return BatchFileResponse(
        status="completed",
        total_files=len(request.files),
        successful=successful,
        failed=failed,
        results=results
    )


@app.get("/knowledge/file/formats")
async def get_supported_formats():
    """Get list of supported file formats."""
    return {
        "formats": [
            {"extension": ".pdf", "name": "PDF", "ocr_support": True},
            {"extension": ".xlsx", "name": "Excel", "ocr_support": False},
            {"extension": ".xls", "name": "Excel (Legacy)", "ocr_support": False},
            {"extension": ".csv", "name": "CSV", "ocr_support": False},
            {"extension": ".json", "name": "JSON", "ocr_support": False},
            {"extension": ".txt", "name": "Text", "ocr_support": False},
            {"extension": ".md", "name": "Markdown", "ocr_support": False},
            {"extension": ".docx", "name": "Word", "ocr_support": False},
            {"extension": ".jpg", "name": "JPEG Image", "ocr_support": True},
            {"extension": ".png", "name": "PNG Image", "ocr_support": True},
            {"extension": ".tiff", "name": "TIFF Image", "ocr_support": True},
        ],
        "max_file_size_mb": 50,
        "ocr_languages": ["eng", "vie", "chi_sim", "jpn", "kor"]
    }
