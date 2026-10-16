"""Edge builds share the connectors service without taking it over (SC-1)."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph import edge_build_runner, edge_builder
from app.modules.code_graph.edge_builder import EdgeBuildResult, build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID


def _runner() -> edge_build_runner.CodeEdgeBuildRunner:
    graph = MagicMock()
    graph.has_nodes_by_filters = AsyncMock(return_value=False)
    graph.get_nodes_by_filters = AsyncMock(return_value=[])
    graph.upsert_sync_point = AsyncMock()
    redis = MagicMock()
    redis.eval = AsyncMock(return_value=1)
    return edge_build_runner.CodeEdgeBuildRunner(graph, redis, MagicMock())


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch) -> None:
    monkeypatch.setattr(edge_build_runner, "_process_slots", {})
    monkeypatch.setattr(edge_build_runner, "_org_slots", {})
    monkeypatch.setattr(edge_build_runner, "_org_users", {})
    monkeypatch.setattr(edge_build_runner, "_MAX_CONCURRENT_BUILDS", 2)
    monkeypatch.setattr(edge_build_runner, "_MAX_BUILDS_PER_ORG", 1)


async def _peak_concurrency(orgs: list[str]) -> int:
    running = peak = 0

    async def fake_build(**_kwargs) -> EdgeBuildResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return EdgeBuildResult()

    with patch.object(edge_build_runner, "build_code_graph_edges", fake_build):
        await asyncio.gather(*(
            _runner().run(org_id=org, connector_id="c", record_group_id=f"g{i}", lease=("l", "t"))
            for i, org in enumerate(orgs)
        ))
    return peak


@pytest.mark.asyncio
async def test_one_orgs_repos_build_one_at_a_time() -> None:
    assert await _peak_concurrency(["org-a"] * 4) == 1


@pytest.mark.asyncio
async def test_builds_across_orgs_stop_at_the_process_cap() -> None:
    assert await _peak_concurrency(["a", "b", "c", "d"]) == 2


@pytest.mark.asyncio
async def test_an_org_keeps_no_slot_once_its_builds_are_done() -> None:
    """O-12: one semaphore per org that ever built, kept for the process's life."""
    await _peak_concurrency(["org-a"] * 3 + ["org-b"])
    assert edge_build_runner._org_slots == {}
    assert edge_build_runner._org_users == {}


@pytest.mark.asyncio
async def test_the_event_loop_keeps_running_while_edges_resolve(graph, index_file) -> None:
    await index_file("recA", "src/a.py", b"def run():\n    return 1\n", "python")

    def slow_resolve(*args) -> list:
        time.sleep(0.3)  # stands in for CPU-bound resolution
        return []

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    with patch.object(edge_builder, "_resolve", slow_resolve):
        await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG_ID,
                                     record_group_id=GROUP_ID)
    task.cancel()
    assert ticks >= 5


@pytest.mark.asyncio
async def test_a_bare_name_with_too_many_definitions_is_not_guessed(graph, index_file) -> None:
    for i in range(edge_builder._MAX_HEURISTIC_CANDIDATES + 1):
        await index_file(f"rec{i}", f"src/m{i}/helpers.py", b"def tidy(x):\n    return x\n", "python")
    await index_file("caller", "src/caller.py", b"def go():\n    return tidy(1)\n", "python")

    result = await build_code_graph_edges(graph_provider=graph, facts_source=graph,
                                          org_id=ORG_ID, record_group_id=GROUP_ID)

    assert graph.edge_targets(RecordRelations.CALLS.value) == []
    assert result.ambiguous_skipped >= 1


class _StreamedRepo:
    """A repo whose blocks are made per page, so only the build's own heap is measured."""

    def __init__(self, files: int, per_file: int = 25) -> None:
        self.files, self.per_file = files, per_file

    def _block(self, i: int) -> dict:
        f, b = divmod(i, self.per_file)
        calls = [{"relation": "CALLS", "toName": f"fn{(f * 7 + k) % self.files}_{k}", "line": k}
                 for k in range(4)]
        return {"_key": f"b{i:09d}", "recordId": f"rec{f:07d}", "name": f"fn{f}_{b}",
                "kind": "function", "qualifiedName": f"function:fn{f}_{b}", "language": "python",
                "pendingEdges": calls}

    async def count_nodes_by_filters(self, **_) -> int:
        return self.files * self.per_file

    async def get_nodes_by_filters(self, **_) -> list[dict]:
        return [{"_key": f"rec{f:07d}"} for f in range(self.files)]

    async def get_file_paths_for_records(self, *, record_ids, **_) -> dict:
        return {r: MagicMock(file_path=f"pkg/m{r[3:]}.py", referenced_names=None) for r in record_ids}

    async def get_documents_paginated(self, *, limit, after_key, **_) -> list[dict]:
        start = int(after_key[1:]) + 1 if after_key else 0
        return [self._block(i) for i in range(start, min(start + limit, self.files * self.per_file))]


@pytest.mark.asyncio
async def test_a_builds_heap_stays_within_its_per_block_budget() -> None:
    """Blocks are indexed page by page and edges kept as tuples until written (SC-1):
    10k blocks and 40k edges peaked at 61 MB before, 39 MB after."""
    import tracemalloc

    tracemalloc.start()
    try:
        result = await build_code_graph_edges(graph_provider=_StreamedRepo(400), org_id=ORG_ID,
                                              record_group_id=GROUP_ID, dry_run=True)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result.edges_written == 39996
    assert peak / result.blocks < 5 * 1024, f"{peak / result.blocks:.0f} bytes per block"
