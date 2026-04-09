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
from src.core.logging_config import setup_logging, RequestLogger
from src.core.metrics import get_metrics, metrics
from src.core.sanitizer import sanitizer
from src.core.supervisor import Supervisor
from src.db import init_db, close_db, async_session
from src.llm import llm_client
from src.memory import redis_cache
from src.memory.service import MemoryService

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