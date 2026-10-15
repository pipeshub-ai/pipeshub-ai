"""How a finished build settles the repo's sync point."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.code_graph.edge_build_runner import CodeEdgeBuildRunner
from app.modules.code_graph.edge_builder import EdgeBuildResult


def _runner(sync_rows: list[dict]) -> tuple[CodeEdgeBuildRunner, MagicMock]:
    graph = MagicMock()
    graph.has_nodes_by_filters = AsyncMock(return_value=False)
    graph.get_nodes_by_filters = AsyncMock(return_value=sync_rows)
    graph.get_nodes_updated_since = AsyncMock(return_value=[{"_key": "rec-1"}])
    graph.upsert_sync_point = AsyncMock()
    redis = MagicMock()
    redis.eval = AsyncMock(return_value=1)
    return CodeEdgeBuildRunner(graph, redis, MagicMock()), graph


async def _run(runner: CodeEdgeBuildRunner, result: EdgeBuildResult) -> None:
    with patch("app.modules.code_graph.edge_build_runner.build_code_graph_edges",
               AsyncMock(return_value=result)):
        await runner.run(org_id="org", connector_id="conn", record_group_id="g",
                         lease=("lock", "token"))


def _settled(graph: MagicMock) -> dict:
    return graph.upsert_sync_point.await_args.kwargs["sync_point_data"]


@pytest.mark.asyncio
async def test_a_build_that_skipped_files_keeps_the_old_watermark() -> None:
    """Skipped files kept their old edges; advancing past them would leave
    their changes out of every later build (O-2)."""
    runner, graph = _runner([{"lastEdgeBuildAt": 1000, "edgeBuildPending": True,
                              "edgeBuildRequestId": "r1"}])
    await _run(runner, EdgeBuildResult(facts_missing=2))
    assert "lastEdgeBuildAt" not in _settled(graph)


@pytest.mark.asyncio
async def test_a_complete_build_advances_the_watermark_and_clears_its_request() -> None:
    runner, graph = _runner([{"lastEdgeBuildAt": 1000, "edgeBuildPending": True,
                              "edgeBuildRequestId": "r1"}])
    await _run(runner, EdgeBuildResult())
    data = _settled(graph)
    assert data["lastEdgeBuildAt"] > 1000
    assert data["edgeBuildPending"] is False


@pytest.mark.asyncio
async def test_a_request_made_during_the_build_stays_owed() -> None:
    runner, graph = _runner([{"lastEdgeBuildAt": 1000, "edgeBuildPending": True,
                              "edgeBuildRequestId": "r1"}])
    graph.get_nodes_by_filters.side_effect = [
        [{"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r1"}],
        [{"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r2"}],
    ]
    await _run(runner, EdgeBuildResult())
    assert _settled(graph)["edgeBuildPending"] is True


async def _run_capturing(runner: CodeEdgeBuildRunner, result: EdgeBuildResult) -> AsyncMock:
    build = AsyncMock(return_value=result)
    with patch("app.modules.code_graph.edge_build_runner.build_code_graph_edges", build):
        await runner.run(org_id="org", connector_id="conn", record_group_id="g",
                         lease=("lock", "token"))
    return build


@pytest.mark.asyncio
async def test_a_full_build_request_runs_a_full_build_and_clears_it() -> None:
    """A deleted file leaves no record for an incremental build to start from (R-3)."""
    row = {"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r1",
           "edgeBuildFullRequestId": "full-1"}
    runner, graph = _runner([row])
    build = await _run_capturing(runner, EdgeBuildResult())
    assert build.await_args.kwargs["touched_record_ids"] is None
    assert _settled(graph)["edgeBuildFullRequestId"] is None


@pytest.mark.asyncio
async def test_a_full_build_request_runs_even_when_no_record_changed() -> None:
    runner, graph = _runner([{"lastEdgeBuildAt": 1000, "edgeBuildPending": True,
                              "edgeBuildRequestId": "r1", "edgeBuildFullRequestId": "full-1"}])
    graph.get_nodes_updated_since = AsyncMock(return_value=[])
    build = await _run_capturing(runner, EdgeBuildResult())
    assert build.await_count == 1


@pytest.mark.asyncio
async def test_a_full_build_asked_for_during_the_build_stays_owed() -> None:
    runner, graph = _runner([])
    graph.get_nodes_by_filters.side_effect = [
        [{"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r1",
          "edgeBuildFullRequestId": "full-1"}],
        [{"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r2",
          "edgeBuildFullRequestId": "full-2"}],
    ]
    await _run_capturing(runner, EdgeBuildResult())
    assert "edgeBuildFullRequestId" not in _settled(graph)
    assert _settled(graph)["edgeBuildPending"] is True


@pytest.mark.asyncio
async def test_a_full_build_that_skipped_files_keeps_its_request() -> None:
    runner, graph = _runner([{"lastEdgeBuildAt": 1000, "edgeBuildPending": True,
                              "edgeBuildRequestId": "r1", "edgeBuildFullRequestId": "full-1"}])
    await _run_capturing(runner, EdgeBuildResult(facts_missing=1))
    assert "edgeBuildFullRequestId" not in _settled(graph)
