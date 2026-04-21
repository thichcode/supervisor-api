from __future__ import annotations

import time
import uuid
from datetime import datetime

from src.config import get_settings
from src.core.approval import approval_service
from src.core.schemas import CaseInfo, ChatRequest, ChatResponse, ConversationInfo, InputPayload, MessageInfo, UserInfo
from src.core.teams_targeting import TeamsTargetResolver, extract_teams_signal
from src.core.thread_targeting import GroupChatTargetResolver
from src.memory.service import MemoryService
from src.services.interaction_service import InteractionService

settings = get_settings()


class ChatService:
    def __init__(self):
        self.group_chat_resolver = GroupChatTargetResolver()
        self.teams_target_resolver = TeamsTargetResolver()

    def _normalize_chat_context(self, request: ChatRequest) -> dict:
        metadata = dict(request.metadata or {})
        platform = metadata.get("platform") or metadata.get("source") or "direct_chat"
        chat_type = metadata.get("chat_type")
        chat_scope = metadata.get("chat_scope")
        group_chat = metadata.get("group_chat")
        channel_type = metadata.get("channel_type")

        if chat_type is None:
            if channel_type in {"channel", "group", "mpim"}:
                chat_type = "group"
            elif metadata.get("guild_id"):
                chat_type = "group"
            elif metadata.get("conversation_type") in {"channel", "group"}:
                chat_type = "group"
            elif platform in {"telegram", "direct_chat", "harness_chat"}:
                chat_type = "private"

        if group_chat is None:
            if chat_type == "private":
                group_chat = False
            elif chat_type in {"group", "supergroup", "channel"}:
                group_chat = True
            elif channel_type in {"channel", "group", "mpim"} or metadata.get("guild_id"):
                group_chat = True
            elif platform in {"telegram", "direct_chat", "harness_chat"}:
                group_chat = False

        if chat_scope is None:
            if group_chat is True or chat_type in {"group", "supergroup", "channel"}:
                chat_scope = "group"
            elif group_chat is False or chat_type == "private":
                chat_scope = "dm"

        if chat_type is None and chat_scope:
            chat_type = "group" if chat_scope == "group" else "private"

        return {
            "platform": platform,
            "chat_type": chat_type,
            "chat_scope": chat_scope,
            "group_chat": bool(group_chat) if group_chat is not None else False,
        }

    async def handle_chat(self, request: ChatRequest, auto_send_callback=None) -> ChatResponse:
        import src.api as api_module

        request_id = str(uuid.uuid4())
        thread_id = request.thread_id or f"chat-{request.user_id}-{int(time.time())}"
        chat_context = self._normalize_chat_context(request)
        payload = InputPayload(
            request_id=request_id,
            source="direct_chat",
            timestamp=datetime.now().isoformat(),
            user=UserInfo(
                id=request.user_id,
                display_name=request.display_name,
                role=request.metadata.get("role"),
                team=request.metadata.get("team"),
                vip_flag=request.metadata.get("vip_flag", False),
            ),
            conversation=ConversationInfo(
                thread_id=thread_id,
                message_id=f"msg-{request_id}",
                chat_type=chat_context["chat_type"],
                chat_scope=chat_context["chat_scope"],
                group_chat=chat_context["group_chat"],
                platform=chat_context["platform"],
            ),
            case=CaseInfo(
                case_id=request.case_id,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
            ) if (request.case_id or request.ticket_id) else None,
            message=MessageInfo(text=request.message),
        )

        is_group_chat = bool(chat_context.get("group_chat", False))
        is_teams_message = request.metadata.get("source") == "ms_teams" or request.metadata.get("platform") == "teams" or any(
            key in request.metadata for key in ("conversation_type", "conversationType", "mention_targets", "mentions", "reply_target", "replyToTarget", "sender_is_bot", "from_bot")
        )

        async with api_module.async_session() as session:
            memory_service = MemoryService(session, api_module.redis_cache)
            interaction_service = InteractionService(session)
            memory = await memory_service.retrieve(payload)

            history_texts = [*memory.recent_messages, memory.conversation_summary or ""]
            routing_metadata = {}
            target_decision = None

            if is_teams_message:
                teams_signal = extract_teams_signal(request.metadata)
                teams_decision = self.teams_target_resolver.resolve(
                    current_text=request.message,
                    signal=teams_signal,
                    history_texts=history_texts,
                )
                routing_metadata = {
                    "teams_target": teams_decision.target.value,
                    "teams_reason": teams_decision.reason,
                    "teams_confidence": teams_decision.confidence,
                }

                if teams_decision.should_skip:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        status="skipped",
                        message="",
                        message_type=request.message_type,
                        confidence=teams_decision.confidence,
                        metadata={**routing_metadata, "teams_message": True, "skipped": True},
                    )

                if teams_decision.should_clarify:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        status="needs_clarification",
                        message="Chưa rõ message này đang nhắm tới Thuong hay workflow bot. Bạn xác nhận giúp mình?",
                        message_type=request.message_type,
                        confidence=teams_decision.confidence,
                        metadata={**routing_metadata, "teams_message": True, "needs_clarification": True},
                    )

                if teams_decision.should_respond:
                    target_decision = teams_decision

            if target_decision is None:
                target_decision = self.group_chat_resolver.resolve(
                    current_text=request.message,
                    history_texts=history_texts,
                    group_chat=is_group_chat,
                )
                routing_metadata = {
                    **routing_metadata,
                    "group_chat": is_group_chat,
                    "group_chat_target": target_decision.target.value,
                    "group_chat_reason": target_decision.reason,
                    "group_chat_confidence": target_decision.confidence,
                }

                if is_group_chat and target_decision.should_skip:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        status="skipped",
                        message="",
                        message_type=request.message_type,
                        confidence=target_decision.confidence,
                        metadata={**routing_metadata, "skipped": True},
                    )

                if is_group_chat and target_decision.should_clarify:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        status="needs_clarification",
                        message="Chưa rõ message này đang nhắm tới Thuong hay workflow bot. Bạn xác nhận giúp mình?",
                        message_type=request.message_type,
                        confidence=target_decision.confidence,
                        metadata={**routing_metadata, "needs_clarification": True},
                    )

            if (
                settings.enable_user_style_learning
                and settings.should_learn_user_style(request.user_id)
            ):
                learning_user_ids = sorted(settings.style_learning_user_ids)
                await memory_service.commit(payload, memory_snapshot=memory)
                return ChatResponse(
                    request_id=request_id,
                    status="skipped",
                    message="",
                    message_type=request.message_type,
                    confidence=0.0,
                    metadata={
                        **routing_metadata,
                        "style_learning_only": True,
                        "style_learning_user_ids": learning_user_ids,
                        "skipped": True,
                    },
                )

            result = await api_module.supervisor.process(payload, memory)
            conversation_metadata = {**routing_metadata, **chat_context}
            if conversation_metadata:
                result.metadata = {**(result.metadata or {}), **conversation_metadata}
            await memory_service.commit(
                payload,
                memory_snapshot=memory,
                assistant_text=result.answer,
                result_metadata=result.metadata or {},
            )

            await interaction_service.log_interaction(
                request_id=request_id,
                thread_id=thread_id,
                user_id=request.user_id,
                input_text=request.message,
                output_text=result.answer,
                intent=result.metadata.get("intent") if result.metadata else None,
                risk_level=result.risk_level,
                confidence_score=result.confidence,
                model_provider=(result.metadata or {}).get("model_provider"),
                model_name=(result.metadata or {}).get("model_name") or settings.llm_model,
                kb_sources=(result.metadata or {}).get("kb_sources", []),
                approval_required=result.status == "needs_review",
                approval_status="pending" if result.status == "needs_review" else "not_needed",
                processing_latency_ms=(result.metadata or {}).get("processing_time_ms"),
                outcome_status=result.status,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
                extra_metadata=result.metadata or {},
            )
            await session.commit()

        if result.status == "needs_review":
            approval = await approval_service.create_approval(
                request_id=request_id,
                user_id=request.user_id,
                display_name=request.display_name,
                original_message=request.message,
                ai_response=result.answer,
                confidence=result.confidence,
                action_type="send_message",
                metadata={
                    "thread_id": thread_id,
                    "case_id": request.case_id,
                    "ticket_id": request.ticket_id,
                    "ticket_system": request.ticket_system,
                    "agents_used": result.metadata.get("agents_used", []),
                    "intent": result.metadata.get("intent"),
                    "risk_level": result.risk_level,
                    "kb_sources": (result.metadata or {}).get("kb_sources", []),
                    "kb_evidence": (result.metadata or {}).get("kb_evidence", []),
                    **conversation_metadata,
                },
            )

            async with api_module.async_session() as session:
                interaction_service = InteractionService(session)
                await interaction_service.log_interaction(
                    request_id=request_id,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    input_text=request.message,
                    output_text=result.answer,
                    intent=result.metadata.get("intent") if result.metadata else None,
                    risk_level=result.risk_level,
                    confidence_score=result.confidence,
                    model_provider=(result.metadata or {}).get("model_provider"),
                    model_name=(result.metadata or {}).get("model_name") or settings.llm_model,
                    kb_sources=(result.metadata or {}).get("kb_sources", []),
                    approval_required=True,
                    approval_status="pending",
                    processing_latency_ms=(result.metadata or {}).get("processing_time_ms"),
                    outcome_status="pending_approval",
                    ticket_id=request.ticket_id,
                    ticket_system=request.ticket_system,
                    extra_metadata={**(result.metadata or {}), "approval_id": approval.id},
                )
                await interaction_service.create_approval_record(
                    request_id=request_id,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    proposed_response=result.answer,
                    reason="confidence_below_threshold",
                    risk_level=result.risk_level,
                    confidence_score=result.confidence,
                    status="pending",
                    ticket_id=request.ticket_id,
                    ticket_system=request.ticket_system,
                )
                await session.commit()

            return ChatResponse(
                request_id=request_id,
                status="pending_approval",
                message=f"⚠️ Phản hồi AI (confidence: {result.confidence:.0%}) cần được duyệt trước khi gửi cho user.",
                message_type=request.message_type,
                confidence=result.confidence,
                metadata={**result.metadata, "approval_id": approval.id, "approval_required": True, "threshold": 0.5},
            )

        if result.status == "completed" and settings.power_automate_webhook_url and auto_send_callback:
            try:
                await auto_send_callback(result)
            except Exception:
                pass

        return ChatResponse(
            request_id=request_id,
            status=result.status,
            message=result.answer,
            message_type=request.message_type,
            confidence=result.confidence,
            metadata=result.metadata,
        )

    async def handle_harness_chat(self, request: ChatRequest, auto_send_callback=None, bridge_getter=None) -> ChatResponse:
        import src.api as api_module

        request_id = str(uuid.uuid4())
        thread_id = request.thread_id or f"chat-harness-{request.user_id}-{int(time.time())}"
        chat_context = self._normalize_chat_context(
            ChatRequest(
                user_id=request.user_id,
                display_name=request.display_name,
                message=request.message,
                thread_id=request.thread_id,
                case_id=request.case_id,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
                message_type=request.message_type,
                metadata={**request.metadata, "platform": request.metadata.get("platform") or "harness_chat"},
            )
        )
        payload = InputPayload(
            request_id=request_id,
            source="harness_chat",
            timestamp=datetime.now().isoformat(),
            user=UserInfo(
                id=request.user_id,
                display_name=request.display_name,
                role=request.metadata.get("role"),
                team=request.metadata.get("team"),
                vip_flag=request.metadata.get("vip_flag", False),
            ),
            conversation=ConversationInfo(
                thread_id=thread_id,
                message_id=f"msg-{request_id}",
                chat_type=chat_context["chat_type"],
                chat_scope=chat_context["chat_scope"],
                group_chat=chat_context["group_chat"],
                platform=chat_context["platform"],
            ),
            case=CaseInfo(
                case_id=request.case_id,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
            ) if (request.case_id or request.ticket_id) else None,
            message=MessageInfo(text=request.message),
        )

        async with api_module.async_session() as session:
            memory_service = MemoryService(session, api_module.redis_cache)
            interaction_service = InteractionService(session)
            memory = await memory_service.retrieve(payload)
            harness_bridge = bridge_getter() if bridge_getter else None
            if harness_bridge:
                result = await harness_bridge.process(payload, memory)
            else:
                result = await api_module.supervisor.process(payload, memory)
            conversation_metadata = dict(chat_context)
            if conversation_metadata:
                result.metadata = {**(result.metadata or {}), **conversation_metadata}
            await memory_service.commit(
                payload,
                memory_snapshot=memory,
                assistant_text=result.answer,
                result_metadata=result.metadata or {},
            )
            await interaction_service.log_interaction(
                request_id=request_id,
                thread_id=thread_id,
                user_id=request.user_id,
                input_text=request.message,
                output_text=result.answer,
                intent=result.metadata.get("intent") if result.metadata else None,
                risk_level=result.risk_level,
                confidence_score=result.confidence,
                model_provider=(result.metadata or {}).get("model_provider"),
                model_name=(result.metadata or {}).get("model_name") or settings.llm_model,
                kb_sources=(result.metadata or {}).get("kb_sources", []),
                approval_required=result.status == "needs_review",
                approval_status="pending" if result.status == "needs_review" else "not_needed",
                processing_latency_ms=(result.metadata or {}).get("processing_time_ms"),
                outcome_status=result.status,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
                extra_metadata={**(result.metadata or {}), "harness_metrics": result.metadata.get("harness_metrics") if result.metadata else None},
            )
            await session.commit()

        harness_metrics = result.metadata.get("harness_metrics") if hasattr(result, "metadata") else {}
        harness_evaluation = result.metadata.get("harness_evaluation") if hasattr(result, "metadata") else {}

        if result.status == "needs_review":
            approval = await approval_service.create_approval(
                request_id=request_id,
                user_id=request.user_id,
                display_name=request.display_name,
                original_message=request.message,
                ai_response=result.answer,
                confidence=result.confidence,
                action_type="send_message",
                metadata={
                    "thread_id": thread_id,
                    "case_id": request.case_id,
                    "ticket_id": request.ticket_id,
                    "ticket_system": request.ticket_system,
                    "agents_used": result.metadata.get("agents_used", []),
                    "intent": result.metadata.get("intent"),
                    "risk_level": result.risk_level,
                    "harness_execution_id": harness_metrics.get("execution_id") if harness_metrics else None,
                },
            )
            async with api_module.async_session() as session:
                interaction_service = InteractionService(session)
                await interaction_service.log_interaction(
                    request_id=request_id,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    input_text=request.message,
                    output_text=result.answer,
                    intent=result.metadata.get("intent") if result.metadata else None,
                    risk_level=result.risk_level,
                    confidence_score=result.confidence,
                    model_provider=(result.metadata or {}).get("model_provider"),
                    model_name=(result.metadata or {}).get("model_name") or settings.llm_model,
                    kb_sources=(result.metadata or {}).get("kb_sources", []),
                    approval_required=True,
                    approval_status="pending",
                    processing_latency_ms=(result.metadata or {}).get("processing_time_ms"),
                    outcome_status="pending_approval",
                    ticket_id=request.ticket_id,
                    ticket_system=request.ticket_system,
                    extra_metadata={**(result.metadata or {}), "approval_id": approval.id},
                )
                await interaction_service.create_approval_record(
                    request_id=request_id,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    proposed_response=result.answer,
                    reason="confidence_below_threshold",
                    risk_level=result.risk_level,
                    confidence_score=result.confidence,
                    status="pending",
                    ticket_id=request.ticket_id,
                    ticket_system=request.ticket_system,
                )
                await session.commit()
            return ChatResponse(
                request_id=request_id,
                status="pending_approval",
                message=f"⚠️ Phản hồi AI (confidence: {result.confidence:.0%}) cần được duyệt trước khi gửi cho user.\n\nHarness: {harness_metrics.get('execution_id', 'N/A') if harness_metrics else 'N/A'}",
                message_type=request.message_type,
                confidence=result.confidence,
                metadata={**result.metadata, "approval_id": approval.id, "approval_required": True, "threshold": 0.5, "harness_metrics": harness_metrics, "harness_evaluation": harness_evaluation},
            )

        if result.status == "completed" and settings.power_automate_webhook_url and auto_send_callback:
            try:
                await auto_send_callback(result)
            except Exception:
                pass

        return ChatResponse(
            request_id=request_id,
            status=result.status,
            message=result.answer,
            message_type=request.message_type,
            confidence=result.confidence,
            metadata={**result.metadata, "harness_metrics": harness_metrics, "harness_evaluation": harness_evaluation},
        )
