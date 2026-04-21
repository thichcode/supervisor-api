from types import SimpleNamespace

import pytest

from src.core import InputPayload, UserInfo, ConversationInfo, MessageInfo
from src.memory.file_provider import FileExternalMemoryProvider
from src.memory.providers import (
    ExternalMemoryProviderConfig,
    NullExternalMemoryProvider,
    get_external_memory_provider,
)
from src.memory.routing import ExternalMemoryRoutingPolicy
from src.memory.service import MemoryService


class FakeCache:
    async def get_json(self, key):
        return None

    async def set_json(self, key, value, ttl=3600):
        return True

    async def delete(self, key):
        return True


class FakeRepo:
    async def get_conversation_summary(self, thread_id):
        return None

    async def get_recent_messages(self, thread_id, limit=10):
        return []

    async def get_user_profile(self, user_id):
        return None

    async def get_case_memory(self, case_id):
        return None

    async def get_memory_items(self, scope, scope_id, limit=10):
        return []

    async def save_message(self, **kwargs):
        return None

    async def upsert_case_memory(self, case_id):
        return None

    async def upsert_conversation_summary(self, **kwargs):
        return None

    async def add_memory_item(self, **kwargs):
        return None

    async def upsert_user_profile(self, **kwargs):
        return None

    async def get_conversation_state(self, thread_id):
        return None


class FakeProvider:
    enabled = True

    async def search(self, **kwargs):
        return SimpleNamespace(
            to_memory_items=lambda: [{"content": "external memory result", "provider": "fake"}]
        )

    async def write_memory(self, **kwargs):
        return True

    async def health_check(self):
        return True


@pytest.fixture
def sample_payload_for_provider():
    return InputPayload(
        request_id="provider-test-123",
        source="ms_teams",
        timestamp="2026-04-07T00:00:00Z",
        user=UserInfo(id="user-001", display_name="John Doe", role="employee", team="Platform"),
        conversation=ConversationInfo(thread_id="thread-001", message_id="msg-001"),
        message=MessageInfo(text="How do I reset my password?"),
    )


@pytest.mark.asyncio
async def test_memory_service_uses_injected_external_provider(sample_payload_for_provider):
    service = MemoryService(session=None, cache=FakeCache(), external_provider=FakeProvider())
    service.repo = FakeRepo()

    context = await service.retrieve(sample_payload_for_provider)

    assert len(context.external_memory) == 1
    assert context.external_memory[0]["provider"] == "fake"


@pytest.mark.asyncio
async def test_provider_factory_returns_null_provider_when_disabled():
    provider = get_external_memory_provider(
        ExternalMemoryProviderConfig(
            provider_name="mempalace", enabled=False, path="/tmp/palace", top_k=3
        )
    )
    assert isinstance(provider, NullExternalMemoryProvider)


def test_provider_factory_rejects_unknown_provider():
    with pytest.raises(ValueError):
        get_external_memory_provider(
            ExternalMemoryProviderConfig(
                provider_name="unknown", enabled=True, path="/tmp/x", top_k=1
            )
        )


@pytest.mark.asyncio
async def test_provider_factory_returns_file_provider(tmp_path):
    provider = get_external_memory_provider(
        ExternalMemoryProviderConfig(
            provider_name="file", enabled=True, path=str(tmp_path / "memory.json"), top_k=3
        )
    )
    assert isinstance(provider, FileExternalMemoryProvider)


@pytest.mark.asyncio
async def test_file_provider_write_and_search(tmp_path):
    path = tmp_path / "memory.json"
    provider = FileExternalMemoryProvider(str(path))

    saved = await provider.write_memory(
        content="password reset steps for platform users",
        user_id="user-1",
        thread_id="thread-1",
    )
    assert saved is True

    results = await provider.search(
        message_text="password reset",
        user_id="user-1",
        thread_id="thread-1",
    )
    assert results.enabled is True
    assert len(results.results) >= 1
    assert results.to_memory_items()[0]["provider"] == "file"


def test_routing_policy_prefers_mempalace_for_cases():
    policy = ExternalMemoryRoutingPolicy(mempalace_enabled=True, file_enabled=True)
    route = policy.select(message_text="need case history", case_id="CASE-1", team="Platform")
    assert route.provider_name == "mempalace"


def test_routing_policy_falls_back_to_file_when_no_deep_context_needed():
    policy = ExternalMemoryRoutingPolicy(mempalace_enabled=False, file_enabled=True)
    route = policy.select(message_text="password reset", team=None)
    assert route.provider_name == "file"
