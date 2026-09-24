"""Lease handling inside the sync task body.

The interesting cases are all about what survives a *second* cancel — a stop
racing a shutdown, or a delete racing a stop. That is where a release placed
after `await asyncio.shield(...)` silently never runs.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    return gp


def _statuses(graph_provider: AsyncMock) -> list[str]:
    return [
        call.args[0][0]["status"]
        for call in graph_provider.batch_upsert_nodes.call_args_list
    ]


def _connector(run_sync=None) -> MagicMock:
    c = MagicMock()
    c.run_sync = run_sync or AsyncMock()
    return c


class TestWithoutALease:
    @pytest.mark.asyncio
    async def test_unchanged_behaviour(self) -> None:
        """Passing no lease must behave exactly as before it existed."""
        gp = _graph_provider()
        await run_sync_task(_connector(), "c1", gp, logging.getLogger("t"))
        assert _statuses(gp) == [AppStatus.SYNCING.value, AppStatus.IDLE.value]


class TestReleaseOrdering:
    @pytest.mark.asyncio
    async def test_idle_is_written_before_release(self) -> None:
        """Releasing first would let the next owner's SYNCING be overwritten."""
        order: list[str] = []
        gp = _graph_provider()
        gp.batch_upsert_nodes = AsyncMock(
            side_effect=lambda *a, **k: order.append(f"status:{a[0][0]['status']}")
        )
        manager = AsyncMock()
        manager.end = AsyncMock(side_effect=lambda _l: order.append("end"))
        lease = SyncLease("c1", "tok", 1)

        await run_sync_task(
            _connector(), "c1", gp, logging.getLogger("t"),
            lease=lease, coordinator=manager,
        )

        assert order == ["status:SYNCING", "status:IDLE", "end"]

    @pytest.mark.asyncio
    async def test_release_still_runs_when_the_idle_write_fails(self) -> None:
        """write_app_status swallows its own errors, so the release must follow.

        This is why the reaper is mandatory rather than optional: the status can
        silently stay SYNCING while the lease is correctly gone.
        """
        gp = _graph_provider()
        gp.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db down"))
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)

        await run_sync_task(
            _connector(), "c1", gp, logging.getLogger("t"),
            lease=lease, coordinator=manager,
        )

        manager.end.assert_awaited_once_with(lease)


class TestSecondCancel:
    @pytest.mark.asyncio
    async def test_release_survives_a_cancel_during_finalize(self) -> None:
        """The regression this design exists to prevent.

        With the release placed after `await asyncio.shield(cleanup)`, a cancel
        arriving while the finalizer runs makes that await raise and the release
        never happens — pinning the connector for a full lease TTL.
        """
        released = asyncio.Event()
        gp = _graph_provider()

        in_finalize = asyncio.Event()

        async def slow_upsert(payloads, *_a, **_k) -> None:
            if payloads[0]["status"] != AppStatus.IDLE.value:
                return
            in_finalize.set()
            await asyncio.sleep(0.05)

        gp.batch_upsert_nodes = AsyncMock(side_effect=slow_upsert)

        manager = AsyncMock()

        async def _end(_lease) -> bool:
            released.set()
            return True

        manager.end = AsyncMock(side_effect=_end)
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=lease, coordinator=manager,
            )
        )

        # Cancel while the finalizer is mid-IDLE-write.
        await in_finalize.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.wait_for(released.wait(), timeout=1)


class TestLeaseLost:
    @pytest.mark.asyncio
    async def test_lost_lease_aborts_the_sync(self) -> None:
        started = asyncio.Event()

        async def never_ending() -> None:
            started.set()
            await asyncio.sleep(3600)

        gp = _graph_provider()
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(never_ending), "c1", gp, logging.getLogger("t"),
                lease=lease, coordinator=manager,
            )
        )
        await started.wait()
        lease.lost.set()
        await asyncio.wait_for(task, timeout=1)

    @pytest.mark.asyncio
    async def test_lost_lease_skips_the_idle_write_but_still_releases(self) -> None:
        """Another executor owns the connector now — its status is not ours to write."""
        gp = _graph_provider()
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)
        lease.lost.set()

        await run_sync_task(
            _connector(), "c1", gp, logging.getLogger("t"),
            lease=lease, coordinator=manager,
        )

        assert AppStatus.IDLE.value not in _statuses(gp)
        manager.end.assert_awaited_once_with(lease)

    @pytest.mark.asyncio
    async def test_stop_request_aborts_but_does_write_idle(self) -> None:
        """A user stop still owns the lease, so the connector must read IDLE."""
        started = asyncio.Event()

        async def never_ending() -> None:
            started.set()
            await asyncio.sleep(3600)

        gp = _graph_provider()
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(never_ending), "c1", gp, logging.getLogger("t"),
                lease=lease, coordinator=manager,
            )
        )
        await started.wait()
        lease.stop_requested.set()
        await asyncio.wait_for(task, timeout=1)

        assert _statuses(gp) == [AppStatus.SYNCING.value, AppStatus.IDLE.value]
        manager.end.assert_awaited_once_with(lease)


class TestSyncFailure:
    @pytest.mark.asyncio
    async def test_sync_exception_propagates_and_still_releases(self) -> None:
        async def boom() -> None:
            raise ValueError("connector blew up")

        gp = _graph_provider()
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)

        with pytest.raises(ValueError):
            await run_sync_task(
                _connector(boom), "c1", gp, logging.getLogger("t"),
                lease=lease, coordinator=manager,
            )

        manager.end.assert_awaited_once_with(lease)


class TestDeclinedResyncIsReissued:
    """A resync asked for while one is running must not be silently lost.

    Observed on the play box at four uvicorn workers: every worker resumes every
    connector, the lease correctly lets one win, and the losers' requests were
    acked and forgotten. Where the winning sync had started before the connector
    was fully configured, that connector then never synced at all.
    """

    def _graph_with_pending(self, pending: bool) -> AsyncMock:
        gp = _graph_provider()
        gp.get_document = AsyncMock(
            return_value={"id": "c1", "pendingResync": pending}
        )
        gp.update_node = AsyncMock()
        return gp

    @pytest.mark.asyncio
    async def test_pending_resync_is_reissued_after_the_lease_is_released(self) -> None:
        from app.connectors.core.sync.sync_dispatcher import SubmitResult, SyncSpec

        order: list[str] = []
        gp = self._graph_with_pending(True)
        manager = AsyncMock()
        manager.end = AsyncMock(side_effect=lambda _l: order.append("end"))
        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(
            side_effect=lambda _s, **_k: order.append("resubmit") or SubmitResult.ACCEPTED
        )
        spec = SyncSpec(connector_id="c1", connector_name="gmail", org_id="o1")

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher,
        ):
            await run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=SyncLease("c1", "tok", 1), coordinator=manager,
                resync_spec=spec,
            )

        # No ignore_local_task flag any more: end() deregisters the task, so by
        # the time this submits, nothing reports the connector as running and the
        # dispatcher has no reason to decline. The flag existed only because the
        # registry outlived the lease.
        dispatcher.submit.assert_awaited_once_with(spec)
        # Re-issuing before the lease ends would just be declined again.
        assert order == ["end", "resubmit"]

    @pytest.mark.asyncio
    async def test_flag_is_cleared_once_the_resync_is_accepted(self) -> None:
        from app.connectors.core.sync.sync_dispatcher import SubmitResult, SyncSpec

        gp = self._graph_with_pending(True)
        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(return_value=SubmitResult.ACCEPTED)

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher,
        ):
            await run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
                resync_spec=SyncSpec(connector_id="c1", connector_name="g", org_id="o"),
            )

        gp.update_node.assert_awaited_once()
        assert gp.update_node.call_args[0][2] == {"pendingResync": False}

    @pytest.mark.parametrize(
        "result", ["DECLINED_RUNNING", "FAILED"],
    )
    @pytest.mark.asyncio
    async def test_a_resync_that_was_not_accepted_stays_flagged(
        self, result: str
    ) -> None:
        """Clearing first turned every failed publish into a lost request.

        The flag is the only record that a resync is owed, so it has to outlive
        a publish that did not happen — the sweep is what picks it up.
        """
        from app.connectors.core.sync.sync_dispatcher import SubmitResult, SyncSpec

        gp = self._graph_with_pending(True)
        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(return_value=getattr(SubmitResult, result))

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher,
        ):
            await run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
                resync_spec=SyncSpec(connector_id="c1", connector_name="g", org_id="o"),
            )

        gp.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dispatcher_leaves_the_flag_for_the_sweep(self) -> None:
        from app.connectors.core.sync.sync_dispatcher import SyncSpec

        gp = self._graph_with_pending(True)

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=None,
        ):
            await run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
                resync_spec=SyncSpec(connector_id="c1", connector_name="g", org_id="o"),
            )

        gp.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_pending_flag_means_no_resubmit(self) -> None:
        from app.connectors.core.sync.sync_dispatcher import SyncSpec

        gp = self._graph_with_pending(False)
        dispatcher = AsyncMock()

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher,
        ):
            await run_sync_task(
                _connector(), "c1", gp, logging.getLogger("t"),
                lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
                resync_spec=SyncSpec(connector_id="c1", connector_name="g", org_id="o"),
            )

        dispatcher.submit.assert_not_awaited()
        gp.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_a_spec_nothing_is_reissued(self) -> None:
        """Callers that opt out keep exactly today's behaviour."""
        gp = self._graph_with_pending(True)
        await run_sync_task(
            _connector(), "c1", gp, logging.getLogger("t"),
            lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
        )
        gp.update_node.assert_not_awaited()


class TestExternalCancelReachesTheSync:
    """`asyncio.wait` does not cancel the futures it waited on.

    A stop cancels the *outer* task; without an explicit cancel in the finally
    the sync coroutine kept running, so the lease was released and cleanup()
    called while run_sync() was still writing records.
    """

    @pytest.mark.asyncio
    async def test_cancelling_the_task_cancels_run_sync(self) -> None:
        observed = {"cancelled": False, "finished": False}

        async def _run_sync() -> None:
            try:
                await asyncio.sleep(5)
                observed["finished"] = True
            except asyncio.CancelledError:
                observed["cancelled"] = True
                raise

        lease = SyncLease("c1", "tok", 1)
        manager = MagicMock()
        manager.end = AsyncMock()

        task = asyncio.ensure_future(
            run_sync_task(
                _connector(run_sync=_run_sync),
                "c1",
                _graph_provider(),
                logging.getLogger("t"),
                start_status=AppStatus.SYNCING.value,
                lease=lease,
                coordinator=manager,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)

        assert observed["cancelled"] is True
        assert observed["finished"] is False

    @pytest.mark.asyncio
    async def test_a_stopped_sync_does_not_reissue_itself(self) -> None:
        """Otherwise the connector restarts seconds after the user stopped it."""
        graph_provider = _graph_provider()
        graph_provider.get_document = AsyncMock(
            return_value={"id": "c1", "pendingResync": True}
        )
        dispatcher = MagicMock()
        dispatcher.submit = AsyncMock()

        lease = SyncLease("c1", "tok", 1)
        manager = MagicMock()
        manager.end = AsyncMock()

        async def _run_sync() -> None:
            await asyncio.sleep(5)

        with patch(
            "app.connectors.core.sync.sync_dispatcher.get_dispatcher",
            return_value=dispatcher,
        ):
            task = asyncio.ensure_future(
                run_sync_task(
                    _connector(run_sync=_run_sync),
                    "c1",
                    graph_provider,
                    logging.getLogger("t"),
                    start_status=AppStatus.SYNCING.value,
                    lease=lease,
                    coordinator=manager,
                    resync_spec=MagicMock(),
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.05)

        dispatcher.submit.assert_not_awaited()
