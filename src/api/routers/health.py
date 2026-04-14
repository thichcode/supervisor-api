from datetime import datetime

from fastapi import APIRouter

from src.config import get_settings
from src.core.metrics import get_metrics, metrics

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "supervisor",
        "llm_model": settings.llm_model,
        "version": settings.app_version,
    }


@router.get("/health/ready")
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


@router.get("/health/detailed")
async def health_detailed():
    import src.api as api_module
    from sqlalchemy import select, func
    from src.db.models import Message, UserProfile, CaseMemory, ConversationSummary
    from src.knowledge import KnowledgeRetrievalService

    health = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {},
        "statistics": {},
    }

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

    try:
        async with api_module.async_session() as session:
            kb_service = KnowledgeRetrievalService(session)
            stats = await kb_service.get_knowledge_stats()
            health["components"]["knowledge_base"] = {"status": "healthy"}
            health["statistics"]["knowledge_base"] = stats
    except Exception as e:
        health["components"]["knowledge_base"] = {"status": "error", "error": str(e)}

    error_count = sum(1 for c in health["components"].values() if c.get("status") == "error")
    health["status"] = "healthy" if error_count == 0 else ("degraded" if error_count == 1 else "unhealthy")

    return health


@router.get("/metrics")
async def metrics_endpoint():
    return get_metrics()
