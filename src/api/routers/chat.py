from fastapi import APIRouter

from src.core.schemas import ChatRequest, ChatResponse
from src.services.chat_service import ChatService

router = APIRouter(tags=["chat"])
chat_service = ChatService()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    from src.api.app import _auto_send_to_power_automate
    return await chat_service.handle_chat(request, auto_send_callback=_auto_send_to_power_automate)


@router.post("/chat/harness", response_model=ChatResponse)
async def chat_via_harness(request: ChatRequest):
    from src.api.app import _auto_send_to_power_automate, get_harness_bridge
    return await chat_service.handle_harness_chat(
        request,
        auto_send_callback=_auto_send_to_power_automate,
        bridge_getter=get_harness_bridge,
    )
