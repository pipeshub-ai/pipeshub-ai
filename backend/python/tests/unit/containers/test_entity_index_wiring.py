"""Only the indexing service recreates the entity collection on a dimension
change, and it is the service that runs the rebuild that refills it."""

import inspect
from unittest.mock import MagicMock

from app import indexing_main
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer
from app.containers.query import QueryAppContainer
from app.containers.utils.utils import ContainerUtils


async def test_factory_defaults_to_raising_on_mismatch() -> None:
    vector_db = MagicMock()
    store = await ContainerUtils().create_entity_vector_store(
        MagicMock(), MagicMock(), vector_db, "entities",
    )
    assert store.recreate_on_dimension_mismatch is False


async def test_factory_passes_the_recreate_flag() -> None:
    store = await ContainerUtils().create_entity_vector_store(
        MagicMock(), MagicMock(), MagicMock(), "entities", recreate_on_dimension_mismatch=True,
    )
    assert store.recreate_on_dimension_mismatch is True


def test_only_indexing_recreates() -> None:
    assert IndexingAppContainer.entity_vector_store.kwargs.get("recreate_on_dimension_mismatch") is True
    for container in (QueryAppContainer, ConnectorAppContainer):
        assert not container.entity_vector_store.kwargs.get("recreate_on_dimension_mismatch")


def test_indexing_starts_and_stops_the_rebuild_loop() -> None:
    source = inspect.getsource(indexing_main)
    # Started on the worker loop (Neo4j) and on the main loop, and cancelled on shutdown.
    assert source.count("run_entity_index_rebuild_loop(app_container, graph_provider)") == 2
    assert 'getattr(app.state, "entity_index_task", None)' in source
    assert 'getattr(app.state, "entity_index_future", None)' in source
