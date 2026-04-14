from typing import Optional
from datetime import datetime, timedelta
import re
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import InputPayload, MemoryScopeType, MemoryItem
from src.memory.cache import RedisCache
from src.memory.mempalace_adapter import MemPalaceAdapter
from src.memory.providers import (
    ExternalMemoryProvider,
    ExternalMemoryProviderConfig,
    get_external_memory_provider,
)
from src.memory.routing import ExternalMemoryRoutingPolicy
from src.memory.repository import MemoryRepository
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
    ):
        self.conversation_summary = conversation_summary
        self.recent_messages = recent_messages or []
        self.user_profile = user_profile or {}
        self.case_memory = case_memory or {}
        self.episodic_memory = episodic_memory or []
        self.external_memory = external_memory or []

    def to_dict(self) -> dict:
        return {
            "conversation_summary": self.conversation_summary,
            "recent_messages": self.recent_messages,
            "user_profile": self.user_profile,
            "case_memory": self.case_memory,
            "episodic_memory": self.episodic_memory,
            "external_memory": self.external_memory,
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
        if self.case_memory:
            parts.append(f"\n=== Case Memory ===\n{self.case_memory.get('summary', 'No summary')}")
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

    async def retrieve(self, payload: InputPayload) -> MemoryContext:
        thread_id = payload.conversation.thread_id
        user_id = payload.user.id
        case_id = payload.case.case_id if payload.case else None

        cached = await self.cache.get_json(f"memory:{thread_id}")
        if cached:
            return MemoryContext(**cached)

        conversation_summary = await self.repo.get_conversation_summary(thread_id)
        summary_text = conversation_summary.summary_text if conversation_summary else None

        messages = await self.repo.get_recent_messages(thread_id, limit=10)
        recent_messages = [m.message_text for m in messages]

        user_profile_model = await self.repo.get_user_profile(user_id)
        user_profile = None
        if user_profile_model:
            user_profile = {
                "user_id": user_profile_model.user_id,
                "display_name": user_profile_model.display_name,
                "role": user_profile_model.role,
                "team": user_profile_model.team,
                "vip_flag": user_profile_model.vip_flag,
                "communication_style": user_profile_model.communication_style,
                "preferences": user_profile_model.preferences,
            }
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
        )

        await self.cache.set_json(
            f"memory:{thread_id}",
            context.to_dict(),
            ttl=settings.memory_conversation_ttl,
        )

        return context

    async def commit(
        self,
        payload: InputPayload,
        new_context: Optional[str] = None,
        case_state_changed: bool = False,
        reusable_insight: Optional[str] = None,
        user_preference_detected: Optional[str] = None,
    ):
        thread_id = payload.conversation.thread_id
        user_id = payload.user.id
        case_id = payload.case.case_id if payload.case else None

        await self.repo.save_message(
            request_id=payload.request_id,
            user_id=user_id,
            thread_id=thread_id,
            message_text=payload.message.text,
            direction="inbound",
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

        style = None
        style_profile = None
        if settings.enable_user_style_learning:
            style, signals = self._infer_user_style(payload.message.text)
            if style == "balanced":
                style = None
            else:
                style_profile = {
                    "communication_style": style,
                    "style_signals": signals,
                    "source": "message_history",
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
