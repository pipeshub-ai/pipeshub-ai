"""Unit tests for app.modules.code_graph.edge_build_reconciler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.modules.code_graph import edge_build_reconciler, edge_build_trigger

NOW_MS = 10_000_000
STALE_MS = NOW_MS - 120_000
FRESH_MS = NOW_MS - 10_000


def _row(record_group_id: str, requested_at: int | None, org_id: str = "org-1") -> dict:
    return {
        "orgId": org_id,
        "connectorId": "connector-1",
        "syncPointKey": edge_build_trigger.sync_point_key_for(record_group_id),
        "lastEdgeBuildAt": None,
        "edgeBuildPending": True,
        "edgeBuildRequestedAt": requested_at,
    }


def _graph_provider(rows: list[dict]) -> MagicMock:
    provider = MagicMock()
    provider.get_nodes_by_filters = AsyncMock(return_value=rows)
    return provider


def _event_service(results=True) -> MagicMock:
    service = MagicMock()
    service.process_event = AsyncMock(
        side_effect=results if isinstance(results, list) else None,
        return_value=None if isinstance(results, list) else results,
    )
    return service


@pytest.mark.asyncio
async def test_stale_pending_repo_is_handed_to_the_event_service() -> None:
    provider = _graph_provider([_row("repo-1", STALE_MS)])
    service = _event_service()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS
    )

    assert started == 1
    service.process_event.assert_awaited_once_with(
        EventTypes.BUILD_CODE_EDGES.value,
        {"orgId": "org-1", "connectorId": "connector-1", "recordGroupId": "repo-1"},
    )
    query = provider.get_nodes_by_filters.await_args.kwargs
    assert query["collection"] == CollectionNames.SYNC_POINTS.value
    assert query["filters"] == {
        "syncDataPointType": "codeEdgeBuild",
        "edgeBuildPending": True,
    }


@pytest.mark.asyncio
async def test_a_request_still_in_flight_is_left_alone() -> None:
    """Requested seconds ago: its message is still in the broker."""
    provider = _graph_provider([_row("repo-1", FRESH_MS)])
    service = _event_service()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS
    )

    assert started == 0
    service.process_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_legacy_mark_with_no_timestamp_is_started() -> None:
    provider = _graph_provider([_row("repo-1", None)])
    service = _event_service()

    assert await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS
    ) == 1


@pytest.mark.asyncio
async def test_a_busy_repo_does_not_count_and_the_sweep_continues() -> None:
    """process_event returning False means another build holds the lock."""
    provider = _graph_provider([_row("repo-1", STALE_MS), _row("repo-2", STALE_MS)])
    service = _event_service([False, True])

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS, limit=1
    )

    assert started == 1
    assert service.process_event.await_count == 2


@pytest.mark.asyncio
async def test_one_failing_repo_does_not_stop_the_next() -> None:
    provider = _graph_provider([_row("repo-1", STALE_MS), _row("repo-2", STALE_MS)])
    service = _event_service([RuntimeError("redis down"), True])
    log = MagicMock()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, log, now_ms=NOW_MS
    )

    assert started == 1
    assert service.process_event.await_count == 2
    log.exception.assert_called_once()


@pytest.mark.asyncio
async def test_starts_per_sweep_are_capped() -> None:
    rows = [_row(f"repo-{i}", STALE_MS) for i in range(4)]
    provider = _graph_provider(rows)
    service = _event_service()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS, limit=2
    )

    assert started == 2
    assert service.process_event.await_count == 2


@pytest.mark.asyncio
async def test_rows_without_a_usable_scope_are_skipped() -> None:
    rows = [
        {**_row("repo-1", STALE_MS), "orgId": None},
        {**_row("repo-2", STALE_MS), "syncPointKey": "repo-2/something-else"},
        _row("repo-3", STALE_MS),
    ]
    provider = _graph_provider(rows)
    service = _event_service()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS
    )

    assert started == 1
    assert service.process_event.await_args.args[1]["recordGroupId"] == "repo-3"
