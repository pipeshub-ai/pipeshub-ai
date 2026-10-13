"""A full sync that does not finish leaves a full sync owed.

A full sync starts by deleting the connector's sync points and its permission
edges (BELONGS_TO, INHERIT_PERMISSIONS, PERMISSION, ...), then links each record
again as the connector re-reads it. Cut short — a container restart cancels it,
a crash kills it, a user stops it — the records it had not reached yet keep
their recordGroupId but lose the edges the read path checks, so their own owner
gets 403 opening them.

Every route that ended such a run used to write a bare IDLE: the run's own
finalizer (which a graceful shutdown reaches by cancelling the task), the boot
sweep (for a run killed outright) and the stop route's stale-state repair. The
connector then looked healthy, a scheduled connector resumed with an incremental
sync that does not revisit unchanged records, and a MANUAL one did nothing at
all. Each of those writes now sets pendingFullSync with the IDLE, which the boot
resume and the start path already turn into a full sync.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_coordinator import SyncLease
from app.connectors.core.sync.sync_runner import run_sync_task

_LOG = logging.getLogger("test")
_OWED = ConnectorStateKeys.PENDING_FULL_SYNC


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    gp.update_node = AsyncMock()
    # A real app doc: a bare mock answers every .get() with a truthy Mock.
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "FULL_SYNCING"})
    return gp


def _final_writes(gp: AsyncMock) -> list[dict]:
    """Every update_node payload after the start status was written."""
    return [c.args[2] for c in gp.update_node.await_args_list]


def _idle_write(gp: AsyncMock) -> dict:
    idle = [w for w in _final_writes(gp) if w.get("status") == AppStatus.IDLE.value]
    assert len(idle) == 1, _final_writes(gp)
    return idle[0]


def _connector(run_sync: Callable[[], Awaitable[None]]) -> MagicMock:
    c = MagicMock()
    c.run_sync = run_sync
    return c


async def _never_ending(started: asyncio.Event) -> None:
    started.set()
    await asyncio.sleep(3600)


class TestTheRunItself:
    @pytest.mark.asyncio
    async def test_a_full_sync_cancelled_by_shutdown_owes_a_full_sync(self) -> None:
        """The restart case. Shutdown cancels every running sync and waits for
        the finalizer, so the connector comes back IDLE — and until now, nothing
        else said the full sync it was running never finished."""
        started = asyncio.Event()
        gp = _graph_provider()
        manager = AsyncMock()
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(lambda: _never_ending(started)), "c1", gp, _LOG,
                start_status=AppStatus.FULL_SYNCING.value,
                lease=lease, coordinator=manager,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        assert _idle_write(gp).get(_OWED) is True
        manager.end.assert_awaited_once_with(lease)

    @pytest.mark.asyncio
    async def test_a_full_sync_that_raised_owes_a_full_sync(self) -> None:
        async def boom() -> None:
            raise RuntimeError("source went away")

        gp = _graph_provider()
        with pytest.raises(RuntimeError):
            await run_sync_task(
                _connector(boom), "c1", gp, _LOG,
                start_status=AppStatus.FULL_SYNCING.value,
            )
        assert _idle_write(gp).get(_OWED) is True

    @pytest.mark.asyncio
    async def test_a_stopped_full_sync_owes_a_full_sync(self) -> None:
        """A stop returns from the run without raising, so it has to be read
        from the lease, or a half-done run would count as finished."""
        started = asyncio.Event()
        gp = _graph_provider()
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(lambda: _never_ending(started)), "c1", gp, _LOG,
                start_status=AppStatus.FULL_SYNCING.value,
                lease=lease, coordinator=AsyncMock(),
            )
        )
        await started.wait()
        lease.stop_requested.set()
        await asyncio.wait_for(task, timeout=1)

        assert _idle_write(gp).get(_OWED) is True

    @pytest.mark.asyncio
    async def test_a_full_sync_whose_lease_was_lost_still_records_the_debt(self) -> None:
        """Its status belongs to the new owner now, so no IDLE — but whatever
        runs next must still be a full sync."""
        started = asyncio.Event()
        gp = _graph_provider()
        lease = SyncLease("c1", "tok", 1)

        task = asyncio.create_task(
            run_sync_task(
                _connector(lambda: _never_ending(started)), "c1", gp, _LOG,
                start_status=AppStatus.FULL_SYNCING.value,
                lease=lease, coordinator=AsyncMock(),
            )
        )
        await started.wait()
        lease.lost.set()
        await asyncio.wait_for(task, timeout=1)

        writes = _final_writes(gp)
        assert not any("status" in w for w in writes)
        assert {_OWED: True} in writes

    @pytest.mark.asyncio
    async def test_a_full_sync_that_finished_owes_nothing(self) -> None:
        gp = _graph_provider()
        await run_sync_task(
            _connector(AsyncMock()), "c1", gp, _LOG,
            start_status=AppStatus.FULL_SYNCING.value,
            lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
        )
        assert _OWED not in _idle_write(gp)

    @pytest.mark.asyncio
    async def test_a_cancelled_incremental_sync_owes_nothing(self) -> None:
        """An incremental run deletes nothing up front, and its checkpoints are
        written as it goes; the next incremental sync carries on from them."""
        started = asyncio.Event()
        gp = _graph_provider()
        task = asyncio.create_task(
            run_sync_task(
                _connector(lambda: _never_ending(started)), "c1", gp, _LOG,
                lease=SyncLease("c1", "tok", 1), coordinator=AsyncMock(),
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        assert _OWED not in _idle_write(gp)


def _stuck_graph(docs: list[dict]) -> AsyncMock:
    gp = AsyncMock()

    async def _by_field(
        collection: str, field: str, values: list[str], fields: list[str]
    ) -> list[dict]:
        if field == "status":
            return [{k: d[k] for k in fields if k in d} for d in docs]
        return []

    gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
    gp.batch_upsert_nodes = AsyncMock()
    return gp


async def _sweep(gp: AsyncMock) -> dict[str, dict]:
    from app.connectors_main import reset_stale_sync_state

    manager = MagicMock()
    manager.reports_liveness = True
    manager.peek_many = AsyncMock(return_value=set())
    with patch("app.connectors_main.get_coordinator", return_value=manager), patch(
        "app.connectors_main.max_connector_workers", return_value=1
    ), patch("app.connectors_main.sync_executor_enabled", return_value=False):
        await reset_stale_sync_state(gp, _LOG)
    return {
        n["id"]: n
        for c in gp.batch_upsert_nodes.await_args_list
        for n in c.args[0]
    }


class TestTheBootSweep:
    @pytest.mark.asyncio
    async def test_a_full_sync_killed_outright_owes_a_full_sync(self) -> None:
        """No finalizer ran (OOM, SIGKILL), so the doc still reads FULL_SYNCING."""
        gp = _stuck_graph([
            {"id": "full", "status": AppStatus.FULL_SYNCING.value},
            {"id": "incr", "status": AppStatus.SYNCING.value},
        ])
        written = await _sweep(gp)

        assert written["full"]["status"] == AppStatus.IDLE.value
        assert written["full"].get(_OWED) is True
        assert written["incr"]["status"] == AppStatus.IDLE.value
        assert _OWED not in written["incr"]


class TestTheBootResume:
    @pytest.mark.asyncio
    async def test_an_owed_full_sync_is_handed_to_the_event_path_at_boot(self) -> None:
        """What the flag buys a scheduled connector: boot publishes a resync
        instead of starting an incremental run in place, and the event path
        (event_service._start_sync_with_lease) merges pendingFullSync into a
        full sync. A MANUAL connector is skipped here, as before, and keeps the
        flag for the next sync someone starts."""
        from app.connectors_main import resume_sync_services

        graph = AsyncMock()
        graph.get_all_orgs = AsyncMock(return_value=[{"_key": "o1"}])
        graph.get_org_apps = AsyncMock(return_value=[
            {"_key": "c1", "type": "Slack", "scope": "personal", _OWED: True},
        ])
        graph.get_users = AsyncMock(return_value=[{"id": "u1"}])
        data_store = MagicMock()
        data_store.graph_provider = graph

        container = MagicMock()
        container.logger = MagicMock(return_value=_LOG)
        container.connectors_map = {}

        create = AsyncMock(return_value=MagicMock())
        publish = AsyncMock()
        with patch(
            "app.connectors_main.scope_org_resources",
            AsyncMock(return_value=(AsyncMock(), data_store)),
        ), patch(
            "app.connectors_main.ConnectorFactory.create_and_start_sync", create
        ), patch(
            "app.connectors_main._publish_startup_resync", publish
        ), patch("app.connectors_main.sync_executor_enabled", return_value=False):
            await resume_sync_services(container, data_store)

        assert create.await_args.kwargs["start_sync"] is False
        publish.assert_awaited_once()
        assert publish.await_args.kwargs["connector_id"] == "c1"


class TestTheStopRouteRepair:
    @pytest.mark.asyncio
    async def test_repairing_a_stale_full_sync_owes_a_full_sync(self) -> None:
        from app.connectors.api.router import stop_connector_sync

        gp = AsyncMock()
        gp.update_node = AsyncMock()
        gp.get_document = AsyncMock(
            return_value={"id": "c1", "status": AppStatus.FULL_SYNCING.value}
        )
        gp.batch_upsert_nodes = AsyncMock()

        request = MagicMock()
        request.app.container.logger = MagicMock(return_value=MagicMock())
        stm = MagicMock()
        stm.is_running_here = MagicMock(return_value=False)
        stm.reports_liveness = False
        dispatcher = MagicMock()
        dispatcher.request_stop = AsyncMock(return_value=False)

        with patch(
            "app.connectors.api.router.get_validated_connector_instance",
            new_callable=AsyncMock,
            return_value={"status": AppStatus.FULL_SYNCING.value},
        ), patch("app.connectors.api.router.get_coordinator", return_value=stm), patch(
            "app.connectors.api.router.get_dispatcher", return_value=dispatcher
        ), patch("app.connectors.api.router.max_connector_workers", return_value=1), patch(
            "app.connectors.api.router.sync_executor_enabled", return_value=False
        ):
            body = await stop_connector_sync("c1", request, gp)

        assert body["status"] == AppStatus.IDLE.value
        written = gp.batch_upsert_nodes.await_args.args[0][0]
        assert written["status"] == AppStatus.IDLE.value
        assert written.get(_OWED) is True
