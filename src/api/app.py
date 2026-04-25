"""Canonical FastAPI application entrypoint.

This module is the long-term home of the application object. Existing import
paths are preserved via compatibility exports in ``src.api`` and ``src/api.py``.
"""

from contextlib import asynccontextmanager
import asyncio
import base64
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional, Any

import structlog
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select

from src.config import get_settings
from src.core import InputPayload, OutputPayload
from src.core.approval import approval_service
from src.core.logging_config import setup_logging, RequestLogger
from src.core.metrics import metrics
from src.core.sanitizer import sanitizer
from src.core.supervisor import Supervisor
from src.db import init_db, close_db, async_session, InteractionLog
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
from src.api.routers.kb_templates import router as kb_templates_router
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



def _attachment_value(attachment: Any, key: str, default: Any = None) -> Any:
    if hasattr(attachment, key):
        return getattr(attachment, key, default)
    if isinstance(attachment, dict):
        return attachment.get(key, default)
    return default



def _attachment_name(attachment: Any, index: int) -> str:
    name = _attachment_value(attachment, "name") or _attachment_value(attachment, "filename")
    if name:
        return str(name)
    url = _attachment_value(attachment, "url") or _attachment_value(attachment, "content_url") or _attachment_value(attachment, "file_url")
    if url:
        try:
            return Path(str(url)).name or f"attachment-{index}"
        except Exception:
            return f"attachment-{index}"
    return f"attachment-{index}"



def _attachment_content_type(attachment: Any) -> str:
    return str(_attachment_value(attachment, "content_type") or _attachment_value(attachment, "mime_type") or "").strip().lower()



def _attachment_url(attachment: Any) -> str:
    for key in ("url", "content_url", "file_url"):
        value = _attachment_value(attachment, key)
        if value:
            return str(value).strip()
    return ""



def _attachment_is_image(attachment: Any) -> bool:
    attachment_type = str(_attachment_value(attachment, "type") or "").strip().lower()
    content_type = _attachment_content_type(attachment)
    name = _attachment_name(attachment, 0).lower()
    return (
        attachment_type in {"image", "photo", "picture"}
        or content_type.startswith("image/")
        or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"))
    )



def _guess_suffix_from_attachment(attachment: Any, fallback: str = ".bin") -> str:
    name = _attachment_name(attachment, 0)
    suffix = Path(name).suffix.lower()
    if suffix:
        return suffix
    content_type = _attachment_content_type(attachment)
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
        "application/pdf": ".pdf",
    }
    return mapping.get(content_type, fallback)


_IMAGE_SIGNATURE_STOPWORDS = {
    "attachment",
    "attachments",
    "image",
    "screenshot",
    "photo",
    "picture",
    "file",
    "files",
    "error",
    "failed",
    "fail",
    "issue",
    "problem",
    "please",
    "send",
    "forwarded",
    "attachment",
    "evidence",
    "ocr",
    "text",
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "và",
    "cho",
    "mình",
    "tôi",
    "của",
    "đang",
    "lỗi",
    "ảnh",
    "hình",
}


def _normalize_issue_signature(*parts: str) -> str:
    combined = " ".join(part for part in parts if part)
    tokens = re.findall(r"[\wÀ-ỹ0-9]+", combined.lower(), flags=re.UNICODE)
    kept: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token or len(token) == 1:
            continue
        if token in _IMAGE_SIGNATURE_STOPWORDS:
            continue
        if token not in kept:
            kept.append(token)
    return " ".join(kept[:12])


async def _extract_attachment_evidence(payload: InputPayload) -> dict[str, Any]:
    attachments = list(payload.message.attachments or [])
    if not attachments:
        return {
            "attachment_count": 0,
            "attachments": [],
            "attachment_summary": "",
            "attachment_text": "",
            "has_images": False,
            "issue_signature": "",
            "has_actionable_text": False,
            "needs_clarification": False,
            "clarification_hint": "",
            "image_case": False,
        }

    extracted: list[dict[str, Any]] = []
    attachment_text_parts: list[str] = []
    has_images = False

    from src.tools.file_processor import get_file_processor

    processor = get_file_processor()

    for idx, attachment in enumerate(attachments[:5], start=1):
        attachment_dict = attachment.model_dump() if hasattr(attachment, "model_dump") else dict(attachment)
        attachment_name = _attachment_name(attachment, idx)
        attachment_type = str(attachment_dict.get("type") or "file").strip().lower()
        content_type = _attachment_content_type(attachment)
        attachment_url = _attachment_url(attachment)
        inline_text = str(attachment_dict.get("ocr_text") or attachment_dict.get("text") or "").strip()
        metadata = attachment_dict.get("metadata") or {}
        item: dict[str, Any] = {
            "index": idx,
            "name": attachment_name,
            "type": attachment_type,
            "content_type": content_type,
            "url": attachment_url,
            "metadata": metadata,
        }

        local_path: Optional[str] = None
        temp_path: Optional[Path] = None
        try:
            if inline_text:
                item["text"] = inline_text
                item["ocr_text"] = inline_text
                attachment_text_parts.append(f"[{idx}] {attachment_name}: {inline_text[:1200]}")

            if _attachment_is_image(attachment):
                has_images = True

            # Prefer explicit OCR text when provided by Power Automate/n8n.
            if inline_text:
                item["ocr_text"] = inline_text
            elif _attachment_is_image(attachment):
                if attachment_url:
                    import httpx

                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                        response = await client.get(attachment_url)
                        response.raise_for_status()
                        fd, tmp_name = tempfile.mkstemp(prefix="supervisor-attachment-", suffix=_guess_suffix_from_attachment(attachment))
                        os.close(fd)
                        temp_path = Path(tmp_name)
                        temp_path.write_bytes(response.content)
                        local_path = str(temp_path)
                else:
                    base64_data = attachment_dict.get("base64_data") or attachment_dict.get("content_base64")
                    if base64_data:
                        raw = str(base64_data).split(",", 1)[-1]
                        decoded = base64.b64decode(raw)
                        fd, tmp_name = tempfile.mkstemp(prefix="supervisor-attachment-", suffix=_guess_suffix_from_attachment(attachment))
                        os.close(fd)
                        temp_path = Path(tmp_name)
                        temp_path.write_bytes(decoded)
                        local_path = str(temp_path)

                if local_path:
                    file_content = processor.process_file(local_path)
                    extracted_text = (file_content.content or "").strip()
                    if extracted_text:
                        item["ocr_text"] = extracted_text
                        attachment_text_parts.append(f"[{idx}] {attachment_name}: {extracted_text[:1200]}")
                elif attachment_url:
                    item["url"] = attachment_url

            if not item.get("ocr_text") and attachment_url:
                attachment_text_parts.append(f"[{idx}] {attachment_name}: {attachment_url}")

        except Exception as exc:
            item["error"] = str(exc)
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

        extracted.append(item)

    attachment_summary = "; ".join(
        [
            f"{item['name']} ({item.get('content_type') or item.get('type') or 'file'})"
            for item in extracted[:5]
        ]
    )
    attachment_text = "\n".join(attachment_text_parts).strip()
    issue_signature = _normalize_issue_signature(
        payload.message.text,
        attachment_text,
        attachment_summary,
        " ".join(item.get("ocr_text", "") for item in extracted),
    )
    has_actionable_text = any(item.get("ocr_text") for item in extracted)
    needs_clarification = bool(has_images and not has_actionable_text and not payload.message.text.strip())
    clarification_hint = (
        "Mình thấy ảnh đính kèm nhưng chưa đọc được đủ nội dung. Bạn gửi lại ảnh rõ hơn hoặc chép lại mã lỗi / bước đang bị kẹt nhé."
        if needs_clarification
        else ""
    )
    return {
        "attachment_count": len(attachments),
        "attachments": extracted,
        "attachment_summary": attachment_summary,
        "attachment_text": attachment_text,
        "has_images": has_images,
        "issue_signature": issue_signature,
        "has_actionable_text": has_actionable_text,
        "needs_clarification": needs_clarification,
        "clarification_hint": clarification_hint,
        "image_case": bool(has_images),
    }


async def _maybe_draft_image_case_candidate(
    session,
    payload: InputPayload,
    result: OutputPayload,
    attachment_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    issue_signature = (attachment_evidence.get("issue_signature") or "").strip()
    if not issue_signature or not attachment_evidence.get("has_images"):
        return None
    if bool((result.metadata or {}).get("kb_hit")):
        return None

    signature_terms = issue_signature.split()
    if len(signature_terms) < 2:
        return None
    signature_fragment = " ".join(signature_terms[:4])

    recent_query = (
        select(InteractionLog)
        .where(
            InteractionLog.traffic_class == "service_like",
            (InteractionLog.kb_hit_count == None) | (InteractionLog.kb_hit_count == 0),
            InteractionLog.input_text.ilike(f"%{signature_fragment}%"),
        )
        .order_by(InteractionLog.created_at.desc())
        .limit(5)
    )
    rows = (await session.execute(recent_query)).scalars().all()
    if len(rows) < 2:
        return None

    from src.services.kb_draft_service import KBDraftService, MissPattern, MissSample

    samples = [
        MissSample(
            request_id=row.request_id,
            created_at=row.created_at.isoformat() if row.created_at else None,
            thread_id=row.thread_id,
            user_id=row.user_id,
            intent=row.intent or "unknown",
            confidence_score=row.confidence_score,
            kb_hit_count=row.kb_hit_count or 0,
            input_text=row.input_text[:220],
            output_text=(row.output_text or "")[:220],
        )
        for row in rows[:3]
    ]
    miss = MissPattern(
        pattern=issue_signature,
        normalized_pattern=_normalize_issue_signature(issue_signature),
        count=len(rows),
        samples=samples,
    )
    draft_service = KBDraftService(
        session,
        telegram_bot_token=settings.telegram_bot_token or "",
        telegram_chat_ids=settings.telegram_approval_chat_ids or "",
    )
    draft = await draft_service.build_draft(miss)
    if not draft:
        return None
    return {
        "candidate_id": draft.candidate_id,
        "source_request_id": draft.source_request_id,
        "title": draft.title,
        "count": draft.miss_count,
    }


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
        
        # Initialize separate image processing LLM if configured
        if settings.ollama_image_model and settings.ollama_image_model != settings.ollama_default_model:
            try:
                image_llm_client = MultiProviderLLMClient(
                    provider=LLMProvider.OLLAMA,
                    model=settings.ollama_image_model,
                    base_url=settings.ollama_base_url,
                    timeout=settings.ollama_timeout,
                )
                await image_llm_client.initialize()
                supervisor.set_image_llm(image_llm_client)
                logger.info("Image LLM initialized", model=settings.ollama_image_model)
            except Exception as e:
                logger.warning("Image LLM initialization failed - using fallback", error=str(e))
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
app.include_router(kb_templates_router)
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

        original_text = payload.message.text or ""
        attachment_evidence = await _extract_attachment_evidence(payload)
        has_attachments = attachment_evidence.get("attachment_count", 0) > 0
        is_valid, error_msg = sanitizer.validate_input(original_text)
        if not original_text.strip() and not has_attachments:
            if not is_valid:
                metrics.record_error("input_validation", "webhook/n8n")
                raise HTTPException(status_code=400, detail=f"Invalid input: {error_msg}")
        elif not original_text.strip() and has_attachments:
            is_valid = True

        combined_text_parts = []
        if original_text.strip():
            combined_text_parts.append(original_text.strip())
        if attachment_evidence.get("attachment_text"):
            combined_text_parts.append(f"[Attachment evidence]\n{attachment_evidence['attachment_text']}")
        elif has_attachments and attachment_evidence.get("attachment_summary"):
            combined_text_parts.append(f"[Attachments]\n{attachment_evidence['attachment_summary']}")
        if attachment_evidence.get("issue_signature"):
            combined_text_parts.append(f"[Image issue signature]\n{attachment_evidence['issue_signature']}")
        if attachment_evidence.get("clarification_hint"):
            combined_text_parts.append(f"[Image clarification hint]\n{attachment_evidence['clarification_hint']}")
        effective_text = "\n\n".join(combined_text_parts).strip() or original_text.strip()

        payload.message.text = sanitizer.sanitize(effective_text or original_text)
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
            result.metadata = {
                **(result.metadata or {}),
                **chat_context,
                "original_text": original_text,
                "has_attachments": has_attachments,
                "attachment_evidence": attachment_evidence,
            }
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
            image_candidate = None
            if attachment_evidence.get("image_case") and not (result.metadata or {}).get("kb_hit"):
                image_candidate = await _maybe_draft_image_case_candidate(session, payload, result, attachment_evidence)
                if image_candidate:
                    result.metadata = {
                        **(result.metadata or {}),
                        "image_case_candidate": image_candidate,
                    }
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
                
                # Send to Telegram for review/approve
                from src.api.routers.approvals import send_telegram_message
                tg_chat_ids = settings.telegram_approval_chat_ids.split(",")
                if tg_chat_ids:
                    review_message = f"⚠️ **Cần duyệt**\n\n**User:** {payload.user.display_name}\n**Confidence:** {result.confidence:.0%}\n\n**Câu hỏi:**\n{result.answer[:500]}..." if len(result.answer) > 500 else f"⚠️ **Cần duyệt**\n\n**User:** {payload.user.display_name}\n**Confidence:** {result.confidence:.0%}\n\n**Câu hỏi:**\n{result.answer}"
                    for tg_id in tg_chat_ids:
                        tg_id = tg_id.strip()
                        if tg_id:
                            try:
                                await send_telegram_message(tg_id, review_message)
                            except Exception as e:
                                logger.warning("Failed to send approval to Telegram", chat_id=tg_id, error=str(e))
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
    
    # Extract metadata for richer payload
    meta = payload.metadata or {}
    
    # Format payload for Power Automate with expanded fields
    pa_payload = {
        "request_id": getattr(payload, 'request_id', ''),
        "message": payload.message.text if payload.message else "",
        "answer": payload.answer,
        "confidence": payload.confidence,
        "intent": meta.get("intent", "unknown"),
        "risk_level": payload.risk_level,
        "agents_used": meta.get("agents_used", []),
        "status": payload.status,
        "processing_time_ms": meta.get("processing_time_ms", 0),
        
        # KB related fields
        "kb_hit": meta.get("kb_hit", False),
        "kb_guides": meta.get("kb_guides", []),
        "kb_sources": meta.get("kb_sources", []),
        "kb_template": meta.get("kb_template", {}),
        "knowledge_results": meta.get("knowledge_results", []),
        
        # ITC ticket related
        "itc_ticket": meta.get("itc_ticket", False),
        "itc_requestid": meta.get("itc_requestid"),
        "ticket_id": meta.get("ticket_id"),
        
        # Approval related
        "approval_id": meta.get("approval_id"),
        "approval_required": meta.get("approval_required", False),
        
        # Full metadata for reference
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
