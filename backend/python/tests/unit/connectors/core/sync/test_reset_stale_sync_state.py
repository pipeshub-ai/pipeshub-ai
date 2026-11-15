"""The startup sweep that clears sync state left behind by a crash.

It is destructive by design — it writes IDLE over whatever it decides is stale —
so the interesting cases are all about when it must refuse to act. `peek_many`
answering "nothing is live" is not the same as knowing nothing is, and acting on
that difference resets a peer's running sync, after which the start guard reads
IDLE and lets a second one begin.

The single-process case still has to work, because there the sweep is the only
thing that ever unwedges a connector stuck SYNCING after a crash.
"""

import asyncio
import contextvars
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.core import org_context
from tests.support.org_partitioned_graph import ArangoLikeGraph, FakeGraph, PgLikeGraph

_LOG = logging.getLogger("test")


def _graph(stuck=(), locked=()) -> AsyncMock:
    gp = AsyncMock()

    async def _by_field(collection, field, values, fields):
        if field == "status":
            return [{"id": c, "orgId": "o1"} for c in stuck]
        return [{"id": c, "status": "SYNCING", "orgId": "o1"} for c in locked]

    gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
    gp.batch_upsert_nodes = AsyncMock()
    gp.get_all_orgs = AsyncMock(return_value=[{"_key": "o1"}])
    return gp


def _manager(*, live=(), reports_liveness=True, peek_raises=False) -> MagicMock:
    m = MagicMock()
    m.reports_liveness = reports_liveness
    m.peek_many = AsyncMock(
        side_effect=RuntimeError("redis down") if peek_raises else None,
        return_value=set(live),
    )
    return m


async def _sweep(graph, manager, *, workers=1, external=False):
    from app.connectors_main import reset_stale_sync_state

    with patch(
        "app.connectors_main.get_coordinator", return_value=manager
    ), patch(
        "app.connectors_main.max_connector_workers", return_value=workers
    ), patch(
        "app.connectors_main.sync_executor_enabled", return_value=external
    ):
        await reset_stale_sync_state(graph, _LOG)


def _written(graph):
    out = []
    for call in graph.batch_upsert_nodes.await_args_list:
        out.extend(call.args[0])
    return out


class TestItRefusesToGuess:
    @pytest.mark.asyncio
    async def test_no_liveness_and_several_workers_means_hands_off(self) -> None:
        """A peer is probably syncing these; resetting invites a second sync."""
        gp = _graph(stuck=["c1", "c2"])
        await _sweep(gp, _manager(reports_liveness=False), workers=4)
        gp.get_nodes_by_field_in.assert_awaited()       # it looked, and then declined
        gp.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_liveness_and_an_external_executor_means_hands_off(self) -> None:
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(reports_liveness=False), workers=1, external=True)
        gp.get_nodes_by_field_in.assert_awaited()
        gp.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_liveness_read_touches_nothing(self) -> None:
        gp = _graph(stuck=["c1"])
        manager = _manager(peek_raises=True)
        await _sweep(gp, manager)
        manager.peek_many.assert_awaited_once()
        gp.batch_upsert_nodes.assert_not_awaited()


class TestTheSingleProcessCaseStillWorks:
    @pytest.mark.asyncio
    async def test_one_worker_with_no_liveness_still_repairs(self) -> None:
        """OSS installs a null lease. If the guard caught this case too, a crash
        would leave the connector SYNCING until someone edited the database."""
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(reports_liveness=False), workers=1, external=False)
        assert [n["status"] for n in _written(gp)] == [AppStatus.IDLE.value]

    @pytest.mark.asyncio
    async def test_the_run_that_died_with_the_process_is_recorded_as_failed(self) -> None:
        """Scenario BU-09: "last synced" must not show a run that never finished as a success."""
        gp = _graph(stuck=["c1"])
        await _sweep(gp, _manager(reports_liveness=False), workers=1, external=False)
        (written,) = _written(gp)
        assert written["lastSyncOutcome"] == "failed"
        assert written["lastSyncFinishedAtTimestamp"] == written["updatedAtTimestamp"]
        # The stored summary is the run before; left, it would read as this failed run's.
        assert "lastSyncSummary" in written and written["lastSyncSummary"] is None

    @pytest.mark.asyncio
    async def test_nothing_stale_writes_nothing(self) -> None:
        gp = _graph()
        await _sweep(gp, _manager())
        assert gp.get_nodes_by_field_in.await_count == 2
        gp.batch_upsert_nodes.assert_not_awaited()


class TestWhatItLeavesAlone:
    @pytest.mark.asyncio
    async def test_a_connector_holding_a_live_lease_is_untouched(self) -> None:
        gp = _graph(stuck=["c1", "c2"])
        await _sweep(gp, _manager(live=["c1"]))
        ids = [n["id"] for n in _written(gp)]
        assert "c1" not in ids
        assert "c2" in ids

    @pytest.mark.asyncio
    async def test_a_deleting_connector_keeps_deleting(self) -> None:
        """It is mid-deletion, not mid-sync. IDLE would invite a resync into a
        connector whose records are being removed underneath it."""
        gp = AsyncMock()

        async def _by_field(collection, field, values, fields):
            if field == "status":
                return []
            return [{"id": "c1", "status": "DELETING", "orgId": "o1"}]

        gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
        gp.batch_upsert_nodes = AsyncMock()
        gp.get_all_orgs = AsyncMock(return_value=[{"_key": "o1"}])
        await _sweep(gp, _manager())
        written = _written(gp)
        assert len(written) == 1
        # The stuck lock is released, but no status is written at all —
        # writing IDLE here is exactly the mistake being avoided.
        assert "status" not in written[0]
        assert written[0]["isLocked"] is False


# --- Org by org (the sweep runs detached, so nothing is bound; Postgres refuses an unbound read) ---

ORGS = [
    {"_key": "a", "isActive": True},
    {"_key": "b", "isActive": False},       # a deactivated org's connectors are still repaired, as on main
    {"_key": "x", "isActive": True, "isExternal": True},     # an extracted organisation: owns no connectors, never listed
]
BOTH = pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])


def _apps(org: str) -> list[dict]:
    return [
        {"_key": f"syncing-{org}", "status": "SYNCING", "isLocked": False, "orgId": org},
        {"_key": f"full-{org}", "status": "FULL_SYNCING", "isLocked": True, "orgId": org},
        {"_key": f"idle-{org}", "status": "IDLE", "isLocked": False, "orgId": org},
    ]


def _two_orgs(graph_cls: type[FakeGraph], **kw: object) -> FakeGraph:
    return graph_cls(ORGS, {"a": _apps("a"), "b": _apps("b")}, **kw)


async def _detached_sweep(graph: FakeGraph, manager: MagicMock | None = None) -> org_context.OrgContext | None:
    """One sweep as _post_startup runs it (spawn_detached: an empty context); whatever is still bound afterwards."""
    from app.connectors_main import reset_stale_sync_state

    async def sweep() -> org_context.OrgContext | None:
        await reset_stale_sync_state(graph, _LOG)
        return org_context._ctx.get()

    with patch("app.connectors_main.get_coordinator", return_value=manager):
        return await asyncio.get_running_loop().create_task(sweep(), context=contextvars.Context())


def _reset(graph: FakeGraph) -> list[tuple[str | None, str]]:
    """(org bound at the write, connector id) for every connector written."""
    return sorted((org, p["id"]) for org, p in graph.upserts)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


STALE_OF_A_AND_B = [("a", "full-a"), ("a", "syncing-a"), ("b", "full-b"), ("b", "syncing-b")]


class TestOrgByOrg:
    @BOTH
    async def test_each_orgs_stale_connectors_are_reset_once_under_their_own_org(self, graph_cls, caplog) -> None:
        graph = _two_orgs(graph_cls)
        await _detached_sweep(graph)
        assert not _errors(caplog)
        assert _reset(graph) == STALE_OF_A_AND_B        # once each: org a's pass never writes org b's connectors
        full_b = next(p for _, p in graph.upserts if p["id"] == "full-b")
        assert (full_b["status"], full_b["isLocked"], full_b["pendingFullSync"], full_b["pendingResync"]) == (
            AppStatus.IDLE.value, False, True, True)

    @BOTH
    async def test_the_org_list_is_read_once_under_system_for_every_internal_org(self, graph_cls) -> None:
        graph = _two_orgs(graph_cls)
        await _detached_sweep(graph)
        assert graph.org_list_calls == [{"kind": "system", "active": False, "is_external": False, "raise_on_error": False}]

    @BOTH
    async def test_one_orgs_failure_is_an_error_naming_it_and_the_next_org_is_still_reset(self, graph_cls, caplog) -> None:
        graph = _two_orgs(graph_cls, raising_orgs=frozenset({"a"}))
        await _detached_sweep(graph)
        assert _errors(caplog) == ["Startup sweep for stale sync state failed org=a: unexpected"]
        assert _reset(graph) == [("b", "full-b"), ("b", "syncing-b")]

    @pytest.mark.parametrize(("graph_cls", "expected"), [
        (PgLikeGraph, "Startup sweep for stale sync state failed: db down"),
        (ArangoLikeGraph, "Get all orgs failed: db down"),
    ])
    async def test_an_org_list_failure_is_one_error_and_nothing_is_written(self, graph_cls, expected, caplog) -> None:
        graph = _two_orgs(graph_cls, orgs_error=RuntimeError("db down"))
        await _detached_sweep(graph)
        assert _errors(caplog) == [expected]
        assert graph.upserts == []

    async def test_an_org_without_id_is_skipped_and_the_rest_are_reset(self) -> None:
        graph = PgLikeGraph([{"name": "no id"}, {"id": "b"}], {"b": _apps("b")})
        await _detached_sweep(graph)
        assert _reset(graph) == [("b", "full-b"), ("b", "syncing-b")]

    async def test_pg_a_legacy_app_without_org_id_is_reset_under_its_partitions_org(self) -> None:
        legacy = {"_key": "legacy-a", "status": "SYNCING"}          # written before Apps carried orgId; nothing backfills it here
        graph = PgLikeGraph(ORGS, {"a": [*_apps("a"), legacy], "b": _apps("b")})
        await _detached_sweep(graph)
        assert ("a", "legacy-a") in _reset(graph)

    async def test_arango_an_app_without_org_id_is_reset_exactly_once(self) -> None:
        orphan = {"_key": "orphan", "status": "SYNCING"}
        graph = ArangoLikeGraph(ORGS, {"a": [*_apps("a"), orphan], "b": _apps("b")})
        await _detached_sweep(graph)
        assert [p["id"] for _, p in graph.upserts].count("orphan") == 1       # the first pass resets it; later passes read it IDLE

    async def test_arango_an_app_of_an_unlisted_org_is_left_alone(self) -> None:
        stray = {"_key": "stray", "status": "SYNCING", "orgId": "deleted-org"}
        graph = ArangoLikeGraph(ORGS, {"a": [*_apps("a"), stray]})
        await _detached_sweep(graph)
        assert "stray" not in [p["id"] for _, p in graph.upserts]

    @BOTH
    async def test_a_live_lease_is_skipped_and_each_org_peeks_only_its_own_connectors(self, graph_cls) -> None:
        graph = _two_orgs(graph_cls)
        manager = _manager(live=["syncing-a"])
        await _detached_sweep(graph, manager)
        assert [set(c.args[0]) for c in manager.peek_many.await_args_list] == [
            {"syncing-a", "full-a"}, {"syncing-b", "full-b"}]
        assert _reset(graph) == [("a", "full-a"), ("b", "full-b"), ("b", "syncing-b")]

    @BOTH
    async def test_one_orgs_failed_lease_read_touches_that_org_only(self, graph_cls, caplog) -> None:
        graph = _two_orgs(graph_cls)
        manager = _manager()

        async def peek(keys) -> set[str]:
            if "syncing-a" in keys:
                raise RuntimeError("redis down")
            return set()
        manager.peek_many = AsyncMock(side_effect=peek)
        await _detached_sweep(graph, manager)
        assert _errors(caplog) == ["Startup sweep could not read sync leases: redis down"]
        assert _reset(graph) == [("b", "full-b"), ("b", "syncing-b")]

    async def test_no_org_is_left_bound_after_the_sweep(self) -> None:
        graph = _two_orgs(PgLikeGraph)
        assert await _detached_sweep(graph) is None
        assert graph.upserts
