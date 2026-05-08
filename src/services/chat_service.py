from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
import structlog

from src.config import get_settings
from src.core.approval import approval_service
from src.core.schemas import CaseInfo, ChatRequest, ChatResponse, ConversationInfo, InputPayload, MessageInfo, UserInfo
from src.core.teams_targeting import TeamsTargetResolver, extract_teams_signal
from src.core.thread_targeting import GroupChatTargetResolver
from src.memory.service import MemoryService
from src.memory.hindsight_service import get_hindsight_service
from src.services.interaction_service import InteractionService

settings = get_settings()
logger = structlog.get_logger(__name__)


class ChatService:
    def __init__(self):
        self.group_chat_resolver = GroupChatTargetResolver()
        self.teams_target_resolver = TeamsTargetResolver()
        self.hindsight = get_hindsight_service()

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

    # ← FIX v3: Entity extraction from user message
    def _extract_entities(self, text: str) -> dict:
        """Extract entities from user message.
        
        Returns:
            - user_mentioned: name of user being discussed (e.g., "anh Sơn")
            - object_type: ticket/case/issue/system
            - action_problem: what happened (e.g., "không vào được", "lỗi")
            - ticket_id: explicit ticket ID found
        """
        import re
        t = (text or "").strip()
        entities = {
            "user_mentioned": None,
            "object_type": None,
            "action_problem": None,
            "ticket_id": None,
        }
        
        # Extract ticket ID patterns: ticket #123, WO#456, case #789
        ticket_patterns = [
            r"(?:ticket|case|wo|inc|request)\s*#?(\d+)",
            r"#(\d{4,})",
        ]
        for pattern in ticket_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                entities["ticket_id"] = match.group(1)
                entities["object_type"] = "ticket"
                break
        
        # Extract user mentions (anh/chị/bạn + name)
        user_patterns = [
            r"(?:anh|chị|bạn|ông|bà|mr\.?|ms\.?)\s+([A-Za-zÀ-ỹ]{2,})",
            r"nhân viên\s+([A-Za-zÀ-ỹ]{2,})",
            r"user\s+([A-Za-zÀ-ỹ]{2,})",
        ]
        for pattern in user_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                entities["user_mentioned"] = match.group(1).strip()
                break
        
        # Extract object type
        if any(kw in t.lower() for kw in ["ticket", "case", "wo#", "request#"]):
            entities["object_type"] = "ticket"
        elif any(kw in t.lower() for kw in ["lỗi", "error", "bug", "sự cố", "incident"]):
            entities["object_type"] = "incident"
        elif any(kw in t.lower() for kw in ["email", "mail", "outlook"]):
            entities["object_type"] = "email"
        elif any(kw in t.lower() for kw in ["vpn", "network", "wifi", "internet"]):
            entities["object_type"] = "network"
        
        # Extract action/problem (what's happening)
        problem_patterns = [
            r"(?:không\s+|không\s+thể\s+|cannot|can't|unable to)\s+(\w+)",
            r"(?:lỗi|error|bug)\s+(.+?)(?:\.|$)",
            r"(?:vấn đề|problem|issue)\s+(?:là|is|:)\s*(.+?)(?:\.|$)",
            r"(\w+)\s+bị\s+(?:lỗi|hỏng|die|down)",
        ]
        for pattern in problem_patterns:
            match = re.search(pattern, t, re.IGNORECASE)
            if match:
                entities["action_problem"] = match.group(1).strip()
                break
        
        # If no specific problem found but message contains problem keywords
        if not entities["action_problem"]:
            problem_keywords = ["không vào được", "không đăng nhập", "lỗi", "chậm", "down", "die", "không truy cập"]
            for kw in problem_keywords:
                if kw in t.lower():
                    entities["action_problem"] = kw
                    break
        
        return entities

    # ← FIX v3: Build clarification question based on extracted entities
    def _build_clarification_message(self, entities: dict, conversation_id: str, kb_hit: bool, kb_sources: list = None) -> str:
        """Build a clarification question when confidence is [0.5, 0.9)."""
        parts = []
        
        # Always include conversation_id
        parts.append(f"[conversation_id: {conversation_id}]")
        
        # Context about what we understood
        understood = []
        if entities.get("user_mentioned"):
            understood.append(f"người dùng liên quan: {entities['user_mentioned']}")
        if entities.get("object_type"):
            understood.append(f"object: {entities['object_type']}")
        if entities.get("action_problem"):
            understood.append(f"vấn đề: {entities['action_problem']}")
        if entities.get("ticket_id"):
            understood.append(f"ticket_id: {entities['ticket_id']}")
        
        if understood:
            parts.append(f"Tóm tắt: {', '.join(understood)}")
        else:
            parts.append("Tóm tắt: Chưa xác định rõ vấn đề")
        
        # KB evidence if available
        if kb_hit and kb_sources:
            sources_info = []
            for src in kb_sources[:3]:
                title = src.get("title", src.get("content", "N/A")[:50])
                sources_info.append(f"- {title}")
            if sources_info:
                parts.append(f"Thông tin xác thực từ KB:\n" + "\n".join(sources_info))
        
        # What we need to clarify
        missing = []
        if not entities.get("ticket_id") and entities.get("object_type") == "ticket":
            missing.append("mã ticket hoặc ảnh lỗi khi user mở ticket")
        if not entities.get("action_problem"):
            missing.append("mô tả chi tiết sự cố (lỗi gì, khi nào, ở đâu)")
        if not kb_hit:
            missing.append("thêm thông tin để tra cứu KB")
        
        if missing:
            parts.append(f"Hướng xử lý / câu hỏi cần bổ sung: {', '.join(missing)}")
        
        # Closing
        if kb_hit:
            parts.append("Tôi đã tìm thấy thông tin liên quan nhưng cần thêm dữ liệu để kết luận chính xác.")
        else:
            parts.append("Tôi chưa đủ thông tin từ KB/hệ thống để kết luận. Bạn bổ sung giúp mình nhé.")
        
        return "\n".join(parts)

    # ← FIX v3: Build grounded answer with entities and KB sources
    def _build_grounded_answer(
        self,
        answer: str,
        entities: dict,
        conversation_id: str,
        kb_hit: bool,
        kb_sources: list = None,
    ) -> str:
        """Build a grounded answer that follows the user's requirements.
        
        Format:
        [conversation_id: {{conversation_id}}]
        Tóm tắt: ...
        Thông tin xác thực từ KB/ticket: ...
        Hướng xử lý / câu hỏi cần bổ sung: ...
        """
        parts = []
        
        # Always include conversation_id
        parts.append(f"[conversation_id: {conversation_id}]")
        
        # Context about what we understood
        understood = []
        if entities.get("user_mentioned"):
            understood.append(f"người dùng liên quan: {entities['user_mentioned']}")
        if entities.get("object_type"):
            understood.append(f"object: {entities['object_type']}")
        if entities.get("action_problem"):
            understood.append(f"vấn đề: {entities['action_problem']}")
        if entities.get("ticket_id"):
            understood.append(f"ticket_id: {entities['ticket_id']}")
        
        if understood:
            parts.append(f"Tóm tắt: {', '.join(understood)}")
        
        # If we have KB sources, add evidence
        if kb_hit and kb_sources:
            sources_info = []
            for src in kb_sources[:3]:
                title = src.get("title", src.get("content", "N/A")[:80])
                sources_info.append(f"- {title}")
            if sources_info:
                parts.append(f"Thông tin xác thực từ KB:\n" + "\n".join(sources_info))
        
        # Add the answer if it's substantive
        if answer and len(answer) > 10:
            if not answer.startswith("[conversation_id:"):
                parts.append(answer)
        
        return "\n\n".join(parts) if len(parts) > 1 else (answer or "")

    def _build_fallback_answer(self, query: str, conversation_id: str, confidence: float) -> str:
        """Generate a structured fallback answer (4W1H or step‑by‑step) when KB is not hit.
        
        Format:
        [conversation_id: ...]
        🔍 *Những gì cần xác định:*
        - **Vấn đề là gì?** (What) – ...
        - **Xảy ra ở đâu?** (Where) – ...
        - **Xảy ra khi nào?** (When) – ...
        - **Tại sao có thể xảy ra?** (Why) – ...
        - **Làm thế nào để xử lý?** (How) – ...
        👉 Hướng dẫn chung hoặc yêu cầu bổ sung thông tin.
        """
        parts = [f"[conversation_id: {conversation_id}]"]
        parts.append("🔍 *Những gì cần xác định để hỗ trợ chính xác:*")
        parts.append("- **Vấn đề là gì?** (What) – Hãy mô tả cụ thể lỗi hoặc yêu cầu.")
        parts.append("- **Xảy ra ở đâu?** (Where) – Hệ thống, thiết bị, ứng dụng nào?")
        parts.append("- **Xảy ra khi nào?** (When) – Thời gian bắt đầu, tần suất.")
        parts.append("- **Tại sao có thể xảy ra?** (Why) – Nguyên nhân phổ biến (nếu biết).")
        parts.append("- **Làm thế nào để xử lý?** (How) – Các bước kiểm tra hoặc khắc phục.")
        parts.append("")
        parts.append("👉 *Hướng dẫn chung:*")
        if confidence < 0.5:
            parts.append("Tôi chưa đủ thông tin để đưa ra câu trả lời chính xác. Vui lòng cung cấp thêm chi tiết theo cấu trúc trên.")
        else:
            parts.append("Tôi đã tìm thấy một số thông tin liên quan nhưng chưa đủ để kết luận. Hãy bổ sung các thông tin còn thiếu theo mẫu trên.")
        parts.append("")
        parts.append("Nếu cần hướng dẫn từng bước cụ thể, vui lòng mô tả rõ hơn về tình huống của bạn.")
        return "\n".join(parts)

    # ← FIX v2: classify whether this message needs a bot response
    def _needs_reply(self, text: str) -> tuple[bool, str]:
        """Returns (needs_reply, reason)."""
        t = (text or "").strip().lower()
        if not t:
            return False, "empty"
        # Emoji-only or reaction
        emoji_pattern = re.compile(
            r"^[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F"
            r"\U0001FA70-\U0001FAFF"
            r"\u2600-\u26FF\u2700-\u27BF"
            r"👍🙏✅❌✔️👏❤️❤️‍🔥🫡💪😊😂🥹😎]+$"
        )
        if emoji_pattern.match(t) or len(t) <= 3 and not t.isalnum():
            return False, "emoji_or_reaction"
        # Ack/no-op patterns
        ack_patterns = [
            "ok", "okay", "oke", "được", "ừ", "ờ", "vâng", "dạ", "có",
            "thanks", "thank", "cảm ơn", "cám ơn",
            "nhận được", "đã nhận", "received", "noted",
            "👍", "👌", "😄", "great",
            "không cần", "bỏ qua", "skip",
        ]
        if t in ack_patterns:
            return False, "acknowledgment"
        # Question or request → needs reply
        if any(q in t for q in ["?", "là gì", "sao", "làm sao", "tại sao", "muốn", "cần", "yêu cầu", "hỗ trợ", "giúp", "where", "how", "what", "why"]):
            return True, "question_or_request"
        # Ticket/case update without question → no reply needed
        if any(k in t for k in ["đã cập nhật", "đã tạo", "ticket #", "case #", "đóng case"]):
            return False, "system_event"
        # Default: if no clear intent to respond, do not reply
        return False, "unclear_intent_no_need"

    async def handle_chat(self, request: ChatRequest, auto_send_callback=None) -> ChatResponse:
        import src.api as api_module

        # ← FIX v2: skip processing for ack/emoji/no-op messages
        needs_response, skip_reason = self._needs_reply(request.message)
        if not needs_response:
            return ChatResponse(
                request_id=request.message_id if request.message_id else str(uuid.uuid4()),
                thread_id=request.thread_id,
                message_id=request.message_id,
                status="skipped",
                customer_reply="",  # no outbound reply
                message_type=request.message_type,
                confidence=0.0,
                metadata={"skip_reason": skip_reason, "conversation_id": request.thread_id},
                delivery_status="skipped",
                conversation_id=request.thread_id,
            )

        # Determine request_id and message_id
        platform_message_id = request.message_id if request.message_id else None
        if platform_message_id:
            request_id = str(platform_message_id)  # use platform message_id as request_id for clarity
        else:
            request_id = str(uuid.uuid4())
        
        conversation_id = request.thread_id or f"chat-{request.user_id}-{int(time.time())}"
        thread_id = conversation_id  # canonical name
        
        # Determine message_id for conversation
        if platform_message_id:
            conv_message_id = platform_message_id
        else:
            conv_message_id = f"msg-{request_id}"
        
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
                message_id=conv_message_id,
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
            message_id=request.message_id,  # Truyền platform message_id vào InputPayload
        )

        is_group_chat = bool(chat_context.get("group_chat", False))
        is_teams_message = request.metadata.get("source") == "ms_teams" or request.metadata.get("platform") == "teams" or any(
            key in request.metadata for key in ("conversation_type", "conversationType", "mention_targets", "mentions", "reply_target", "replyToTarget", "sender_is_bot", "from_bot")
        )

        async with api_module.async_session() as session:
            memory_service = MemoryService(session, api_module.redis_cache)
            interaction_service = InteractionService(session)
            memory = await memory_service.retrieve(payload)

            # ← HINDSIGHT: Recall relevant memories before processing with timeout
            if self.hindsight.enabled:
                try:
                    hindsight_memories = await asyncio.wait_for(
                        self.hindsight.recall(
                            query=request.message,
                            limit=5,
                        ),
                        timeout=2.0,  # 2 second timeout to avoid blocking
                    )
                    if hindsight_memories:
                        memory.external_memory = hindsight_memories
                        logger.debug(f"Hindsight recall: '{request.message[:50]}...' -> {len(hindsight_memories)} memories")
                except asyncio.TimeoutError:
                    logger.warning("Hindsight recall timed out after 2 seconds", query=request.message[:50])
                    # Continue without external memories

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
                        thread_id=thread_id,
                        message_id=conv_message_id,
                        status="skipped",
                        customer_reply="",
                        message_type=request.message_type,
                        confidence=teams_decision.confidence,
                        metadata={**routing_metadata, "teams_message": True, "skipped": True},
                    )

                if teams_decision.should_clarify:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        thread_id=thread_id,
                        message_id=conv_message_id,
                        status="needs_clarification",
                        customer_reply="Chưa rõ message này đang nhắm tới Thuong hay workflow bot. Bạn xác nhận giúp mình?",
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
                        thread_id=thread_id,
                        message_id=conv_message_id,
                        status="skipped",
                        customer_reply="",
                        message_type=request.message_type,
                        confidence=target_decision.confidence,
                        metadata={**routing_metadata, "skipped": True},
                    )

                if is_group_chat and target_decision.should_clarify:
                    await memory_service.commit(payload, memory_snapshot=memory)
                    return ChatResponse(
                        request_id=request_id,
                        thread_id=thread_id,
                        message_id=conv_message_id,
                        status="needs_clarification",
                        customer_reply="Chưa rõ message này đang nhắm tới Thuong hay workflow bot. Bạn xác nhận giúp mình?",
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
                    thread_id=thread_id,
                    message_id=conv_message_id,
                    status="skipped",
                    customer_reply="",
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
                model_name=(result.metadata or {}).get("model_name") or settings.primary_llm_model,
                kb_sources=(result.metadata or {}).get("kb_sources", []),
                approval_required=result.status == "needs_review",
                approval_status="pending" if result.status == "needs_review" else "not_needed",
                processing_latency_ms=(result.metadata or {}).get("processing_time_ms"),
                outcome_status=result.status,
                ticket_id=request.ticket_id,
                ticket_system=request.ticket_system,
                extra_metadata=result.metadata or {},
            )

            # ← HINDSIGHT: Store interaction for future recall
            if self.hindsight.enabled:
                await self.hindsight.retain(
                    content=f"User: {request.message}\nAssistant: {result.answer}",
                    metadata={
                        "user_id": request.user_id,
                        "thread_id": thread_id,
                        "intent": (result.metadata or {}).get("intent"),
                        "confidence": result.confidence,
                        "kb_sources": (result.metadata or {}).get("kb_sources", []),
                    },
                )

            await session.commit()

        if result.status == "needs_review":
            approval = await approval_service.create_approval(
                request_id=request_id,
                user_id=request.user_id,
                display_name=request.display_name,
                original_customer_reply=request.message,
                ai_response=result.answer,
                confidence=result.confidence,
                action_type="send_message",
                metadata={
                    "thread_id": thread_id,
                    "conversation_summary": getattr(memory, "conversation_summary", None) or "",
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
                    model_name=(result.metadata or {}).get("model_name") or settings.primary_llm_model,
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
                thread_id=thread_id,
                message_id=conv_message_id,
                status="pending_approval",
                customer_reply=f"⚠️ Phản hồi AI (confidence: {result.confidence:.0%}) cần được duyệt trước khi gửi cho user.",
                message_type=request.message_type,
                confidence=result.confidence,
                metadata={**result.metadata, "approval_id": approval.id, "approval_required": True, "threshold": 0.5},
                conversation_id=conversation_id,
            )

        # Only auto-send to Power Automate when kb_hit=true AND confidence >= 0.9
        if result.status == "completed" and settings.power_automate_webhook_url and auto_send_callback:
            kb_hit = result.metadata.get("kb_hit", False) if result.metadata else False
            kb_sources = result.metadata.get("kb_sources", []) if result.metadata else []
            if kb_hit and result.confidence >= 0.9:
                # Build grounded answer for webhook payload
                entities = self._extract_entities(request.message)
                grounded_answer = self._build_grounded_answer(
                    answer=result.answer,
                    entities=entities,
                    conversation_id=conversation_id,
                    kb_hit=kb_hit,
                    kb_sources=kb_sources,
                )
                # Create a modified result-like object for the callback
                # ← Include conversation info with summary for Power Automate
                webhook_metadata = {**(result.metadata or {}), 'conversation_id': conversation_id}
                if hasattr(memory, 'conversation_summary') and memory.conversation_summary:
                    webhook_metadata['conversation_summary'] = memory.conversation_summary
                
                webhook_payload = type('obj', (object,), {
                    'answer': grounded_answer,
                    'confidence': result.confidence,
                    'metadata': webhook_metadata
                })()
                try:
                    await auto_send_callback(webhook_payload)
                except Exception:
                    pass

        # Extract entities from user message (for clarification flow)
        entities = self._extract_entities(request.message)
        
        # ← FIX: confidence threshold gating (gap 0.8-0.9 treated as approval to avoid undefined)
        confidence = result.confidence
        delivery_status = "direct"
        approval_request_id = None
        response_text = result.answer
        response_status = result.status

        # Extract kb_hit for threshold gating
        kb_hit = result.metadata.get("kb_hit", False) if result.metadata else False
        kb_sources = result.metadata.get("kb_sources", []) if result.metadata else []

        # ← FIX v3: Use entity-based clarification or 4W1H fallback
        if confidence < 0.5:
            # Low confidence
            delivery_status = "skipped"
            if kb_hit:
                response_text = self._build_clarification_message(
                    entities=entities,
                    conversation_id=conversation_id,
                    kb_hit=kb_hit,
                    kb_sources=kb_sources,
                )
            else:
                response_text = self._build_fallback_answer(
                    query=request.message,
                    conversation_id=conversation_id,
                    confidence=confidence,
                )
            response_status = "skipped"
        elif 0.5 <= confidence < 0.9:
            # Medium confidence
            delivery_status = "clarification"
            if kb_hit:
                response_text = self._build_clarification_message(
                    entities=entities,
                    conversation_id=conversation_id,
                    kb_hit=kb_hit,
                    kb_sources=kb_sources,
                )
            else:
                response_text = self._build_fallback_answer(
                    query=request.message,
                    conversation_id=conversation_id,
                    confidence=confidence,
                )
            response_status = "needs_clarification"

        # ← FIX v2: update conversation summary every turn
        try:
            from src.db import async_session
            from src.memory.repository import MemoryRepository
            async with async_session() as ss:
                repo = MemoryRepository(ss)
                summary_text = await repo.build_conversation_summary(conversation_id)
                await repo.upsert_conversation_summary(conversation_id, summary_text, [])
        except Exception:
            pass  # non-critical

        # Extract internal_note from metadata if present
        internal_note = result.metadata.get("internal_note", "") if result.metadata else ""
        metadata_without_internal = {k: v for k, v in result.metadata.items() if k != "internal_note"} if result.metadata else {}

        # ← FIX v3: Format answer with grounded answer when confidence >= 0.9 and status is completed
        if response_status == "completed" and delivery_status == "direct" and confidence >= 0.9:
            response_text = self._build_grounded_answer(
                answer=result.answer,
                entities=entities,
                conversation_id=conversation_id,
                kb_hit=kb_hit,
                kb_sources=kb_sources,
            )

        return ChatResponse(
            request_id=request_id,
            thread_id=thread_id,
            message_id=conv_message_id,
            status=response_status,
            customer_reply=response_text,
            internal_note=internal_note,
            message_type=request.message_type,
            confidence=confidence,
            metadata={**metadata_without_internal, "delivery_status": delivery_status},
            delivery_status=delivery_status,
            approval_request_id=approval_request_id,
            conversation_id=conversation_id,
        )

    async def handle_harness_chat(self, request: ChatRequest, auto_send_callback=None, bridge_getter=None) -> ChatResponse:
        import src.api as api_module

        # Determine request_id and message_id
        platform_message_id = request.message_id if request.message_id else None
        if platform_message_id:
            request_id = str(platform_message_id)
        else:
            request_id = str(uuid.uuid4())
        conversation_id = request.thread_id or f"chat-harness-{request.user_id}-{int(time.time())}"
        thread_id = conversation_id  # canonical name
        # For message_id in conversation
        if platform_message_id:
            conv_message_id = platform_message_id
        else:
            conv_message_id = f"msg-{request_id}"
        chat_context = self._normalize_chat_context(
            ChatRequest(
                user_id=request.user_id,
                display_name=request.display_name,
                customer_reply=request.message,
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
                message_id=conv_message_id,
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
            message_id=getattr(request, 'message_id', None),  # Truyền platform message_id
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
                model_name=(result.metadata or {}).get("model_name") or settings.primary_llm_model,
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
                original_customer_reply=request.message,
                ai_response=result.answer,
                confidence=result.confidence,
                action_type="send_message",
                metadata={
                    "thread_id": thread_id,
                    "conversation_summary": getattr(memory, "conversation_summary", None) or "",
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
                    model_name=(result.metadata or {}).get("model_name") or settings.primary_llm_model,
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
                thread_id=thread_id,
                message_id=conv_message_id,
                status="pending_approval",
                customer_reply=f"⚠️ Phản hồi AI (confidence: {result.confidence:.0%}) cần được duyệt trước khi gửi cho user.\n\nHarness: {harness_metrics.get('execution_id', 'N/A') if harness_metrics else 'N/A'}",
                message_type=request.message_type,
                confidence=result.confidence,
                metadata={**result.metadata, "approval_id": approval.id, "approval_required": True, "threshold": 0.5, "harness_metrics": harness_metrics, "harness_evaluation": harness_evaluation},
                conversation_id=conversation_id,
            )

        # Only auto-send to Power Automate when kb_hit=true AND confidence >= 0.9
        if result.status == "completed" and settings.power_automate_webhook_url and auto_send_callback:
            kb_hit = result.metadata.get("kb_hit", False) if result.metadata else False
            if kb_hit and result.confidence >= 0.9:
                try:
                    await auto_send_callback(result)
                except Exception:
                    pass

        # Extract internal_note from metadata if present
        internal_note = result.metadata.get("internal_note", "") if result.metadata else ""
        metadata_without_internal = {k: v for k, v in result.metadata.items() if k != "internal_note"} if result.metadata else {}

        return ChatResponse(
            request_id=request_id,
            thread_id=thread_id,
            message_id=conv_message_id,
            status=result.status,
            customer_reply=result.answer,
            internal_note=internal_note,
            message_type=request.message_type,
            confidence=result.confidence,
            metadata={**metadata_without_internal, "harness_metrics": harness_metrics, "harness_evaluation": harness_evaluation},
            conversation_id=conversation_id,
        )
