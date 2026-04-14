"""
n8n Tool - Tool wrapper for LLM to call n8n actions
Integrates with Supervisor's tool calling system
"""

import json
from typing import Optional, Dict, Any
from src.tools.n8n_connector import (
    N8NConnector,
    get_n8n_connector,
    ActionType,
    RiskLevel,
)


class N8NTool:
    """
    Tool for LLM to interact with n8n webhooks
    Supports queries and action requests with approval workflow
    """
    
    def __init__(self, connector: Optional[N8NConnector] = None):
        self.connector = connector or get_n8n_connector()
    
    def execute_query(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str = "system",
    ) -> str:
        """
        Execute a read-only query (no approval needed)
        
        Args:
            action_name: Name of the query action (e.g., 'backup_status', 'monitor_status')
            parameters: Parameters for the query
            user_id: ID of the user making the request
            
        Returns:
            JSON string with query result
        """
        import asyncio
        
        try:
            result = asyncio.get_event_loop().run_until_complete(
                self.connector.execute_query(action_name, parameters, user_id)
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    def list_available_actions(self, action_type: Optional[str] = None) -> str:
        """
        List all available actions
        
        Args:
            action_type: Filter by type ('query' or 'action')
            
        Returns:
            JSON string with list of available actions
        """
        at_filter = None
        if action_type:
            at_filter = ActionType.QUERY if action_type == "query" else ActionType.ACTION
        
        actions = self.connector.get_available_actions(at_filter)
        
        result = []
        for action in actions:
            result.append({
                "name": action.name,
                "display_name": action.display_name,
                "system": action.system,
                "type": action.action_type.value,
                "risk_level": action.risk_level.value,
                "description": action.description,
                "parameters": action.parameters,
                "approval_required": action.approval_required,
            })
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    def request_action(
        self,
        action_name: str,
        parameters: Dict[str, Any],
        user_id: str,
        user_display_name: str = "Unknown",
    ) -> str:
        """
        Request an action that requires approval
        
        Args:
            action_name: Name of the action (e.g., 'backup_restore', 'server_restart')
            parameters: Parameters for the action
            user_id: ID of the user making the request
            user_display_name: Display name of the user
            
        Returns:
            JSON string with approval request info
        """
        try:
            request = self.connector.request_action(
                action_name=action_name,
                parameters=parameters,
                user_id=user_id,
                user_display_name=user_display_name,
            )
            
            # Build approval message
            risk_emoji = {
                RiskLevel.LOW: "🟢",
                RiskLevel.MEDIUM: "🟡",
                RiskLevel.HIGH: "🟠",
                RiskLevel.CRITICAL: "🔴",
            }.get(request.risk_level, "⚪")
            
            return json.dumps({
                "success": True,
                "request_id": request.request_id,
                "action": request.action,
                "system": request.system,
                "risk_level": request.risk_level.value,
                "risk_emoji": risk_emoji,
                "status": "pending_approval",
                "approval_needed": True,
                "message": f"{risk_emoji} Yêu cầu này cần được phê duyệt trước khi thực hiện.",
                "approval_hint": f"Duyệt với: /approve {request.request_id}",
            }, ensure_ascii=False, indent=2)
            
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    def approve_action(
        self,
        request_id: str,
        approver_name: str = "Admin",
    ) -> str:
        """
        Approve a pending action request
        
        Args:
            request_id: ID of the approval request
            approver_name: Name of the approver
            
        Returns:
            JSON string with approval result
        """
        try:
            request = self.connector.approve_action(request_id, approver_name)
            
            return json.dumps({
                "success": True,
                "request_id": request.request_id,
                "action": request.action,
                "status": "approved",
                "message": f"Đã duyệt yêu cầu: {request.action}",
            }, ensure_ascii=False, indent=2)
            
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    def reject_action(
        self,
        request_id: str,
        rejector_name: str = "Admin",
        reason: str = "",
    ) -> str:
        """
        Reject a pending action request
        
        Args:
            request_id: ID of the approval request
            rejector_name: Name of the rejector
            reason: Reason for rejection
            
        Returns:
            JSON string with rejection result
        """
        try:
            request = self.connector.reject_action(request_id, rejector_name, reason)
            
            return json.dumps({
                "success": True,
                "request_id": request.request_id,
                "action": request.action,
                "status": "rejected",
                "message": f"Đã từ chối yêu cầu: {request.action}",
            }, ensure_ascii=False, indent=2)
            
        except ValueError as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    def execute_approved_action(self, request_id: str) -> str:
        """
        Execute an approved action
        
        Args:
            request_id: ID of the approved request
            
        Returns:
            JSON string with execution result
        """
        import asyncio
        
        try:
            result = asyncio.get_event_loop().run_until_complete(
                self.connector.execute_approved_action(request_id)
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    def get_pending_approvals(
        self,
        system: Optional[str] = None,
        risk_level: Optional[str] = None,
    ) -> str:
        """
        Get all pending approval requests
        
        Args:
            system: Filter by system name
            risk_level: Filter by risk level (low, medium, high, critical)
            
        Returns:
            JSON string with pending requests
        """
        rl_filter = None
        if risk_level:
            rl_filter = RiskLevel(risk_level.lower())
        
        pending = self.connector.get_pending_approvals(system, rl_filter)
        
        result = []
        for req in pending:
            result.append({
                "request_id": req.request_id,
                "action": req.action,
                "system": req.system,
                "risk_level": req.risk_level.value,
                "parameters": req.parameters,
                "requested_by": req.requested_by,
                "requested_at": req.requested_at.isoformat(),
            })
        
        return json.dumps({
            "count": len(result),
            "pending": result,
        }, ensure_ascii=False, indent=2)
    
    def detect_action_from_text(self, text: str) -> Optional[str]:
        """
        Detect if user wants to perform an action based on text
        
        Args:
            text: User's message text
            
        Returns:
            Detected action name or None
        """
        text_lower = text.lower()
        
        # Keyword mappings to actions
        action_keywords = {
            # Backup
            "backup_status": ["trạng thái backup", "kiểm tra backup", "backup status", "tình trạng backup"],
            "backup_restore": ["khôi phục backup", "restore backup", "phục hồi backup"],
            
            # Monitoring
            "monitor_status": ["trạng thái monitor", "monitor status", "tình trạng giám sát"],
            "monitor_alert_ack": ["acknowledge alert", "ack alert", "xác nhận alert"],
            
            # ITSM
            "ticket_create": ["tạo ticket", "tạo yêu cầu", "create ticket", "new ticket"],
            "ticket_update": ["cập nhật ticket", "update ticket", "đóng ticket", "close ticket"],
            
            # Infrastructure
            "server_restart": ["restart server", "khởi động lại server", "reboot server"],
            "server_status": ["trạng thái server", "server status", "kiểm tra server"],
            
            # IAM
            "account_unlock": ["mở khóa tài khoản", "unlock account", "bỏ khóa tài khoản"],
            "account_reset_password": ["reset password", "đặt lại mật khẩu", "change password"],
        }
        
        for action, keywords in action_keywords.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return action
        
        return None


# Tool instance
_n8n_tool: Optional[N8NTool] = None


def get_n8n_tool() -> N8NTool:
    """Get or create global n8n tool"""
    global _n8n_tool
    if _n8n_tool is None:
        _n8n_tool = N8NTool()
    return _n8n_tool
