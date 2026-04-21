from typing import Optional
from datetime import datetime, timedelta
import re
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import InputPayload, MemoryScopeType
from src.memory.cache import RedisCache
from src.memory.providers import (
    ExternalMemoryProvider,
    ExternalMemoryProviderConfig,
    get_external_memory_provider,
)
from src.memory.routing import ExternalMemoryRoutingPolicy
from src.memory.repository import MemoryRepository
from src.core.conversation_continuity import ConversationContinuityEvaluator
from src.config import get_settings

settings = get_settings()


class MemoryContext:
    def __init__(
        self,
        conversation_summary: Optional[str] = None,
        recent_messages: list[str] = None,
        user_profile: Optional[dict] = None,
        case_memory: Optional[dict] = None,
        episodic_memory: list[dict] = None,
        external_memory: list[dict] = None,
        conversation_state: Optional[dict] = None,
    ):
        self.conversation_summary = conversation_summary
        self.recent_messages = recent_messages or []
        self.user_profile = user_profile or {}
        self.case_memory = case_memory or {}
        self.episodic_memory = episodic_memory or []
        self.external_memory = external_memory or []
        self.conversation_state = conversation_state or {}

    def to_dict(self) -> dict:
        return {
            "conversation_summary": self.conversation_summary,
            "recent_messages": self.recent_messages,
            "user_profile": self.user_profile,
            "case_memory": self.case_memory,
            "episodic_memory": self.episodic_memory,
            "external_memory": self.external_memory,
            "conversation_state": self.conversation_state,
        }

    def get_context_text(self) -> str:
        parts = []
        if self.recent_messages:
            parts.append("=== Recent Messages ===")
            parts.extend(self.recent_messages[-5:])
        if self.conversation_summary:
            parts.append(f"\n=== Conversation Summary ===\n{self.conversation_summary}")
        if self.user_profile:
            parts.append(f"\n=== User Profile ===\n{self.user_profile.get('display_name', 'Unknown')}")
            if self.user_profile.get("role"):
                parts.append(f"Role: {self.user_profile['role']}")
            if self.user_profile.get("communication_style"):
                parts.append(f"Communication Style: {self.user_profile['communication_style']}")
            prefs = self.user_profile.get("preferences", {}) if isinstance(self.user_profile.get("preferences", {}), dict) else {}
            style_profile = prefs.get("style_profile", {}) if isinstance(prefs, dict) else {}
            persona_hint = (
                prefs.get("response_persona_hint")
                or style_profile.get("response_persona_hint")
                or self.user_profile.get("response_persona_hint")
            )
            if persona_hint:
                parts.append(f"Persona Hint: {persona_hint}")
        if self.case_memory:
            parts.append(f"\n=== Case Memory ===\n{self.case_memory.get('summary', 'No summary')}")
        if self.conversation_state:
            parts.append("\n=== Conversation State ===")
            if self.conversation_state.get("platform"):
                parts.append(f"Platform: {self.conversation_state['platform']}")
            if self.conversation_state.get("chat_type"):
                parts.append(f"Chat Type: {self.conversation_state['chat_type']}")
            if self.conversation_state.get("chat_scope"):
                parts.append(f"Chat Scope: {self.conversation_state['chat_scope']}")
            if self.conversation_state.get("group_chat") is not None:
                parts.append(f"Group Chat: {self.conversation_state['group_chat']}")
            if self.conversation_state.get("active_topic_title"):
                parts.append(f"Current Topic: {self.conversation_state['active_topic_title']}")
            if self.conversation_state.get("conversation_mode"):
                parts.append(f"Conversation Mode: {self.conversation_state['conversation_mode']}")
            if self.conversation_state.get("last_user_message_mode"):
                parts.append(f"Message Mode: {self.conversation_state['last_user_message_mode']}")
            if self.conversation_state.get("continuity_score") is not None:
                parts.append(f"Continuity Score: {self.conversation_state['continuity_score']}")
            if self.conversation_state.get("open_loops"):
                open_loops = self.conversation_state.get("open_loops", [])
                parts.append(f"Open Loops: {open_loops[:3]}")
            if self.conversation_state.get("key_entities"):
                parts.append(f"Key Entities: {self.conversation_state['key_entities'][:5]}")
        if self.episodic_memory:
            parts.append("\n=== Learned Patterns ===")
            for item in self.episodic_memory[:3]:
                parts.append(f"- {item.get('content', '')}")
        if self.external_memory:
            parts.append("\n=== External Memory ===")
            for item in self.external_memory[:3]:
                parts.append(f"- {item.get('content', '')}")
        return "\n".join(parts)


class MemoryService:
    def __init__(
        self,
        session: AsyncSession,
        cache: RedisCache,
        external_provider: Optional[ExternalMemoryProvider] = None,
    ):
        self.session = session
        self.cache = cache
        self.repo = MemoryRepository(session)
        self.routing_policy = ExternalMemoryRoutingPolicy(
            mempalace_enabled=settings.mempalace_enabled,
            file_enabled=settings.file_memory_enabled,
        )
        self.external_provider = external_provider

    def _resolve_external_provider(self, payload: InputPayload) -> ExternalMemoryProvider:
        if self.external_provider is not None:
            return self.external_provider

        route = self.routing_policy.select(
            message_text=payload.message.text,
            case_id=payload.case.case_id if payload.case else None,
            team=payload.user.team,
        )

        if route.provider_name == "file":
            return get_external_memory_provider(
                ExternalMemoryProviderConfig(
                    provider_name="file",
                    enabled=settings.file_memory_enabled,
                    path=settings.file_memory_path,
                    top_k=settings.mempalace_top_k,
                )
            )

        return get_external_memory_provider(
            ExternalMemoryProviderConfig(
                provider_name=route.provider_name,
                enabled=settings.mempalace_enabled,
                path=settings.mempalace_path,
                top_k=settings.mempalace_top_k,
            )
        )

    def _infer_user_style(self, text: str) -> tuple[str, dict]:
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        words = re.findall(r"[\wÀ-ỹ']+", normalized)
        word_count = len(words)
        has_numbered_steps = bool(re.search(r"(^|\n)\s*(\d+\.|[-*])\s+", text))
        has_bullets = "\n-" in text or "\n*" in text
        has_formal_markers = any(token in normalized for token in ["anh chị", "xin vui lòng", "vui lòng", "please", "thank you", "cảm ơn"])
        has_detail_markers = any(token in normalized for token in ["chi tiết", "detail", "explain", "giải thích", "step by step", "từng bước", "deep dive"])
        has_short_markers = any(token in normalized for token in ["ok", "oke", "yes", "no", "xong", "done"])
        has_casual_markers = any(token in normalized for token in ["haha", "lol", ":)", "😊", "👍", "bro", "bạn ơi"])

        if has_numbered_steps or has_bullets:
            style = "structured"
        elif has_detail_markers or word_count > 24:
            style = "detailed"
        elif has_formal_markers:
            style = "formal"
        elif has_casual_markers:
            style = "casual"
        elif has_short_markers or word_count <= 8:
            style = "concise"
        else:
            style = "balanced"

        signals = {
            "word_count": word_count,
            "has_numbered_steps": has_numbered_steps,
            "has_bullets": has_bullets,
            "has_formal_markers": has_formal_markers,
            "has_detail_markers": has_detail_markers,
            "has_short_markers": has_short_markers,
            "has_casual_markers": has_casual_markers,
        }
        return style, signals

    def _build_response_persona_hint(self, style: str, signals: dict) -> str:
        parts = [f"style={style}"]
        if signals.get("has_formal_markers"):
            parts.append("tone=formal")
        if signals.get("has_casual_markers"):
            parts.append("tone=casual")
        if signals.get("has_detail_markers"):
            parts.append("verbosity=detailed")
        if signals.get("has_short_markers"):
            parts.append("verbosity=concise")
        if signals.get("has_numbered_steps"):
            parts.append("format=numbered_steps")
        if signals.get("has_bullets"):
            parts.append("format=bullets")
        if signals.get("has_detail_markers") or signals.get("word_count", 0) > 24:
            parts.append("explain_key_steps")
        return ", ".join(parts)

    async def retrieve(self, payload: InputPayload) -> MemoryContext:
        thread_id = payload.conversation.thread_id
        user_id = payload.user.id
        case_id = payload.case.case_id if payload.case else None

        cached = await self.cache.get_json(f"memory:{thread_id}")
        if cached:
            context = MemoryContext(**cached)
            context.conversation_state = self._merge_runtime_chat_context(payload, context.conversation_state)
            return context

        conversation_summary = await self.repo.get_conversation_summary(thread_id)
        summary_text = conversation_summary.summary_text if conversation_summary else None
        conversation_state_model = await self.repo.get_conversation_state(thread_id)
        conversation_state = self._merge_runtime_chat_context(payload, self._conversation_state_to_dict(conversation_state_model))

        messages = await self.repo.get_recent_messages(thread_id, limit=10)
        recent_messages = [m.message_text for m in messages]

        user_profile_model = await self.repo.get_user_profile(user_id)
        user_profile = None
        if user_profile_model:
            preferences = dict(user_profile_model.preferences or {})
            style_profile = preferences.get("style_profile", {}) if isinstance(preferences, dict) else {}
            response_persona_hint = (
                preferences.get("response_persona_hint")
                or (style_profile.get("response_persona_hint") if isinstance(style_profile, dict) else None)
            )
            user_profile = {
                "user_id": user_profile_model.user_id,
                "display_name": user_profile_model.display_name,
                "role": user_profile_model.role,
                "team": user_profile_model.team,
                "vip_flag": user_profile_model.vip_flag,
                "communication_style": user_profile_model.communication_style,
                "preferences": preferences,
            }
            if response_persona_hint:
                user_profile["response_persona_hint"] = response_persona_hint
            if not user_profile_model.display_name:
                user_profile["display_name"] = payload.user.display_name

        case_memory_model = await self.repo.get_case_memory(case_id) if case_id else None
        case_memory = None
        if case_memory_model:
            case_memory = {
                "case_id": case_memory_model.case_id,
                "status": case_memory_model.status,
                "owner": case_memory_model.owner,
                "summary": case_memory_model.summary,
                "open_items": case_memory_model.open_items,
                "priority": case_memory_model.priority,
            }

        episodic_items = await self.repo.get_memory_items(
            MemoryScopeType.EPISODIC, "global", limit=5
        )
        episodic_memory = [
            {"content": item.content, "confidence": item.confidence_score}
            for item in episodic_items
        ]

        provider = self._resolve_external_provider(payload)

        mempalace_context = await provider.search(
            message_text=payload.message.text,
            user_id=user_id,
            thread_id=thread_id,
            case_id=case_id,
            team=payload.user.team,
        )
        external_memory = mempalace_context.to_memory_items()

        context = MemoryContext(
            conversation_summary=summary_text,
            recent_messages=recent_messages,
            user_profile=user_profile,
            case_memory=case_memory,
            episodic_memory=episodic_memory,
            external_memory=external_memory,
            conversation_state=conversation_state,
        )

        await self.cache.set_json(
            f"memory:{thread_id}",
            context.to_dict(),
            ttl=settings.memory_conversation_ttl,
        )

        return context

    def _conversation_state_to_dict(self, state_model) -> Optional[dict]:
        if not state_model:
            return None
        return {
            "thread_id": state_model.thread_id,
            "active_topic_title": state_model.active_topic_title,
            "active_topic_summary": state_model.active_topic_summary,
            "conversation_mode": state_model.conversation_mode,
            "continuity_score": state_model.continuity_score,
            "last_user_intent": state_model.last_user_intent,
            "last_assistant_intent": state_model.last_assistant_intent,
            "open_loops": list(state_model.open_loops or []),
            "key_entities": list(state_model.key_entities or []),
            "recent_decisions": list(state_model.recent_decisions or []),
            "state_json": dict(state_model.state_json or {}),
            "last_message_at": state_model.last_message_at.isoformat() if getattr(state_model, "last_message_at", None) else None,
            "turn_count": state_model.turn_count or 0,
        }

    def _runtime_chat_context(self, payload: InputPayload) -> dict:
        conversation = payload.conversation
        chat_type = conversation.chat_type
        chat_scope = conversation.chat_scope
        group_chat = conversation.group_chat
        platform = conversation.platform or payload.source

        if chat_type is None and chat_scope:
            chat_type = "group" if chat_scope == "group" else "private"
        if chat_scope is None:
            if group_chat or chat_type in {"group", "supergroup", "channel"}:
                chat_scope = "group"
            elif chat_type == "private":
                chat_scope = "dm"
        if group_chat is None:
            group_chat = chat_type in {"group", "supergroup", "channel"} or chat_scope == "group"

        return {
            "platform": platform,
            "chat_type": chat_type,
            "chat_scope": chat_scope,
            "group_chat": bool(group_chat),
        }

    def _merge_runtime_chat_context(self, payload: InputPayload, conversation_state: Optional[dict]) -> dict:
        merged = dict(conversation_state or {})
        runtime_context = self._runtime_chat_context(payload)
        for key, value in runtime_context.items():
            if value is not None:
                merged[key] = value

        if payload.conversation_state:
            for key, value in payload.conversation_state.model_dump(exclude_none=True).items():
                merged[key] = value
        return merged

    def _build_topic_summary(self, payload: InputPayload, memory: MemoryContext, state_result: dict, assistant_text: Optional[str]) -> tuple[str, list[str]]:
        topic_title = state_result.get("suggested_topic_title") or payload.message.text[:80].strip()
        snippets = [payload.message.text[:160].strip()]
        if assistant_text:
            snippets.append(assistant_text[:160].strip())
        if memory.conversation_summary and state_result.get("mode") != "new_topic":
            summary_text = memory.conversation_summary
        else:
            summary_text = f"Topic: {topic_title}\nLatest turn: {' / '.join(s for s in snippets if s)}"
        unresolved_points = [loop.get("text", "") for loop in state_result.get("new_open_loops", []) if loop.get("text")]
        existing_loops = memory.conversation_state.get("open_loops", []) if memory.conversation_state else []
        unresolved_points.extend([loop.get("text", "") for loop in existing_loops if loop.get("text")])
        unresolved_points = [point for point in unresolved_points if point]
        return summary_text, unresolved_points[:5]

    async def update_conversation_state_from_turn(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        assistant_text: Optional[str] = None,
        result_metadata: Optional[dict] = None,
    ):
        if not memory:
            return None

        evaluator = ConversationContinuityEvaluator()
        state_result = evaluator.evaluate(
            current_message=payload.message.text,
            recent_messages=memory.recent_messages,
            conversation_summary=memory.conversation_summary,
            active_topic_summary=(memory.conversation_state or {}).get("active_topic_summary"),
            active_topic_title=(memory.conversation_state or {}).get("active_topic_title"),
            open_loops=(memory.conversation_state or {}).get("open_loops", []),
            key_entities=(memory.conversation_state or {}).get("key_entities", []),
        )

        current_state = dict(memory.conversation_state or {})
        current_state["last_user_message_mode"] = state_result.get("message_mode")
        merged_open_loops = list(current_state.get("open_loops", []))
        for loop in state_result.get("new_open_loops", []):
            if loop not in merged_open_loops:
                merged_open_loops.append(loop)
        if state_result.get("closed_loops"):
            closed = set(state_result.get("closed_loops", []))
            merged_open_loops = [loop for loop in merged_open_loops if loop.get("key") not in closed]

        merged_entities = list(dict.fromkeys([
            *current_state.get("key_entities", []),
            *state_result.get("matched_entities", []),
        ]))
        recent_decisions = list(current_state.get("recent_decisions", []))
        recent_decisions.append({
            "message": payload.message.text[:160],
            "mode": state_result.get("mode"),
            "score": state_result.get("continuity_score"),
        })
        recent_decisions = recent_decisions[-8:]

        if assistant_text:
            current_state["last_assistant_excerpt"] = assistant_text[:240]
        if result_metadata:
            current_state["last_result_metadata"] = {
                key: value for key, value in result_metadata.items()
                if key in {"intent", "risk_level", "agents_used", "model_name", "model_provider", "kb_sources"}
            }

        topic_title = state_result.get("suggested_topic_title") or current_state.get("active_topic_title")
        active_topic_summary = current_state.get("active_topic_summary")
        if state_result.get("mode") == "new_topic" or not active_topic_summary:
            active_topic_summary, unresolved_points = self._build_topic_summary(payload, memory, state_result, assistant_text)
        else:
            unresolved_points = [loop.get("text", "") for loop in merged_open_loops if loop.get("text")]
            unresolved_points = unresolved_points[:5]

        state = await self.repo.upsert_conversation_state(
            thread_id=payload.conversation.thread_id,
            active_topic_title=topic_title,
            active_topic_summary=active_topic_summary,
            conversation_mode=state_result.get("mode"),
            continuity_score=state_result.get("continuity_score"),
            last_user_intent=(result_metadata or {}).get("intent"),
            last_assistant_intent=(result_metadata or {}).get("assistant_intent"),
            open_loops=merged_open_loops,
            key_entities=merged_entities,
            recent_decisions=recent_decisions,
            state_json={
                **current_state,
                "continuity_reason": state_result.get("reason"),
                "should_refresh_summary": state_result.get("should_refresh_summary"),
            },
            last_message_at=datetime.now(),
            turn_count=int(current_state.get("turn_count", 0)) + 1,
        )

        refresh_summary = state_result.get("should_refresh_summary") or (int(current_state.get("turn_count", 0)) + 1) % 3 == 0
        if refresh_summary:
            await self.repo.upsert_conversation_summary(
                conversation_id=payload.conversation.thread_id,
                summary_text=active_topic_summary or memory.conversation_summary or payload.message.text[:240],
                unresolved_points=unresolved_points,
            )

        await self.cache.delete(f"memory:{payload.conversation.thread_id}")
        return state

    async def commit(
        self,
        payload: InputPayload,
        new_context: Optional[str] = None,
        case_state_changed: bool = False,
        reusable_insight: Optional[str] = None,
        user_preference_detected: Optional[str] = None,
        memory_snapshot: Optional[MemoryContext] = None,
        assistant_text: Optional[str] = None,
        result_metadata: Optional[dict] = None,
    ):
        thread_id = payload.conversation.thread_id
        user_id = payload.user.id
        case_id = payload.case.case_id if payload.case else None
        ticket_id = payload.case.ticket_id if payload.case else None
        ticket_system = payload.case.ticket_system if payload.case else None

        await self.repo.save_message(
            request_id=payload.request_id,
            user_id=user_id,
            thread_id=thread_id,
            message_text=payload.message.text,
            direction="inbound",
            ticket_id=ticket_id,
            ticket_system=ticket_system,
        )

        await self.repo.upsert_conversation_thread(
            thread_id=thread_id,
            user_id=user_id,
            team_id=payload.user.team,
            platform=payload.source,
            channel=payload.source,
            primary_ticket_id=ticket_id,
            ticket_system=ticket_system,
            title=(payload.message.text[:120] if payload.message and payload.message.text else None),
        )

        if ticket_id:
            await self.repo.link_thread_ticket(
                thread_id=thread_id,
                ticket_id=ticket_id,
                ticket_system=ticket_system or "servicedesk_plus",
                relation_type="primary",
                linked_by="system",
            )

        if case_state_changed and case_id:
            await self.repo.upsert_case_memory(case_id)

        if new_context and thread_id:
            existing_summary = await self.repo.get_conversation_summary(thread_id)
            unresolved = existing_summary.unresolved_points if existing_summary else []
            await self.repo.upsert_conversation_summary(
                conversation_id=thread_id,
                summary_text=new_context,
                unresolved_points=unresolved,
            )

        if reusable_insight:
            await self.repo.add_memory_item(
                scope=MemoryScopeType.EPISODIC,
                scope_id="global",
                content=reusable_insight,
                confidence_score=0.8,
                ttl_at=datetime.now().replace(tzinfo=None) + timedelta(days=settings.memory_summary_ttl),
            )
            provider = self._resolve_external_provider(payload)
            await provider.write_memory(
                content=reusable_insight,
                user_id=user_id,
                thread_id=thread_id,
                case_id=case_id,
                team=payload.user.team,
                room="episodic-insights",
            )

        if memory_snapshot:
            await self.update_conversation_state_from_turn(
                payload=payload,
                memory=memory_snapshot,
                assistant_text=assistant_text,
                result_metadata=result_metadata,
            )

        style = None
        style_profile = None
        if settings.enable_user_style_learning:
            owner_ids = settings.style_learning_user_ids
            if owner_ids and user_id in owner_ids:
                user_messages = await self.repo.get_recent_user_messages(user_id, limit=20)
                style_source_text = "\n".join(m.message_text for m in user_messages)
                if style_source_text:
                    style, signals = self._infer_user_style(style_source_text)
                    if style == "balanced":
                        style = None
                    else:
                        response_persona_hint = self._build_response_persona_hint(style, signals)
                        style_profile = {
                            "communication_style": style,
                            "style_signals": signals,
                            "response_persona_hint": response_persona_hint,
                            "source": "user_id_history",
                            "sample_count": len(user_messages),
                        }

        if user_preference_detected or style:
            preference_updates = {}
            if user_preference_detected:
                preference_updates["last_preference"] = user_preference_detected
            if style_profile:
                preference_updates["style_profile"] = style_profile

            await self.repo.upsert_user_profile(
                user_id=user_id,
                display_name=payload.user.display_name,
                communication_style=style,
                preferences=preference_updates or None,
            )
            provider = self._resolve_external_provider(payload)
            if user_preference_detected:
                await provider.write_memory(
                    content=f"user_preference:{user_preference_detected}",
                    user_id=user_id,
                    thread_id=thread_id,
                    case_id=case_id,
                    team=payload.user.team,
                    room="user-preferences",
                )
            if style:
                await provider.write_memory(
                    content=f"user_style:{style}",
                    user_id=user_id,
                    thread_id=thread_id,
                    case_id=case_id,
                    team=payload.user.team,
                    room="user-style",
                )

        await self.cache.delete(f"memory:{thread_id}")
