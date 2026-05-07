"""
n8n Connector - Connect to internal systems via n8n webhooks
Supports both query (read) and action (write/execute) operations
with approval workflow for actions

Features:
- Exponential backoff retry (base_delay=1s, retries=3)
- Circuit breaker integration
- Approval workflow for write operations
"""

import asyncio
import httpx
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import structlog
from src.core.circuit_breaker import get_circuit_breaker, CircuitBreakerError

logger = structlog.get_logger()

N8N_RETRY_CONFIG = {
    "base_delay": 1.0,
    "max_delay": 10.0,
    "retries": 3,
    "exponential_base": 2,
}


class ActionType(str, Enum):
    """Type of operation"""
    QUERY = "query"           # Read-only, no approval needed
    ACTION = "action"         # Write/execute, needs approval
    APPROVED = "approved"      # Action approved by human


class RiskLevel(str, Enum):
    """Risk level for actions"""
    LOW = "low"           # Safe operations
    MEDIUM = "medium"      # Moderate risk
    HIGH = "high"         # Risky operations
    CRITICAL = "critical" # Very risky, needs senior approval


@dataclass
class ActionRequest:
    """Action request that needs approval"""
    request_id: str
    action_type: str
    system: str
    action: str
    parameters: Dict[str, Any]
    risk_level: RiskLevel
    requested_by: str
    requested_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "pending"  # pending, approved, rejected, executed
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    result: Optional[Dict] = None


@dataclass
class SystemAction:
    """Definition of a system action"""
    name: str
    display_name: str
    system: str
    action_type: ActionType
    risk_level: RiskLevel
    webhook_path: str
    parameters: List[Dict[str, str]] = field(default_factory=list)
    description: str = ""
    approval_required: bool = True


# Predefined system actions
SYSTEM_ACTIONS: Dict[str, SystemAction] = {
    # Backup Service Actions
    "backup_status": SystemAction(
        name="backup_status",
        display_name="Kiểm tra trạng thái Backup",
        system="backup",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/backup/status",
        description="Xem trạng thái backup của hệ thống"
    ),
    "backup_restore": SystemAction(
        name="backup_restore",
        display_name="Khôi phục từ Backup",
        system="backup",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.HIGH,
        webhook_path="/webhook/backup/restore",
        parameters=[
            {"name": "backup_id", "type": "string", "required": True},
            {"name": "target_server", "type": "string", "required": True},
        ],
        description="Khôi phục dữ liệu từ backup"
    ),

    # Monitoring Service Actions
    "monitor_status": SystemAction(
        name="monitor_status",
        display_name="Xem trạng thái Monitor",
        system="monitoring",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/monitoring/status",
        description="Xem trạng thái giám sát hệ thống"
    ),
    "monitor_alert_ack": SystemAction(
        name="monitor_alert_ack",
        display_name="Acknowledge Alert",
        system="monitoring",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/monitoring/ack-alert",
        parameters=[
            {"name": "alert_id", "type": "string", "required": True},
            {"name": "note", "type": "string", "required": False},
        ],
        description="Acknowledge một alert"
    ),

    # IT Service / Ticket Actions
    "ticket_create": SystemAction(
        name="ticket_create",
        display_name="Tạo Ticket IT",
        system="itsm",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itsm/create-ticket",
        parameters=[
            {"name": "title", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": True},
            {"name": "category", "type": "string", "required": True},
            {"name": "priority", "type": "string", "required": False},
        ],
        description="Tạo ticket IT mới"
    ),
    "ticket_update": SystemAction(
        name="ticket_update",
        display_name="Cập nhật Ticket",
        system="itsm",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/itsm/update-ticket",
        parameters=[
            {"name": "ticket_id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": False},
            {"name": "note", "type": "string", "required": False},
        ],
        description="Cập nhật trạng thái ticket"
    ),
    "ticket_get": SystemAction(
        name="ticket_get",
        display_name="Xem chi tiết Ticket IT",
        system="itsm",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/itsm/get-ticket",
        parameters=[
            {"name": "ticket_id", "type": "string", "required": True},
        ],
        description="Lấy thông tin chi tiết của một ticket IT (subject, status, description, assignee)"
    ),

    # Server Management Actions
    "server_restart": SystemAction(
        name="server_restart",
        display_name="Restart Server",
        system="infrastructure",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.CRITICAL,
        webhook_path="/webhook/infra/restart-server",
        parameters=[
            {"name": "server_name", "type": "string", "required": True},
            {"name": "reason", "type": "string", "required": True},
        ],
        description="Restart một server (NGUY HIỂM)"
    ),
    "server_status": SystemAction(
        name="server_status",
        display_name="Kiểm tra Server",
        system="infrastructure",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/infra/server-status",
        description="Xem trạng thái server"
    ),

    # Account Management
    "account_unlock": SystemAction(
        name="account_unlock",
        display_name="Mở khóa tài khoản",
        system="iam",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.MEDIUM,
        webhook_path="/webhook/iam/unlock-account",
        parameters=[
            {"name": "username", "type": "string", "required": True},
        ],
        description="Mở khóa tài khoản bị lock"
    ),
    "account_reset_password": SystemAction(
        name="account_reset_password",
        display_name="Reset Password",
        system="iam",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.HIGH,
        webhook_path="/webhook/iam/reset-password",
        parameters=[
            {"name": "username", "type": "string", "required": True},
        ],
        description="Reset password của user"
    ),

    # =============================================================================
    # GitLab Integration
    # =============================================================================
    "gitlab_merge_requests": SystemAction(
        name="gitlab_merge_requests",
        display_name="GitLab Merge Requests",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/merge-requests",
        parameters=[
            {"name": "project_id", "type": "string", "required": False},
            {"name": "state", "type": "string", "required": False},
            {"name": "limit", "type": "integer", "required": False},
        ],
        description="Lấy danh sách Merge Requests"
    ),
    "gitlab_issues": SystemAction(
        name="gitlab_issues",
        display_name="GitLab Issues",
        system="gitlab",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/gitlab/issues",
        parameters=[
            {"name": "project_id", "type": "string", "required": False},
            {"name": "state", "type": "string", "required": False},
            {"name": "labels", "type": "string", "required": False},
        ],
        description="Lấy danh sách Issues"
    ),
    "jira_projects": SystemAction(
        name="jira_projects",
        display_name="Jira Projects",
        system="jira",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/jira/projects",
        description="Lấy danh sách projects"
    ),
    "jira_issue_detail": SystemAction(
        name="jira_issue_detail",
        display_name="Jira Issue Detail",
        system="jira",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/jira/issue-detail",
        parameters=[
            {"name": "issue_key", "type": "string", "required": True},
        ],
        description="Lấy thông tin chi tiết issue Jira (summary, description, status, assignee)"
    ),

    # =============================================================================
    # Database / SQL
    # =============================================================================
    "db_query": SystemAction(
        name="db_query",
        display_name="Truy vấn Database",
        system="database",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.HIGH,
        webhook_path="/webhook/db/query",
        parameters=[
            {"name": "query", "type": "string", "required": True},
            {"name": "database", "type": "string", "required": True},
        ],
        description="Thực thi truy vấn SQL (READ-ONLY)"
    ),
    "db_execute": SystemAction(
        name="db_execute",
        display_name="Thực thi SQL",
        system="database",
        action_type=ActionType.ACTION,
        risk_level=RiskLevel.CRITICAL,
        webhook_path="/webhook/db/execute",
        parameters=[
            {"name": "sql", "type": "string", "required": True},
            {"name": "database", "type": "string", "required": True},
        ],
        description="Thực thi câu lệnh SQL (CẢNH BÁO)"
    ),

    # =============================================================================
    # General / System
    # =============================================================================
    "system_status": SystemAction(
        name="system_status",
        display_name="System Status",
        system="system",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/system/status",
        description="Lấy tổng quan trạng thái hệ thống"
    ),
    "system_metrics": SystemAction(
        name="system_metrics",
        display_name="System Metrics",
        system="system",
        action_type=ActionType.QUERY,
        risk_level=RiskLevel.LOW,
        webhook_path="/webhook/system/metrics",
        description="Lấy metrics hệ thống"
    ),
}


class N8NConnector:
    """n8n webhook connector with approval workflow.

    Features:
    - Exponential backoff retry (base_delay=1s, retries=3)
    - Circuit breaker integration for fault tolerance
    """

    def __init__(self, base_url: str = "", api_key: str = "", webhook_secret: str = ""):
        from src.config import get_settings
        settings = get_settings()
        self.base_url = base_url or settings.n8n_base_url or "http://localhost:5678"
        self.api_key = api_key or settings.n8n_api_key or ""
        self.webhook_secret = webhook_secret or settings.n8n_webhook_secret or ""
        self._client: Optional[httpx.AsyncClient] = None
        self._circuit_breaker = get_circuit_breaker("n8n")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["X-N8N-API-KEY"] = self.api_key
            if self.webhook_secret:
                headers["X-Webhook-Secret"] = self.webhook_secret
            self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30)
        return self._client

    async def _retry_with_backoff(
        self,
        func,
        *args,
        base_delay: float = N8N_RETRY_CONFIG["base_delay"],
        max_delay: float = N8N_RETRY_CONFIG["max_delay"],
        max_retries: int = N8N_RETRY_CONFIG["retries"],
        **kwargs
    ):
        """Execute function with exponential backoff retry."""
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    delay = min(base_delay * (N8N_RETRY_CONFIG["exponential_base"] ** attempt), max_delay)
                    logger.warning(
                        "n8n_retry_attempt",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay=delay,
                        error=str(e)
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "n8n_retry_exhausted",
                        attempts=max_retries + 1,
                        error=str(e)
                    )

        raise last_exception

    async def trigger_workflow(self, webhook_path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Trigger an n8n webhook workflow with retry and circuit breaker."""
        if not await self._circuit_breaker.can_execute():
            logger.warning("n8n_circuit_breaker_open", path=webhook_path)
            return None

        async def _do_request():
            client = await self._get_client()
            response = await client.post(webhook_path, json=payload)
            if response.status_code in (200, 201):
                await self._circuit_breaker.record_success()
                return response.json()
            await self._circuit_breaker.record_failure()
            logger.warning("n8n_workflow_failed", path=webhook_path, status=response.status_code)
            return None

        try:
            result = await self._retry_with_backoff(_do_request)
            return result
        except Exception as e:
            await self._circuit_breaker.record_failure()
            logger.warning("n8n_workflow_error", path=webhook_path, error=str(e))
            return None

    async def get_ticket_detail(self, ticket_id: str, system: str = "itc") -> Optional[Dict[str, Any]]:
        """Get ticket details by ID.

        Tries:
        1. n8n webhook (itc_ticket_detail or ticket_get)
        2. Direct ITC API call if configured
        3. Returns None if both fail

        Args:
            ticket_id: The ticket ID to look up
            system: Which system to query ("itc", "jira", "itsm")

        Returns:
            Dict with ticket details or None
        """
        # Map system to appropriate webhook
        webhooks = {
            "itc": "/webhook/itc/ticket-detail",
            "jira": "/webhook/jira/issue-detail",
            "itsm": "/webhook/itsm/get-ticket",
        }
        webhook_path = webhooks.get(system, "/webhook/itc/ticket-detail")

        # Try n8n first
        result = await self.trigger_workflow(webhook_path, {"ticket_id": ticket_id})
        if result:
            return result

        # Fallback: try direct ITC API
        try:
            from src.config import get_settings
            settings = get_settings()
            itc_api_url = getattr(settings, 'itc_api_url', None)
            if itc_api_url:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(
                        f"{itc_api_url}/WorkOrder.do",
                        params={"woMode": "viewWO", "woID": ticket_id}
                    )
                    if response.status_code == 200:
                        content = response.text
                        import re
                        subject = ""
                        desc = ""
                        subject_match = re.search(r'<subject>([^<]+)</subject>', content, re.IGNORECASE)
                        if subject_match:
                            subject = subject_match.group(1).strip()
                        desc_match = re.search(r'<description>([^<]+)</description>', content, re.IGNORECASE)
                        if desc_match:
                            desc = desc_match.group(1).strip()
                        if subject or desc:
                            return {
                                "ticket_id": ticket_id,
                                "subject": subject or f"Ticket #{ticket_id}",
                                "description": desc,
                                "status": "unknown",
                                "source": "itc_api_direct",
                            }
        except Exception as e:
            logger.debug("direct_itc_api_failed", ticket_id=ticket_id, error=str(e))

        return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def get_n8n_connector() -> N8NConnector:
    """Get or create n8n connector singleton."""
    from src.config import get_settings
    settings = get_settings()
    return N8NConnector(
        base_url=settings.n8n_base_url,
        api_key=settings.n8n_api_key,
        webhook_secret=settings.n8n_webhook_secret,
    )


__all__ = [
    "N8NConnector", "get_n8n_connector",
    "SystemAction", "ActionRequest",
    "ActionType", "RiskLevel",
    "SYSTEM_ACTIONS",

]