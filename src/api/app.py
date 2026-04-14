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
from src.api.routers.approvals import router as approvals_router
from src.api.routers.chat import router as chat_router
from src.api.routers.feedback import router as feedback_router
from src.api.routers.health import router as health_router
from src.api.routers.knowledge import router as knowledge_router
from src.api.routers.knowledge_files import router as knowledge_files_router
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
app.include_router(approvals_router)
app.include_router(chat_router)
app.include_router(feedback_router)
app.include_router(n8n_router)
app.include_router(knowledge_router)
app.include_router(knowledge_files_router)


@app.get("/metrics/dashboard")
async def dashboard_metrics():
    """Dashboard metrics for monitoring - FULL VERSION with all analytics."""
    import src.api as api_module
    from src.core.approval import approval_service
    
    stats = {
        "timestamp": datetime.now().isoformat(),
        "overview": {},
        "performance": {},
        "ai_quality": {},
        "user_satisfaction": {},
        "approvals": {},
    }

    try:
        # Get approvals from Redis-based approval service
        all_approvals = await approval_service.get_all_approvals()
        
        pending_count = sum(1 for a in all_approvals if a.status == "pending")
        approved_count = sum(1 for a in all_approvals if a.status == "approved")
        rejected_count = sum(1 for a in all_approvals if a.status == "rejected")
        
        # AI Quality metrics
        confidences = [a.confidence for a in all_approvals if a.confidence]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        auto_send = sum(1 for a in all_approvals if a.confidence >= 0.9)
        need_approval = sum(1 for a in all_approvals if a.confidence < 0.9)
        
        # User satisfaction (votes)
        votes_agree = sum(1 for a in all_approvals if a.vote == "agree")
        votes_change = sum(1 for a in all_approvals if a.vote == "change")
        votes_skip = sum(1 for a in all_approvals if a.vote == "skip")
        total_voted = votes_agree + votes_change + votes_skip
        
        stats["overview"] = {
            "total_approvals": len(all_approvals),
            "auto_sent": auto_send,
            "need_manual_review": need_approval,
            "auto_send_rate": round(auto_send / len(all_approvals) * 100, 1) if all_approvals else 0,
        }
        
        stats["approvals"] = {
            "pending": pending_count,
            "approved": approved_count,
            "rejected": rejected_count,
            "approve_rate": round(approved_count / (approved_count + rejected_count) * 100, 1) 
                if (approved_count + rejected_count) > 0 else 0,
        }
        
        stats["ai_quality"] = {
            "avg_confidence": round(avg_confidence * 100, 1),
            "high_confidence_count": sum(1 for c in confidences if c >= 0.9),
            "low_confidence_count": sum(1 for c in confidences if c < 0.9),
            "auto_send_count": auto_send,
            "approval_needed_count": need_approval,
        }
        
        stats["user_satisfaction"] = {
            "total_votes": total_voted,
            "agree": votes_agree,
            "change": votes_change,
            "skip": votes_skip,
            "satisfaction_rate": round(votes_agree / total_voted * 100, 1) if total_voted > 0 else 0,
        }
        
        # Performance - simplified (approvals based)
        stats["performance"] = {
            "total_approvals": len(all_approvals),
            "avg_processing_time_sec": "N/A",
        }

    except Exception as e:
        stats["error"] = str(e)

    return stats


@app.get("/metrics/dashboard/html")
async def dashboard_html():
    """Full analytics dashboard with charts - HTML version."""
    import src.api as api_module
    from src.core.approval import approval_service
    
    # Get data
    try:
        all_approvals = await approval_service.get_all_approvals()
        
        pending = sum(1 for a in all_approvals if a.status == "pending")
        approved = sum(1 for a in all_approvals if a.status == "approved")
        rejected = sum(1 for a in all_approvals if a.status == "rejected")
        
        confidences = [a.confidence for a in all_approvals if a.confidence]
        avg_conf = sum(confidences) / len(confidences) * 100 if confidences else 0
        
        auto_send = sum(1 for a in all_approvals if a.confidence >= 0.9)
        
        votes_agree = sum(1 for a in all_approvals if a.vote == "agree")
        votes_change = sum(1 for a in all_approvals if a.vote == "change")
        votes_skip = sum(1 for a in all_approvals if a.vote == "skip")
        total_votes = votes_agree + votes_change + votes_skip
        
        sat_rate = round(votes_agree / total_votes * 100, 1) if total_votes > 0 else 0
        approve_rate = round(approved / (approved + rejected) * 100, 1) if (approved + rejected) > 0 else 0
        
    except Exception as e:
        error_msg = str(e)
        pending = approved = rejected = 0
        avg_conf = auto_send = 0
        votes_agree = votes_change = votes_skip = total_votes = sat_rate = approve_rate = 0
    
    html = f"""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Supervisor Analytics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        
        header {{ text-align: center; margin-bottom: 30px; color: #fff; }}
        header h1 {{ font-size: 2.5rem; margin-bottom: 10px; text-shadow: 0 0 20px rgba(0,255,255,0.3); }}
        header p {{ color: #aaa; font-size: 1rem; }}
        
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        
        .stat-card {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 25px; border: 1px solid rgba(255,255,255,0.1); transition: transform 0.3s, box-shadow 0.3s; }}
        .stat-card:hover {{ transform: translateY(-5px); box-shadow: 0 20px 40px rgba(0,0,0,0.3); }}
        .stat-card h3 {{ color: #aaa; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
        .stat-card .value {{ font-size: 2.5rem; font-weight: bold; }}
        .stat-card .sub {{ color: #888; font-size: 0.85rem; margin-top: 5px; }}
        
        .stat-card.green .value {{ color: #00ff88; }}
        .stat-card.yellow .value {{ color: #ffd700; }}
        .stat-card.red .value {{ color: #ff6b6b; }}
        .stat-card.blue .value {{ color: #00d4ff; }}
        .stat-card.purple .value {{ color: #a855f7; }}
        
        .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 25px; }}
        
        .chart-card {{ background: rgba(255,255,255,0.05); backdrop-filter: blur(10px); border-radius: 20px; padding: 25px; border: 1px solid rgba(255,255,255,0.1); }}
        .chart-card h3 {{ color: #fff; font-size: 1.1rem; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        
        .satisfaction-bar {{ display: flex; height: 40px; border-radius: 20px; overflow: hidden; margin: 15px 0; }}
        .satisfaction-bar .agree {{ background: #00ff88; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .change {{ background: #ffd700; display: flex; align-items: center; justify-content: center; color: #000; font-weight: bold; }}
        .satisfaction-bar .skip {{ background: #666; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; }}
        
        .confidence-bar {{ height: 30px; background: rgba(255,255,255,0.1); border-radius: 15px; overflow: hidden; position: relative; margin: 15px 0; }}
        .confidence-bar .fill {{ height: 100%; background: linear-gradient(90deg, #ff6b6b, #ffd700, #00ff88); border-radius: 15px; transition: width 1s; }}
        .confidence-bar .marker {{ position: absolute; left: 90%; top: 0; height: 100%; width: 2px; background: #fff; }}
        
        .metric-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-row .label {{ color: #aaa; }}
        .metric-row .val {{ color: #fff; font-weight: bold; }}
        
        @media (max-width: 768px) {{ .charts-grid {{ grid-template-columns: 1fr; }} .stat-card .value {{ font-size: 2rem; }} }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Supervisor Analytics Dashboard</h1>
            <p>Cập nhật: {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}</p>
        </header>
        
        <div class="stats-grid">
            <div class="stat-card green">
                <h3>📨 Tổng Requests</h3>
                <div class="value">{len(all_approvals) if 'all_approvals' in dir() else 0}</div>
                <div class="sub">Tất cả approval requests</div>
            </div>
            <div class="stat-card blue">
                <h3>✅ Auto Send</h3>
                <div class="value">{round(auto_send / len(all_approvals) * 100, 1) if 'all_approvals' in dir() and all_approvals else 0}%</div>
                <div class="sub">Gửi tự động (confidence ≥ 90%)</div>
            </div>
            <div class="stat-card purple">
                <h3>🤖 AI Confidence</h3>
                <div class="value">{round(avg_conf, 1)}%</div>
                <div class="sub">Trung bình</div>
            </div>
            <div class="stat-card yellow">
                <h3>⭐ User Satisfaction</h3>
                <div class="value">{sat_rate}%</div>
                <div class="sub">{total_votes} votes</div>
            </div>
        </div>
        
        <div class="charts-grid">
            <div class="chart-card">
                <h3>📋 Approval Status</h3>
                <canvas id="approvalChart"></canvas>
            </div>
            
            <div class="chart-card">
                <h3>📈 AI Quality Metrics</h3>
                <div class="metric-row">
                    <span class="label">High Confidence (≥90%)</span>
                    <span class="val" style="color:#00ff88">{sum(1 for c in confidences if c >= 0.9) if 'confidences' in dir() else 0}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Low Confidence (<90%)</span>
                    <span class="val" style="color:#ff6b6b">{sum(1 for c in confidences if c < 0.9) if 'confidences' in dir() else 0}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Approve Rate</span>
                    <span class="val" style="color:#00d4ff">{approve_rate}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Reject Rate</span>
                    <span class="val" style="color:#ff6b6b">{100 - approve_rate}%</span>
                </div>
                <div class="confidence-bar">
                    <div class="fill" style="width: {avg_conf}%"></div>
                    <div class="marker"></div>
                </div>
                <p style="color:#888;font-size:0.8rem">Vertical line = 90% threshold</p>
            </div>
            
            <div class="chart-card">
                <h3>👤 User Satisfaction</h3>
                <div class="satisfaction-bar">
                    <div class="agree" style="width: {votes_agree / max(total_votes,1) * 100}%">{votes_agree}</div>
                    <div class="change" style="width: {votes_change / max(total_votes,1) * 100}%">{votes_change}</div>
                    <div class="skip" style="width: {votes_skip / max(total_votes,1) * 100}%">{votes_skip}</div>
                </div>
                <div class="metric-row">
                    <span class="label">👍 Agree</span>
                    <span class="val" style="color:#00ff88">{votes_agree}</span>
                </div>
                <div class="metric-row">
                    <span class="label">✋ Need Change</span>
                    <span class="val" style="color:#ffd700">{votes_change}</span>
                </div>
                <div class="metric-row">
                    <span class="label">⏭️ Skip</span>
                    <span class="val" style="color:#666">{votes_skip}</span>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>⚡ Performance</h3>
                <div class="metric-row">
                    <span class="label">Pending</span>
                    <span class="val" style="color:#ffd700">{pending}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Approved</span>
                    <span class="val" style="color:#00ff88">{approved}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Rejected</span>
                    <span class="val" style="color:#ff6b6b">{rejected}</span>
                </div>
                <canvas id="pieChart"></canvas>
            </div>
        </div>
    </div>
    
    <script>
        // Approval Status Chart
        new Chart(document.getElementById('approvalChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Pending', 'Approved', 'Rejected'],
                datasets: [{{
                    data: [{pending}, {approved}, {rejected}],
                    backgroundColor: ['#ffd700', '#00ff88', '#ff6b6b'],
                    borderWidth: 0
                }}]
            }},
            options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#fff' }} }} }} }}
        }});
    </script>
</body>
</html>
"""
    
    return HTMLResponse(content=html, media_type="text/html")


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


@app.get("/harness/status")
async def get_harness_status():
    """Get current harness status and statistics"""
    harness = get_harness()
    tool_registry = get_tool_registry()
    
    return {
        "harness": {
            "name": harness.config.name,
            "status": harness.status.value,
            "execution_id": harness.execution_id,
            "config": {
                "max_iterations": harness.config.max_iterations,
                "max_tool_calls": harness.config.max_tool_calls,
                "timeout_seconds": harness.config.timeout_seconds,
                "enable_planning": harness.config.enable_planning,
                "enable_evaluation": harness.config.enable_evaluation,
                "enable_context_compaction": harness.config.enable_context_compaction,
            },
        },
        "tools": tool_registry.get_stats(),
        "lifecycle": harness.lifecycle.get_registered_hooks(),
        "context": harness.context_manager.get_stats() if harness.context_manager else {},
        "evaluator": harness.evaluator.get_stats() if harness.evaluator else {},
    }


@app.post("/harness/execute")
async def harness_execute(request: dict):
    """Execute a task through the harness with full management"""
    harness = get_harness()
    
    prompt = request.get("prompt", "")
    tools = request.get("tools")
    context = request.get("context")
    
    try:
        result = await harness.execute(
            prompt=prompt,
            tools=tools,
            context=context,
        )
        
        return {
            "status": "success",
            "result": result,
            "metrics": harness.get_metrics(),
        }
    except Exception as e:
        logger.error(f"Harness execution error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/harness/tools")
async def list_harness_tools():
    """List all registered tools in the harness"""
    tool_registry = get_tool_registry()
    tools = tool_registry.list_all()
    
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category.value,
                "requires_approval": t.requires_approval,
                "dangerous": t.dangerous,
            }
            for t in tools
        ],
        "total": len(tools),
        "schemas": tool_registry.get_schemas(),
    }


@app.post("/harness/tools/{tool_name}/execute")
async def execute_tool(tool_name: str, arguments: dict):
    """Execute a specific tool through the harness"""
    tool_registry = get_tool_registry()
    
    try:
        result = await tool_registry.execute(tool_name, arguments)
        return {
            "status": "success",
            "tool": tool_name,
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Tool execution error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/harness/hooks")
async def register_hook(request: dict):
    """Register a lifecycle hook"""
    harness = get_harness()
    
    hook_type_str = request.get("hook_type")
    callback_url = request.get("callback_url")
    
    try:
        hook_type = HookType(hook_type_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid hook type. Valid types: {[h.value for h in HookType]}"
        )
    
    # TODO: Implement webhook-based callback
    # For now, just acknowledge registration
    
    return {
        "status": "registered",
        "hook_type": hook_type.value,
        "message": "Hook registered successfully",
    }


@app.get("/harness/evaluations")
async def get_evaluations(limit: int = 100, only_failed: bool = False):
    """Get evaluation history"""
    harness = get_harness()
    
    if not harness.evaluator:
        return {"error": "Evaluator not enabled"}
    
    return {
        "evaluations": harness.evaluator.get_history(limit, only_failed),
        "stats": harness.evaluator.get_stats(),
    }


@app.post("/harness/benchmark")
async def run_benchmark(request: dict):
    """Run a benchmark with test cases"""
    harness = get_harness()
    
    if not harness.evaluator:
        raise HTTPException(status_code=400, detail="Evaluator not enabled")
    
    test_name = request.get("test_name", "unnamed_benchmark")
    test_cases = request.get("test_cases", [])
    iterations = request.get("iterations", 3)
    
    if not test_cases:
        raise HTTPException(status_code=400, detail="test_cases required")
    
    run = await harness.evaluator.run_benchmark(
        test_name=test_name,
        test_cases=test_cases,
        iterations=iterations,
    )
    
    return {
        "run_id": run.run_id,
        "test_name": run.test_name,
        "duration": run.duration,
        "iterations": run.iterations,
        "avg_score": run.avg_score,
        "success_rate": run.success_rate,
    }


@app.post("/harness/compare")
async def compare_versions(request: dict):
    """Compare performance between two agent versions"""
    harness = get_harness()
    
    if not harness.evaluator:
        raise HTTPException(status_code=400, detail="Evaluator not enabled")
    
    version_a = request.get("version_a", [])
    version_b = request.get("version_b", [])
    
    comparison = harness.evaluator.compare_versions(version_a, version_b)
    
    return comparison


@app.post("/harness/reset")
async def reset_harness():
    """Reset harness state"""
    harness = get_harness()
    
    harness.context_manager.reset() if harness.context_manager else None
    harness.planner.clear_cache() if harness.planner else None
    
    return {"status": "reset", "message": "Harness state cleared"}
