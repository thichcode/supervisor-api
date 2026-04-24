import pytest
from unittest.mock import AsyncMock, MagicMock
from src.memory.fact_store_adapter import AsyncFactStore, get_async_fact_store
from src.fact_store import Fact, FactStore


@pytest.fixture
def mock_fact_store():
    store = MagicMock(spec=FactStore)
    store.add = MagicMock(return_value=42)
    store.search = MagicMock(return_value=[])
    store.probe = MagicMock(return_value=[])
    store.related = MagicMock(return_value=[])
    store.reason = MagicMock(return_value=[])
    store.contradict = MagicMock(return_value=[])
    store.update = MagicMock()
    store.remove = MagicMock()
    store.list = MagicMock(return_value=[])
    store.close = MagicMock()
    return store


class TestAsyncFactStore:
    @pytest.mark.asyncio
    async def test_add_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        result = await async_store.add("test content", category="user_pref", entities=["user:001"])
        assert result == 42
        mock_fact_store.add.assert_called_once_with("test content", "user_pref", ["user:001"], None)

    @pytest.mark.asyncio
    async def test_search_dispatches_to_thread(self, mock_fact_store):
        mock_fact_store.search.return_value = [
            Fact(id=1, content="hello", category="general", entities=["a"], tags=["t"], trust=0.8)
        ]
        async_store = AsyncFactStore(store=mock_fact_store)
        results = await async_store.search("hello")
        assert len(results) == 1
        assert results[0].id == 1
        mock_fact_store.search.assert_called_once_with("hello", 10, 0.3)

    @pytest.mark.asyncio
    async def test_probe_dispatches_to_thread(self, mock_fact_store):
        mock_fact_store.probe.return_value = [
            Fact(id=2, content="probe result", category="project", entities=["x"], tags=["y"])
        ]
        async_store = AsyncFactStore(store=mock_fact_store)
        results = await async_store.probe("entity_x")
        assert len(results) == 1
        mock_fact_store.probe.assert_called_once_with("entity_x", 10, 0.3)

    @pytest.mark.asyncio
    async def test_related_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.related("entity_a")
        mock_fact_store.related.assert_called_once_with("entity_a", 10)

    @pytest.mark.asyncio
    async def test_reason_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.reason(["a", "b"])
        mock_fact_store.reason.assert_called_once_with(["a", "b"], 10)

    @pytest.mark.asyncio
    async def test_contradict_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.contradict("query")
        mock_fact_store.contradict.assert_called_once_with("query", 5)

    @pytest.mark.asyncio
    async def test_update_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.update(1, 0.2)
        mock_fact_store.update.assert_called_once_with(1, 0.2)

    @pytest.mark.asyncio
    async def test_remove_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.remove(1)
        mock_fact_store.remove.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_list_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.list(category="general", limit=10)
        mock_fact_store.list.assert_called_once_with("general", 10)

    @pytest.mark.asyncio
    async def test_close_dispatches_to_thread(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        await async_store.close()
        mock_fact_store.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_and_store_facts(self, mock_fact_store):
        async_store = AsyncFactStore(store=mock_fact_store)
        fact_ids = await async_store.extract_and_store_facts(
            text="My laptop is broken",
            thread_id="thread-001",
            user_id="user-001",
        )
        assert fact_ids == [42]
        call_args = mock_fact_store.add.call_args
        # asyncio.to_thread passes positional args to the sync store.add
        assert call_args.args[1] == "general"  # category positional
        assert "thread:thread-001" in call_args.args[2]  # entities positional
        assert "user:user-001" in call_args.args[2]  # entities positional

    @pytest.mark.asyncio
    async def test_retrieve_for_context_combines_sources(self, mock_fact_store):
        mock_fact_store.search.return_value = [
            Fact(id=1, content="search hit", category="general", entities=["a"], tags=["t"])
        ]
        mock_fact_store.probe.side_effect = [
            [Fact(id=2, content="user hit", category="general", entities=["b"], tags=["t"])],
            [Fact(id=3, content="thread hit", category="general", entities=["c"], tags=["t"])],
        ]
        async_store = AsyncFactStore(store=mock_fact_store)
        results = await async_store.retrieve_for_context(
            text="hello world query",
            user_id="user-001",
            thread_id="thread-001",
            limit=5,
        )
        assert len(results) == 3
        assert {r.id for r in results} == {1, 2, 3}

    def test_build_entity_list_extracts_quoted_and_long_words(self):
        async_store = AsyncFactStore(store=MagicMock())
        entities = async_store._build_entity_list('The "Project Alpha" is important')
        assert "Project Alpha" in entities

    def test_get_async_fact_store_returns_instance(self):
        store = get_async_fact_store()
        assert isinstance(store, AsyncFactStore)


class TestMemoryServiceFactStoreIntegration:
    @pytest.mark.asyncio
    async def test_memory_context_includes_facts_field(self):
        from src.memory.service import MemoryContext
        ctx = MemoryContext(facts=[{"id": 1, "content": "test"}])
        assert ctx.facts == [{"id": 1, "content": "test"}]
        d = ctx.to_dict()
        assert "facts" in d
        assert d["facts"] == [{"id": 1, "content": "test"}]

    @pytest.mark.asyncio
    async def test_memory_context_facts_render_in_context_text(self):
        from src.memory.service import MemoryContext
        ctx = MemoryContext(facts=[{"content": "User prefers email"}])
        text = ctx.get_context_text()
        assert "Known Facts" in text
        assert "User prefers email" in text
