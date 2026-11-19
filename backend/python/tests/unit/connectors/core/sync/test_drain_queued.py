"""Releasing syncs parked at the concurrency limit.

This is the part that broke most often on a real box, always in a way unit tests
of the pieces would not have caught:

  * it wrote IDLE the moment it published, so anything watching concluded there
    was nothing left to wait for and scored those connectors at zero records
  * every worker ran it against the same rows, so the losers re-flagged what the
    winner had just cleared
  * clearing the flag left no way back for a request whose event never arrived
  * its throttle did not throttle — `submit()` only publishes, and the lease is
    taken later by whichever consumer picks the event up, so the count it
    re-checked each iteration could not move. One completed sync republished the
    whole queue, and every one of those came straight back to QUEUED.

So the tests here are about *what the drain leaves behind* and *how much it
releases*, not about whether it publishes.
"""

import asyncio
import contextvars
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_coordinator import _now_ms
from app.connectors.core.sync.sync_dispatcher import SubmitResult
from app.connectors.core.sync.sync_runner import _QUEUE_GRACE_MS, drain_queued_syncs
from app.core import org_context
from app.core.org_context import OrgContext
from app.utils.logger import log_record_fields
from tests.support.org_partitioned_graph import ArangoLikeGraph, FakeGraph, PgLikeGraph

_LOG = logging.getLogger("test")


def _graph(queued_docs, orgs=None, apps=None) -> AsyncMock:
    gp = AsyncMock()
    gp.get_nodes_by_field_in = AsyncMock(return_value=queued_docs)
    gp.get_all_orgs = AsyncMock(return_value=orgs if orgs is not None else [{"_key": "o1"}])
    gp.get_org_apps = AsyncMock(
        return_value=apps
        if apps is not None
        else [{"_key": d["id"], "type": "Gmail", "isActive": True} for d in queued_docs if d.get("id")]
    )
    gp.update_node = AsyncMock()
    return gp


def _dispatcher(result=SubmitResult.ACCEPTED) -> MagicMock:
    d = MagicMock()
    d.submit = AsyncMock(return_value=result)
    return d


def _coordinator(*, running=0, claim=True) -> MagicMock:
    c = MagicMock()
    c.running_count = MagicMock(return_value=running)
    c.try_claim_once = AsyncMock(return_value=claim)
    return c


def _doc(cid, *, flagged=True, age_ms=0):
    return {
        "id": cid,
        ConnectorStateKeys.PENDING_RESYNC: flagged,
        "updatedAtTimestamp": _now_ms() - age_ms,
    }


def _run(graph, *, dispatcher=None, coordinator=None, limit="8"):
    """Patch the two module-level lookups the drain does at call time."""
    return (
        patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher if dispatcher is not None else _dispatcher(),
        ),
        patch(
            "app.connectors.core.sync.sync_coordinator.get_coordinator",
            return_value=coordinator if coordinator is not None else _coordinator(),
        ),
        patch.dict("os.environ", {"CONNECTOR_SYNC_MAX_CONCURRENT": limit}),
    )


class TestItDeclinesToRun:
    @pytest.mark.asyncio
    async def test_no_dispatcher_means_no_work(self) -> None:
        gp = _graph([_doc("c1")])
        _a, b, c = _run(gp)
        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher", return_value=None
        ), b, c:
            assert await drain_queued_syncs(gp, _LOG) == []
        gp.get_nodes_by_field_in.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_coordinator_means_no_work(self) -> None:
        gp = _graph([_doc("c1")])
        a, _b, c = _run(gp)
        with a, patch(
            "app.connectors.core.sync.sync_coordinator.get_coordinator",
            return_value=None,
        ), c:
            assert await drain_queued_syncs(gp, _LOG) == []
        gp.get_nodes_by_field_in.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_another_worker_holds_the_turn(self) -> None:
        """All four finalizers fire this; only one may act on the same rows."""
        gp = _graph([_doc("c1")])
        a, b, c = _run(gp, coordinator=_coordinator(claim=False))
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == []
        gp.get_nodes_by_field_in.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_is_not_fatal(self) -> None:
        gp = _graph([])
        gp.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("graph down"))
        a, b, c = _run(gp)
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == []


class TestWhichConnectorsAreOwedASync:
    @pytest.mark.asyncio
    async def test_it_asks_only_for_queued_connectors(self) -> None:
        gp = _graph([_doc("c1")])
        a, b, c = _run(gp)
        with a, b, c:
            await drain_queued_syncs(gp, _LOG)
        args = gp.get_nodes_by_field_in.await_args.args
        assert args[0] == CollectionNames.APPS.value
        assert args[1] == "status"
        assert args[2] == [AppStatus.QUEUED.value]

    @pytest.mark.asyncio
    async def test_a_flagged_connector_is_owed(self) -> None:
        gp = _graph([_doc("c1", flagged=True)])
        a, b, c = _run(gp)
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == ["c1"]

    @pytest.mark.asyncio
    async def test_an_unflagged_connector_is_left_alone_while_fresh(self) -> None:
        """Its event is in flight; re-issuing now would be a duplicate."""
        gp = _graph([_doc("c1", flagged=False, age_ms=1_000)])
        disp = _dispatcher()
        a, b, c = _run(gp, dispatcher=disp)
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == []
        gp.get_nodes_by_field_in.assert_awaited()       # it did read the queue
        disp.submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unflagged_connector_past_the_grace_window_is_owed_again(
        self,
    ) -> None:
        """The stranded case: submitted, flag cleared, event never arrived.

        Without this it stays QUEUED, unflagged and invisible to every later
        drain — which on a real box hung a run until its timeout.
        """
        gp = _graph([_doc("c1", flagged=False, age_ms=_QUEUE_GRACE_MS + 5_000)])
        a, b, c = _run(gp)
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == ["c1"]


class TestWhatItLeavesBehind:
    @pytest.mark.asyncio
    async def test_it_never_writes_idle(self) -> None:
        """The bug that ended runs early.

        Between publishing and the sync starting, an IDLE status tells everything
        watching that there is nothing left to wait for.
        """
        gp = _graph([_doc("c1")])
        a, b, c = _run(gp)
        with a, b, c:
            await drain_queued_syncs(gp, _LOG)
        for call in gp.update_node.await_args_list:
            assert "status" not in call.args[2], f"drain wrote a status: {call.args[2]}"

    @pytest.mark.asyncio
    async def test_the_flag_is_cleared_only_once_accepted(self) -> None:
        gp = _graph([_doc("c1")])
        a, b, c = _run(gp)
        with a, b, c:
            await drain_queued_syncs(gp, _LOG)
        call = gp.update_node.await_args
        assert call.args[0] == "c1"
        # Restamped as well as cleared: without a fresh timestamp a row whose
        # event never arrives stays "stale" and is re-published every pass.
        assert call.args[2][ConnectorStateKeys.PENDING_RESYNC] is False
        assert "updatedAtTimestamp" in call.args[2]

    @pytest.mark.asyncio
    async def test_a_declined_submit_keeps_the_flag(self) -> None:
        gp = _graph([_doc("c1")])
        a, b, c = _run(gp, dispatcher=_dispatcher(SubmitResult.DECLINED_RUNNING))
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == []
        gp.update_node.assert_not_awaited()


class TestPublishingIsNotAdmission:
    """The drain publishes; `begin()` decides.

    It used to bound each pass by the draining worker's own free slots, which is
    wrong twice over: that worker does not run the syncs it releases, and it can
    be full while the rest of the fleet is idle. Measured at n=70 it released ~7
    connectors per 87-second burst instead of keeping the pipeline full -- 456s
    against 115s. Over-publishing is cheap and self-correcting: the consumer's
    capacity gate meters arrivals, and anything past the limit is answered
    AT_CAPACITY and stays QUEUED.
    """

    @pytest.mark.asyncio
    async def test_a_full_worker_still_releases_for_the_fleet(self) -> None:
        gp = _graph([_doc("c1"), _doc("c2"), _doc("c3")])
        disp = _dispatcher()
        a, b, c = _run(gp, dispatcher=disp, coordinator=_coordinator(running=8), limit="8")
        with a, b, c:
            started = await drain_queued_syncs(gp, _LOG)
        assert started == ["c1", "c2", "c3"], (
            "a saturated worker must still publish -- the syncs run elsewhere"
        )

    @pytest.mark.asyncio
    async def test_every_owed_connector_is_released(self) -> None:
        gp = _graph([_doc(f"c{i}") for i in range(5)])
        a, b, c = _run(gp, coordinator=_coordinator(running=0), limit="8")
        with a, b, c:
            assert len(await drain_queued_syncs(gp, _LOG)) == 5

    @pytest.mark.asyncio
    async def test_a_declined_submit_is_not_counted_as_released(self) -> None:
        """A decline means the event never reached the topic, so the flag stays
        set and the next pass tries again."""
        gp = _graph([_doc("c1"), _doc("c2")])
        results = iter([SubmitResult.DECLINED_RUNNING, SubmitResult.ACCEPTED])
        disp = MagicMock()
        disp.submit = AsyncMock(side_effect=lambda _s: next(results))
        a, b, c = _run(gp, dispatcher=disp, coordinator=_coordinator(running=0), limit="8")
        with a, b, c:
            assert await drain_queued_syncs(gp, _LOG) == ["c2"]


# --- org by org: one pass reads each org's queue under that org, then publishes oldest first across orgs, each under its own org ---

_DRAIN_LOG = logging.getLogger("drain-under-test")
_ORGS = [{"_key": "a", "isActive": True}, {"_key": "b", "isActive": True}]


def _queued(key: str, *, queued_at: int = 0, flagged: bool = True, **fields) -> dict:
    """A QUEUED App document; a field given as None is left out."""
    doc = {"_key": key, "type": "Gmail", "status": AppStatus.QUEUED.value, "isActive": True,
           ConnectorStateKeys.PENDING_RESYNC: flagged, "updatedAtTimestamp": _now_ms(), "queuedAtTimestamp": queued_at, **fields}
    return {k: v for k, v in doc.items() if v is not None}


def _two_orgs(graph_cls: type[FakeGraph], **kwargs) -> FakeGraph:
    """Orgs a and b, each with a connector owed a resync and one disabled while it waited."""
    return graph_cls(_ORGS, {org: [_queued(f"q{org}"), _queued(f"off{org}", isActive=False)] for org in "ab"}, **kwargs)


class _Recorder:
    """A dispatcher that records (connector, the spec's org, the org bound when it published)."""

    def __init__(self, gate: asyncio.Event | None = None) -> None:
        self.published: list[tuple[str, str, str | None]] = []
        self.gate, self.entered, self.task = gate, asyncio.Event(), None

    async def submit(self, spec) -> SubmitResult:
        self.task = asyncio.current_task()
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        bound = org_context.peek()
        self.published.append((spec.connector_id, spec.org_id, bound.org_id if bound else None))
        return SubmitResult.ACCEPTED


def _one_process(*, budget: int | None = 8, claim: bool = True) -> MagicMock:
    """A one-process coordinator: it grants the claim and counts its free slots (or, with no budget, cannot count them)."""
    c = MagicMock(spec=["try_claim_once"] + (["drain_budget"] if budget is not None else []))
    c.try_claim_once = AsyncMock(return_value=claim)
    if budget is not None:
        c.drain_budget = MagicMock(return_value=budget)
    return c


async def _drain(graph, recorder: _Recorder, *, bound: str | None = None,
                 coordinator: MagicMock | None = None) -> tuple[list[str], OrgContext | None]:
    """Run the drain detached as the startup does, or with ``bound`` bound as a finalizer does; its result and what is bound after."""
    context = contextvars.Context()
    if bound:
        context.run(org_context._ctx.set, OrgContext(bound, None, "service", "envelope", "t"))

    async def caller() -> tuple[list[str], OrgContext | None]:
        return await drain_queued_syncs(graph, _DRAIN_LOG), org_context.peek()

    with patch("app.connectors.core.sync.sync_dispatcher.get_dispatcher", return_value=recorder), \
            patch("app.connectors.core.sync.sync_coordinator.get_coordinator", return_value=coordinator or _one_process()):
        return await asyncio.get_running_loop().create_task(caller(), context=context)


def _cleared(graph) -> list[tuple[str | None, str]]:
    return [(org, key) for org, key, _, u in graph.updates if u.get(ConnectorStateKeys.PENDING_RESYNC) is False and "status" not in u]


def _dropped(graph) -> list[tuple[str | None, str]]:
    return [(org, key) for org, key, _, u in graph.updates if u.get("status") == AppStatus.IDLE.value]


_LINE_FIELDS = ("org_id", "step", "error_message", "unowned", "target_connector_id", "target_connector_ids")


def _drain_lines(caplog, level: int) -> list[str]:
    """Each drain line as its message and the fields that tell it apart, read as the formatters print them."""
    return [
        " ".join([r.getMessage(), *(f"{k}={v}" for k, v in log_record_fields(r).items() if k in _LINE_FIELDS)])
        for r in caplog.records if r.name == _DRAIN_LOG.name and r.levelno == level
    ]


class TestOrgByOrg:
    @pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
    @pytest.mark.parametrize("bound", [None, "a"])
    async def test_one_pass_releases_every_orgs_queue_each_under_its_org(self, graph_cls, bound, caplog) -> None:
        """Detached (the startup drain) or inside org a (a finalizer): every org's queue, each read, drop, publish and clear under its org."""
        caplog.set_level(logging.INFO)
        graph, rec = _two_orgs(graph_cls), _Recorder()
        started, after = await _drain(graph, rec, bound=bound)

        assert sorted(started) == ["qa", "qb"]
        assert sorted(rec.published) == [("qa", "a", "a"), ("qb", "b", "b")]
        assert sorted(_cleared(graph)) == [("a", "qa"), ("b", "qb")]
        assert sorted(_dropped(graph)) == [("a", "offa"), ("b", "offb")]
        assert graph.listings == [("a", "a"), ("b", "b")]
        assert not _drain_lines(caplog, logging.ERROR) and not _drain_lines(caplog, logging.WARNING)
        assert (after.org_id if after else None) == bound          # the caller's own org is untouched
        if graph_cls is ArangoLikeGraph:      # positive control: this store's read under a does return b's queue
            with org_context.for_org("a", job="t"):
                assert "qb" in {d["id"] for d in await graph.get_nodes_by_field_in("apps", "status", ["QUEUED"])}

    @pytest.mark.parametrize(("budget", "expected"), [(2, ["q2", "q3"]), (None, ["q2", "q3", "q1"])])
    async def test_oldest_first_across_orgs_within_one_budget(self, budget, expected) -> None:
        """Fails on PostgreSQL today (only the bound org is seen); a regression guard on Arango. No budget is the positive control."""
        graph = PgLikeGraph(_ORGS, {"a": [_queued("q1", queued_at=30)], "b": [_queued("q2", queued_at=10), _queued("q3", queued_at=20)]})
        rec = _Recorder()
        started, _ = await _drain(graph, rec, coordinator=_one_process(budget=budget))
        assert started == expected
        assert [p[0] for p in rec.published] == expected
        assert sorted(key for _, key in _cleared(graph)) == sorted(expected)      # q1 keeps its flag when it does not fit

    async def test_one_claim_per_pass_and_a_refused_claim_reads_nothing(self) -> None:
        """Regression guard: the fleet claim is taken once for every org, not once per org."""
        coordinator = _one_process()
        await _drain(_two_orgs(PgLikeGraph), _Recorder(), coordinator=coordinator)
        coordinator.try_claim_once.assert_awaited_once()

        graph, rec = _two_orgs(PgLikeGraph), _Recorder()
        assert await _drain(graph, rec, coordinator=_one_process(claim=False)) == ([], None)
        assert graph.org_list_calls == [] and rec.published == []

    @pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
    async def test_one_orgs_failed_read_is_its_error_and_the_next_org_is_still_drained(self, graph_cls, caplog) -> None:
        """On Arango, b's pass also sees a's queue: a's connectors are not called orgless when a could not answer."""
        graph, rec = _two_orgs(graph_cls, raising_orgs=frozenset({"a"})), _Recorder()
        _, after = await _drain(graph, rec, bound="a")
        assert _drain_lines(caplog, logging.ERROR) == ["sync_step.failed org_id=a step=drain error_message=RuntimeError: unexpected"]
        assert not _drain_lines(caplog, logging.WARNING)
        assert rec.published == [("qb", "b", "b")]         # positive control: b is served
        assert after.org_id == "a"                         # the failure did not leak into the caller's context

    async def test_a_failed_flag_clear_is_one_error_and_the_pass_publishes_the_rest(self, caplog) -> None:
        """One connector's clear failing must not stop the publish loop: later connectors in every org still go out, all are reported."""
        graph = _two_orgs(PgLikeGraph)
        real_update = graph.update_node

        async def update_node(key: str, collection: str, updates: dict, transaction: str | None = None) -> bool:
            if key == "qa" and updates.get(ConnectorStateKeys.PENDING_RESYNC) is False:
                raise RuntimeError("write lost")
            return await real_update(key, collection, updates, transaction)

        graph.update_node = update_node  # type: ignore[method-assign]
        rec = _Recorder()
        started, _ = await _drain(graph, rec, coordinator=_one_process(budget=None))

        assert sorted(started) == ["qa", "qb"]                       # qa was published: it is reported as released
        assert sorted(p[0] for p in rec.published) == ["qa", "qb"]   # b's connector still goes out
        assert _cleared(graph) == [("b", "qb")]                      # qa keeps its flag, so a later pass re-issues it
        assert _drain_lines(caplog, logging.ERROR) == [
            "sync_step.failed org_id=a step=drain target_connector_id=qa error_message=RuntimeError: write lost"
        ]

    async def test_one_orgs_failed_app_listing_is_one_drain_error_and_nothing_of_that_org_is_touched(self, caplog) -> None:
        """Arango and Neo4j also log the failure themselves (pre-existing): only the drain's own line is counted here."""
        graph, rec = _two_orgs(ArangoLikeGraph, failing_orgs=frozenset({"a"})), _Recorder()
        await _drain(graph, rec)
        assert _drain_lines(caplog, logging.ERROR) == ["sync_step.failed org_id=a step=drain error_message=RuntimeError: org apps down"]
        assert [r for r in caplog.records if r.name == "fake-graph" and r.levelno == logging.ERROR]      # the provider's line
        assert rec.published == [("qb", "b", "b")]
        assert _dropped(graph) == [("b", "offb")]
        assert not _drain_lines(caplog, logging.WARNING)      # a's queue, seen from b, is not called orgless

    async def test_an_org_list_failure_is_one_error_and_no_work(self, caplog) -> None:
        graph, rec = _two_orgs(PgLikeGraph, orgs_error=RuntimeError("orgs down")), _Recorder()
        assert await _drain(graph, rec) == ([], None)
        assert _drain_lines(caplog, logging.ERROR) == ["sync_step.failed step=drain error_message=RuntimeError: orgs down"]
        assert graph.org_list_calls == [{"kind": "system", "active": True, "is_external": False, "raise_on_error": False}]
        assert rec.published == [] and graph.updates == []

    async def test_a_connector_no_visited_org_lists_is_warned_once_and_left_alone(self, caplog) -> None:
        """Both org passes see the edge-less Apps (Arango reads across orgs); neither the owed nor the disabled one is touched."""
        graph = ArangoLikeGraph(_ORGS, {"a": [_queued("qa"), _queued("lone"), _queued("lone-off", isActive=False)]},
                                unlinked=frozenset({"lone", "lone-off"}))
        rec = _Recorder()
        await _drain(graph, rec)
        assert _drain_lines(caplog, logging.WARNING) == [
            "Pending resync flagged on connectors that belong to no active org; leaving them alone"
            " unowned=2 target_connector_ids=lone,lone-off"]
        assert rec.published == [("qa", "a", "a")]          # positive control: the linked one is published
        assert [(org, key) for org, key, *_ in graph.updates] == [("a", "qa")]      # only the published one's flag clear

    async def test_isactive_unset_is_not_published_and_isactive_false_is_dropped(self, caplog) -> None:
        """Parity with the old get_org_apps(active_only=True): an App with isActive unset was never resolved, so never published."""
        caplog.set_level(logging.INFO)
        graph = PgLikeGraph(_ORGS, {"a": [_queued("qa"), _queued("unset", isActive=None), _queued("off", isActive=False)]})
        rec = _Recorder()
        await _drain(graph, rec)
        assert rec.published == [("qa", "a", "a")]
        assert _drain_lines(caplog, logging.WARNING) == [
            "Pending resync flagged on connectors that belong to no active org; leaving them alone"
            " unowned=1 target_connector_ids=unset"]
        assert _dropped(graph) == [("a", "off")]
        assert "Dropped the queued sync of an inactive connector org_id=a target_connector_id=off" in _drain_lines(caplog, logging.INFO)

    async def test_a_deactivated_orgs_queue_is_not_read(self) -> None:
        graph = PgLikeGraph([*_ORGS, {"_key": "c", "isActive": False}], {"a": [_queued("qa")], "c": [_queued("qc")]})
        rec = _Recorder()
        await _drain(graph, rec)
        assert [c["active"] for c in graph.org_list_calls] == [True]
        assert graph.reads == ["a", "b"]
        assert rec.published == [("qa", "a", "a")]

    async def test_a_connector_linked_to_two_orgs_is_released_once_by_the_first(self) -> None:
        """Arango/Neo4j: duplicate orgAppRelation edges. Once a has claimed it, b's pass has nothing to act on and lists no apps."""
        both = _queued("both")
        graph, rec = ArangoLikeGraph(_ORGS, {"a": [both], "b": [both]}), _Recorder()
        assert (await _drain(graph, rec))[0] == ["both"]
        assert rec.published == [("both", "a", "a")]
        assert _cleared(graph) == [("a", "both")]
        assert graph.listings == [("a", "a")]

    async def test_a_queue_with_nothing_owed_lists_no_apps(self) -> None:
        """Everything queued is in flight (unflagged, inside the grace window): each org's read, and no app listing."""
        graph = ArangoLikeGraph(_ORGS, {org: [_queued(f"q{org}", flagged=False)] for org in "ab"})
        rec = _Recorder()
        assert (await _drain(graph, rec))[0] == []
        assert graph.reads == ["a", "b"]                    # positive control: both orgs were read
        assert graph.listings == [] and rec.published == []

    async def test_cancelling_the_caller_cancels_the_pass_and_frees_the_lock(self) -> None:
        """Regression guard: the pass runs in its own task now, and a cancel must still reach it and release the lock."""
        graph, blocked = _two_orgs(ArangoLikeGraph), _Recorder(gate=asyncio.Event())
        caller = asyncio.create_task(_drain(graph, blocked))
        await blocked.entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert blocked.task.cancelled()
        assert blocked.published == [] and _cleared(graph) == []

        rec = _Recorder()                                   # positive control: the next pass runs
        assert sorted((await _drain(graph, rec))[0]) == ["qa", "qb"]
