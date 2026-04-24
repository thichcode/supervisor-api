"""Subagent Delegation Pattern for multi-source / complex tasks.

When a task requires synthesizing information from multiple sources or
steps, the Supervisor spawns lightweight subagents that run in parallel,
then aggregates their outputs into a coherent response.

Example:
    "Viết báo cáo tổng hợp từ 3 nguồn" → 3 parallel subagents → aggregate
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import structlog

logger = structlog.get_logger()


@dataclass
class SubagentTask:
    """Definition of a single subagent task."""

    task_id: str
    name: str
    instruction: str
    payload_context: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    max_retries: int = 1


@dataclass
class SubagentResult:
    """Result returned by a single subagent execution."""

    task_id: str
    success: bool
    output: str
    confidence: float = 0.5
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class SubagentPool:
    """Pool that spawns and executes subagent tasks concurrently.

    Usage:
        pool = SubagentPool()
        results = await pool.run([
            SubagentTask(task_id="src1", name="research", instruction="..."),
            SubagentTask(task_id="src2", name="research", instruction="..."),
        ], handler=my_handler)
    """

    def __init__(self, max_concurrency: int = 5) -> None:
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run(
        self,
        tasks: list[SubagentTask],
        handler: Callable[[SubagentTask], Awaitable[SubagentResult]],
    ) -> list[SubagentResult]:
        """Execute all tasks concurrently with semaphore-bound concurrency."""
        if not tasks:
            return []

        logger.info("subagent_pool_start", task_count=len(tasks), max_concurrency=self.max_concurrency)
        start = time.time()

        async def _bounded(task: SubagentTask) -> SubagentResult:
            async with self._semaphore:
                return await self._execute_one(task, handler)

        results = await asyncio.gather(*(_bounded(t) for t in tasks), return_exceptions=True)

        elapsed_ms = int((time.time() - start) * 1000)
        successful = sum(1 for r in results if isinstance(r, SubagentResult) and r.success)
        logger.info(
            "subagent_pool_done",
            task_count=len(tasks),
            successful=successful,
            elapsed_ms=elapsed_ms,
        )
        return self._normalize_results(tasks, results)

    async def _execute_one(
        self,
        task: SubagentTask,
        handler: Callable[[SubagentTask], Awaitable[SubagentResult]],
    ) -> SubagentResult:
        task_start = time.time()
        last_error: Optional[str] = None

        for attempt in range(task.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    handler(task),
                    timeout=task.timeout,
                )
                result.latency_ms = int((time.time() - task_start) * 1000)
                logger.debug(
                    "subagent_task_success",
                    task_id=task.task_id,
                    attempt=attempt + 1,
                    latency_ms=result.latency_ms,
                )
                return result
            except asyncio.TimeoutError:
                last_error = f"timeout after {task.timeout}s"
                logger.warning("subagent_task_timeout", task_id=task.task_id, attempt=attempt + 1)
            except Exception as exc:
                last_error = str(exc)
                logger.warning("subagent_task_error", task_id=task.task_id, attempt=attempt + 1, error=last_error)

        return SubagentResult(
            task_id=task.task_id,
            success=False,
            output="",
            error=last_error or "all retries exhausted",
            latency_ms=int((time.time() - task_start) * 1000),
        )

    def _normalize_results(
        self,
        tasks: list[SubagentTask],
        results: list[Any],
    ) -> list[SubagentResult]:
        out: list[SubagentResult] = []
        for task, raw in zip(tasks, results):
            if isinstance(raw, SubagentResult):
                out.append(raw)
            elif isinstance(raw, Exception):
                out.append(
                    SubagentResult(
                        task_id=task.task_id,
                        success=False,
                        output="",
                        error=str(raw),
                    )
                )
            else:
                out.append(
                    SubagentResult(
                        task_id=task.task_id,
                        success=False,
                        output="",
                        error="unexpected result type",
                    )
                )
        return out


class SubagentAggregator:
    """Aggregate multiple subagent results into a single coherent output."""

    def __init__(self, joiner: str = "\n\n---\n\n") -> None:
        self.joiner = joiner

    def aggregate(self, results: list[SubagentResult]) -> str:
        """Simple aggregation: concatenate successful outputs."""
        successes = [r for r in results if r.success and r.output.strip()]
        if not successes:
            return "Không thu thập được thông tin từ các nguồn."
        if len(successes) == 1:
            return successes[0].output
        parts = []
        for i, r in enumerate(successes, 1):
            parts.append(f"### Nguồn {i} ({r.task_id})\n{r.output}")
        return self.joiner.join(parts)

    def aggregate_with_summary(
        self,
        results: list[SubagentResult],
        llm_handler: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> str:
        """Aggregate and optionally ask LLM to synthesize a summary."""
        combined = self.aggregate(results)
        if llm_handler and len(results) > 1:
            # Fire-and-forget synthesis via LLM if provided
            return combined  # Placeholder: real synthesis would call llm_handler
        return combined

    def confidence(self, results: list[SubagentResult]) -> float:
        """Compute aggregate confidence from subagent results."""
        if not results:
            return 0.0
        successes = [r for r in results if r.success]
        if not successes:
            return 0.3
        avg_conf = sum(r.confidence for r in successes) / len(successes)
        # Penalize partial failures
        penalty = 0.1 * (len(results) - len(successes))
        return max(0.0, min(1.0, avg_conf - penalty))


def build_multi_source_tasks(
    instruction: str,
    sources: list[str],
    base_context: Optional[dict[str, Any]] = None,
) -> list[SubagentTask]:
    """Helper: build parallel subagent tasks for multi-source gathering.

    Args:
        instruction: Base instruction (e.g., "tìm thông tin về X")
        sources: List of source identifiers / URLs / topics
        base_context: Shared context dict for all tasks
    """
    return [
        SubagentTask(
            task_id=f"src_{i}_{source[:20].replace(' ', '_')}",
            name="research",
            instruction=f"{instruction} từ nguồn: {source}",
            payload_context={**(base_context or {}), "source": source},
        )
        for i, source in enumerate(sources)
    ]
