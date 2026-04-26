import json
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException

from src.tools.n8n_connector import get_n8n_connector
from src.tools.n8n_tool import get_n8n_tool

router = APIRouter(prefix="/n8n", tags=["n8n"])


@router.get("/actions")
async def list_n8n_actions(action_type: Optional[str] = None):
    tool = get_n8n_tool()
    return {"actions": json.loads(tool.list_available_actions(action_type))}


@router.post("/query")
async def execute_n8n_query(
    action_name: str,
    parameters: dict = {},
    user_id: str = "system",
):
    tool = get_n8n_tool()
    result = json.loads(tool.execute_query(action_name, parameters, user_id))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Query failed"))
    return result


@router.post("/action/request")
async def request_n8n_action(
    action_name: str,
    parameters: dict = {},
    user_id: str = "unknown",
    user_display_name: str = "Unknown User",
):
    tool = get_n8n_tool()
    result = json.loads(tool.request_action(action_name, parameters, user_id, user_display_name))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to request action"))
    return result


@router.get("/approvals/pending")
async def list_pending_n8n_approvals(
    system: Optional[str] = None,
    risk_level: Optional[str] = None,
):
    tool = get_n8n_tool()
    return json.loads(tool.get_pending_approvals(system, risk_level))


@router.post("/approvals/{request_id}/approve")
async def approve_n8n_action(
    request_id: str,
    approver_name: str = "Admin",
):
    tool = get_n8n_tool()
    result = json.loads(tool.approve_action(request_id, approver_name))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to approve"))
    exec_result = json.loads(tool.execute_approved_action(request_id))
    return {"approval": result, "execution": exec_result}


@router.post("/approvals/{request_id}/reject")
async def reject_n8n_action(
    request_id: str,
    rejector_name: str = "Admin",
    reason: str = "",
):
    tool = get_n8n_tool()
    result = json.loads(tool.reject_action(request_id, rejector_name, reason))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to reject"))
    return result


@router.get("/approvals/{request_id}")
async def get_n8n_approval_status(request_id: str):
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


# =============================================================================
# External system webhook - nhận params từ GitLab, AD, Zabbix, ITC...
# Forward to n8n để n8n route đúng workflow
# =============================================================================
@router.post("/webhook/external/{source_system}")
async def external_webhook(
    source_system: str,  # gitlab, ad, zabbix, itc, servicenow, jira...
    payload: dict = {},
    # Optional routing params for n8n workflow filtering
    workflow: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
):
    """
    Generic webhook cho external systems gọi vào.
    
    Path params:
    - source_system: Tên hệ thống nguồn (gitlab, ad, zabbix, itc, servicenow, jira...)
    
    Query/body params:
    - payload: Dữ liệu từ hệ thống nguồn
    - workflow: Tên workflow n8n (ít khi dùng, để n8n tự detect)
    - priority: Priority (low, medium, high, critical)
    - category: Category để phân loại
    - tags: Comma-separated tags cho n8n filter
    
    Returns:
    - request_id: Để track
    - forwarded_to_n8n: URL n8n đã forward đến
    - source_system: System nguồn
    """
    from src.config import get_settings
    
    settings = get_settings()
    n8n_base = settings.n8n_base_url or "http://localhost:5678"
    
    # Build n8n webhook URL based on source system
    # n8n sẽ có webhook cho từng source: /webhook/gitlab, /webhook/ad, etc.
    webhook_path = f"/webhook/{source_system.lower()}"
    n8n_url = f"{n8n_base}{webhook_path}"
    
    # Enrich payload với metadata cho n8n route
    enriched_payload = {
        "source_system": source_system.lower(),
        "received_at": datetime.now().isoformat(),
        "routing": {
            "workflow": workflow or source_system.lower(),
            "priority": priority,
            "category": category,
            "tags": tags.split(",") if tags else [],
        },
        "data": payload,
    }
    
    # Forward to n8n
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(n8n_url, json=enriched_payload)
            n8n_response = resp.json() if resp.text else {}
    except Exception as e:
        n8n_response = {"error": str(e)}
    
    import uuid
    request_id = str(uuid.uuid4())[:8]
    
    return {
        "success": True,
        "request_id": request_id,
        "source_system": source_system.lower(),
        "forwarded_to_n8n": n8n_url,
        "n8n_response": n8n_response,
        "routing": enriched_payload["routing"],
    }


@router.get("/webhook/external/sources")
async def list_external_sources():
    """Liệt kê các external systems được support."""
    return {
        "sources": [
            {"name": "gitlab", "description": "GitLab events (push, merge, issue...)"},
            {"name": "ad", "description": "Active Directory events (user created, password reset...)"},
            {"name": "zabbix", "description": "Zabbix alerts (trigger, problem...)"},
            {"name": "itc", "description": "ITC ServiceDesk tickets"},
            {"name": "servicenow", "description": "ServiceNow incidents"},
            {"name": "jira", "description": "Jira issues"},
            {"name": "azuredevops", "description": "Azure DevOps events"},
            {"name": "custom", "description": "Custom external system"},
        ]
    }
