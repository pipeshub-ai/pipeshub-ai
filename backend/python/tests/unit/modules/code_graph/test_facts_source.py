"""Full facts come from the blob; the nodes carry only the residue.

`facts_from_blob_record` has to key facts exactly as block projection keyed the
nodes, and the builder has to produce the same graph from residue + blob as it
did from full facts on every node.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.block_projection import RESIDENT_RELATIONS
from app.modules.code_graph.edge_builder import build_code_graph_edges
from app.modules.code_graph.facts_source import (
    BlobCodeFactsSource,
    facts_from_blob_record,
)
from app.modules.code_graph.version import PARSER_VERSION

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value

A = b"from .b import foo\nfrom .c import bar\n\nclass Svc(Base):\n    def run(self):\n        return foo(1) + bar(2)\n"
B = b"def foo(x):\n    return x\n"
C = b"def bar(x):\n    return x\n"


def _edges(graph) -> list[tuple]:
    return sorted((e["_from"], e["_to"], e["relationshipType"]) for e in graph.code_edges())


async def _index_all(index_file) -> None:
    await index_file("recA", "src/a.py", A, "python")
    await index_file("recB", "src/b.py", B, "python")
    await index_file("recC", "src/c.py", C, "python")


def _put_full_facts_on_nodes(graph) -> None:
    """Shape a node the way the projection wrote it before the residue change."""
    for record_id, blob in graph.blobs.items():
        facts = facts_from_blob_record(record_id, blob)
        for key, doc in graph.blocks.items():
            if doc["recordId"] != record_id:
                continue
            doc["pendingEdges"] = facts.pending_edges.get(key) or None
            doc["referencedNames"] = None
        summary = next(k for k, d in graph.blocks.items()
                       if d["recordId"] == record_id and d.get("qualifiedName") is None)
        graph.blocks[summary]["typeTable"] = facts.type_table or None


@pytest.mark.asyncio
async def test_blob_facts_key_to_the_projected_nodes(graph, index_file) -> None:
    await _index_all(index_file)
    facts = facts_from_blob_record("recA", graph.blobs["recA"])

    assert set(facts.pending_edges) <= set(graph.blocks)
    projected = {fact["relation"] for doc in graph.blocks.values()
                 if doc["recordId"] == "recA" for fact in (doc.get("pendingEdges") or [])}
    stored = {fact["relation"] for facts_ in facts.pending_edges.values() for fact in facts_}
    assert projected <= RESIDENT_RELATIONS
    assert CALLS in stored and projected < stored


@pytest.mark.asyncio
async def test_residue_plus_blob_matches_full_facts_on_nodes(graph, index_file) -> None:
    await _index_all(index_file)
    with_blob = await build_code_graph_edges(
        graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
    )
    expected = _edges(graph)
    assert any(rel == CALLS for _, _, rel in expected)

    graph.edges.clear()
    _put_full_facts_on_nodes(graph)
    legacy = await build_code_graph_edges(
        graph_provider=graph, facts_source=None, org_id=ORG_ID, record_group_id=GROUP_ID,
    )
    assert _edges(graph) == expected
    assert legacy.edges_by_type == with_blob.edges_by_type
    assert with_blob.facts_loaded == 3 and legacy.facts_loaded == 0


@pytest.mark.asyncio
async def test_legacy_nodes_are_selected_incrementally_without_a_name_list(graph, index_file) -> None:
    """A repo indexed before the residue change has full facts and no
    `referencedNames`; the touched-name scan must still find its dependents."""
    await index_file("recA", "src/a.py", A, "python")
    await index_file("recC", "src/c.py", C, "python")
    _put_full_facts_on_nodes(graph)
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID)
    assert "function:foo" not in graph.edge_targets(CALLS)

    await index_file("recB", "src/b.py", B, "python")
    result = await build_code_graph_edges(
        graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
        touched_record_ids={"recB"},
    )
    assert result.files_reresolved == 2
    assert "function:foo" in graph.edge_targets(CALLS)


@pytest.mark.asyncio
async def test_missing_blob_keeps_heritage_and_drops_calls(graph, index_file) -> None:
    await _index_all(index_file)
    del graph.blobs["recA"]
    result = await build_code_graph_edges(
        graph_provider=graph, facts_source=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
    )
    assert result.facts_missing == 1 and result.facts_loaded == 2
    assert graph.edge_targets(CALLS) == []
    # The residue still carries the heritage fact, so the class is known to
    # extend something even though its calls could not be resolved.
    svc = next(d for d in graph.blocks.values() if d.get("qualifiedName") == "class:Svc")
    assert [f["relation"] for f in svc["pendingEdges"]] == [RecordRelations.INHERITS.value]


@pytest.mark.asyncio
async def test_a_blob_says_which_parser_version_wrote_its_facts(graph, index_file) -> None:
    await index_file("recB", "src/b.py", B, "python")
    stored = graph.blobs["recB"]
    assert stored["block_containers"]["code_facts_version"] == PARSER_VERSION

    stored["block_containers"]["code_facts_version"] = "2099.01.01"
    assert facts_from_blob_record("recB", stored).facts_version == "2099.01.01"


@pytest.mark.asyncio
async def test_a_blob_stored_before_the_stamp_loads_as_the_current_version(graph, index_file) -> None:
    await index_file("recB", "src/b.py", B, "python")
    stamped = facts_from_blob_record("recB", graph.blobs["recB"])
    del graph.blobs["recB"]["block_containers"]["code_facts_version"]
    old = facts_from_blob_record("recB", graph.blobs["recB"])
    assert old.recognised and old.facts_version == PARSER_VERSION
    assert old.pending_edges == stamped.pending_edges


class TestBlobCodeFactsSource:
    def _source(self, records: dict[str, dict | None], virtual_ids: dict[str, str]) -> tuple[BlobCodeFactsSource, MagicMock]:
        graph_provider = MagicMock()
        graph_provider.get_virtual_record_ids_for_record_ids = AsyncMock(return_value=virtual_ids)
        blob = MagicMock()
        blob.get_document_ids_by_virtual_record_ids = AsyncMock(
            return_value={v: {"record_doc_id": f"doc-{v}"} for v in virtual_ids.values()}
        )

        async def fetch(virtual_id, org_id, lookup_result=None) -> dict | None:
            assert lookup_result == {"record_doc_id": f"doc-{virtual_id}"}
            value = records[virtual_id]
            if isinstance(value, Exception):
                raise value
            return value

        blob.get_record_from_storage = AsyncMock(side_effect=fetch)
        return BlobCodeFactsSource(blob, graph_provider, MagicMock()), blob

    @pytest.mark.asyncio
    async def test_loads_by_virtual_record_id_with_one_mapping_query(self) -> None:
        record = {"block_containers": {"blocks": [
            {"code_metadata": {"qualified_name": "function:run",
                               "pending_edges": [{"relation": CALLS, "toName": "foo"}]}},
        ]}}
        source, blob = self._source({"v1": record}, {"r1": "v1"})

        loaded = await source.load(ORG_ID, {"r1"})

        assert list(loaded) == ["r1"]
        assert sum(len(f) for f in loaded["r1"].pending_edges.values()) == 1
        blob.get_document_ids_by_virtual_record_ids.assert_awaited_once_with(["v1"])

    @pytest.mark.asyncio
    async def test_unreadable_records_are_left_out_not_raised(self) -> None:
        source, _ = self._source(
            {"v1": None, "v2": RuntimeError("storage down")},
            {"r1": "v1", "r2": "v2"},  # r3 has no virtual id at all
        )
        assert await source.load(ORG_ID, {"r1", "r2", "r3"}) == {}

    @pytest.mark.asyncio
    async def test_empty_request_touches_nothing(self) -> None:
        source, blob = self._source({}, {})
        assert await source.load(ORG_ID, set()) == {}
        blob.get_document_ids_by_virtual_record_ids.assert_not_called()


def test_facts_from_blob_record_tolerates_odd_shapes() -> None:
    assert facts_from_blob_record("r", {}).pending_edges == {}
    assert facts_from_blob_record("r", {"block_containers": "junk"}).pending_edges == {}
    record = {"block_containers": {
        "block_groups": [{"code_metadata": {"pending_edges": [{"relation": CALLS}]}}],  # no name: skipped
        "blocks": [{"code_metadata": {"type_table": {"x": "Foo"}}}, "not a block"],
    }}
    facts = facts_from_blob_record("r", record)
    assert facts.pending_edges == {} and facts.type_table == {"x": "Foo"}
