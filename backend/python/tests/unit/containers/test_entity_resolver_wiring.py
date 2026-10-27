"""Indexing container wires the resolver into the sink orchestrator."""

from unittest.mock import MagicMock

from app.containers.indexing import IndexingAppContainer
from app.containers.utils.utils import ContainerUtils
from app.modules.entity_resolution import EntityResolver


async def test_create_entity_resolver_wires_dependencies() -> None:
    utils = ContainerUtils()
    logger, config, graph, store = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    resolver = await utils.create_entity_resolver(logger, config, graph, store)
    assert isinstance(resolver, EntityResolver)
    assert resolver.graph_provider is graph
    assert resolver.entity_vector_store is store
    assert resolver.config_service is config


async def test_sink_orchestrator_receives_the_resolver() -> None:
    utils = ContainerUtils()
    resolver = MagicMock()
    orchestrator = await utils.create_sink_orchestrator(
        logger=MagicMock(), graphdb=MagicMock(), blob_storage=MagicMock(), vector_store=MagicMock(),
        graph_provider=MagicMock(), config_service=MagicMock(), entity_vector_store=MagicMock(),
        entity_resolver=resolver,
    )
    assert orchestrator.entity_resolver is resolver


def test_indexing_container_declares_the_provider() -> None:
    assert hasattr(IndexingAppContainer, "entity_resolver")
    assert "entity_resolver" in IndexingAppContainer.sink_orchestrator.kwargs
