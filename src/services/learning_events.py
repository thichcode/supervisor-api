from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.db.models import ResponseLearningEvent

logger = structlog.get_logger()


def build_learning_event_key(request_id: str, event_type: str, event_payload: Mapping[str, Any]) -> str:
    """Build a stable deduplication key for a learning event."""

    canonical = {
        "request_id": request_id,
        "event_type": event_type,
        "event_payload": event_payload,
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


async def record_learning_event(
    session: AsyncSession,
    *,
    request_id: str,
    event_type: str,
    event_payload: Mapping[str, Any],
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    ticket_system: Optional[str] = None,
) -> ResponseLearningEvent:
    """Insert a learning event idempotently.

    If the same logical event is retried, this helper returns the existing row
    instead of inserting a duplicate.
    """

    dedupe_key = build_learning_event_key(request_id, event_type, event_payload)

    existing_result = await session.execute(
        select(ResponseLearningEvent).where(ResponseLearningEvent.dedupe_key == dedupe_key)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return existing

    event = ResponseLearningEvent(
        request_id=request_id,
        user_id=user_id,
        thread_id=thread_id,
        ticket_id=ticket_id,
        ticket_system=ticket_system,
        event_type=event_type,
        event_payload=dict(event_payload),
        dedupe_key=dedupe_key,
    )
    session.add(event)

    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        logger.info("learning_event_deduplicated", request_id=request_id, event_type=event_type)
        duplicate_result = await session.execute(
            select(ResponseLearningEvent).where(ResponseLearningEvent.dedupe_key == dedupe_key)
        )
        duplicate = duplicate_result.scalar_one_or_none()
        if duplicate:
            return duplicate
        raise

    return event
