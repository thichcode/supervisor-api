"""Async adapter for the synchronous FactStore.

Wraps src.fact_store in asyncio.to_thread so it can be used safely
from async request handlers without blocking the event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List, Optional

from src.fact_store import Fact, FactStore, get_fact_store


class AsyncFactStore:
    """Thin async wrapper around the sqlite-backed FactStore.

    All read/write operations are dispatched via asyncio.to_thread
    so the sync sqlite3 connection never blocks the async loop.
    """

    def __init__(self, store: FactStore | None = None) -> None:
        self._store = store or get_fact_store()

    async def add(
        self,
        content: str,
        category: str = "general",
        entities: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> int:
        return await asyncio.to_thread(
            self._store.add,
            content,
            category,
            entities,
            tags,
        )

    async def search(self, query: str, limit: int = 10, min_trust: float = 0.3) -> List[Fact]:
        return await asyncio.to_thread(self._store.search, query, limit, min_trust)

    async def probe(self, entity: str, limit: int = 10, min_trust: float = 0.3) -> List[Fact]:
        return await asyncio.to_thread(self._store.probe, entity, limit, min_trust)

    async def related(self, entity: str, limit: int = 10) -> List[Fact]:
        return await asyncio.to_thread(self._store.related, entity, limit)

    async def reason(self, entities: List[str], limit: int = 10) -> List[Fact]:
        return await asyncio.to_thread(self._store.reason, entities, limit)

    async def contradict(self, query: str, limit: int = 5) -> List[tuple]:
        return await asyncio.to_thread(self._store.contradict, query, limit)

    async def update(self, fact_id: int, trust_delta: float) -> None:
        await asyncio.to_thread(self._store.update, fact_id, trust_delta)

    async def remove(self, fact_id: int) -> None:
        await asyncio.to_thread(self._store.remove, fact_id)

    async def list(self, category: Optional[str] = None, limit: int = 50) -> List[Fact]:
        return await asyncio.to_thread(self._store.list, category, limit)

    async def close(self) -> None:
        await asyncio.to_thread(self._store.close)

    def _build_entity_list(self, text: str, payload_entities: Optional[List[str]] = None) -> List[str]:
        """Heuristic entity extraction from message text."""
        import re
        # Simple noun-phrase extraction: capitalized words or quoted phrases
        found = re.findall(r'"([^"]+)"', text)
        found += re.findall(r"'([^']+)'", text)
        # Add any explicitly provided entities
        if payload_entities:
            found.extend(payload_entities)
        return list(dict.fromkeys(f for f in found if len(f) > 2))

    async def extract_and_store_facts(
        self,
        text: str,
        thread_id: str,
        user_id: str,
        category: str = "general",
        payload_entities: Optional[List[str]] = None,
    ) -> List[int]:
        """Extract simple facts from a message and store them.

        Returns list of stored fact IDs.
        """
        entities = self._build_entity_list(text, payload_entities)
        entities.append(f"thread:{thread_id}")
        entities.append(f"user:{user_id}")

        # Store the whole message as a general fact
        fact_id = await self.add(
            content=text[:2000],
            category=category,
            entities=entities,
            tags=["auto_extracted", category],
        )
        return [fact_id]

    async def retrieve_for_context(
        self,
        text: str,
        user_id: str,
        thread_id: str,
        limit: int = 5,
    ) -> List[Fact]:
        """Fetch relevant facts for a given message context.

        Tries: keyword search → entity probe → thread probe.
        """
        results: List[Fact] = []

        # 1. Keyword search on message text
        keywords = [w for w in text.lower().split() if len(w) > 3]
        for kw in keywords[:3]:
            hits = await self.search(kw, limit=limit, min_trust=0.3)
            results.extend(hits)

        # 2. Probe user entity
        user_hits = await self.probe(f"user:{user_id}", limit=limit)
        results.extend(user_hits)

        # 3. Probe thread entity
        thread_hits = await self.probe(f"thread:{thread_id}", limit=limit)
        results.extend(thread_hits)

        # Deduplicate and sort by trust desc
        seen: set[int] = set()
        unique: List[Fact] = []
        for fact in sorted(results, key=lambda f: f.trust, reverse=True):
            if fact.id not in seen:
                seen.add(fact.id)
                unique.append(fact)
        return unique[:limit]


def get_async_fact_store() -> AsyncFactStore:
    return AsyncFactStore()
