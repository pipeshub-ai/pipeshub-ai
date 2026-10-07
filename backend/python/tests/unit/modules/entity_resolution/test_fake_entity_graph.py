"""FakeGraph keeps the provider contract for the writes the transformer makes."""
from __future__ import annotations

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.taxonomy import MAX_TAXONOMY_ALIASES
from tests.support.fake_entity_graph import FakeGraph

RECORDS = CollectionNames.RECORDS.value
TOPICS = CollectionNames.TOPICS.value


async def test_a_patch_is_merged_into_the_existing_record_and_node() -> None:
    graph = FakeGraph()
    graph.add_record("r1", "acme")
    graph.add_legacy_node(TOPICS, "t1", "Pricing")

    assert await graph.batch_update_nodes([{"id": "r1", "extractionStatus": "COMPLETED"}], RECORDS)
    assert await graph.batch_update_nodes([{"id": "t1", "aliases": ["pricing"]}], TOPICS)

    assert graph.records["r1"]["extractionStatus"] == "COMPLETED"
    assert graph.records["r1"]["orgId"] == "acme"
    assert graph.nodes[(TOPICS, "t1")] == {"name": "Pricing", "aliases": ["pricing"]}


async def test_a_missing_target_is_reported_and_the_rest_still_applied() -> None:
    # As on both providers: UPDATE / MATCH skip the missing node, and the
    # count of updated nodes falls short.
    graph = FakeGraph()
    graph.add_record("r1", "acme")

    assert not await graph.batch_update_nodes(
        [{"id": "r1", "extractionStatus": "COMPLETED"}, {"id": "gone", "extractionStatus": "COMPLETED"}],
        RECORDS,
    )
    assert graph.records["r1"]["extractionStatus"] == "COMPLETED"
    assert "gone" not in graph.records


async def test_the_record_write_marks_the_record_completed(make_transformer, fake_graph, metadata_factory) -> None:
    fake_graph.add_record("r1", "acme")
    await make_transformer().save_metadata_to_db("r1", metadata_factory(topics=["Pricing"]), "vr-1")
    assert fake_graph.records["r1"]["extractionStatus"] == "COMPLETED"
    assert fake_graph.records["r1"]["virtualRecordId"] == "vr-1"


async def test_aliases_are_capped_at_the_providers_limit_when_none_is_given() -> None:
    graph = FakeGraph()
    graph.nodes[(TOPICS, "t1")] = {"name": "Pricing", "orgId": "acme"}
    names = [f"pricing {i}" for i in range(25)]
    await graph.add_taxonomy_aliases(TOPICS, "t1", names, names, org_id="acme")
    assert len(graph.nodes[(TOPICS, "t1")]["aliases"]) == 25 <= MAX_TAXONOMY_ALIASES
