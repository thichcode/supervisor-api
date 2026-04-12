"""
Approval Service - Manages approval queue for AI responses requiring human review
"""
import uuid
import json
from datetime import datetime, timedelta
from typing import Optional
import structlog

from src.core.schemas import ApprovalRequest, ApprovalStatus
from src.config import get_settings
from src.memory.cache import redis_cache

logger = structlog.get_logger()
settings = get_settings()

APPROVAL_QUEUE_KEY = "approval:queue"
APPROVAL_TTL = 86400 * 7  # 7 days


class ApprovalService:
    def __init__(self):
        self.default_threshold = 0.9  # 90% confidence threshold
    
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
        
        # Send notification to Power Automate for Teams approval request
        await self._notify_approval_request(approval)
        
        logger.info(
            "approval_created",
            approval_id=approval.id,
            request_id=request_id,
            confidence=confidence,
            threshold=self.default_threshold,
        )
        
        return approval
    
    async def _notify_approval_request(self, approval: ApprovalRequest):
        """Send approval request notification to Power Automate"""
        if not settings.power_automate_webhook_url:
            logger.debug("Power Automate webhook not configured, skipping notification")
            return
        
        import httpx
        
        payload = {
            "type": "approval_request",
            "approval_id": approval.id,
            "request_id": approval.request_id,
            "user_id": approval.user_id,
            "display_name": approval.display_name,
            "original_message": approval.original_message[:200] if approval.original_message else "",
            "ai_response": approval.ai_response,
            "confidence": round(approval.confidence * 100, 1),
            "threshold": round(approval.threshold * 100, 1),
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
        
        return approval
    
    async def needs_approval(self, confidence: float) -> bool:
        return confidence < self.default_threshold
    
    async def get_pending_count(self) -> int:
        approvals = await self.get_pending_approvals()
        return len(approvals)


approval_service = ApprovalService()


async def get_approval_service() -> ApprovalService:
    return approval_service