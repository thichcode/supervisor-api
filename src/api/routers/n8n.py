import json
from typing import Optional

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
