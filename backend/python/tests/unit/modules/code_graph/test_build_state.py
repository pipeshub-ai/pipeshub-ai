"""A repo's build state: its rules, and how a build's settle survives races (O-3, O-5, O-6)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph import build_state
from app.modules.code_graph.build_state import (
    BuildOutcome,
    BuildState,
    BuildStateStore,
    GraphStatus,
    PendingBuild,
    RetryPolicy,
    full_build_still_owed,
    graph_status,
    is_due,
    needs_full_build,
    record_group_id_from_key,
    request_is_stale,
    still_owed,
    sync_point_key_for,
)
from app.modules.code_graph.version import EDGE_RULES_VERSION
from tests.unit.modules.code_graph.sync_point_fakes import FakeSyncPoints

ORG, CONN, REPO = "org-1", "connector-1", "repo-1"


def _store(points: FakeSyncPoints, **kw) -> BuildStateStore:
    return BuildStateStore(points, MagicMock(), **kw)


def _point(points: FakeSyncPoints) -> dict:
    return points.points[sync_point_key_for(REPO)]


class TestEdgeRulesVersion:
    def _built(self, version: object) -> BuildState:
        return build_state._state_from_row({"lastEdgeBuildAt": 1, "edgeBuildVersion": version})

    def test_a_build_under_the_current_rules_is_incremental(self) -> None:
        assert not needs_full_build(self._built(EDGE_RULES_VERSION))

    def test_an_edge_rules_bump_owes_a_full_rebuild(self, monkeypatch) -> None:
        state = self._built(EDGE_RULES_VERSION)
        monkeypatch.setattr(build_state, "EDGE_RULES_VERSION", "2099.01.01")
        assert needs_full_build(state)

    @pytest.mark.parametrize("stored", [3, "2026.10.19", None])
    def test_a_version_stored_before_the_split_loads_and_rebuilds(self, stored) -> None:
        assert needs_full_build(self._built(stored))


class TestRetryPolicy:
    @pytest.mark.parametrize(("failures", "delay"), [(1, 60_000), (2, 120_000), (3, 240_000),
                                                      (5, 960_000)])
    def test_backs_off_exponentially(self, failures, delay) -> None:
        assert RetryPolicy().next_attempt_at(failures, 0, rand=lambda: 0) == delay

    def test_caps_the_delay_and_adds_jitter(self) -> None:
        policy = RetryPolicy(base_ms=1000, max_ms=5000, max_failures=99)
        assert policy.next_attempt_at(20, 0, rand=lambda: 0) == 5000
        assert policy.next_attempt_at(20, 0, rand=lambda: 1) == 6000

    def test_gives_up_after_the_last_allowed_failure(self) -> None:
        assert RetryPolicy(max_failures=3).next_attempt_at(3, 0) is None


class TestRules:
    def test_a_request_after_the_build_started_stays_owed(self) -> None:
        assert still_owed(BuildState(1000, True, 2001), 2000)

    def test_a_request_the_build_covered_is_settled(self) -> None:
        assert not still_owed(BuildState(1000, True, 1999), 2000)
        assert not still_owed(BuildState(1000, True, None), 2000)

    def test_a_request_id_decides_regardless_of_clocks(self) -> None:
        assert still_owed(BuildState(1000, True, 1500, "b"), 2000, "a")
        assert not still_owed(BuildState(1000, True, 2500, "a"), 2000, "a")
        assert still_owed(BuildState(1000, True, 1500, "b"), 2000, None)

    def test_a_full_build_serves_only_the_request_it_started_under(self) -> None:
        assert not full_build_still_owed(BuildState(1, False, None, None, "full-1"), "full-1")
        assert full_build_still_owed(BuildState(1, True, None, None, "full-2"), "full-1")
        assert not full_build_still_owed(BuildState(1, False, None, None, None), None)

    def test_staleness(self) -> None:
        assert not request_is_stale(BuildState(None, True, 100_000), 110_000)
        assert request_is_stale(BuildState(None, True, 100_000), 161_000)
        assert request_is_stale(BuildState(None, True, None), 0)
        assert not request_is_stale(BuildState(None, False, 0), 10**9)

    def test_due_respects_backoff_and_terminal_failure(self) -> None:
        stale = {"last_build": None, "pending": True, "requested_at": 0}
        assert is_due(BuildState(**stale), 100_000)
        assert not is_due(BuildState(**stale, next_attempt_at=200_000), 100_000)
        assert is_due(BuildState(**stale, next_attempt_at=100_000), 100_000)
        assert not is_due(BuildState(**stale, outcome=BuildOutcome.FAILED.value), 100_000)
        assert is_due(BuildState(**stale, outcome=BuildOutcome.RETRYING.value), 100_000)

    @pytest.mark.parametrize(("state", "status"), [
        (None, GraphStatus.NEVER_BUILT),
        (BuildState(None, True, 1), GraphStatus.NEVER_BUILT),
        (BuildState(5, False, 1, outcome="ready"), GraphStatus.READY),
        (BuildState(5, True, 1, outcome="ready"), GraphStatus.STALE),
        (BuildState(5, True, 1, outcome="building"), GraphStatus.BUILDING),
        (BuildState(5, True, 1, outcome="retrying"), GraphStatus.RETRYING),
        (BuildState(5, True, 1, outcome="failed"), GraphStatus.FAILED),
        (BuildState(None, False, 1, outcome="too_large"), GraphStatus.TOO_LARGE),
        (BuildState(5, True, 1, outcome="waiting_on_indexing"), GraphStatus.WAITING_ON_INDEXING),
        # Built before statuses were stored.
        (BuildState(5, False, 1), GraphStatus.READY),
    ])
    def test_graph_status(self, state, status) -> None:
        assert graph_status(state) is status

    def test_sync_point_key_round_trips(self) -> None:
        assert record_group_id_from_key(sync_point_key_for("repo-1-code")) == "repo-1-code"
        for key in ["repo-1/vector-rebuild", "code-edge-build", "/code-edge-build", None, 42]:
            assert record_group_id_from_key(key) is None


class TestReads:
    @pytest.mark.asyncio
    async def test_no_sync_point_means_never_asked(self) -> None:
        assert await _store(FakeSyncPoints()).read(ORG, REPO) is None

    @pytest.mark.asyncio
    async def test_unusable_values_read_as_absent(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_filters = AsyncMock(return_value=[{
            "lastEdgeBuildAt": "not-a-number", "edgeBuildRequestedAt": True,
            "edgeBuildAttempts": None,
        }])
        assert await BuildStateStore(graph).read(ORG, REPO) == BuildState(None, False, None)
        kwargs = graph.get_nodes_by_filters.await_args.kwargs
        assert kwargs["filters"]["syncPointKey"] == "repo-1/code-edge-build"
        assert kwargs["raise_on_error"] is True

    @pytest.mark.asyncio
    async def test_all_maps_rows_and_skips_foreign_points(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_filters = AsyncMock(return_value=[
            {"orgId": ORG, "connectorId": CONN, "syncPointKey": "repo-1/code-edge-build",
             "lastEdgeBuildAt": 1000, "edgeBuildPending": True, "edgeBuildRequestedAt": 2000},
            {"orgId": ORG, "connectorId": "", "syncPointKey": "repo-2/code-edge-build"},
            {"orgId": ORG, "connectorId": CONN, "syncPointKey": "repo-3/other"},
        ])
        owed = await BuildStateStore(graph).all(owed_only=True)
        assert owed == [PendingBuild(ORG, CONN, REPO, BuildState(1000, True, 2000))]
        query = graph.get_nodes_by_filters.await_args.kwargs
        assert query["collection"] == CollectionNames.SYNC_POINTS.value
        assert query["filters"] == {"syncDataPointType": "codeEdgeBuild",
                                    "edgeBuildPending": True}


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_a_build_settles_ready_and_clears_its_request(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        await store.mark_requested(ORG, CONN, REPO, 100)
        start = await store.begin(ORG, CONN, REPO, "lease-1")
        assert graph_status(start) is GraphStatus.BUILDING

        assert await store.settle_built(ORG, REPO, lease_token="lease-1", start=start,
                                        started_at_ms=500, advance_watermark=True, full=True)
        point = _point(points)
        assert point["edgeBuildStatus"] == "ready"
        assert point["edgeBuildPending"] is False
        assert point["lastEdgeBuildAt"] == 500
        assert point["lastFullEdgeBuildAt"] == 500
        assert point["edgeBuildLeaseToken"] is None

    @pytest.mark.asyncio
    async def test_a_request_landing_mid_settle_is_kept(self) -> None:
        """The read-then-write window in settle: a plain upsert cleared this request (O-6)."""
        points = FakeSyncPoints()
        store = _store(points)
        await store.mark_requested(ORG, CONN, REPO, 100)
        start = await store.begin(ORG, CONN, REPO, "lease-1")
        points.before_cas = lambda: store.mark_requested(ORG, CONN, REPO, 200)

        assert await store.settle_built(ORG, REPO, lease_token="lease-1", start=start,
                                        started_at_ms=150, advance_watermark=True, full=False)
        assert _point(points)["edgeBuildPending"] is True

    @pytest.mark.asyncio
    async def test_a_build_that_lost_its_lease_does_not_settle(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        start = await store.begin(ORG, CONN, REPO, "lease-1")
        await store.begin(ORG, CONN, REPO, "lease-2")

        assert not await store.settle_built(ORG, REPO, lease_token="lease-1", start=start,
                                            started_at_ms=150, advance_watermark=True,
                                            full=True)
        assert "lastEdgeBuildAt" not in _point(points)
        assert _point(points)["edgeBuildLeaseToken"] == "lease-2"

    @pytest.mark.asyncio
    async def test_failures_back_off_then_stop(self) -> None:
        points = FakeSyncPoints()
        store = _store(points, retry_policy=RetryPolicy(base_ms=1000, max_failures=2))
        await store.mark_requested(ORG, CONN, REPO, 100)

        await store.begin(ORG, CONN, REPO, "l1")
        await store.settle_failed(ORG, REPO, lease_token="l1", now_ms=10_000,
                                  error=ValueError("boom"))
        point = _point(points)
        assert point["edgeBuildStatus"] == "retrying"
        assert point["edgeBuildAttempts"] == 1
        assert 11_000 <= point["edgeBuildNextAttemptAt"] <= 11_200
        assert point["edgeBuildLastError"] == "ValueError: boom"
        assert point["edgeBuildPending"] is True

        await store.begin(ORG, CONN, REPO, "l2")
        await store.settle_failed(ORG, REPO, lease_token="l2", now_ms=20_000,
                                  error=ValueError("boom"))
        assert _point(points)["edgeBuildStatus"] == "failed"
        assert _point(points)["edgeBuildNextAttemptAt"] is None
        assert not is_due(await store.read_or_empty(ORG, REPO), 10**9)

    @pytest.mark.asyncio
    async def test_a_success_resets_the_failure_count(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        await store.begin(ORG, CONN, REPO, "l1")
        await store.settle_failed(ORG, REPO, lease_token="l1", now_ms=0, error=ValueError())
        start = await store.begin(ORG, CONN, REPO, "l2")
        await store.settle_built(ORG, REPO, lease_token="l2", start=start, started_at_ms=1,
                                 advance_watermark=True, full=False)
        point = _point(points)
        assert point["edgeBuildAttempts"] == 0
        assert point["edgeBuildLastError"] is None
        assert "lastFullEdgeBuildAt" not in point

    @pytest.mark.asyncio
    async def test_waiting_on_indexing_keeps_the_request(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        await store.mark_requested(ORG, CONN, REPO, 100)
        await store.begin(ORG, CONN, REPO, "l1")
        await store.settle_deferred(ORG, REPO, lease_token="l1", now_ms=1000)
        point = _point(points)
        assert point["edgeBuildStatus"] == "waiting_on_indexing"
        assert point["edgeBuildPending"] is True
        assert point["edgeBuildNextAttemptAt"] > 1000

    @pytest.mark.asyncio
    async def test_too_large_is_reported_and_not_retried(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        await store.mark_requested(ORG, CONN, REPO, 100)
        start = await store.begin(ORG, CONN, REPO, "l1")
        await store.settle_too_large(ORG, REPO, lease_token="l1", start=start,
                                     started_at_ms=200, reason="400000 blocks")
        state = await store.read_or_empty(ORG, REPO)
        assert graph_status(state) is GraphStatus.TOO_LARGE
        assert not state.pending
        assert state.last_error == "400000 blocks"

    @pytest.mark.asyncio
    async def test_a_full_request_made_during_a_full_build_stays_owed(self) -> None:
        points = FakeSyncPoints()
        store = _store(points)
        await store.request_full(ORG, CONN, REPO)
        start = await store.begin(ORG, CONN, REPO, "l1")
        await store.request_full(ORG, CONN, REPO)
        await store.settle_built(ORG, REPO, lease_token="l1", start=start, started_at_ms=5,
                                 advance_watermark=True, full=True)
        assert _point(points)["edgeBuildFullRequestId"] is not None
