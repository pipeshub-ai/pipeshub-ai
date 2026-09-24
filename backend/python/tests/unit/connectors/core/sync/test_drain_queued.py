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

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_coordinator import _now_ms
from app.connectors.core.sync.sync_dispatcher import SubmitResult
from app.connectors.core.sync.sync_runner import _QUEUE_GRACE_MS, drain_queued_syncs

_LOG = logging.getLogger("test")


def _graph(queued_docs, orgs=None, apps=None) -> AsyncMock:
    gp = AsyncMock()
    gp.get_nodes_by_field_in = AsyncMock(return_value=queued_docs)
    gp.get_all_orgs = AsyncMock(return_value=orgs if orgs is not None else [{"_key": "o1"}])
    gp.get_org_apps = AsyncMock(
        return_value=apps
        if apps is not None
        else [{"_key": d["id"], "type": "Gmail"} for d in queued_docs if d.get("id")]
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
