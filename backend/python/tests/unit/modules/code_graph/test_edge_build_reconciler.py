"""Unit tests for app.modules.code_graph.edge_build_reconciler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph import edge_build_reconciler
from app.modules.code_graph.build_state import sync_point_key_for
from tests.unit.modules.code_graph.sync_point_fakes import FakeSyncPoints

NOW_MS = 10_000_000
STALE_MS = NOW_MS - 120_000
FRESH_MS = NOW_MS - 10_000


def _row(record_group_id: str, requested_at: int | None, org_id: str = "org-1") -> dict:
    return {
        "orgId": org_id,
        "connectorId": "connector-1",
        "syncPointKey": sync_point_key_for(record_group_id),
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
    service.start_build = AsyncMock(
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
    service.start_build.assert_awaited_once_with("org-1", "connector-1", "repo-1")
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
    service.start_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_legacy_mark_with_no_timestamp_is_started() -> None:
    provider = _graph_provider([_row("repo-1", None)])
    service = _event_service()

    assert await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS
    ) == 1


@pytest.mark.asyncio
async def test_a_busy_repo_does_not_count_and_the_sweep_continues() -> None:
    """start_build returning False means another build holds the lock."""
    provider = _graph_provider([_row("repo-1", STALE_MS), _row("repo-2", STALE_MS)])
    service = _event_service([False, True])

    started = await edge_build_reconciler.reconcile_once(
        provider, service, MagicMock(), now_ms=NOW_MS, limit=1
    )

    assert started == 1
    assert service.start_build.await_count == 2


@pytest.mark.asyncio
async def test_one_failing_repo_does_not_stop_the_next() -> None:
    provider = _graph_provider([_row("repo-1", STALE_MS), _row("repo-2", STALE_MS)])
    service = _event_service([RuntimeError("redis down"), True])
    log = MagicMock()

    started = await edge_build_reconciler.reconcile_once(
        provider, service, log, now_ms=NOW_MS
    )

    assert started == 1
    assert service.start_build.await_count == 2
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
    assert service.start_build.await_count == 2


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
    assert service.start_build.await_args.args[2] == "repo-3"


def _started(service) -> list[str]:
    return [c.args[2] for c in service.start_build.await_args_list]


@pytest.mark.asyncio
async def test_a_retry_waits_out_its_backoff() -> None:
    provider = _graph_provider([
        {**_row("repo-1", STALE_MS), "edgeBuildNextAttemptAt": NOW_MS + 1},
        {**_row("repo-2", STALE_MS), "edgeBuildNextAttemptAt": NOW_MS - 1},
    ])
    service = _event_service()
    await edge_build_reconciler.reconcile_once(provider, service, MagicMock(), now_ms=NOW_MS)
    assert _started(service) == ["repo-2"]


@pytest.mark.asyncio
async def test_a_repo_that_failed_for_good_is_not_retried() -> None:
    """A repo that always fails used to come back first on every sweep (O-5)."""
    provider = _graph_provider([{**_row("repo-1", STALE_MS), "edgeBuildStatus": "failed"},
                                _row("repo-2", STALE_MS)])
    service = _event_service()
    await edge_build_reconciler.reconcile_once(provider, service, MagicMock(), now_ms=NOW_MS)
    assert _started(service) == ["repo-2"]


@pytest.mark.asyncio
async def test_orgs_take_turns_within_a_sweep() -> None:
    rows = [_row(f"a-{i}", STALE_MS, org_id="org-a") for i in range(3)]
    rows.append(_row("b-0", STALE_MS, org_id="org-b"))
    service = _event_service()
    await edge_build_reconciler.reconcile_once(
        _graph_provider(rows), service, MagicMock(), now_ms=NOW_MS, limit=2
    )
    assert sorted(_started(service)) == ["a-0", "b-0"]


@pytest.mark.asyncio
async def test_a_repo_without_a_recent_full_build_is_owed_one() -> None:
    """The scheduled full rebuild the incremental builds are checked against (R-3)."""
    day = 24 * 3600 * 1000
    points = FakeSyncPoints()
    for key, extra in [
        ("old", {"lastEdgeBuildAt": 5, "lastFullEdgeBuildAt": NOW_MS - 8 * day}),
        ("recent", {"lastEdgeBuildAt": 5, "lastFullEdgeBuildAt": NOW_MS - day}),
        ("never-full", {"lastEdgeBuildAt": 5}),
        ("never-built", {}),
        ("already-owed", {"lastEdgeBuildAt": 5, "edgeBuildPending": True}),
    ]:
        points.points[sync_point_key_for(key)] = {
            "syncPointKey": sync_point_key_for(key), "orgId": "org-1",
            "connectorId": "c-1", "syncDataPointType": "codeEdgeBuild", **extra,
        }

    requested = await edge_build_reconciler.request_due_full_rebuilds(
        points, MagicMock(), now_ms=NOW_MS, interval_s=7 * 24 * 3600
    )

    assert requested == 2
    for key in ("old", "never-full"):
        point = points.points[sync_point_key_for(key)]
        assert point["edgeBuildPending"] is True
        assert point["edgeBuildFullRequestId"]
    assert "edgeBuildFullRequestId" not in points.points[sync_point_key_for("recent")]



@pytest.mark.asyncio
async def test_the_sweep_loop_survives_a_failed_sweep_and_stops_when_cancelled(monkeypatch) -> None:
    """reconcile_forever: a jittered first delay, a failing sweep logged not fatal, then
    one sweep per interval until the task is cancelled (T-3)."""
    import asyncio

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    sweeps = AsyncMock(side_effect=[RuntimeError("graph down"), 0])
    monkeypatch.setattr(edge_build_reconciler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(edge_build_reconciler, "reconcile_once", sweeps)
    monkeypatch.setattr(edge_build_reconciler, "request_due_full_rebuilds", AsyncMock(return_value=0))
    log = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await edge_build_reconciler.reconcile_forever(MagicMock(), MagicMock(), log, interval_s=100)

    assert sweeps.await_count == 2
    log.exception.assert_called_once()
    assert 30 <= sleeps[0] <= 90
    assert all(100 <= s <= 110 for s in sleeps[1:])


@pytest.mark.asyncio
async def test_disabled_code_graph_runs_no_sweep(monkeypatch) -> None:
    from app.modules.code_graph import enabled
    monkeypatch.setattr(enabled, "CODE_GRAPH_ENABLED", False)
    sweeps = AsyncMock()
    monkeypatch.setattr(edge_build_reconciler, "reconcile_once", sweeps)
    await edge_build_reconciler.reconcile_forever(MagicMock(), MagicMock(), MagicMock())
    sweeps.assert_not_awaited()
