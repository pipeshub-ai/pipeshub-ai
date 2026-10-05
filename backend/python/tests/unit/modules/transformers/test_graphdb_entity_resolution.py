"""GraphDBTransformer with an EntityResolution: canonical nodes, aliases, edge provenance."""

from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import SemanticMetadata
from app.models.entities import EntityType
from app.modules.entity_resolution.keys import taxonomy_node_key
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


def _transformer(store, provider=None) -> GraphDBTransformer:
    """``provider`` receives the non-transactional canonical-node writes."""
    transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=MagicMock())
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=store)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    transformer.graph_data_store = MagicMock()
    transformer.graph_data_store.graph_provider = provider or AsyncMock()
    transformer.graph_data_store.transaction = MagicMock(return_value=ctx_mgr)
    transformer.graph_data_store.execute_idempotent_in_transaction = partial(
        GraphDataStore.execute_idempotent_in_transaction, transformer.graph_data_store,
    )
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
        provider = AsyncMock()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=True, decision="new", extracted_names=["  bug bash testing "])
        touched = await _transformer(store, provider).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        # Written before the record's transaction, not inside it.
        store.create_taxonomy_node_if_absent.assert_not_awaited()
        provider.create_taxonomy_node_if_absent.assert_awaited_once()
        collection, node = provider.create_taxonomy_node_if_absent.await_args.args
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
        provider = AsyncMock()
        touched = await _transformer(store, provider).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        provider.create_taxonomy_node_if_absent.assert_not_awaited()
        store.add_taxonomy_aliases.assert_not_awaited()
        provider.add_taxonomy_aliases.assert_awaited_once_with(
            TOPICS, "k-bug", ["Bug bash testing session"], ["bug bash testing session"],
            org_id="org-1",
        )
        (record,) = [t for t in touched if t.entity_type is EntityType.TOPIC]
        assert record.aliases == ["old", "Bug bash testing session"]

    async def test_existing_entity_without_new_alias_touches_nothing(self) -> None:
        store = _tx_store()
        provider = AsyncMock()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="exact", extracted_names=["BUG BASH TESTING"])
        await _transformer(store, provider).save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )
        provider.create_taxonomy_node_if_absent.assert_not_awaited()
        provider.add_taxonomy_aliases.assert_not_awaited()

    async def test_subcategory_entity_carries_level_and_hierarchy_edge(self) -> None:
        """The hierarchy edge is shared by every record with the chain, so it
        is written through the provider before the record's transaction
        (KG-32), never inside it."""
        store = _tx_store()
        provider = AsyncMock()
        order: list[str] = []
        provider.ensure_taxonomy_hierarchy_edge = AsyncMock(side_effect=lambda *a: order.append("edge"))
        cat = ResolvedEntity(kind=CATEGORY, key="k-cat", name="Legal", normalized="legal", is_new=True, decision="new", extracted_names=["Legal"])
        sub = ResolvedEntity(kind=SUBCATEGORY_1, key="k-sub", name="Contract", normalized="contract", is_new=True, decision="new", extracted_names=["Contract"])
        transformer = _transformer(store, provider)
        transformer.graph_data_store.transaction.side_effect = (
            lambda: (order.append("txn"), transformer.graph_data_store.transaction.return_value)[1]
        )
        touched = await transformer.save_metadata_to_db(
            "rec-1", _metadata(categories=["Legal"], sub_category_level_1="Contract"), "vr-1",
            resolution=_resolution(cat, sub),
        )
        (record,) = [t for t in touched if t.entity_type is EntityType.SUBCATEGORY]
        assert record.level == "1" and record.entity_id == "k-sub"
        provider.ensure_taxonomy_hierarchy_edge.assert_awaited_once_with(
            CollectionNames.SUBCATEGORIES1.value, "k-sub", "k-cat",
        )
        assert order == ["edge", "txn"]
        assert _created_edges(store, CollectionNames.INTER_CATEGORY_RELATIONS.value) == []

    async def test_name_missing_from_resolution_links_its_per_org_node(self) -> None:
        """The legacy lookup is by name alone across orgs; with a resolution a
        miss must still land on this org's node."""
        store = _tx_store()
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="exact", extracted_names=["Bug bash testing"])
        provider = AsyncMock()
        transformer = _transformer(store, provider)
        await transformer.save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing", "Unseen topic."]), "vr-1", resolution=_resolution(entity),
        )
        expected_key = taxonomy_node_key("org-1", TOPICS, "unseen topic")
        # Outside the transaction, for the same reason as every canonical node.
        store.create_taxonomy_node_if_absent.assert_not_awaited()
        provider.create_taxonomy_node_if_absent.assert_awaited_once()
        collection, node = provider.create_taxonomy_node_if_absent.await_args.args
        assert collection == TOPICS
        assert node["id"] == expected_key
        assert node["orgId"] == "org-1"
        assert node["name"] == "Unseen topic"
        assert node["normalizedName"] == "unseen topic"
        assert all(call.args[0] != TOPICS for call in store.get_nodes_by_filters.await_args_list)
        store.batch_upsert_nodes.assert_not_awaited()
        assert {e["to_id"] for e in _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value)} == {
            "k-bug", expected_key,
        }
        transformer.logger.warning.assert_called()

    async def test_without_a_resolution_the_legacy_lookup_still_runs(self) -> None:
        store = _tx_store()
        await _transformer(store).save_metadata_to_db(
            "rec-1", _metadata(topics=["Unseen topic"]), "vr-1", resolution=None,
        )
        store.get_nodes_by_filters.assert_any_await(TOPICS, {"name": "Unseen topic"}, raise_on_error=True)
        store.create_taxonomy_node_if_absent.assert_not_awaited()

    async def test_existing_edge_keeps_its_original_extracted_name(self) -> None:
        store = _tx_store()
        existing = [{"_to": f"{TOPICS}/k-bug", "name": "Bug bash testing", "extractedName": "original"}]
        store.get_edges_from_node_with_target_name = AsyncMock(
            side_effect=lambda record_from, edge_collection, **_kwargs: (
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
        store.get_nodes_by_filters.assert_awaited_with(TOPICS, {"name": "Bug bash testing"}, raise_on_error=True)
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


class TestCanonicalNodesBeforeTheTransaction:
    async def test_a_failed_node_write_fails_before_any_edge(self) -> None:
        """An edge to a node that was never created would be dangling."""
        store = _tx_store()
        provider = AsyncMock()
        provider.create_taxonomy_node_if_absent.side_effect = RuntimeError("graph down")
        transformer = _transformer(store, provider)
        entity = ResolvedEntity(kind=TOPIC, key="k-new", name="Fresh", normalized="fresh",
                                is_new=True, decision="new", extracted_names=["Fresh"])

        with pytest.raises(RuntimeError):
            await transformer.save_metadata_to_db(
                "rec-1", _metadata(topics=["Fresh"]), "vr-1", resolution=_resolution(entity),
            )

        transformer.graph_data_store.transaction.assert_not_called()
        store.batch_create_edges.assert_not_awaited()

    async def test_a_failed_alias_write_does_not_fail_the_enrichment(self) -> None:
        """An alias only saves a later model call; the record's taxonomy
        edges are what matter."""
        store = _tx_store()
        provider = AsyncMock()
        provider.add_taxonomy_aliases.side_effect = RuntimeError("write-write conflict")
        transformer = _transformer(store, provider)
        entity = ResolvedEntity(kind=TOPIC, key="k-bug", name="Bug bash testing", normalized="bug bash testing",
                                is_new=False, decision="merge", aliases=["Bug bash session"],
                                new_aliases=["Bug bash session"], extracted_names=["Bug bash session"])

        await transformer.save_metadata_to_db(
            "rec-1", _metadata(topics=["Bug bash testing"]), "vr-1", resolution=_resolution(entity),
        )

        (edge,) = _created_edges(store, CollectionNames.BELONGS_TO_TOPIC.value)
        assert edge["to_id"] == "k-bug"
        transformer.logger.warning.assert_called()

    async def test_nodes_are_written_before_the_transaction_opens(self) -> None:
        order: list[str] = []
        store = _tx_store()
        provider = AsyncMock()
        provider.create_taxonomy_node_if_absent.side_effect = lambda *a, **k: order.append("node")
        transformer = _transformer(store, provider)
        transformer.graph_data_store.transaction.side_effect = (
            lambda: (order.append("txn"), transformer.graph_data_store.transaction.return_value)[1]
        )
        entity = ResolvedEntity(kind=TOPIC, key="k-new", name="Fresh", normalized="fresh",
                                is_new=True, decision="new", extracted_names=["Fresh"])

        await transformer.save_metadata_to_db(
            "rec-1", _metadata(topics=["Fresh"]), "vr-1", resolution=_resolution(entity),
        )

        assert order == ["node", "txn"]


class TestPartlyLandedAttemptIsRerun:
    """KG-32 under Neo4j auto-commit: a write conflict after some edges
    already landed re-runs the whole write, which must not duplicate them."""

    async def test_the_rerun_leaves_one_edge_per_target(self, monkeypatch) -> None:
        from tests.support.fake_entity_graph import FakeGraph

        monkeypatch.setattr("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock())
        graph = FakeGraph()
        graph.records["rec-1"] = {"_key": "rec-1", "orgId": "org-1"}
        graph.departments["Engineering"] = "d-eng"
        transformer = GraphDBTransformer(graph_provider=MagicMock(), logger=MagicMock())

        class _Txn:
            async def __aenter__(self) -> FakeGraph:
                return graph

            async def __aexit__(self, *exc: object) -> bool:
                return False

        transformer.graph_data_store = MagicMock()
        transformer.graph_data_store.graph_provider = graph
        transformer.graph_data_store.transaction = MagicMock(side_effect=lambda: _Txn())
        transformer.graph_data_store.execute_idempotent_in_transaction = partial(
            GraphDataStore.execute_idempotent_in_transaction, transformer.graph_data_store,
        )
        status_writes = AsyncMock(side_effect=[RuntimeError("write conflict"), True])
        graph.batch_update_nodes = status_writes
        topic = ResolvedEntity(kind=TOPIC, key="k-t", name="Budget", normalized="budget", is_new=True,
                               decision="new", extracted_names=["Budget"])

        touched = await transformer.save_metadata_to_db(
            "rec-1", _metadata(topics=["Budget"], departments=["Engineering"]), "vr-1",
            resolution=_resolution(topic),
        )

        assert status_writes.await_count == 2
        assert len(graph.edges_from("rec-1", CollectionNames.BELONGS_TO_DEPARTMENT.value)) == 1
        assert len(graph.edges_from("rec-1", CollectionNames.BELONGS_TO_TOPIC.value)) == 1
        assert sorted(e.entity_id for e in touched) == ["d-eng", "k-t"]


async def test_a_name_missing_from_the_resolution_is_not_logged() -> None:
    """KG-19: the warning for a resolution miss names the collection, not the
    extracted text."""
    store = _tx_store()
    other = ResolvedEntity(kind=TOPIC, key="k-x", name="Other", normalized="other", is_new=False,
                           decision="exact", extracted_names=["Other"])
    transformer = _transformer(store)
    await transformer.save_metadata_to_db(
        "rec-1", _metadata(topics=["Secret codename"]), "vr-1", resolution=_resolution(other),
    )
    logger = transformer.logger
    logged = " ".join(
        str(a) for m in (logger.warning, logger.debug, logger.error, logger.info)
        for c in m.call_args_list for a in c.args
    )
    assert "codename" not in logged.casefold()
