import pytest
import asyncio
from unittest.mock import AsyncMock
from src.core.subagent_delegation import (
    SubagentPool,
    SubagentAggregator,
    SubagentTask,
    SubagentResult,
    build_multi_source_tasks,
)


class TestSubagentPool:
    @pytest.mark.asyncio
    async def test_run_empty_tasks(self):
        pool = SubagentPool()
        results = await pool.run([], handler=AsyncMock())
        assert results == []

    @pytest.mark.asyncio
    async def test_run_single_task_success(self):
        pool = SubagentPool()

        async def handler(task: SubagentTask) -> SubagentResult:
            return SubagentResult(task_id=task.task_id, success=True, output="ok")

        results = await pool.run(
            [SubagentTask(task_id="t1", name="test", instruction="do it")],
            handler=handler,
        )
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "ok"

    @pytest.mark.asyncio
    async def test_run_multiple_tasks_parallel(self):
        pool = SubagentPool(max_concurrency=2)
        call_order = []

        async def handler(task: SubagentTask) -> SubagentResult:
            await asyncio.sleep(0.05)
            call_order.append(task.task_id)
            return SubagentResult(task_id=task.task_id, success=True, output=task.task_id)

        tasks = [
            SubagentTask(task_id=f"t{i}", name="test", instruction="do it")
            for i in range(3)
        ]
        results = await pool.run(tasks, handler=handler)
        assert len(results) == 3
        assert all(r.success for r in results)
        # Parallel execution: order may vary
        assert set(r.task_id for r in results) == {"t0", "t1", "t2"}

    @pytest.mark.asyncio
    async def test_run_task_timeout_then_retry(self):
        pool = SubagentPool()
        attempt = {"count": 0}

        async def handler(task: SubagentTask) -> SubagentResult:
            attempt["count"] += 1
            if attempt["count"] == 1:
                await asyncio.sleep(10)
            return SubagentResult(task_id=task.task_id, success=True, output="ok")

        results = await pool.run(
            [SubagentTask(task_id="t1", name="test", instruction="x", timeout=0.1, max_retries=1)],
            handler=handler,
        )
        assert len(results) == 1
        assert results[0].success is True
        assert attempt["count"] == 2

    @pytest.mark.asyncio
    async def test_run_task_all_retries_exhausted(self):
        pool = SubagentPool()

        async def handler(task: SubagentTask) -> SubagentResult:
            raise RuntimeError("always fail")

        results = await pool.run(
            [SubagentTask(task_id="t1", name="test", instruction="x", timeout=1.0, max_retries=1)],
            handler=handler,
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "always fail" in results[0].error

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        pool = SubagentPool(max_concurrency=1)
        active = {"max": 0, "current": 0}

        async def handler(task: SubagentTask) -> SubagentResult:
            active["current"] += 1
            active["max"] = max(active["max"], active["current"])
            await asyncio.sleep(0.05)
            active["current"] -= 1
            return SubagentResult(task_id=task.task_id, success=True, output="ok")

        tasks = [SubagentTask(task_id=f"t{i}", name="test", instruction="x") for i in range(3)]
        await pool.run(tasks, handler=handler)
        assert active["max"] == 1


class TestSubagentAggregator:
    def test_aggregate_all_success(self):
        agg = SubagentAggregator()
        results = [
            SubagentResult(task_id="a", success=True, output="result A"),
            SubagentResult(task_id="b", success=True, output="result B"),
        ]
        text = agg.aggregate(results)
        assert "Nguồn 1" in text
        assert "result A" in text
        assert "Nguồn 2" in text
        assert "result B" in text

    def test_aggregate_single_success(self):
        agg = SubagentAggregator()
        results = [SubagentResult(task_id="a", success=True, output="only one")]
        assert agg.aggregate(results) == "only one"

    def test_aggregate_no_success(self):
        agg = SubagentAggregator()
        results = [SubagentResult(task_id="a", success=False, output="", error="fail")]
        assert "Không thu thập" in agg.aggregate(results)

    def test_confidence_all_success(self):
        agg = SubagentAggregator()
        results = [
            SubagentResult(task_id="a", success=True, output="x", confidence=0.8),
            SubagentResult(task_id="b", success=True, output="y", confidence=0.6),
        ]
        assert agg.confidence(results) == 0.7

    def test_confidence_partial_failure(self):
        agg = SubagentAggregator()
        results = [
            SubagentResult(task_id="a", success=True, output="x", confidence=0.8),
            SubagentResult(task_id="b", success=False, output="", error="fail"),
        ]
        assert round(agg.confidence(results), 3) == 0.7

    def test_confidence_all_fail(self):
        agg = SubagentAggregator()
        results = [SubagentResult(task_id="a", success=False, output="", error="fail")]
        assert agg.confidence(results) == 0.3

    def test_confidence_empty(self):
        agg = SubagentAggregator()
        assert agg.confidence([]) == 0.0


class TestBuildMultiSourceTasks:
    def test_build_from_sources(self):
        tasks = build_multi_source_tasks(
            instruction="Tìm thông tin về AI",
            sources=["https://a.com", "https://b.com"],
        )
        assert len(tasks) == 2
        assert tasks[0].task_id.startswith("src_0_")
        assert "https://a.com" in tasks[0].instruction

    def test_build_with_base_context(self):
        tasks = build_multi_source_tasks(
            instruction="X",
            sources=["s1"],
            base_context={"user_id": "u1"},
        )
        assert tasks[0].payload_context["user_id"] == "u1"
        assert tasks[0].payload_context["source"] == "s1"


class TestReasoningLoopSubagentIntegration:
    @pytest.mark.asyncio
    async def test_should_delegate_detects_trigger_words(self):
        from src.core.reasoning_loop import ReasoningLoopOrchestrator
        from unittest.mock import MagicMock

        supervisor = MagicMock()
        orch = ReasoningLoopOrchestrator(supervisor)

        class FakePayload:
            message = type("M", (), {"text": "Viết báo cáo tổng hợp từ 3 nguồn"})()

        assert orch._should_delegate_to_subagent_pool(FakePayload()) is True

    @pytest.mark.asyncio
    async def test_should_delegate_disabled_by_config(self, monkeypatch):
        from src.core.reasoning_loop import ReasoningLoopOrchestrator
        from unittest.mock import MagicMock

        supervisor = MagicMock()
        orch = ReasoningLoopOrchestrator(supervisor)

        class FakeSettings:
            enable_subagent_delegation = False

        monkeypatch.setattr("src.core.reasoning_loop.get_settings", FakeSettings)

        class FakePayload:
            message = type("M", (), {"text": "Viết báo cáo tổng hợp"})()

        assert orch._should_delegate_to_subagent_pool(FakePayload()) is False

    @pytest.mark.asyncio
    async def test_build_subagent_tasks_from_urls(self):
        from src.core.reasoning_loop import ReasoningLoopOrchestrator
        from unittest.mock import MagicMock

        supervisor = MagicMock()
        orch = ReasoningLoopOrchestrator(supervisor)

        class FakePayload:
            message = type("M", (), {
                "text": 'Tổng hợp từ "Nguồn A" và https://example.com/page'
            })()
            user = type("U", (), {"id": "u1"})()
            conversation = type("C", (), {"thread_id": "t1"})()

        tasks = orch._build_subagent_tasks(FakePayload(), {"knowledge_results": []})
        assert len(tasks) == 2
        assert any("Nguồn A" in t.instruction for t in tasks)
        assert any("https://example.com/page" in t.instruction for t in tasks)
