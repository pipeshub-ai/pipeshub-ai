import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.connectors.core.sync.task_manager import SyncTaskManager
from app.connectors.services.code_graph_event_service import CodeGraphEventService
from app.modules.code_graph.edge_build_runner import CodeEdgeBuildRunner
from app.modules.code_graph.edge_builder import EdgeBuildResult
from tests.unit.modules.code_graph.sync_point_fakes import FakeSyncPoints


def _redis(*, lock_results=True) -> MagicMock:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=lock_results)
    redis.eval = AsyncMock(return_value=1)
    return redis


def _graph_provider() -> FakeSyncPoints:
    provider = FakeSyncPoints()
    provider.has_nodes_by_filters = AsyncMock(return_value=False)
    provider.get_nodes_updated_since = AsyncMock(return_value=[])
    return provider


@pytest.mark.asyncio
async def test_runner_builds_settles_and_releases_lock() -> None:
    graph_provider = _graph_provider()
    redis = _redis()
    logger = MagicMock()
    result = EdgeBuildResult(edges_written=3)
    runner = CodeEdgeBuildRunner(graph_provider, redis, logger)

    with patch(
        "app.modules.code_graph.edge_build_runner.build_code_graph_edges",
        AsyncMock(return_value=result),
    ) as build:
        await runner.run(
            org_id="org-1",
            connector_id="connector-1",
            record_group_id="repo-1",
        )

    kwargs = build.await_args.kwargs
    assert kwargs["graph_provider"] is graph_provider
    assert (kwargs["org_id"], kwargs["record_group_id"]) == ("org-1", "repo-1")
    assert kwargs["touched_record_ids"] is None
    sync_point = graph_provider.points["repo-1/code-edge-build"]
    assert sync_point["edgeBuildPending"] is False
    assert sync_point["edgeBuildStatus"] == "ready"
    assert "lastEdgeBuildAt" in sync_point
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_failure_stays_owed_with_a_retry_and_releases_lock() -> None:
    graph_provider = _graph_provider()
    redis = _redis()
    runner = CodeEdgeBuildRunner(graph_provider, redis, MagicMock())

    with (
        patch(
            "app.modules.code_graph.edge_build_runner.build_code_graph_edges",
            AsyncMock(side_effect=RuntimeError("build failed")),
        ),
        pytest.raises(RuntimeError, match="build failed"),
    ):
        await runner.run(
            org_id="org-1",
            connector_id="connector-1",
            record_group_id="repo-1",
        )

    sync_point = graph_provider.points["repo-1/code-edge-build"]
    assert sync_point["edgeBuildPending"] is True
    assert sync_point["edgeBuildStatus"] == "retrying"
    assert "lastEdgeBuildAt" not in sync_point
    redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_acks_then_runs_build_in_background() -> None:
    runner = MagicMock()
    runner.acquire = AsyncMock(return_value=("lock-key", "lock-token"))
    started = asyncio.Event()
    finish = asyncio.Event()

    async def run(**_kwargs) -> None:
        started.set()
        await finish.wait()

    runner.run = run
    manager = SyncTaskManager(label="Code edge build test")
    service = CodeGraphEventService(MagicMock(), runner, manager)

    handled = await service.process_event(
        EventTypes.BUILD_CODE_EDGES.value,
        {
            "orgId": "org-1",
            "connectorId": "connector-1",
            "recordGroupId": "repo-1",
        },
    )

    assert handled is True
    await started.wait()
    assert manager.is_running("org-1:repo-1")
    finish.set()
    await manager.cancel_all()


@pytest.mark.asyncio
async def test_lock_contention_is_acked_without_starting_second_build() -> None:
    runner = MagicMock()
    runner.acquire = AsyncMock(
        side_effect=[("lock-key", "lock-token"), None]
    )
    finish = asyncio.Event()

    async def run(**_kwargs) -> None:
        await finish.wait()

    runner.run = AsyncMock(side_effect=run)
    manager = SyncTaskManager(label="Code edge build test")
    service = CodeGraphEventService(MagicMock(), runner, manager)
    payload = {
        "orgId": "org-1",
        "connectorId": "connector-1",
        "recordGroupId": "repo-1",
    }

    assert await service.process_event(
        EventTypes.BUILD_CODE_EDGES.value,
        payload,
    )
    # The request is marked pending before publish; the running build serves it.
    assert await service.process_event(
        EventTypes.BUILD_CODE_EDGES.value,
        payload,
    )

    assert runner.run.call_count == 1
    finish.set()
    await manager.cancel_all()


@pytest.mark.asyncio
async def test_malformed_event_is_acked_without_acquiring_lock() -> None:
    runner = MagicMock()
    runner.acquire = AsyncMock()
    service = CodeGraphEventService(
        MagicMock(),
        runner,
        SyncTaskManager(label="Code edge build test"),
    )

    handled = await service.process_event(
        EventTypes.BUILD_CODE_EDGES.value,
        {"orgId": "org-1"},
    )

    assert handled is True
    runner.acquire.assert_not_awaited()
