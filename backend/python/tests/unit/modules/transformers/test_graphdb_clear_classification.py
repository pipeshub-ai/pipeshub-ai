"""``GraphDBTransformer.clear_classification``: the record's classification as if a classification had found nothing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, EntityOrigin
from app.modules.transformers.graphdb import GraphDBTransformer

RECORD = "rec-1"
FROM = f"{CollectionNames.RECORDS.value}/{RECORD}"
COLLECTIONS = [
    CollectionNames.BELONGS_TO_DEPARTMENT.value,
    CollectionNames.BELONGS_TO_CATEGORY.value,
    CollectionNames.BELONGS_TO_LANGUAGE.value,
    CollectionNames.BELONGS_TO_TOPIC.value,
]


class _Tx:
    """A transaction store holding the record's edges per collection."""

    def __init__(self, edges: dict[str, list[dict]]) -> None:
        self.edges = edges
        self.deleted: list[tuple[str, list[dict]]] = []
        self.created: list[str] = []

    async def get_edges_from_node_with_target_name(self, record_from: str, collection: str, raise_on_error: bool = False) -> list[dict]:
        assert record_from == FROM and raise_on_error is True
        return list(self.edges.get(collection, []))

    async def batch_create_edges(self, edges: list[dict], collection: str) -> None:
        self.created.append(collection)

    async def batch_delete_edges(self, edges: list[dict], collection: str) -> int:
        self.deleted.append((collection, edges))
        self.edges[collection] = []
        return len(edges)


def _transformer(tx: _Tx) -> GraphDBTransformer:
    transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=MagicMock())
    transformer.graph_data_store = MagicMock()

    async def run(func, *args, **kwargs) -> object:
        return await func(tx, *args, **kwargs)

    transformer.graph_data_store.execute_idempotent_in_transaction = AsyncMock(side_effect=run)
    transformer.graph_data_store.graph_provider = MagicMock()
    transformer.graph_data_store.graph_provider.delete_record_entity_relations = AsyncMock(return_value=0)
    return transformer


def _edge(collection: str, key: str) -> dict:
    return {"_to": f"{collection}/{key}", "name": key}


async def test_every_record_edge_collection_the_writer_reconciles_is_emptied() -> None:
    tx = _Tx({c: [_edge(c.replace("belongsTo", "x"), "a"), _edge(c.replace("belongsTo", "x"), "b")] for c in COLLECTIONS})
    await _transformer(tx).clear_classification(RECORD)
    assert tx.edges == {c: [] for c in COLLECTIONS}
    assert [c for c, _ in tx.deleted] == COLLECTIONS
    assert tx.created == []


async def test_a_record_with_no_edges_deletes_nothing() -> None:
    tx = _Tx({})
    await _transformer(tx).clear_classification(RECORD)
    assert tx.deleted == []


async def test_a_failed_edge_read_raises_instead_of_reading_as_empty() -> None:
    tx = _Tx({})
    tx.get_edges_from_node_with_target_name = AsyncMock(side_effect=RuntimeError("down"))
    transformer = _transformer(tx)
    with pytest.raises(RuntimeError):
        await transformer.clear_classification(RECORD)
    transformer.graph_data_store.graph_provider.delete_record_entity_relations.assert_not_awaited()


async def test_extracted_organisation_links_are_deleted_directly() -> None:
    """Not through ``link_organizations``, which returns before its delete when the summary names no organisations."""
    tx = _Tx({})
    transformer = _transformer(tx)
    transformer.link_organizations = AsyncMock()
    await transformer.clear_classification(RECORD)
    transformer.graph_data_store.graph_provider.delete_record_entity_relations.assert_awaited_once_with(
        RECORD, CollectionNames.ORGS.value, EntityOrigin.EXTRACTED.value,
    )
    transformer.link_organizations.assert_not_awaited()
