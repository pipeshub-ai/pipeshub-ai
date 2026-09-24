"""`POST /api/v1/connectors/{id}/sync/stop`.

The endpoint had no test at all, and two of its branches were wrong in ways that
only showed up against a running box:

  * it answered `IDLE` whether or not the repair write landed, and for a doc it
    had deliberately left at `DELETING`
  * it did nothing to a QUEUED connector — no task to cancel, and QUEUED was
    missing from the repair set — so it cleared the flag, replied "no sync is
    currently running", and the drain started the connector 120s later anyway

What matters here is what the caller is *told* and what the connector is *left
in*, so the assertions are about the response body and the writes, not about
whether cancel was called.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.constants import ConnectorStateKeys


def _request() -> MagicMock:
    req = MagicMock()
    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())
    req.app.container = container
    return req


def _graph(app_doc=None) -> AsyncMock:
    gp = AsyncMock()
    gp.update_node = AsyncMock()
    gp.get_document = AsyncMock(return_value=app_doc)
    gp.batch_upsert_nodes = AsyncMock()
    return gp


async def _call(graph_provider, *, instance=None, running=False, remote_stop=False):
    from app.connectors.api.router import stop_connector_sync

    dispatcher = MagicMock()
    dispatcher.request_stop = AsyncMock(return_value=remote_stop)

    # is_running_here is the local question and stays synchronous; request_stop
    # covers both halves and is awaited.
    stm = MagicMock()
    stm.is_running_here = MagicMock(return_value=running)
    stm.request_stop = AsyncMock(return_value=True)
    # Single-process default: it cannot see peers, and the repair branch is only
    # allowed to act because max_connector_workers() is 1 here. peek_many must
    # be a real empty set -- a bare Mock is truthy and reads as "still running".
    stm.reports_liveness = False
    stm.peek_many = AsyncMock(return_value=set())

    with patch(
        "app.connectors.api.router.get_validated_connector_instance",
        new_callable=AsyncMock,
        return_value=instance if instance is not None else {"status": "IDLE"},
    ), patch("app.connectors.api.router.get_coordinator", return_value=stm), patch(
        "app.connectors.api.router.get_dispatcher",
        return_value=dispatcher,
    ):
        body = await stop_connector_sync("c1", _request(), graph_provider)
    return body, stm, dispatcher


class TestTheQueuedRequestIsAlwaysCleared:
    @pytest.mark.asyncio
    async def test_pending_resync_is_cleared_before_any_branch(self) -> None:
        """Otherwise the finalizer or the sweep re-issues what was just stopped."""
        gp = _graph()
        await _call(gp, running=True)
        first = gp.update_node.await_args_list[0]
        assert first.args[0] == "c1"
        assert first.args[1] == CollectionNames.APPS.value
        # Both flags: a surviving pendingFullSync merged into the next PLAIN
        # sync and silently ran a full one, deleting sync points.
        assert first.args[2] == {
            ConnectorStateKeys.PENDING_RESYNC: False,
            ConnectorStateKeys.PENDING_FULL_SYNC: False,
        }

    @pytest.mark.asyncio
    async def test_a_failed_clear_does_not_fail_the_stop(self) -> None:
        gp = _graph()
        gp.update_node = AsyncMock(side_effect=RuntimeError("graph down"))
        body, _, _ = await _call(gp, running=True)
        assert body["success"] is True


class TestSomethingIsRunning:
    @pytest.mark.asyncio
    async def test_a_local_task_is_asked_to_stop(self) -> None:
        gp = _graph()
        body, stm, _ = await _call(gp, running=True, instance={"status": "SYNCING"})
        assert body["stopped"] is True
        stm.request_stop.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    async def test_a_sync_on_another_process_is_stopped_through_the_lease(self) -> None:
        gp = _graph()
        body, stm, dispatcher = await _call(gp, running=False, remote_stop=True)
        assert body["stopped"] is True
        stm.request_stop.assert_not_awaited()
        dispatcher.request_stop.assert_awaited_once_with("c1")


class TestNothingIsRunning:
    @pytest.mark.asyncio
    async def test_an_idle_connector_is_a_truthful_no_op(self) -> None:
        gp = _graph({"id": "c1", "status": AppStatus.IDLE.value})
        body, _, _ = await _call(gp)
        assert body["success"] is True
        assert body["stopped"] is False

    @pytest.mark.asyncio
    async def test_a_queued_connector_is_returned_to_idle(self) -> None:
        """The bug: QUEUED was absent from the repair set, so stop cleared the
        flag, reported nothing running, and the drain ran it 120s later."""
        gp = _graph({"id": "c1", "status": AppStatus.QUEUED.value})
        body, _, _ = await _call(gp)
        assert body["status"] == AppStatus.IDLE.value
        statuses = [
            c.args[0][0].get("status")
            for c in gp.batch_upsert_nodes.await_args_list
            if c.args and c.args[0]
        ]
        assert AppStatus.IDLE.value in statuses

    @pytest.mark.asyncio
    async def test_a_stale_syncing_status_is_repaired(self) -> None:
        gp = _graph({"id": "c1", "status": AppStatus.SYNCING.value})
        body, _, _ = await _call(gp)
        assert body["status"] == AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_a_deleting_connector_keeps_deleting(self) -> None:
        """It is mid-deletion, not mid-sync. Reporting IDLE would invite a retry."""
        gp = _graph({"id": "c1", "status": "DELETING", "isLocked": True})
        body, _, _ = await _call(gp)
        assert body["status"] == "DELETING"

    @pytest.mark.asyncio
    async def test_a_failed_repair_is_not_reported_as_idle(self) -> None:
        """write_app_status swallows its own exceptions by design, so without
        this the caller is told the connector is usable while it stays wedged."""
        gp = _graph({"id": "c1", "status": AppStatus.SYNCING.value})
        gp.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("graph down"))
        body, _, _ = await _call(gp)
        assert body["stopped"] is False
        assert body["status"] != AppStatus.IDLE.value

    @pytest.mark.asyncio
    async def test_a_sync_that_started_during_the_read_is_stopped_not_repaired(
        self,
    ) -> None:
        """Reading the doc yields the loop, so a queued event can be consumed in
        the gap. Repairing then would mark a just-started sync IDLE."""
        from app.connectors.api.router import stop_connector_sync

        gp = _graph({"id": "c1", "status": AppStatus.SYNCING.value})
        stm = MagicMock()
        stm.is_running_here = MagicMock(side_effect=[False, True])
        stm.request_stop = AsyncMock(return_value=True)
        dispatcher = MagicMock()
        dispatcher.request_stop = AsyncMock(return_value=False)

        with patch(
            "app.connectors.api.router.get_validated_connector_instance",
            new_callable=AsyncMock,
            return_value={"status": "SYNCING"},
        ), patch("app.connectors.api.router.get_coordinator", return_value=stm), patch(
            "app.connectors.api.router.get_dispatcher",
            return_value=dispatcher,
        ):
            body = await stop_connector_sync("c1", _request(), gp)

        assert body["stopped"] is True
        stm.request_stop.assert_awaited_once_with("c1")
        gp.batch_upsert_nodes.assert_not_awaited()
