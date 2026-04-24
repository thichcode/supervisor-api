"""
Approval Service - Manages approval queue for AI responses requiring human review
"""
import uuid
from datetime import datetime
from typing import Optional
import structlog

from src.core.schemas import ApprovalRequest, ApprovalStatus
from src.config import get_settings
from src.memory.cache import redis_cache
from src.gateway.platforms.telegram import build_approval_inline_keyboard, build_approval_message_text
from src.core.metrics import metrics

logger = structlog.get_logger()
settings = get_settings()

APPROVAL_QUEUE_KEY = "approval:queue"
APPROVAL_TTL = 86400 * 7  # 7 days


class ApprovalService:
    def __init__(self):
        self.default_threshold = 0.5  # Approval is required for medium-confidence responses

    def _notification_cooldown_seconds(self) -> int:
        return max(0, int(getattr(settings, "approval_notification_cooldown_seconds", 0) or 0))

    def _notification_key(self, channel: str) -> str:
        return f"approval:notification:{channel}"

    async def _notification_allowed(self, channel: str) -> bool:
        cooldown = self._notification_cooldown_seconds()
        if cooldown <= 0:
            return True

        try:
            return await redis_cache.set_if_absent(
                self._notification_key(channel),
                datetime.utcnow().isoformat(),
                ttl=cooldown,
            )
        except Exception as exc:
            logger.warning(
                "notification_rate_limit_check_failed",
                channel=channel,
                error=str(exc),
            )
            return True
    
    async def create_approval(
        self,
        request_id: str,
        user_id: str,
        display_name: str,
        original_message: str,
        ai_response: str,
        confidence: float,
        action_type: str = "send_message",
        metadata: Optional[dict] = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            id=str(uuid.uuid4()),
            request_id=request_id,
            user_id=user_id,
            display_name=display_name,
            original_message=original_message,
            ai_response=ai_response,
            confidence=confidence,
            threshold=self.default_threshold,
            status=ApprovalStatus.PENDING,
            action_type=action_type,
            metadata=metadata or {},
            created_at=datetime.now(),
        )
        
        await self._save_approval(approval)
        
        # Only notify via Teams/Telegram - NOT Power Automate
        # Power Automate receives auto-sent responses only (confidence >= 0.9 AND kb_hit = true)
        await self._notify_telegram_approval_request(approval)
        
        logger.info(
            "approval_created",
            approval_id=approval.id,
            request_id=request_id,
            confidence=confidence,
            threshold=self.default_threshold,
        )
        metrics.record_approval_action("created")
        return approval
    
    async def _notify_approval_request(self, approval: ApprovalRequest):
        """Send approval request notification to Power Automate"""
        if not settings.power_automate_webhook_url:
            logger.debug("Power Automate webhook not configured, skipping notification")
            return

        if not await self._notification_allowed("power_automate"):
            logger.info(
                "Approval notification rate-limited for Power Automate",
                approval_id=approval.id,
                cooldown_seconds=self._notification_cooldown_seconds(),
            )
            return
        
        import httpx
        
        payload = {
            "type": "approval_request",
            "approval_id": approval.id,
            "request_id": approval.request_id,
            "user_id": approval.user_id,
            "display_name": approval.display_name,
            "thread_id": approval.metadata.get("thread_id", ""),
            "original_message": approval.original_message[:200] if approval.original_message else "",
            "ai_response": approval.ai_response,
            "confidence": round(approval.confidence * 100, 1),
            "threshold": round(approval.threshold * 100, 1),
            "kb_sources": approval.metadata.get("kb_sources", []),
            "kb_evidence": approval.metadata.get("kb_evidence", []),
            "message": f"⚠️ Cần duyệt phản hồi cho {approval.display_name}\n\n"
                      f"Confidence: {round(approval.confidence * 100, 1)}% (threshold: {round(approval.threshold * 100, 1)}%)\n\n"
                      f"**Tin nhắn gốc:** {approval.original_message[:100] if approval.original_message else 'N/A'}...\n\n"
                      f"**Phản hồi AI:**\n{approval.ai_response[:300]}...",
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
                logger.info("Approval notification sent to Power Automate", 
                          approval_id=approval.id, status=response.status_code)
        except httpx.HTTPError as e:
            logger.warning("Failed to send approval notification", 
                         approval_id=approval.id, error=str(e))

    async def _notify_telegram_approval_request(self, approval: ApprovalRequest):
        """Send an approval request directly to Telegram with inline buttons."""
        if not settings.telegram_bot_token or not settings.telegram_approval_chat_ids:
            logger.debug("Telegram approval notification not configured, skipping notification")
            return

        if not await self._notification_allowed("telegram"):
            logger.info(
                "Approval notification rate-limited for Telegram",
                approval_id=approval.id,
                cooldown_seconds=self._notification_cooldown_seconds(),
            )
            return

        import httpx

        chat_ids = [
            chat_id.strip()
            for chat_id in settings.telegram_approval_chat_ids.split(",")
            if chat_id.strip()
        ]
        if not chat_ids:
            return

        payload_text = build_approval_message_text(approval)
        reply_markup = build_approval_inline_keyboard(approval.id)
        endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

        try:
            async with httpx.AsyncClient() as client:
                for chat_id in chat_ids:
                    response = await client.post(
                        endpoint,
                        json={
                            "chat_id": chat_id,
                            "text": payload_text,
                            "parse_mode": settings.telegram_parse_mode,
                            "reply_markup": reply_markup,
                        },
                        timeout=settings.webhook_timeout,
                    )
                    response.raise_for_status()
                logger.info("Approval notification sent to Telegram", approval_id=approval.id, recipients=chat_ids)
        except httpx.HTTPError as e:
            logger.warning("Failed to send Telegram approval notification", approval_id=approval.id, error=str(e))
    
    async def _save_approval(self, approval: ApprovalRequest):
        key = f"approval:{approval.id}"
        data = approval.model_dump(mode='json')
        data['created_at'] = approval.created_at.isoformat()
        data['reviewed_at'] = approval.reviewed_at.isoformat() if approval.reviewed_at else None
        
        await redis_cache.set_json(key, data, ttl=APPROVAL_TTL)
        
        await redis_cache.sadd(APPROVAL_QUEUE_KEY, approval.id)
    
    async def get_approval(self, approval_id: str) -> Optional[ApprovalRequest]:
        key = f"approval:{approval_id}"
        data = await redis_cache.get_json(key)
        
        if not data:
            return None
        
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        if data.get('reviewed_at'):
            data['reviewed_at'] = datetime.fromisoformat(data['reviewed_at'])
        
        return ApprovalRequest(**data)
    
    async def get_pending_approvals(self) -> list[ApprovalRequest]:
        approval_ids = await redis_cache.smembers(APPROVAL_QUEUE_KEY)
        
        approvals = []
        for approval_id in approval_ids:
            approval = await self.get_approval(approval_id)
            if approval and approval.status == ApprovalStatus.PENDING:
                approvals.append(approval)
        
        return sorted(approvals, key=lambda a: a.created_at, reverse=True)
    
    async def get_all_approvals(self, status: Optional[ApprovalStatus] = None) -> list[ApprovalRequest]:
        approval_ids = await redis_cache.smembers(APPROVAL_QUEUE_KEY)
        
        approvals = []
        for approval_id in approval_ids:
            approval = await self.get_approval(approval_id)
            if approval:
                if status is None or approval.status == status:
                    approvals.append(approval)
        
        return sorted(approvals, key=lambda a: a.created_at, reverse=True)
    
    async def approve(
        self,
        approval_id: str,
        reviewed_by: str,
        comment: Optional[str] = None,
    ) -> Optional[ApprovalRequest]:
        approval = await self.get_approval(approval_id)
        
        if not approval:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None
        
        if approval.status != ApprovalStatus.PENDING:
            logger.warning("approval_not_pending", approval_id=approval_id, status=approval.status)
            return None
        
        approval.status = ApprovalStatus.APPROVED
        approval.reviewed_by = reviewed_by
        approval.reviewed_at = datetime.now()
        approval.review_comment = comment
        
        await self._save_approval(approval)
        
        logger.info(
            "approval_approved",
            approval_id=approval_id,
            reviewed_by=reviewed_by,
        )
        metrics.record_approval_action("approved")
        
        return approval
    
    async def reject(
        self,
        approval_id: str,
        reviewed_by: str,
        comment: Optional[str] = None,
    ) -> Optional[ApprovalRequest]:
        approval = await self.get_approval(approval_id)
        
        if not approval:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None
        
        if approval.status != ApprovalStatus.PENDING:
            logger.warning("approval_not_pending", approval_id=approval_id, status=approval.status)
            return None
        
        approval.status = ApprovalStatus.REJECTED
        approval.reviewed_by = reviewed_by
        approval.reviewed_at = datetime.now()
        approval.review_comment = comment
        
        await self._save_approval(approval)
        
        logger.info(
            "approval_rejected",
            approval_id=approval_id,
            reviewed_by=reviewed_by,
        )
        metrics.record_approval_action("rejected")
        
        return approval
    
    async def record_vote(
        self,
        approval_id: str,
        vote: str,
        user_id: str,
        feedback: Optional[str] = None,
    ) -> Optional[ApprovalRequest]:
        """Record user vote on an approved response"""
        approval = await self.get_approval(approval_id)
        
        if not approval:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None
        
        # Update vote fields
        approval.vote = vote
        approval.voted_by = user_id
        approval.voted_at = datetime.now()
        approval.user_feedback = feedback
        
        await self._save_approval(approval)
        
        logger.info(
            "vote_recorded",
            approval_id=approval_id,
            vote=vote,
            user_id=user_id,
        )
        metrics.record_approval_action(f"vote_{vote}")
        
        return approval
    
    async def needs_approval(self, confidence: float, kb_hit: bool = False) -> bool:
        if confidence < 0.5:
            return False

        if confidence >= 0.9:
            return False

        return True
    
    async def get_pending_count(self) -> int:
        approvals = await self.get_pending_approvals()
        return len(approvals)


approval_service = ApprovalService()


async def get_approval_service() -> ApprovalService:
    return approval_service