"""A build that cannot trust what it read keeps the edges it has (R-6, O-2).

Storage reads used to return empty on failure and a missing or pre-metadata
blob read as "no facts", so a build deleted a file's call and import edges,
wrote nothing back, and reported success.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import (
    EdgeBuildAbortedError,
    EdgeBuildResult,
    build_code_graph_edges,
)

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value

A = b"from .b import foo\n\n\ndef run():\n    return foo(1)\n"
B = b"def foo(x):\n    return x\n"


def _calls(graph) -> list[str]:
    return graph.edge_targets(CALLS)


async def _build(graph) -> EdgeBuildResult:
    return await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                        record_group_id=GROUP_ID)


async def _good_build(graph, index_file) -> None:
    await index_file("recA", "src/a.py", A, "python")
    await index_file("recB", "src/b.py", B, "python")
    await _build(graph)
    assert _calls(graph) == ["function:foo"]


@pytest.mark.asyncio
async def test_a_file_whose_facts_cannot_be_read_keeps_its_edges(graph, index_file) -> None:
    await _good_build(graph, index_file)
    del graph.blobs["recA"]

    result = await _build(graph)

    assert result.facts_missing == 1
    assert _calls(graph) == ["function:foo"]


@pytest.mark.asyncio
async def test_a_blob_from_before_code_metadata_is_unknown_not_empty(graph, index_file) -> None:
    await _good_build(graph, index_file)
    graph.blobs["recA"] = {"block_containers": {"blocks": [{"data": {"text": "x"}}],
                                                "block_groups": []}}

    result = await _build(graph)

    assert result.facts_missing == 1
    assert _calls(graph) == ["function:foo"]


@pytest.mark.asyncio
async def test_widespread_missing_facts_abort_before_any_edge_changes(graph, index_file) -> None:
    for i in range(6):
        await index_file(f"rec{i}", f"src/m{i}.py", b"from .b import foo\n\n\ndef run():\n    return foo(1)\n", "python")
    await index_file("recB", "src/b.py", B, "python")
    await _build(graph)
    before = sorted((e["_from"], e["_to"]) for e in graph.code_edges())
    for i in range(6):
        del graph.blobs[f"rec{i}"]

    with pytest.raises(EdgeBuildAbortedError):
        await _build(graph)
    assert sorted((e["_from"], e["_to"]) for e in graph.code_edges()) == before


@pytest.mark.asyncio
async def test_a_failed_block_scan_stops_the_build_with_the_graph_untouched(graph, index_file) -> None:
    await _good_build(graph, index_file)

    async def broken(*args, raise_on_error=False, **kwargs) -> list:
        if raise_on_error:
            raise RuntimeError("graph unavailable")
        return []
    graph.get_documents_paginated = broken

    with pytest.raises(RuntimeError, match="graph unavailable"):
        await _build(graph)
    assert _calls(graph) == ["function:foo"]


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_previous_edges(graph, index_file) -> None:
    """Edges were deleted before the new ones were written, so readers saw an
    empty graph during every build, and after a failed write until the next (R-8)."""
    await _good_build(graph, index_file)

    async def _fail(edges, transaction=None) -> bool:
        raise ConnectionError("graph unavailable")
    graph.batch_upsert_node_relations = _fail

    with pytest.raises(ConnectionError):
        await _build(graph)
    assert _calls(graph) == ["function:foo"]


@pytest.mark.asyncio
async def test_a_rebuild_drops_only_the_edges_it_no_longer_finds(graph, index_file) -> None:
    await _good_build(graph, index_file)
    await index_file("recA", "src/a.py", b"from .b import foo\n\n\ndef run():\n    return 1\n", "python")

    result = await _build(graph)

    assert _calls(graph) == []
    assert result.edges_removed == 1
    assert len({e["buildId"] for e in graph.code_edges()}) == 1


@pytest.mark.asyncio
async def test_touched_records_without_blocks_do_not_abort_the_build(graph, index_file) -> None:
    """B3: a sync touches docs and unindexed tests too; they have no facts by design."""
    await index_file("recA", "src/a.py", A, "python")
    await index_file("recB", "src/b.py", B, "python")
    docs = {f"doc{i}" for i in range(20)}  # records the sync touched that never get blocks
    result = await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                          record_group_id=GROUP_ID, touched_record_ids={"recA", "recB", *docs})
    assert _calls(graph) == ["function:foo"]
    assert result.facts_missing == 0
