"""``load_entity_vector_store``: the one loader both chat routes use for the
optional store behind the knowledge-graph entity tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.chatbot import load_entity_vector_store


def _store(*, collection: bool = True) -> MagicMock:
    store = MagicMock()
    store.collection_exists = AsyncMock(return_value=collection)
    return store


class TestLoadEntityVectorStore:
    @pytest.mark.asyncio
    async def test_awaitable_provider_is_awaited(self) -> None:
        store = _store()
        container = MagicMock(entity_vector_store=AsyncMock(return_value=store))
        assert await load_entity_vector_store(container, MagicMock()) is store

    @pytest.mark.asyncio
    async def test_sync_provider_result_is_used_as_is(self) -> None:
        """A non-awaitable provider result used to read as "no store"."""
        store = _store()
        container = MagicMock(entity_vector_store=MagicMock(return_value=store))
        assert await load_entity_vector_store(container, MagicMock()) is store

    @pytest.mark.asyncio
    async def test_no_entities_collection_hides_the_tools(self) -> None:
        """Nothing indexed yet: the tools could only come back empty, and
        their schemas and prompt paragraph cost every request."""
        container = MagicMock(entity_vector_store=AsyncMock(return_value=_store(collection=False)))
        assert await load_entity_vector_store(container, MagicMock()) is None

    @pytest.mark.asyncio
    async def test_a_failing_provider_is_none_and_logged(self) -> None:
        logger = MagicMock()
        container = MagicMock(entity_vector_store=AsyncMock(side_effect=RuntimeError("vector db down")))
        assert await load_entity_vector_store(container, logger) is None
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_provider_is_none(self) -> None:
        container = MagicMock(spec=[])
        assert await load_entity_vector_store(container, MagicMock()) is None

    @pytest.mark.asyncio
    async def test_get_services_does_not_touch_the_vector_db(self, monkeypatch) -> None:
        """Fifteen agent routes share get_services; only chat needs the store,
        and loading it is a vector-DB round trip."""
        import inspect

        from app.api.routes import agent as agent_routes

        loader = AsyncMock(return_value="store")
        monkeypatch.setattr(agent_routes, "load_entity_vector_store", loader)
        container = MagicMock()
        container.retrieval_service = AsyncMock()
        container.graph_provider = AsyncMock()

        services = await agent_routes.get_services(MagicMock(app=MagicMock(container=container)))

        assert "entity_vector_store" not in services
        loader.assert_not_awaited()
        assert "await load_entity_vector_store(" in inspect.getsource(agent_routes.chat_stream)
