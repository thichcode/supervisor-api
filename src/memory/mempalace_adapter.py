from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
import asyncio

import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.core.circuit_breaker import CircuitBreakerConfig, CircuitBreakerError, get_circuit_breaker
from src.core.metrics import metrics
from src.memory.mapping import MemPalaceMappingPolicy

logger = structlog.get_logger()


@dataclass
class MemPalaceSearchHit:
    text: str
    wing: str
    room: str
    source_file: str
    similarity: float


@dataclass
class MemPalaceContext:
    query: str
    results: list[MemPalaceSearchHit]
    enabled: bool = False

    def to_memory_items(self) -> list[dict]:
        return [
            {
                "content": hit.text,
                "confidence": hit.similarity,
                "wing": hit.wing,
                "room": hit.room,
                "source_file": hit.source_file,
                "provider": "mempalace",
            }
            for hit in self.results
        ]


class MemPalaceAdapter:
    """Optional adapter for querying a local MemPalace palace.

    This POC keeps integration intentionally thin:
    - if MemPalace is not installed, it degrades gracefully
    - if no palace path is configured, it is disabled
    - search results are mapped into episodic memory-style items
    """

    def __init__(self, palace_path: Optional[str] = None, top_k: int = 3):
        settings = get_settings()
        self._enabled = settings.mempalace_enabled
        self.palace_path = palace_path or settings.mempalace_path
        self.top_k = top_k
        self.timeout_seconds = settings.mempalace_timeout_seconds
        self.retry_attempts = settings.mempalace_retry_attempts
        self.mapping_policy = MemPalaceMappingPolicy()
        self.circuit_breaker = get_circuit_breaker(
            "mempalace_provider",
            CircuitBreakerConfig(
                failure_threshold=settings.mempalace_circuit_failure_threshold,
                success_threshold=settings.mempalace_circuit_success_threshold,
                timeout=settings.mempalace_circuit_timeout_seconds,
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self.palace_path)

    def _build_query(self, message_text: str, thread_id: str, case_id: Optional[str]) -> str:
        parts = [message_text.strip()]
        if case_id:
            parts.append(f"case:{case_id}")
        if thread_id:
            parts.append(f"thread:{thread_id}")
        return " | ".join(part for part in parts if part)

    def _load_search_memories(self):
        from mempalace.searcher import search_memories

        return search_memories

    def _load_add_drawer(self):
        from mempalace.miner import add_drawer, get_collection

        return add_drawer, get_collection

    async def _run_with_resilience(self, operation: str, func, *args, **kwargs):
        if not await self.circuit_breaker.can_execute():
            metrics.record_external_memory("mempalace", operation, "circuit_open")
            raise CircuitBreakerError("mempalace_provider", "MemPalace circuit breaker is open")

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.retry_attempts),
                wait=wait_exponential(multiplier=0.2, min=0.2, max=1),
                retry=retry_if_exception_type((TimeoutError, RuntimeError, OSError)),
                reraise=True,
            ):
                with attempt:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(func, *args, **kwargs),
                        timeout=self.timeout_seconds,
                    )
            await self.circuit_breaker.record_success()
            return result
        except asyncio.TimeoutError as exc:
            await self.circuit_breaker.record_failure()
            raise TimeoutError(f"MemPalace {operation} timed out") from exc
        except Exception:
            await self.circuit_breaker.record_failure()
            raise

    async def health_check(self) -> bool:
        if not self.enabled:
            return False

        try:
            search_memories = self._load_search_memories()
            result = search_memories(
                "health check",
                palace_path=str(Path(self.palace_path)),
                n_results=1,
            )
            metrics.record_external_memory("mempalace", "health_check", "success")
            return isinstance(result, dict)
        except Exception as exc:
            metrics.record_external_memory("mempalace", "health_check", "error")
            logger.warning("mempalace_health_check_failed", error=str(exc))
            return False

    async def search(
        self,
        *,
        message_text: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
    ) -> MemPalaceContext:
        if not self.enabled:
            metrics.record_external_memory("mempalace", "search", "disabled")
            return MemPalaceContext(query=message_text, results=[], enabled=False)

        try:
            search_memories = self._load_search_memories()
        except ImportError:
            metrics.record_external_memory("mempalace", "search", "unavailable")
            return MemPalaceContext(query=message_text, results=[], enabled=False)

        query = self._build_query(message_text, thread_id, case_id)
        mapping = self.mapping_policy.resolve(
            user_id=user_id,
            message_text=message_text,
            thread_id=thread_id,
            case_id=case_id,
            team=team,
        )
        wing = mapping.wing

        try:
            result = await self._run_with_resilience(
                "search",
                search_memories,
                query,
                palace_path=str(Path(self.palace_path)),
                wing=wing,
                room=mapping.read_room,
                n_results=self.top_k,
            )
            metrics.record_external_memory("mempalace", "search", "success")
            logger.info("mempalace_search_completed", query=query, wing=wing, top_k=self.top_k)
        except CircuitBreakerError as exc:
            metrics.record_external_memory("mempalace", "search", "circuit_open")
            logger.warning("mempalace_search_blocked", error=str(exc), wing=wing)
            return MemPalaceContext(query=query, results=[], enabled=True)
        except Exception as exc:
            metrics.record_external_memory("mempalace", "search", "error")
            logger.warning("mempalace_search_failed", error=str(exc), wing=wing)
            return MemPalaceContext(query=query, results=[], enabled=True)

        raw_hits = result.get("results", []) if isinstance(result, dict) else []
        hits = [
            MemPalaceSearchHit(
                text=hit.get("text", ""),
                wing=hit.get("wing", "unknown"),
                room=hit.get("room", "unknown"),
                source_file=hit.get("source_file", "?"),
                similarity=float(hit.get("similarity", 0.0)),
            )
            for hit in raw_hits
        ]
        return MemPalaceContext(query=query, results=hits, enabled=True)

    async def write_memory(
        self,
        *,
        content: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
        room: str = "supervisor-insights",
        source_file: str = "supervisor-api",
        agent: str = "supervisor-api",
    ) -> bool:
        if not self.enabled:
            metrics.record_external_memory("mempalace", "write", "disabled")
            return False

        try:
            add_drawer, get_collection = self._load_add_drawer()
        except ImportError:
            metrics.record_external_memory("mempalace", "write", "unavailable")
            return False

        mapping = self.mapping_policy.resolve(
            user_id=user_id,
            message_text=content,
            thread_id=thread_id,
            case_id=case_id,
            team=team,
        )
        wing = mapping.wing
        resolved_room = room or mapping.write_room or "supervisor-insights"

        try:
            collection = get_collection(str(Path(self.palace_path)))
            saved = await self._run_with_resilience(
                "write",
                add_drawer,
                collection=collection,
                wing=wing,
                room=resolved_room,
                content=content,
                source_file=source_file,
                chunk_index=int(datetime.now().timestamp()),
                agent=agent,
            )
            metrics.record_external_memory("mempalace", "write", "success" if saved else "duplicate")
            logger.info("mempalace_write_completed", wing=wing, room=resolved_room, saved=saved)
            return bool(saved)
        except CircuitBreakerError as exc:
            metrics.record_external_memory("mempalace", "write", "circuit_open")
            logger.warning("mempalace_write_blocked", error=str(exc), wing=wing, room=resolved_room)
            return False
        except Exception as exc:
            metrics.record_external_memory("mempalace", "write", "error")
            logger.warning("mempalace_write_failed", error=str(exc), wing=wing, room=resolved_room)
            return False