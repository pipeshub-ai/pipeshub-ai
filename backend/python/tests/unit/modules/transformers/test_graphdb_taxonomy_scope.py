"""Taxonomy nodes are per org, created without write conflicts, and unknown
metadata never clears a record's existing edges."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import SemanticMetadata
from app.modules.transformers.graphdb import GraphDBTransformer, taxonomy_node_key

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
    existing_edges: list[dict] | None = None,
) -> tuple[GraphDBTransformer, AsyncMock, MagicMock]:
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value={"_key": RECORD, "orgId": ORG})
    transformer = GraphDBTransformer(graph_provider, MagicMock())

    tx = MagicMock()
    tx.get_edges_from_node_with_target_name = AsyncMock(return_value=existing_edges or [])
    tx.batch_create_edges = AsyncMock()
    tx.batch_delete_edges = AsyncMock(return_value=0)
    tx.get_edge = AsyncMock(return_value=None)
    tx.get_nodes_by_filters = AsyncMock(return_value=[{"_key": "dept-eng"}])
    tx.batch_update_nodes = AsyncMock(return_value=True)
    transformer.graph_data_store = MagicMock()
    transformer.graph_data_store.transaction = MagicMock(return_value=_AsyncContext(tx))
    return transformer, graph_provider, tx


def _reconciled(tx: MagicMock) -> set[str]:
    return {call.args[1] for call in tx.get_edges_from_node_with_target_name.await_args_list}


class TestTaxonomyNodeKey:
    def test_the_same_term_in_two_orgs_is_two_nodes(self) -> None:
        collection = CollectionNames.TOPICS.value
        assert taxonomy_node_key("o1", collection, "Security") != taxonomy_node_key("o2", collection, "Security")

    def test_case_and_spacing_do_not_split_a_term(self) -> None:
        collection = CollectionNames.TOPICS.value
        assert taxonomy_node_key(ORG, collection, "Machine  Learning") == taxonomy_node_key(
            ORG, collection, " machine learning "
        )

    def test_collections_do_not_share_keys(self) -> None:
        assert taxonomy_node_key(ORG, CollectionNames.TOPICS.value, "x") != taxonomy_node_key(
            ORG, CollectionNames.CATEGORIES.value, "x"
        )


@pytest.mark.asyncio
async def test_nodes_are_created_org_scoped_outside_the_transaction() -> None:
    transformer, graph_provider, tx = _transformer()
    metadata = SemanticMetadata(
        departments=["Engineering"],
        categories=["Security"],
        sub_category_level_1="Network",
        topics=["VPN"],
        languages=["English"],
        summary="s",
    )

    await transformer.save_metadata_to_db(RECORD, metadata, "vr-1")

    written = {
        call.args[1]: call.args[0] for call in graph_provider.ensure_nodes.await_args_list
    }
    category = written[CollectionNames.CATEGORIES.value][0]
    assert category["id"] == taxonomy_node_key(ORG, CollectionNames.CATEGORIES.value, "Security")
    assert category["orgId"] == ORG
    assert category["normalizedName"] == "security"
    assert written[CollectionNames.TOPICS.value][0]["id"] == taxonomy_node_key(
        ORG, CollectionNames.TOPICS.value, "VPN"
    )
    assert _reconciled(tx) == {
        CollectionNames.BELONGS_TO_DEPARTMENT.value,
        CollectionNames.BELONGS_TO_CATEGORY.value,
        CollectionNames.BELONGS_TO_LANGUAGE.value,
        CollectionNames.BELONGS_TO_TOPIC.value,
    }


@pytest.mark.asyncio
async def test_summary_only_metadata_keeps_existing_edges() -> None:
    existing = [{"_to": "topics/t-old", "name": "Old topic"}]
    transformer, graph_provider, tx = _transformer(existing)

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="only a summary", categories=[]), "vr-1"
    )

    assert _reconciled(tx) == set()
    tx.batch_delete_edges.assert_not_awaited()
    graph_provider.ensure_nodes.assert_not_awaited()
    status = tx.batch_update_nodes.await_args.args[0][0]
    assert status["extractionStatus"] == "COMPLETED"


@pytest.mark.asyncio
async def test_a_known_empty_list_clears_that_fields_edges() -> None:
    existing = [{"_to": "topics/t-old", "name": "Old topic"}]
    transformer, _, tx = _transformer(existing)

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="s", categories=[], topics=[]), "vr-1"
    )

    assert _reconciled(tx) == {CollectionNames.BELONGS_TO_TOPIC.value}
    tx.batch_delete_edges.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_blank_category_creates_no_empty_named_node() -> None:
    transformer, graph_provider, tx = _transformer()

    await transformer.save_metadata_to_db(
        RECORD, SemanticMetadata(summary="s", categories=["  "], topics=["a"]), "vr-1"
    )

    collections = {call.args[1] for call in graph_provider.ensure_nodes.await_args_list}
    assert CollectionNames.CATEGORIES.value not in collections
    assert CollectionNames.BELONGS_TO_CATEGORY.value not in _reconciled(tx)


@pytest.mark.asyncio
async def test_subcategory_hierarchy_stops_at_the_first_missing_level() -> None:
    transformer, graph_provider, tx = _transformer()
    metadata = SemanticMetadata(
        categories=["Legal"], sub_category_level_1="Contract", sub_category_level_3="Orphan"
    )

    await transformer.save_metadata_to_db(RECORD, metadata, "vr-1")

    collections = {call.args[1] for call in graph_provider.ensure_nodes.await_args_list}
    assert CollectionNames.SUBCATEGORIES1.value in collections
    assert CollectionNames.SUBCATEGORIES3.value not in collections
    hierarchy = [
        call for call in tx.batch_create_edges.await_args_list
        if call.args[1] == CollectionNames.INTER_CATEGORY_RELATIONS.value
    ]
    assert len(hierarchy) == 1


@pytest.mark.asyncio
async def test_skipped_extraction_is_recorded_as_skipped_not_failed() -> None:
    transformer, _, tx = _transformer()
    record = MagicMock(id=RECORD, virtual_record_id="vr-1", semantic_metadata=None)
    await transformer.apply(MagicMock(record=record, extraction_skip_reason="No LLM is configured"))
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "SKIPPED"
    assert status_doc["reason"] == "No LLM is configured"


@pytest.mark.asyncio
async def test_missing_metadata_without_a_skip_reason_is_failed() -> None:
    transformer, _, tx = _transformer()
    record = MagicMock(id=RECORD, virtual_record_id="vr-1", semantic_metadata=None)
    await transformer.apply(MagicMock(record=record, extraction_skip_reason=None))
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "FAILED"
    assert "reason" not in status_doc


@pytest.mark.asyncio
async def test_write_taxonomy_reconciles_edges_without_writing_status() -> None:
    transformer, _, tx = _transformer()
    await transformer.write_taxonomy(RECORD, SemanticMetadata(topics=["VPN"], categories=[]))
    assert CollectionNames.BELONGS_TO_TOPIC.value in _reconciled(tx)
    # The stage owns extractionStatus and writes it by CAS on the content revision.
    tx.batch_update_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_metadata_to_db_still_marks_extraction_completed() -> None:
    transformer, _, tx = _transformer()
    await transformer.save_metadata_to_db(RECORD, SemanticMetadata(topics=["VPN"], categories=[]), "vr-1")
    status_doc = tx.batch_update_nodes.await_args.args[0][0]
    assert status_doc["extractionStatus"] == "COMPLETED"
