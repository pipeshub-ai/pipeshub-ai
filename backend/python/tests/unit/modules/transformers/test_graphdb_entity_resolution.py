"""GraphDBTransformer with an EntityResolution: canonical nodes, aliases, edge provenance."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import SemanticMetadata
from app.models.entities import EntityType
from app.modules.entity_resolution.models import (
    CATEGORY,
    SUBCATEGORY_1,
    TOPIC,
    EntityResolution,
    ResolutionMode,
    ResolvedEntity,
)
from app.modules.transformers.graphdb import GraphDBTransformer

TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
SUB1 = CollectionNames.SUBCATEGORIES1.value


def _tx_store() -> AsyncMock:
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1", "orgId": "org-1", "connectorId": "c1", "recordGroupId": "g1"})
    store.get_nodes_by_filters = AsyncMock(return_value=[])
    store.get_edge = AsyncMock(return_value=None)
    store.get_edges_from_node_with_target_name = AsyncMock(return_value=[])
    store.batch_upsert_nodes = AsyncMock()
    store.batch_update_nodes = AsyncMock(return_value=True)
    store.batch_create_edges = AsyncMock()
    store.batch_delete_edges = AsyncMock(return_value=0)
    store.create_taxonomy_node_if_absent = AsyncMock()
    store.add_taxonomy_aliases = AsyncMock()
    return store


def _transformer(store) -> GraphDBTransformer:
    transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=MagicMock())
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=store)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    transformer.graph_data_store = MagicMock()
    transformer.graph_data_store.transaction = MagicMock(return_value=ctx_mgr)
    return transformer


def _metadata(**kwargs) -> SemanticMetadata:
    base = {"categories": [], "topics": [], "languages": [], "departments": []}
    base.update(kwargs)
    return SemanticMetadata(**base)


def _resolution(*entities) -> EntityResolution:
    resolution = EntityResolution(org_id="org-1", mode=ResolutionMode.APPLY)
    for entity in entities:
        resolution.add(entity)
    return resolution


def _created_edges(store, collection) -> list:
    return [
        edge
        for call in store.batch_create_edges.await_args_list
        if call.args[1] == collection
        for edge in call.args[0]
    ]


class TestWithResolution:
    async def test_new_entity_is_created_idempotently_with_canonical_fields(self) -> None:
        store = _tx_store()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=True, decision="new", extracted_names=["  bug bash testing "])
        touched = await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        store.create_taxonomy_node_if_absent.assert_awaited_once()
        collection, node = store.create_taxonomy_node_if_absent.await_args.args
        assert collection == TOPICS
        assert node["id"] == "k-bug" and node["name"] == "Bug bash testing"
        assert node["normalizedName"] == "bug bash testing" and node["orgId"] == "org-1"
        assert node["createdAtTimestamp"] > 0
        store.get_nodes_by_filters.assert_not_awaited()
        store.batch_upsert_nodes.assert_not_awaited()
        (edge,) = _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value)
        assert edge["to_id"] == "k-bug" and edge["extractedName"] == "  bug bash testing "
        (record,) = [t for t in touched if t.entity_type is EntityType.TOPIC]
        assert record.entity_id == "k-bug" and record.aliases == [] and record.level is None

    async def test_existing_entity_with_new_alias_only_unions_aliases(self) -> None:
        store = _tx_store()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="merge", aliases=["old", "Bug bash testing session"],
                                new_aliases=["Bug bash testing session"], extracted_names=["Bug bash testing session"])
        touched = await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        store.create_taxonomy_node_if_absent.assert_not_awaited()
        store.add_taxonomy_aliases.assert_awaited_once_with(
            TOPICS, "k-bug", ["Bug bash testing session"], ["bug bash testing session"]
        )
        (record,) = [t for t in touched if t.entity_type is EntityType.TOPIC]
        assert record.aliases == ["old", "Bug bash testing session"]

    async def test_existing_entity_without_new_alias_touches_nothing(self) -> None:
        store = _tx_store()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="exact", extracted_names=["BUG BASH TESTING"])
        await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        store.create_taxonomy_node_if_absent.assert_not_awaited()
        store.add_taxonomy_aliases.assert_not_awaited()

    async def test_subcategory_entity_carries_level_and_hierarchy_edge(self) -> None:
        store = _tx_store()
        cat = ResolvedEntity(kind=CATEGORY, key="k-cat", name="Legal", normalized="legal", is_new=True, decision="new", extracted_names=["Legal"])
        sub = ResolvedEntity(kind=SUBCATEGORY_1, key="k-sub", name="Contract", normalized="contract", is_new=True, decision="new", extracted_names=["Contract"])
        touched = await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(categories=["Legal"], sub_category_level_1="Contract"), "vr-1",
            resolution=_resolution(cat, sub),
        )
        (record,) = [t for t in touched if t.entity_type is EntityType.SUBCATEGORY]
        assert record.level == "1" and record.entity_id == "k-sub"
        (hierarchy,) = _created_edges(store, CollectionNames.INTER_CATEGORY_RELATIONS.value)
        assert hierarchy["from_id"] == "k-sub" and hierarchy["to_id"] == "k-cat"

    async def test_name_missing_from_resolution_falls_back_to_legacy_lookup(self) -> None:
        store = _tx_store()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="exact", extracted_names=["Bug bash testing"])
        await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing", "Unseen topic"]), "vr-1", resolution=_resolution(entity),
        )
        store.get_nodes_by_filters.assert_awaited_once_with(TOPICS, {"name": "Unseen topic"})
        store.batch_upsert_nodes.assert_awaited_once()

    async def test_existing_edge_keeps_its_original_extracted_name(self) -> None:
        store = _tx_store()
        existing = [{"_to": f"{TOPICS}/k-bug", "name": "Bug bash testing", "extractedName": "original"}]
        store.get_edges_from_node_with_target_name = AsyncMock(
            side_effect=lambda record_from, edge_collection: (
                existing if edge_collection == CollectionNames.BELONGS_TO_TOPIC.value else []
            )
        )
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="exact", extracted_names=["Bug Bash Testing"])
        await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        assert _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value) == []
        store.batch_delete_edges.assert_not_awaited()


class TestWithoutResolution:
    async def test_legacy_path_unchanged_and_edges_have_no_extracted_name(self) -> None:
        store = _tx_store()
        await _transformer(store).save_metadata_to_db("rec-1", _metadata(topics=["Bug bash testing"]), "vr-1")
        store.get_nodes_by_filters.assert_awaited_with(TOPICS, {"name": "Bug bash testing"})
        store.create_taxonomy_node_if_absent.assert_not_awaited()
        (edge,) = _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value)
        assert "extractedName" not in edge

    async def test_empty_category_is_skipped_instead_of_creating_a_blank_node(self) -> None:
        store = _tx_store()
        transformer = _transformer(store)
        await transformer.save_metadata_to_db(
            "rec-1", _metadata(categories=[""], sub_category_level_1="Contract"), "vr-1",
        )
        assert not any(call.args[1].get("name") == "" for call in store.get_nodes_by_filters.await_args_list)
        assert _created_edges(store, CollectionNames.BELONGS_TO_CATEGORY.value) == []
        transformer.logger.warning.assert_called()

    async def test_apply_ignores_a_non_resolution_context_attribute(self) -> None:
        store = _tx_store()
        transformer = _transformer(store)
        transformer.save_metadata_to_db = AsyncMock(return_value=[])
        ctx = MagicMock()
        ctx.record.semantic_metadata = _metadata(topics=["x y"])
        ctx.record.virtual_record_id = "vr-1"
        ctx.record.id = "rec-1"
        ctx.record.is_vlm_ocr_processed = False
        await transformer.apply(ctx)
        assert transformer.save_metadata_to_db.await_args.kwargs["resolution"] is None

    async def test_apply_forwards_a_real_resolution(self) -> None:
        store = _tx_store()
        transformer = _transformer(store)
        transformer.save_metadata_to_db = AsyncMock(return_value=[])
        resolution = _resolution()
        ctx = MagicMock()
        ctx.record.semantic_metadata = _metadata(topics=["x y"])
        ctx.record.virtual_record_id = "vr-1"
        ctx.record.id = "rec-1"
        ctx.record.is_vlm_ocr_processed = False
        ctx.entity_resolution = resolution
        await transformer.apply(ctx)
        assert transformer.save_metadata_to_db.await_args.kwargs["resolution"] is resolution


@pytest.mark.parametrize("extracted", [None, {}])
async def test_reconcile_edges_without_extracted_names_writes_plain_edges(extracted) -> None:
    store = _tx_store()
    transformer = _transformer(store)
    await transformer._reconcile_edges(
        store, "rec-1", "records/rec-1", CollectionNames.BELONGS_TO_TOPIC.value,
        {f"{TOPICS}/k1": "One"}, "topic", extracted_names=extracted,
    )
    (edge,) = _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value)
    assert "extractedName" not in edge
