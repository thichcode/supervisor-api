import structlog

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import Optional
from datetime import datetime

from src.core import MemoryScopeType
from src.db.models import (
    Message,
    ConversationThread,
    ThreadTicketLink,
    ConversationSummary,
    ConversationState,
    UserProfile,
    CaseMemory,
    MemoryItem,
)

logger = structlog.get_logger(__name__)


def utc_now() -> datetime:
    # Return naive datetime to match PostgreSQL TIMESTAMP WITHOUT TIME ZONE columns
    # The previous UTC-aware datetime was causing comparison issues with PostgreSQL
    return datetime.now().replace(tzinfo=None)


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message(
        self,
        request_id: str,
        user_id: str,
        thread_id: str,
        message_text: str,
        direction: str,
        ticket_id: Optional[str] = None,
        ticket_system: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> Message:
        msg = Message(
            request_id=request_id,
            user_id=user_id,
            thread_id=thread_id,
            message_id=message_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system,
            message_text=message_text,
            direction=direction,
        )
        self.session.add(msg)
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def upsert_conversation_thread(
        self,
        thread_id: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        platform: Optional[str] = None,
        channel: Optional[str] = None,
        primary_ticket_id: Optional[str] = None,
        ticket_system: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ConversationThread:
        result = await self.session.execute(
            select(ConversationThread).where(ConversationThread.thread_id == thread_id)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if user_id:
                existing.user_id = user_id
            if team_id:
                existing.team_id = team_id
            if platform:
                existing.platform = platform
            if channel:
                existing.channel = channel
            if primary_ticket_id:
                existing.primary_ticket_id = primary_ticket_id
            if ticket_system:
                existing.ticket_system = ticket_system
            if title:
                existing.title = title
            existing.last_message_at = utc_now()
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        thread = ConversationThread(
            thread_id=thread_id,
            user_id=user_id,
            team_id=team_id,
            platform=platform,
            channel=channel,
            primary_ticket_id=primary_ticket_id,
            ticket_system=ticket_system,
            title=title,
            last_message_at=utc_now(),
        )
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def link_thread_ticket(
        self,
        thread_id: str,
        ticket_id: str,
        ticket_system: str = "servicedesk_plus",
        relation_type: str = "primary",
        linked_by: str = "system",
        confidence_score: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> ThreadTicketLink:
        result = await self.session.execute(
            select(ThreadTicketLink).where(
                ThreadTicketLink.thread_id == thread_id,
                ThreadTicketLink.ticket_id == ticket_id,
                ThreadTicketLink.ticket_system == ticket_system,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.relation_type = relation_type
            existing.linked_by = linked_by
            existing.confidence_score = confidence_score
            existing.extra_metadata = metadata or existing.extra_metadata
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        link = ThreadTicketLink(
            thread_id=thread_id,
            ticket_id=ticket_id,
            ticket_system=ticket_system,
            relation_type=relation_type,
            linked_by=linked_by,
            confidence_score=confidence_score,
            extra_metadata=metadata or {},
        )
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def get_recent_messages(self, thread_id: str, limit: int = 10) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_recent_user_messages(self, user_id: str, limit: int = 20) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.user_id == user_id)
            .where(Message.direction == "inbound")
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        return list(reversed(messages))

    async def get_conversation_summary(self, conversation_id: str) -> Optional[ConversationSummary]:
        result = await self.session.execute(
            select(ConversationSummary)
            .where(ConversationSummary.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def upsert_conversation_summary(
        self,
        conversation_id: str,
        summary_text: str,
        unresolved_points: list[str],
    ) -> ConversationSummary:
        logger.info("Upserting conversation summary", thread_id=conversation_id, summary_length=len(summary_text))
        existing = await self.get_conversation_summary(conversation_id)
        if existing:
            logger.info("Updating existing summary", thread_id=conversation_id)
            existing.summary_text = summary_text
            existing.unresolved_points = unresolved_points
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            logger.info("Creating new summary", thread_id=conversation_id)
            summary = ConversationSummary(
                conversation_id=conversation_id,
                summary_text=summary_text,
                unresolved_points=unresolved_points,
            )
            self.session.add(summary)
            await self.session.commit()
            await self.session.refresh(summary)
            return summary

    async def build_conversation_summary(self, conversation_id: str) -> str:
        """Build a rolling summary from recent messages + current summary."""
        logger.info("Building conversation summary", thread_id=conversation_id)
        messages = await self.get_conversation_messages(conversation_id, limit=10)
        existing = await self.get_conversation_summary(conversation_id)

        if not messages:
            logger.info("No messages found for summary", thread_id=conversation_id)
            return existing.summary_text if existing else ""
        
        logger.info("Building summary from messages", thread_id=conversation_id, message_count=len(messages))

        # Build summary from last 10 messages
        user_msgs = [m.content for m in messages if m.direction == "inbound"]
        bot_msgs = [m.content for m in messages if m.direction == "outbound"]

        unresolved = []
        if user_msgs:
            last_user = user_msgs[-1].strip()
            if any(q in last_user.lower() for q in ["?", "chưa", "sao", "làm sao", "muốn", "cần"]):
                unresolved.append(last_user[:100])

        # Combine with existing summary if present
        if existing and existing.summary_text:
            combined = f"{existing.summary_text}\n---\n[CURRENT SESSION]\nUser: {user_msgs[-1] if user_msgs else 'N/A'}"
        else:
            combined = f"Session started. User: {user_msgs[-1] if user_msgs else 'N/A'}"

        logger.info("Summary built", thread_id=conversation_id, summary_length=len(combined))
        return combined[:2000]  # cap to avoid DB overflow

    async def get_conversation_state(self, thread_id: str) -> Optional[ConversationState]:
        result = await self.session.execute(
            select(ConversationState).where(ConversationState.thread_id == thread_id)
        )
        return result.scalar_one_or_none()

    async def upsert_conversation_state(
        self,
        thread_id: str,
        active_topic_title: Optional[str] = None,
        active_topic_summary: Optional[str] = None,
        conversation_mode: Optional[str] = None,
        continuity_score: Optional[float] = None,
        last_user_intent: Optional[str] = None,
        last_assistant_intent: Optional[str] = None,
        open_loops: Optional[list] = None,
        key_entities: Optional[list] = None,
        recent_decisions: Optional[list] = None,
        state_json: Optional[dict] = None,
        last_message_at: Optional[datetime] = None,
        turn_count: Optional[int] = None,
    ) -> ConversationState:
        existing = await self.get_conversation_state(thread_id)
        if existing:
            if active_topic_title is not None:
                existing.active_topic_title = active_topic_title
            if active_topic_summary is not None:
                existing.active_topic_summary = active_topic_summary
            if conversation_mode is not None:
                existing.conversation_mode = conversation_mode
            if continuity_score is not None:
                existing.continuity_score = continuity_score
            if last_user_intent is not None:
                existing.last_user_intent = last_user_intent
            if last_assistant_intent is not None:
                existing.last_assistant_intent = last_assistant_intent
            if open_loops is not None:
                existing.open_loops = open_loops
            if key_entities is not None:
                existing.key_entities = key_entities
            if recent_decisions is not None:
                existing.recent_decisions = recent_decisions
            if state_json is not None:
                current_state = dict(existing.state_json or {})
                current_state.update(state_json)
                existing.state_json = current_state
            if last_message_at is not None:
                existing.last_message_at = last_message_at
            if turn_count is not None:
                existing.turn_count = turn_count
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing

        state = ConversationState(
            thread_id=thread_id,
            active_topic_title=active_topic_title,
            active_topic_summary=active_topic_summary,
            conversation_mode=conversation_mode or "continuation",
            continuity_score=continuity_score if continuity_score is not None else 0.5,
            last_user_intent=last_user_intent,
            last_assistant_intent=last_assistant_intent,
            open_loops=open_loops or [],
            key_entities=key_entities or [],
            recent_decisions=recent_decisions or [],
            state_json=state_json or {},
            last_message_at=last_message_at,
            turn_count=turn_count or 0,
        )
        self.session.add(state)
        await self.session.commit()
        await self.session.refresh(state)
        return state

    async def patch_conversation_state(self, thread_id: str, patch: dict) -> ConversationState:
        existing = await self.get_conversation_state(thread_id)
        if not existing:
            return await self.upsert_conversation_state(thread_id, **patch)

        for key, value in patch.items():
            if key == "state_json" and isinstance(value, dict):
                current_state = dict(existing.state_json or {})
                current_state.update(value)
                existing.state_json = current_state
            elif hasattr(existing, key) and value is not None:
                setattr(existing, key, value)

        existing.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(existing)
        return existing

    async def append_open_loop(self, thread_id: str, loop_item: dict) -> ConversationState:
        state = await self.get_conversation_state(thread_id)
        open_loops = list(state.open_loops or []) if state else []
        open_loops.append(loop_item)
        return await self.upsert_conversation_state(
            thread_id=thread_id,
            open_loops=open_loops,
            state_json={"last_open_loop": loop_item},
        )

    async def close_open_loop(self, thread_id: str, loop_key: str) -> ConversationState:
        state = await self.get_conversation_state(thread_id)
        if not state:
            return await self.upsert_conversation_state(thread_id, state_json={"closed_loop_key": loop_key})

        open_loops = [loop for loop in (state.open_loops or []) if loop.get("key") != loop_key]
        recent_decisions = list(state.recent_decisions or [])
        recent_decisions.append({"action": "close_loop", "key": loop_key, "at": utc_now().isoformat()})
        return await self.upsert_conversation_state(
            thread_id=thread_id,
            open_loops=open_loops,
            recent_decisions=recent_decisions,
            state_json={"closed_loop_key": loop_key},
        )

    async def get_user_profile(self, user_id: str) -> Optional[UserProfile]:
        result = await self.session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert_user_profile(
        self,
        user_id: str,
        display_name: str,
        role: Optional[str] = None,
        team: Optional[str] = None,
        vip_flag: Optional[bool] = None,
        communication_style: Optional[str] = None,
        preferences: Optional[dict] = None,
    ) -> UserProfile:
        existing = await self.get_user_profile(user_id)

        def _merge_preferences(current: Optional[dict], updates: Optional[dict]) -> dict:
            merged = dict(current or {})
            if updates:
                for key, value in updates.items():
                    if isinstance(value, dict) and isinstance(merged.get(key), dict):
                        merged[key] = {**merged[key], **value}
                    else:
                        merged[key] = value
            return merged

        if existing:
            existing.display_name = display_name
            if role:
                existing.role = role
            if team:
                existing.team = team
            if vip_flag is not None:
                existing.vip_flag = vip_flag
            if communication_style:
                existing.communication_style = communication_style
            if preferences:
                existing.preferences = _merge_preferences(existing.preferences, preferences)
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            profile = UserProfile(
                user_id=user_id,
                display_name=display_name,
                role=role,
                team=team,
                vip_flag=bool(vip_flag) if vip_flag is not None else False,
                communication_style=communication_style,
                preferences=preferences or {},
            )
            self.session.add(profile)
            await self.session.commit()
            await self.session.refresh(profile)
            return profile

    async def get_case_memory(self, case_id: str) -> Optional[CaseMemory]:
        result = await self.session.execute(
            select(CaseMemory).where(CaseMemory.case_id == case_id)
        )
        return result.scalar_one_or_none()

    async def upsert_case_memory(
        self,
        case_id: str,
        status: Optional[str] = None,
        owner: Optional[str] = None,
        summary: Optional[str] = None,
        open_items: Optional[list] = None,
        priority: Optional[str] = None,
    ) -> CaseMemory:
        existing = await self.get_case_memory(case_id)
        if existing:
            if status:
                existing.status = status
            if owner:
                existing.owner = owner
            if summary:
                existing.summary = summary
            if open_items is not None:
                existing.open_items = open_items
            if priority:
                existing.priority = priority
            existing.updated_at = utc_now()
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            case = CaseMemory(
                case_id=case_id,
                status=status,
                owner=owner,
                summary=summary,
                open_items=open_items or [],
                priority=priority,
            )
            self.session.add(case)
            await self.session.commit()
            await self.session.refresh(case)
            return case

    async def get_memory_items(
        self,
        scope: MemoryScopeType,
        scope_id: str,
        limit: int = 10,
    ) -> list[MemoryItem]:
        result = await self.session.execute(
            select(MemoryItem)
            .where(MemoryItem.memory_scope == scope.value)
            .where(MemoryItem.scope_id == scope_id)
            .where(
                (MemoryItem.ttl_at.is_(None)) | (MemoryItem.ttl_at > utc_now())
            )
            .order_by(MemoryItem.confidence_score.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_memory_item(
        self,
        scope: MemoryScopeType,
        scope_id: str,
        content: str,
        confidence_score: float = 1.0,
        ttl_at: Optional[datetime] = None,
    ) -> MemoryItem:
        item = MemoryItem(
            memory_scope=scope.value,
            scope_id=scope_id,
            content=content,
            confidence_score=confidence_score,
            ttl_at=ttl_at,
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def delete_low_confidence_items(
        self, scope: MemoryScopeType, scope_id: str, threshold: float = 0.3
    ) -> int:
        result = await self.session.execute(
            delete(MemoryItem)
            .where(MemoryItem.memory_scope == scope.value)
            .where(MemoryItem.scope_id == scope_id)
            .where(MemoryItem.confidence_score < threshold)
        )
        await self.session.commit()
        return result.rowcount
