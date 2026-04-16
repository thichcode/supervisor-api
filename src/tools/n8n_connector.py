"""
n8n Connector - Connect to internal systems via n8n webhooks
Supports both query (read) and action (write/execute) operations
with approval workflow for actions
"""

import httpx
from typing import Optional, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import structlog

logger = structlog.get_logger()


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
}


class N8NConnector:
    """
    Connector to n8n webhooks for internal system integration
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        api_key: Optional[str] = None,
        timeout: int = 30,
        approval_store: Optional[Dict[str, ActionRequest]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.approval_store = approval_store or {}  # In-memory store
        
        # HTTP client
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def execute_query(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str = "system",
    ) -> Dict[str, Any]:
        """
        Execute a read-only query (no approval needed)
        """
        if action_name not in SYSTEM_ACTIONS:
            return {
                "success": False,
                "error": f"Unknown action: {action_name}",
            }
        
        action_def = SYSTEM_ACTIONS[action_name]
        
        if action_def.action_type != ActionType.QUERY:
            return {
                "success": False,
                "error": f"Action {action_name} requires approval. Use request_action() instead.",
            }
        
        return await self._execute_webhook(action_def, parameters, user_id)
    
    def request_action(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str,
        user_display_name: str = "Unknown",
    ) -> ActionRequest:
        """
        Request an action that requires approval
        Returns an ActionRequest that needs to be approved
        """
        if action_name not in SYSTEM_ACTIONS:
            raise ValueError(f"Unknown action: {action_name}")
        
        action_def = SYSTEM_ACTIONS[action_name]
        
        if action_def.action_type == ActionType.QUERY:
            raise ValueError(f"Action {action_name} is a query, no approval needed. Use execute_query() instead.")
        
        # Generate request ID
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        # Create approval request
        request = ActionRequest(
            request_id=request_id,
            action_type=action_name,
            system=action_def.system,
            action=action_def.display_name,
            parameters=parameters,
            risk_level=action_def.risk_level,
            requested_by=user_display_name,
        )
        
        # Store for later approval
        self.approval_store[request_id] = request
        
        logger.info("Action requested", 
                   request_id=request_id,
                   action=action_name,
                   risk_level=action_def.risk_level.value,
                   requested_by=user_display_name)
        
        return request
    
    def approve_action(
        self,
        request_id: str,
        approver_name: str = "Admin",
    ) -> ActionRequest:
        """
        Approve a pending action request
        """
        if request_id not in self.approval_store:
            raise ValueError(f"Request not found: {request_id}")
        
        request = self.approval_store[request_id]
        
        if request.status != "pending":
            raise ValueError(f"Request already {request.status}")
        
        # Get action definition
        action_def = SYSTEM_ACTIONS.get(request.action_type)
        if not action_def:
            raise ValueError(f"Unknown action type: {request.action_type}")
        
        # Mark as approved
        request.status = "approved"
        request.approved_by = approver_name
        request.approved_at = datetime.now(timezone.utc)
        
        logger.info("Action approved", 
                   request_id=request_id,
                   approver=approver_name)
        
        return request
    
    def reject_action(
        self,
        request_id: str,
        rejector_name: str = "Admin",
        reason: str = "",
    ) -> ActionRequest:
        """
        Reject a pending action request
        """
        if request_id not in self.approval_store:
            raise ValueError(f"Request not found: {request_id}")
        
        request = self.approval_store[request_id]
        
        if request.status != "pending":
            raise ValueError(f"Request already {request.status}")
        
        request.status = "rejected"
        request.result = {"rejected": True, "reason": reason}
        
        logger.info("Action rejected", 
                   request_id=request_id,
                   rejector=rejector_name,
                   reason=reason)
        
        return request
    
    async def execute_approved_action(
        self,
        request_id: str,
    ) -> Dict[str, Any]:
        """
        Execute an approved action
        """
        if request_id not in self.approval_store:
            return {
                "success": False,
                "error": f"Request not found: {request_id}",
            }
        
        request = self.approval_store[request_id]
        
        if request.status != "approved":
            return {
                "success": False,
                "error": f"Request not approved. Status: {request.status}",
            }
        
        # Get action definition
        action_def = SYSTEM_ACTIONS.get(request.action_type)
        if not action_def:
            return {
                "success": False,
                "error": f"Unknown action type: {request.action_type}",
            }
        
        # Execute webhook
        result = await self._execute_webhook(action_def, request.parameters, request.requested_by)
        
        # Store result
        request.result = result
        request.status = "executed"
        
        logger.info("Action executed",
                   request_id=request_id,
                   action=request.action_type,
                   success=result.get("success", False))
        
        return result
    
    async def _execute_webhook(
        self,
        action_def: SystemAction,
        parameters: Dict[str, Any],
        user_id: str,
    ) -> Dict[str, Any]:
        """Execute n8n webhook"""
        try:
            client = await self._get_client()
            
            # Build webhook URL
            url = f"{self.base_url}{action_def.webhook_path}"
            
            # Build payload
            payload = {
                "action": action_def.name,
                "system": action_def.system,
                "parameters": parameters,
                "triggered_by": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
            logger.debug("Executing webhook",
                        url=url,
                        action=action_def.name)
            
            # Execute
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "data": response.json() if response.text else {},
                }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}",
                    "details": response.text[:500],
                }
                
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Cannot connect to n8n at {self.base_url}",
            }
        except Exception as e:
            logger.error("Webhook execution failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    def get_pending_approvals(
        self,
        system: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
    ) -> List[ActionRequest]:
        """Get all pending approval requests"""
        pending = [
            req for req in self.approval_store.values()
            if req.status == "pending"
        ]
        
        if system:
            pending = [req for req in pending if req.system == system]
        
        if risk_level:
            pending = [req for req in pending if req.risk_level == risk_level]
        
        return sorted(pending, key=lambda x: x.requested_at)
    
    def get_available_actions(self, action_type: Optional[ActionType] = None) -> List[SystemAction]:
        """Get list of available actions"""
        actions = list(SYSTEM_ACTIONS.values())
        
        if action_type:
            actions = [a for a in actions if a.action_type == action_type]
        
        return actions


# Global connector instance (lazy initialization)
_connector: Optional[N8NConnector] = None


def get_n8n_connector() -> N8NConnector:
    """Get or create global n8n connector"""
    global _connector
    if _connector is None:
        import os
        _connector = N8NConnector(
            base_url=os.getenv("N8N_BASE_URL", "http://localhost:5678"),
            api_key=os.getenv("N8N_API_KEY"),
        )
    return _connector
