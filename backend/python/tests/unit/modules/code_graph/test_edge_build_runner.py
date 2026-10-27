"""How a build moves the repo's sync point: begin, then settle by outcome."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.code_graph import edge_build_trigger
from app.modules.code_graph.build_state import BuildStateStore, sync_point_key_for
from app.modules.code_graph.edge_build_runner import CodeEdgeBuildRunner
from app.modules.code_graph.edge_builder import EdgeBuildResult, EdgeBuildTooLargeError
from app.modules.code_graph.version import EDGE_RULES_VERSION
from tests.unit.modules.code_graph.sync_point_fakes import FakeSyncPoints

_BUILD = "app.modules.code_graph.edge_build_runner.build_code_graph_edges"


def _runner(point: dict | None = None, *, touched=("rec-1",), drained=True
            ) -> tuple[CodeEdgeBuildRunner, FakeSyncPoints]:
    graph = FakeSyncPoints()
    if point is not None:
        graph.points[sync_point_key_for("g")] = {
            "syncPointKey": sync_point_key_for("g"), "orgId": "org", **point,
        }
    graph.has_nodes_by_filters = AsyncMock(return_value=not drained)
    graph.get_nodes_updated_since = AsyncMock(return_value=[{"_key": k} for k in touched])
    redis = MagicMock()
    redis.eval = AsyncMock(return_value=1)
    return CodeEdgeBuildRunner(graph, redis, MagicMock()), graph


async def _run(runner: CodeEdgeBuildRunner, build) -> bool:
    with patch(_BUILD, build):
        return await runner.run(org_id="org", connector_id="conn", record_group_id="g",
                                lease=("lock", "token"))


def _point(graph: FakeSyncPoints) -> dict:
    return graph.points[sync_point_key_for("g")]


OWED = {"lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestId": "r1",
        "edgeBuildVersion": EDGE_RULES_VERSION}


@pytest.mark.asyncio
async def test_a_build_that_skipped_files_keeps_the_old_watermark() -> None:
    """Skipped files kept their old edges; advancing past them would leave
    their changes out of every later build (O-2)."""
    runner, graph = _runner(OWED)
    await _run(runner, AsyncMock(return_value=EdgeBuildResult(facts_missing=2)))
    assert _point(graph)["lastEdgeBuildAt"] == 1000


@pytest.mark.asyncio
async def test_a_complete_build_advances_the_watermark_and_clears_its_request() -> None:
    runner, graph = _runner(OWED)
    await _run(runner, AsyncMock(return_value=EdgeBuildResult(edges_written=7)))
    point = _point(graph)
    assert point["lastEdgeBuildAt"] > 1000
    assert point["edgeBuildPending"] is False
    assert point["edgeBuildStatus"] == "ready"
    assert point["edgeBuildLastEdgesWritten"] == 7


@pytest.mark.asyncio
async def test_a_request_made_during_the_build_stays_owed() -> None:
    runner, graph = _runner(OWED)

    async def build(**_) -> EdgeBuildResult:
        await BuildStateStore(graph).mark_requested("org", "conn", "g", 5000)
        return EdgeBuildResult()

    await _run(runner, build)
    assert _point(graph)["edgeBuildPending"] is True


@pytest.mark.asyncio
async def test_a_request_made_during_the_build_is_served_before_the_lease_is_released() -> None:
    """A busy repo's event is acked, so the build in hand serves what arrived meanwhile (SC-7)."""
    runner, graph = _runner(OWED)
    calls = []

    async def build(**_) -> EdgeBuildResult:
        calls.append(1)
        if len(calls) == 1:
            await BuildStateStore(graph).mark_requested("org", "conn", "g", 5000)
        return EdgeBuildResult()

    await _run(runner, build)
    assert len(calls) == 2
    assert _point(graph)["edgeBuildPending"] is False


@pytest.mark.asyncio
async def test_follow_up_builds_are_bounded() -> None:
    runner, graph = _runner(OWED)
    build = AsyncMock(return_value=EdgeBuildResult())

    async def always_requested(**kwargs) -> EdgeBuildResult:
        await BuildStateStore(graph).mark_requested("org", "conn", "g", 5000)
        return await build(**kwargs)

    assert await _run(runner, always_requested) is True, "still owed: the caller starts the next run"
    assert build.await_count == 4, "the first build plus three follow-ups"


@pytest.mark.asyncio
async def test_a_full_build_request_runs_a_full_build_and_clears_it() -> None:
    """A deleted file leaves no record for an incremental build to start from (R-3)."""
    runner, graph = _runner({**OWED, "edgeBuildFullRequestId": "full-1"}, touched=())
    build = AsyncMock(return_value=EdgeBuildResult())
    await _run(runner, build)
    assert build.await_args.kwargs["touched_record_ids"] is None
    assert _point(graph)["edgeBuildFullRequestId"] is None
    assert _point(graph)["lastFullEdgeBuildAt"] == _point(graph)["lastEdgeBuildAt"]


@pytest.mark.asyncio
async def test_a_full_build_that_skipped_files_keeps_its_request() -> None:
    runner, graph = _runner({**OWED, "edgeBuildFullRequestId": "full-1"})
    await _run(runner, AsyncMock(return_value=EdgeBuildResult(facts_missing=1)))
    assert _point(graph)["edgeBuildFullRequestId"] == "full-1"


@pytest.mark.asyncio
async def test_nothing_changed_settles_without_building() -> None:
    runner, graph = _runner(OWED, touched=())
    build = AsyncMock()
    await _run(runner, build)
    build.assert_not_awaited()
    assert _point(graph)["edgeBuildPending"] is False
    assert _point(graph)["lastEdgeBuildAt"] == 1000


@pytest.mark.asyncio
async def test_files_still_indexing_defer_the_build_and_keep_it_owed() -> None:
    runner, graph = _runner(OWED, drained=False)
    build = AsyncMock()
    await _run(runner, build)
    build.assert_not_awaited()
    assert _point(graph)["edgeBuildStatus"] == "waiting_on_indexing"
    assert _point(graph)["edgeBuildPending"] is True
    runner._log.info.assert_called()


@pytest.mark.asyncio
async def test_a_failed_build_is_counted_scheduled_and_reraised() -> None:
    runner, graph = _runner(OWED)
    with pytest.raises(RuntimeError, match="boom"):
        await _run(runner, AsyncMock(side_effect=RuntimeError("boom")))
    point = _point(graph)
    assert point["edgeBuildStatus"] == "retrying"
    assert point["edgeBuildAttempts"] == 1
    assert point["edgeBuildNextAttemptAt"] is not None
    assert point["edgeBuildPending"] is True
    assert point["edgeBuildLeaseToken"] is None


@pytest.mark.asyncio
async def test_a_repo_too_large_to_build_says_so_instead_of_failing() -> None:
    runner, graph = _runner(OWED)
    await _run(runner, AsyncMock(side_effect=EdgeBuildTooLargeError("400000 blocks")))
    assert _point(graph)["edgeBuildStatus"] == "too_large"
    assert _point(graph)["edgeBuildLastError"] == "400000 blocks"


@pytest.mark.asyncio
async def test_a_build_that_lost_its_lease_leaves_the_state_to_the_new_owner() -> None:
    runner, graph = _runner(OWED)

    async def build(**_) -> EdgeBuildResult:
        await BuildStateStore(graph).begin("org", "conn", "g", "someone-else")
        raise edge_build_trigger.LeaseLostError("lost")

    with pytest.raises(edge_build_trigger.LeaseLostError):
        await _run(runner, build)
    point = _point(graph)
    assert point["edgeBuildLeaseToken"] == "someone-else"
    assert point.get("edgeBuildAttempts", 0) == 0


@pytest.mark.asyncio
async def test_the_build_is_fenced_on_its_own_lease() -> None:
    runner, _ = _runner(OWED)
    runner._redis.get = AsyncMock(return_value="other-token")
    captured = {}

    async def build(**kwargs) -> EdgeBuildResult:
        captured["fence"] = kwargs["fence"]
        return EdgeBuildResult()

    await _run(runner, build)
    with pytest.raises(edge_build_trigger.LeaseLostError):
        await captured["fence"]()
    runner._redis.get.assert_awaited_with("lock")


@pytest.mark.asyncio
async def test_a_repo_built_by_another_code_graph_version_is_rebuilt_in_full() -> None:
    """Blocks and edges change shape between versions; an incremental build mixes both (O-10)."""
    runner, graph = _runner({**OWED, "edgeBuildVersion": "older"})
    build = AsyncMock(return_value=EdgeBuildResult())
    await _run(runner, build)
    assert build.await_args.kwargs["touched_record_ids"] is None
    assert _point(graph)["edgeBuildVersion"] == EDGE_RULES_VERSION



@pytest.mark.asyncio
async def test_a_cancelled_build_releases_its_lock_and_stays_owed() -> None:
    """Shutdown cancels a build mid-way: the lock is freed for the next replica and
    the request is still pending for the reconciler (T-3)."""
    import asyncio

    runner, graph = _runner(OWED)
    started = asyncio.Event()

    async def hang(**_) -> EdgeBuildResult:
        started.set()
        await asyncio.Event().wait()
        return EdgeBuildResult()

    with patch(_BUILD, hang):
        task = asyncio.create_task(runner.run(org_id="org", connector_id="conn",
                                              record_group_id="g", lease=("lock", "token")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    runner._redis.eval.assert_awaited()  # release_build_lock
    assert _point(graph)["edgeBuildPending"] is True
