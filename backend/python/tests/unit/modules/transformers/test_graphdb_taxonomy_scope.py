"""Unknown metadata never clears a record's existing edges, extraction that was skipped is not
reported as failed, and the classify stage's taxonomy write leaves extractionStatus alone.

Taxonomy node keys and org scoping are the entity resolver's (``taxonomy_node_key``,
``tests/unit/modules/entity_resolution``); these tests run the legacy name lookup
(no resolution), which every rule here applies to as well.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import SemanticMetadata
from app.modules.transformers.graphdb import GraphDBTransformer

ORG = "org-1"
RECORD = "rec-1"


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _transformer(
    existing_edges: list[dict[str, Any]] | None = None,
) -> tuple[GraphDBTransformer, MagicMock]:
    graph_provider = AsyncMock()
    graph_provider.is_write_conflict = MagicMock(return_value=False)
    transformer = GraphDBTransformer(graph_provider, MagicMock())

    tx = MagicMock()
    tx.get_record_by_key = AsyncMock(return_value={"_key": RECORD, "orgId": ORG})
    tx.get_edges_from_node_with_target_name = AsyncMock(return_value=existing_edges or [])
    tx.batch_create_edges = AsyncMock()
    tx.batch_delete_edges = AsyncMock(return_value=0)
    tx.batch_upsert_nodes = AsyncMock()
    tx.get_edge = AsyncMock(return_value=None)
    tx.get_nodes_by_filters = AsyncMock(return_value=[])
    tx.batch_update_nodes = AsyncMock(return_value=True)

    async def in_transaction(fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return await fn(tx, *args, **kwargs)

    store = MagicMock()
    store.graph_provider = graph_provider
    store.transaction = MagicMock(return_value=_AsyncContext(tx))
    store.execute_idempotent_in_transaction = AsyncMock(side_effect=in_transaction)
    transformer.graph_data_store = store
    return transformer, tx


def _reconciled(tx: MagicMock) -> set[str]:
    return {call.args[1] for call in tx.get_edges_from_node_with_target_name.await_args_list}


def _created_in(tx: MagicMock) -> set[str]:
    return {call.args[1] for call in tx.batch_upsert_nodes.await_args_list}


@pytest.mark.asyncio
async def test_summary_only_metadata_keeps_existing_edges() -> None:
    existing = [{"_to": "topics/t-old", "name": "Old topic"}]
    transformer, tx = _transformer(existing)

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="only a summary", categories=[]), "vr-1"
    )

    assert _reconciled(tx) == set()
    tx.batch_delete_edges.assert_not_awaited()
    assert _created_in(tx) == set()
    status = tx.batch_update_nodes.await_args.args[0][0]
    assert status["extractionStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_known_empty_list_clears_that_fields_edges() -> None:
    existing = [{"_to": "topics/t-old", "name": "Old topic"}]
    transformer, tx = _transformer(existing)

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="s", categories=[], topics=[]), "vr-1"
    )

    assert _reconciled(tx) == {CollectionNames.BELONGS_TO_TOPIC.value}
    tx.batch_delete_edges.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_blank_category_creates_no_empty_named_node() -> None:
    transformer, tx = _transformer()

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="s", categories=["  "], topics=["a"]), "vr-1"
    )

    assert CollectionNames.CATEGORIES.value not in _created_in(tx)
    assert CollectionNames.BELONGS_TO_CATEGORY.value not in _reconciled(tx)


@pytest.mark.asyncio
async def test_subcategory_hierarchy_stops_at_the_first_missing_level() -> None:
    transformer, tx = _transformer()
    metadata = SemanticMetadata(
        categories=["Legal"], sub_category_level_1="Contract", sub_category_level_3="Orphan"
    )

    await transformer.save_metadata_to_db(RECORD, metadata, "vr-1")

    assert CollectionNames.SUBCATEGORIES1.value in _created_in(tx)
    assert CollectionNames.SUBCATEGORIES3.value not in _created_in(tx)
    hierarchy = [
        call for call in tx.batch_create_edges.await_args_list
        if call.args[1] == CollectionNames.INTER_CATEGORY_RELATIONS.value
    ]
    assert len(hierarchy) == 1


@pytest.mark.asyncio
async def test_skipped_extraction_is_recorded_as_skipped_not_failed() -> None:
    transformer, tx = _transformer()
    record = MagicMock(id=RECORD, virtual_record_id="vr-1", semantic_metadata=None)
    await transformer.apply(MagicMock(record=record, extraction_skip_reason="No LLM is configured"))
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "SKIPPED"
    assert status_doc["reason"] == "No LLM is configured"


@pytest.mark.asyncio
async def test_missing_metadata_without_a_skip_reason_is_failed() -> None:
    transformer, tx = _transformer()
    record = MagicMock(id=RECORD, virtual_record_id="vr-1", semantic_metadata=None)
    await transformer.apply(MagicMock(record=record, extraction_skip_reason=None))
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "FAILED"
    assert "reason" not in status_doc


@pytest.mark.asyncio
async def test_write_taxonomy_reconciles_edges_without_writing_status() -> None:
    transformer, tx = _transformer()
    touched = await transformer.write_taxonomy(RECORD, SemanticMetadata(topics=["VPN"], categories=[]))
    assert CollectionNames.BELONGS_TO_TOPIC.value in _reconciled(tx)
    assert [entity.name for entity in touched] == ["VPN"]
    # The stage owns extractionStatus and writes it by CAS on the content revision.
    tx.batch_update_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_metadata_to_db_still_marks_extraction_completed() -> None:
    transformer, tx = _transformer()
    await transformer.save_metadata_to_db(RECORD, SemanticMetadata(topics=["VPN"], categories=[]), "vr-1")
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "COMPLETED"
