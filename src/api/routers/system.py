from fastapi import APIRouter

from src.core.schemas import SystemQueryRequest, SystemQueryResponse

router = APIRouter(tags=["system"])


@router.post("/system/query", response_model=SystemQueryResponse)
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
