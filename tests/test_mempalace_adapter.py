from unittest.mock import patch

import pytest

from src.core.circuit_breaker import CircuitState
from src.memory.mapping import MemPalaceMappingPolicy
from src.memory.mempalace_adapter import MemPalaceAdapter
from src.memory.service import MemoryContext


@pytest.mark.asyncio
async def test_mempalace_adapter_disabled_without_path():
    adapter = MemPalaceAdapter(palace_path="")
    adapter._enabled = False
    result = await adapter.search(
        message_text="auth migration decision",
        user_id="user-1",
        thread_id="thread-1",
    )
    assert result.enabled is False
    assert result.results == []


@pytest.mark.asyncio
async def test_mempalace_adapter_maps_results():
    adapter = MemPalaceAdapter(palace_path="/tmp/palace", top_k=2)
    adapter._enabled = True

    fake_result = {
        "results": [
            {
                "text": "We switched auth to Clerk because of DX.",
                "wing": "wing_team_platform",
                "room": "auth-migration",
                "source_file": "decision.md",
                "similarity": 0.91,
            }
        ]
    }

    with patch.object(adapter, "_load_search_memories", return_value=lambda *args, **kwargs: fake_result):
        result = await adapter.search(
            message_text="why did we switch auth",
            user_id="user-1",
            thread_id="thread-1",
            team="Platform",
        )

    assert result.enabled is True
    assert len(result.results) == 1
    assert result.to_memory_items()[0]["provider"] == "mempalace"
    assert result.to_memory_items()[0]["room"] == "auth-migration"


def test_mapping_policy_prefers_case_over_team():
    policy = MemPalaceMappingPolicy()
    mapping = policy.resolve(
        user_id="user-1",
        message_text="Need case update",
        thread_id="thread-1",
        case_id="CASE-123",
        team="Platform",
    )
    assert mapping.wing == "wing_case_case-123" or mapping.wing == "wing_case_case_123"
    assert mapping.read_room == "case-history"


def test_mapping_policy_maps_team_policy_intent():
    policy = MemPalaceMappingPolicy()
    mapping = policy.resolve(
        user_id="user-1",
        message_text="what is the annual leave policy",
        thread_id="thread-1",
        team="People Ops",
        intent="policy",
    )
    assert mapping.wing == "wing_team_people_ops"
    assert mapping.read_room == "policy-guidance"


@pytest.mark.asyncio
async def test_mempalace_adapter_write_memory_success():
    adapter = MemPalaceAdapter(palace_path="/tmp/palace", top_k=2)
    adapter._enabled = True

    fake_collection = object()

    def fake_add_drawer(**kwargs):
        return True

    with patch.object(adapter, "_load_add_drawer", return_value=(fake_add_drawer, lambda path: fake_collection)):
        saved = await adapter.write_memory(
            content="important insight",
            user_id="user-1",
            thread_id="thread-1",
            team="Platform",
        )

    assert saved is True


@pytest.mark.asyncio
async def test_mempalace_adapter_health_check_success():
    adapter = MemPalaceAdapter(palace_path="/tmp/palace", top_k=1)
    adapter._enabled = True

    with patch.object(adapter, "_load_search_memories", return_value=lambda *args, **kwargs: {"results": []}):
        healthy = await adapter.health_check()

    assert healthy is True


@pytest.mark.asyncio
async def test_mempalace_adapter_search_timeout_returns_empty_context():
    adapter = MemPalaceAdapter(palace_path="/tmp/palace", top_k=1)
    adapter._enabled = True
    adapter.timeout_seconds = 0.01

    def slow_search(*args, **kwargs):
        import time
        time.sleep(0.05)
        return {"results": []}

    with patch.object(adapter, "_load_search_memories", return_value=slow_search):
        result = await adapter.search(
            message_text="why did we switch auth",
            user_id="user-1",
            thread_id="thread-1",
        )

    assert result.results == []


@pytest.mark.asyncio
async def test_mempalace_adapter_circuit_opens_after_failures():
    adapter = MemPalaceAdapter(palace_path="/tmp/palace", top_k=1)
    adapter._enabled = True
    adapter.retry_attempts = 1
    adapter.circuit_breaker.config.failure_threshold = 2

    def failing_search(*args, **kwargs):
        raise RuntimeError("boom")

    with patch.object(adapter, "_load_search_memories", return_value=failing_search):
        await adapter.search(message_text="q1", user_id="u1", thread_id="t1")
        await adapter.search(message_text="q2", user_id="u1", thread_id="t1")

    assert adapter.circuit_breaker.state == CircuitState.OPEN


def test_memory_context_includes_external_memory_in_dict_and_text():
    context = MemoryContext(
        conversation_summary="summary",
        recent_messages=["a"],
        external_memory=[{"content": "external fact", "provider": "mempalace"}],
    )
    as_dict = context.to_dict()
    assert "external_memory" in as_dict
    assert "external fact" in context.get_context_text()