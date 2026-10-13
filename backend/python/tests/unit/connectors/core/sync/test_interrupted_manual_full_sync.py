"""A MANUAL connector resumes a full sync that the service itself cut short.

MANUAL means "no schedule", not "abandon a sync someone started". Before this,
a restart or crash during a MANUAL connector's full sync left it IDLE with
pendingFullSync set, and boot skipped it like any other MANUAL connector: the
records the cut-short run had not relinked stayed unreadable until someone
happened to press sync.

pendingFullSync alone cannot be the signal, because it also means "the filters
changed" and "someone stopped a full sync", and neither of those should start a
MANUAL connector on its own. So the run that is cut short by shutdown, and the
boot sweep that finds one killed outright, also record the request as still
owed (pendingResync). Boot resumes a MANUAL connector only when it carries both,
and only when it can run at all: not disabled, not signed out, not queued (the
startup drain owns those) and not being deleted.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppStatus
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.sync.sync_coordinator import Admission, SyncLease
from app.connectors.core.sync.sync_dispatcher import (
    SubmitResult,
    SyncEventDispatcher,
    SyncSpec,
)
from app.connectors.core.sync.sync_runner import run_sync_task

_LOG = logging.getLogger("test")
_FULL = ConnectorStateKeys.PENDING_FULL_SYNC
_OWED = ConnectorStateKeys.PENDING_RESYNC

# What a full sync cut short by a restart or a crash leaves on the app doc.
_INTERRUPTED = {_FULL: True, _OWED: True}


def _app(**fields: object) -> dict:
    return {
        "_key": "c1",
        "type": "Slack",
        "scope": "personal",
        "isActive": True,
        "isAuthenticated": True,
        "status": AppStatus.IDLE.value,
        **fields,
    }


async def _boot(
    app: dict, strategy: str = "MANUAL", *, current: dict | None = None
) -> tuple[AsyncMock, MagicMock]:
    """Run the real boot resume for one connector.

    Only the edges are mocked: the graph, the connector's init, the dispatcher
    (what a resume publishes to) and the coordinator (what an in-place start
    would take a lease from). Returns those last two so a test can assert that
    nothing was published and nothing was started.

    ``current`` is the app doc as a re-read finds it after init, when something
    changed it in the meantime; by default it is unchanged.
    """
    from app.connectors_main import resume_sync_services

    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "o1"}])
    graph.get_org_apps = AsyncMock(return_value=[app])
    graph.get_document = AsyncMock(return_value=current if current is not None else app)
    graph.get_users = AsyncMock(return_value=[{"id": "u1"}])
    data_store = MagicMock()
    data_store.graph_provider = graph

    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value={"sync": {"selectedStrategy": strategy}}
    )

    container = MagicMock()
    container.logger = MagicMock(return_value=_LOG)
    container.connectors_map = {}

    dispatcher = AsyncMock()
    dispatcher.submit = AsyncMock(return_value=SubmitResult.ACCEPTED)
    coordinator = MagicMock()
    coordinator.begin = AsyncMock(return_value=(Admission.GRANTED, SyncLease("c1", "t", 1)))
    coordinator.spawn = AsyncMock()

    with patch(
        "app.connectors_main.scope_org_resources",
        AsyncMock(return_value=(config_service, data_store)),
    ), patch.object(
        ConnectorFactory, "initialize_connector", AsyncMock(return_value=MagicMock())
    ), patch(
        "app.connectors.core.factory.connector_factory.get_coordinator",
        return_value=coordinator,
    ), patch(
        "app.connectors_main.get_dispatcher", return_value=dispatcher
    ), patch("app.connectors_main.sync_executor_enabled", return_value=False):
        await resume_sync_services(container, data_store)

    return dispatcher, coordinator


def _started_nothing(dispatcher: AsyncMock, coordinator: MagicMock) -> None:
    dispatcher.submit.assert_not_awaited()
    coordinator.begin.assert_not_awaited()
    coordinator.spawn.assert_not_awaited()


class TestBootResumesAnInterruptedManualFullSync:
    @pytest.mark.asyncio
    async def test_a_manual_connector_cut_short_mid_full_sync_resumes_at_boot(self) -> None:
        """The case this change is for. Published, not started in place: only
        the event path runs a full sync (it merges pendingFullSync)."""
        dispatcher, coordinator = await _boot(_app(**_INTERRUPTED))

        dispatcher.submit.assert_awaited_once()
        spec = dispatcher.submit.await_args.args[0]
        assert spec.connector_id == "c1"
        # Marked, so the start handler checks it is still owed when consumed.
        assert spec.boot_resume is True
        coordinator.spawn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_scheduled_connector_cut_short_still_resumes(self) -> None:
        dispatcher, _ = await _boot(_app(**_INTERRUPTED), strategy="SCHEDULED")
        dispatcher.submit.assert_awaited_once()
        # A scheduled connector resumes whatever its marks say; nothing to recheck.
        assert dispatcher.submit.await_args.args[0].boot_resume is False


class TestBootLeavesOtherManualConnectorsAlone:
    @pytest.mark.asyncio
    async def test_a_manual_connector_with_no_mark_does_not_start(self) -> None:
        _started_nothing(*await _boot(_app()))

    @pytest.mark.asyncio
    async def test_a_filter_change_alone_does_not_start_a_manual_connector(self) -> None:
        """Saving new sync filters sets pendingFullSync and starts nothing. The
        full sync is owed to the next sync someone starts, not to the next boot."""
        _started_nothing(*await _boot(_app(**{_FULL: True})))

    @pytest.mark.asyncio
    async def test_a_full_sync_someone_stopped_does_not_start_at_boot(self) -> None:
        """Stopping a full sync leaves pendingFullSync (the edges it removed are
        still missing) but clears pendingResync first, so the stop holds."""
        _started_nothing(*await _boot(_app(**{_FULL: True, _OWED: False})))

    @pytest.mark.asyncio
    async def test_a_stop_that_lands_while_the_connector_initialises_holds(self) -> None:
        """The boot traversal reads the app doc before the connector's init,
        which can take seconds while the API already serves requests. A stop in
        that window clears pendingResync in the database; resuming from the
        earlier read would start the sync the person just stopped."""
        _started_nothing(*await _boot(_app(**_INTERRUPTED), current=_app(**{_FULL: True, _OWED: False})))

    @pytest.mark.asyncio
    async def test_a_queued_manual_connector_is_left_to_the_startup_drain(self) -> None:
        """drain_queued_syncs re-issues it after this; publishing here as well
        would run it twice back to back."""
        _started_nothing(*await _boot(_app(**_INTERRUPTED, status=AppStatus.QUEUED.value)))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "state",
        [
            {"isActive": False},
            {"isAuthenticated": False},
            {"status": "DELETING"},
        ],
        ids=["disabled", "signed-out", "deleting"],
    )
    async def test_a_manual_connector_that_cannot_run_does_not_start(
        self, state: dict
    ) -> None:
        """The same states the event path refuses
        (event_service._start_sync_with_lease). get_org_apps already drops
        disabled connectors; this pins the second guard."""
        _started_nothing(*await _boot(_app(**_INTERRUPTED, **state)))


async def _never_ending(started: asyncio.Event) -> None:
    started.set()
    await asyncio.sleep(3600)


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.batch_upsert_nodes = AsyncMock()
    gp.update_node = AsyncMock()
    gp.get_document = AsyncMock(return_value={"id": "c1", "status": "FULL_SYNCING"})
    return gp


def _idle_write(gp: AsyncMock) -> dict:
    idle = [
        c.args[2]
        for c in gp.update_node.await_args_list
        if c.args[2].get("status") == AppStatus.IDLE.value
    ]
    assert len(idle) == 1, gp.update_node.await_args_list
    return idle[0]


async def _cut_short(
    *, shutting_down: bool, user_stop: bool, start_status: str = AppStatus.FULL_SYNCING.value
) -> AsyncMock:
    """Start a sync that never ends, then end it the way a restart or a user does."""
    started = asyncio.Event()
    gp = _graph_provider()
    coordinator = AsyncMock()
    coordinator.shutting_down = shutting_down
    lease = SyncLease("c1", "tok", 1)
    connector = MagicMock()
    connector.run_sync = lambda: _never_ending(started)

    task = asyncio.create_task(
        run_sync_task(
            connector, "c1", gp, _LOG,
            start_status=start_status, lease=lease, coordinator=coordinator,
        )
    )
    await started.wait()
    if user_stop:
        lease.stop_requested.set()
    if shutting_down:
        # What LocalSyncCoordinator.cancel_all does to every running sync.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    return gp


class TestTheRunRecordsWhyItStopped:
    @pytest.mark.asyncio
    async def test_a_full_sync_cut_short_by_shutdown_is_still_owed(self) -> None:
        idle = _idle_write(await _cut_short(shutting_down=True, user_stop=False))
        assert idle.get(_FULL) is True
        assert idle.get(_OWED) is True

    @pytest.mark.asyncio
    async def test_a_full_sync_someone_stopped_is_not_owed(self) -> None:
        idle = _idle_write(await _cut_short(shutting_down=False, user_stop=True))
        assert idle.get(_FULL) is True
        assert _OWED not in idle

    @pytest.mark.asyncio
    async def test_a_stop_still_winding_down_at_shutdown_is_not_owed(self) -> None:
        """The person asked for it to stop; a restart a moment later must not
        undo that."""
        idle = _idle_write(await _cut_short(shutting_down=True, user_stop=True))
        assert _OWED not in idle

    @pytest.mark.asyncio
    async def test_an_incremental_sync_cut_short_by_shutdown_is_not_owed(self) -> None:
        idle = _idle_write(
            await _cut_short(
                shutting_down=True, user_stop=False, start_status=AppStatus.SYNCING.value
            )
        )
        assert _FULL not in idle
        assert _OWED not in idle

    @pytest.mark.asyncio
    async def test_a_full_sync_that_failed_is_not_owed(self) -> None:
        """A run that raised is not one the service cut short. It stays owed a
        full sync, but a MANUAL connector does not retry it at every boot."""

        async def boom() -> None:
            raise RuntimeError("source went away")

        gp = _graph_provider()
        connector = MagicMock()
        connector.run_sync = boom
        coordinator = AsyncMock()
        coordinator.shutting_down = False
        with pytest.raises(RuntimeError):
            await run_sync_task(
                connector, "c1", gp, _LOG,
                start_status=AppStatus.FULL_SYNCING.value,
                lease=SyncLease("c1", "tok", 1), coordinator=coordinator,
            )
        await asyncio.sleep(0)
        idle = _idle_write(gp)
        assert idle.get(_FULL) is True
        assert _OWED not in idle


class TestTheBootSweep:
    @pytest.mark.asyncio
    async def test_a_full_sync_killed_outright_is_still_owed(self) -> None:
        """No finalizer ran (OOM, SIGKILL), so the doc still reads FULL_SYNCING."""
        from app.connectors_main import reset_stale_sync_state

        docs = [
            {"id": "full", "status": AppStatus.FULL_SYNCING.value},
            {"id": "incr", "status": AppStatus.SYNCING.value},
        ]

        async def _by_field(
            collection: str, field: str, values: list[str], fields: list[str]
        ) -> list[dict]:
            if field == "status":
                return [{k: d[k] for k in fields if k in d} for d in docs]
            return []

        gp = AsyncMock()
        gp.get_nodes_by_field_in = AsyncMock(side_effect=_by_field)
        gp.batch_upsert_nodes = AsyncMock()
        coordinator = MagicMock()
        coordinator.reports_liveness = True
        coordinator.peek_many = AsyncMock(return_value=set())
        with patch("app.connectors_main.get_coordinator", return_value=coordinator), patch(
            "app.connectors_main.max_connector_workers", return_value=1
        ), patch("app.connectors_main.sync_executor_enabled", return_value=False):
            await reset_stale_sync_state(gp, _LOG)

        written = {
            n["id"]: n for c in gp.batch_upsert_nodes.await_args_list for n in c.args[0]
        }
        assert written["full"].get(_FULL) is True
        assert written["full"].get(_OWED) is True
        assert _OWED not in written["incr"]


_GONE = object()


def _app_reads(reads: list[object], default: object) -> AsyncMock:
    """An app-doc reader that fails the way the real providers do.

    Each scripted read is a doc, ``_GONE`` (no such document: None) or an
    exception. A failed query answers None, exactly like a missing doc, unless
    the caller passes raise_on_error=True; only then does it raise
    (graph_db_provider.get_document). Once the script runs out, every read
    returns ``default``.
    """
    script = list(reads)

    async def get_document(*_args: object, raise_on_error: bool = False, **_kw: object) -> object:
        item = script.pop(0) if script else default
        if isinstance(item, Exception):
            if raise_on_error:
                raise item
            return None
        return None if item is _GONE else item

    return AsyncMock(side_effect=get_document)


def _asked_to_raise(graph: AsyncMock, n: int) -> bool:
    """Whether the n-th app-doc read passed raise_on_error=True."""
    return graph.get_document.await_args_list[n].kwargs.get("raise_on_error") is True


async def _published_payload(*, boot_resume: bool) -> dict:
    """What the real dispatcher puts on the sync topic for this spec."""
    producer = AsyncMock()
    dispatcher = SyncEventDispatcher(_LOG, producer)
    with patch("app.connectors.core.sync.sync_dispatcher.get_coordinator", return_value=None):
        result = await dispatcher.submit(
            SyncSpec(connector_id="c1", connector_name="slack", org_id="o1", boot_resume=boot_resume)
        )
    assert result is SubmitResult.ACCEPTED
    return producer.send_message.await_args.kwargs["message"]["payload"]


async def _consume(
    payload: dict,
    doc: dict,
    *,
    admission: Admission = Admission.GRANTED,
    reads: list[object] | None = None,
    acked: bool = True,
) -> tuple[AsyncMock, MagicMock]:
    """Run the real start handler on one event, against the app doc as it is now.

    ``reads`` scripts successive app-doc reads (see ``_app_reads``); by default
    every read returns ``doc``. ``acked`` is what the handler must return: False
    asks the consumer to redeliver. Returns the graph (whose sync-point and edge
    deletes are the full-sync prep, and whose update_node holds the queue
    write) and the coordinator (whose spawn is the sync starting).
    """
    from app.connectors.services.event_service import EventService

    graph = AsyncMock()
    graph.get_document = _app_reads(reads or [], doc)
    graph.update_node = AsyncMock()
    graph.batch_upsert_nodes = AsyncMock()
    graph.delete_sync_points_by_connector_id = AsyncMock(return_value=(1, True))
    graph.delete_connector_sync_edges = AsyncMock(return_value=(1, True))
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    svc = EventService(MagicMock(spec=logging.Logger), container, graph)

    async def _spawn(_lease: SyncLease, coro: object) -> MagicMock:
        coro.close()  # the sync itself is not under test here
        return MagicMock(name="task")

    lease = SyncLease("c1", "tok", 1)
    coordinator = MagicMock()
    coordinator.begin = AsyncMock(
        return_value=(admission, lease if admission is Admission.GRANTED else None)
    )
    coordinator.spawn = AsyncMock(side_effect=_spawn)
    coordinator.end = AsyncMock()
    with patch(
        "app.connectors.services.event_service.get_coordinator", return_value=coordinator
    ), patch.object(svc, "_ensure_connector", AsyncMock(return_value=MagicMock())):
        assert await svc._handle_start_sync("slack", payload) is acked
    return graph, coordinator


def _queue_writes(graph: AsyncMock) -> list[dict]:
    return [
        c.args[2]
        for c in graph.update_node.await_args_list
        if c.args[2].get(_OWED) is True or c.args[2].get("status") == AppStatus.QUEUED.value
    ]


# The app doc after someone pressed stop while the boot event waited for the
# consumers to start: /sync/stop found no lease to stop and cleared pendingResync.
_STOPPED_SINCE_BOOT = _app(**{_FULL: True, _OWED: False})


class TestTheBootResumeIsCheckedAgainWhenConsumed:
    """Boot publishes the resume before the consumers start, and joining the
    consumer group can take tens of seconds. A stop in that gap finds no lease
    and only clears pendingResync, which the start path does not otherwise
    read, so the event itself has to carry "check this is still owed"."""

    @pytest.mark.asyncio
    async def test_a_stop_after_the_resume_was_published_holds(self) -> None:
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True), _STOPPED_SINCE_BOOT
        )
        graph.delete_sync_points_by_connector_id.assert_not_awaited()
        graph.delete_connector_sync_edges.assert_not_awaited()
        coordinator.spawn.assert_not_awaited()
        # Dropped before admission, so it never took a slot.
        coordinator.begin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_stop_during_connector_init_holds(self) -> None:
        """Admitted while still owed, stopped while _ensure_connector ran (OAuth
        and HTTP, seconds): the prep must still not run."""
        owed = _app(**_INTERRUPTED)
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            owed,
            # before admission, at the top of the start, then after init
            reads=[owed, owed, _STOPPED_SINCE_BOOT],
        )
        graph.delete_sync_points_by_connector_id.assert_not_awaited()
        coordinator.spawn.assert_not_awaited()
        coordinator.end.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_resume_still_owed_runs_in_full(self) -> None:
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True), _app(**_INTERRUPTED)
        )
        graph.delete_sync_points_by_connector_id.assert_awaited_once()
        coordinator.spawn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unmarked_resync_still_runs(self) -> None:
        """The Sync button publishes the same event without pendingResync set,
        so only the boot resume is checked against it."""
        payload = await _published_payload(boot_resume=False)
        assert "bootResume" not in payload
        graph, coordinator = await _consume(payload, _STOPPED_SINCE_BOOT)
        graph.delete_sync_points_by_connector_id.assert_awaited_once()
        coordinator.spawn.assert_awaited_once()


class TestAQueuedBootResumeIsCheckedFirst:
    """At the concurrency limit the event is parked instead of started, and
    parking writes pendingResync back. Checked before admission, a stop that
    landed while the event waited cannot be undone by the queue."""

    @pytest.mark.asyncio
    async def test_a_stopped_resume_at_capacity_is_not_queued(self) -> None:
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            _STOPPED_SINCE_BOOT,
            admission=Admission.AT_CAPACITY,
        )
        assert _queue_writes(graph) == []
        graph.delete_sync_points_by_connector_id.assert_not_awaited()
        coordinator.spawn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_resume_still_owed_at_capacity_is_queued(self) -> None:
        graph, _ = await _consume(
            await _published_payload(boot_resume=True),
            _app(**_INTERRUPTED),
            admission=Admission.AT_CAPACITY,
        )
        assert any(
            w.get("status") == AppStatus.QUEUED.value and w.get(_OWED) is True
            for w in _queue_writes(graph)
        )


class TestAFailedReadNeitherDropsNorStartsTheResume:
    """A read error is not an answer. Dropping the event would lose a resume
    that is owed, and running it could start one someone stopped, so the
    handler asks for the event again."""

    @pytest.mark.asyncio
    async def test_a_failed_read_before_admission_redelivers(self) -> None:
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            _app(**_INTERRUPTED),
            reads=[RuntimeError("db blip")],
            acked=False,
        )
        assert _asked_to_raise(graph, 0)
        coordinator.begin.assert_not_awaited()
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_connector_gone_before_admission_is_dropped(self) -> None:
        """None still means "no such document": nothing to resume, nothing to
        redeliver."""
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            _app(**_INTERRUPTED),
            reads=[_GONE],
        )
        coordinator.begin.assert_not_awaited()
        graph.update_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_after_init_redelivers_and_deletes_nothing(self) -> None:
        owed = _app(**_INTERRUPTED)
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            owed,
            # before admission, at the top of the start, then after init
            reads=[owed, owed, RuntimeError("db blip")],
            acked=False,
        )
        assert _asked_to_raise(graph, 2)
        graph.delete_sync_points_by_connector_id.assert_not_awaited()
        coordinator.spawn.assert_not_awaited()
        coordinator.end.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failed_read_at_the_top_of_the_start_still_runs_in_full(self) -> None:
        """The event carries fullSync false, so only the app doc says the resume
        is a full sync. That read does not ask to raise, so a failed one answers
        None; the owed check after init reads the doc again and decides."""
        owed = _app(**_INTERRUPTED)
        graph, coordinator = await _consume(
            await _published_payload(boot_resume=True),
            owed,
            # before admission, at the top of the start, then after init
            reads=[owed, RuntimeError("db blip"), owed],
        )
        assert not _asked_to_raise(graph, 1)
        # The prep runs in full: it deletes sync points. Since #4119 it no longer
        # deletes edges up front; the full sync unlinks what it missed at the end.
        graph.delete_sync_points_by_connector_id.assert_awaited_once()
        graph.delete_connector_sync_edges.assert_not_awaited()
        coordinator.spawn.assert_awaited_once()
        # Consumed, so the next sync does not run the full-sync prep again.
        assert {_FULL: False} in [c.args[2] for c in graph.update_node.await_args_list]

    @pytest.mark.asyncio
    async def test_a_failed_read_at_capacity_redelivers_without_queueing(self) -> None:
        owed = _app(**_INTERRUPTED)
        graph, _ = await _consume(
            await _published_payload(boot_resume=True),
            owed,
            admission=Admission.AT_CAPACITY,
            reads=[owed, RuntimeError("db blip")],
            acked=False,
        )
        assert _asked_to_raise(graph, 1)
        assert _queue_writes(graph) == []


class TestAFailedReadAtBoot:
    async def _boot_with_reread(self, reread: object) -> tuple[AsyncMock, AsyncMock]:
        from app.connectors_main import resume_sync_services

        app = _app(**_INTERRUPTED)
        graph = AsyncMock()
        graph.get_all_orgs = AsyncMock(return_value=[{"_key": "o1"}])
        graph.get_org_apps = AsyncMock(return_value=[app])
        graph.get_users = AsyncMock(return_value=[{"id": "u1"}])
        graph.get_document = _app_reads([reread], app)
        data_store = MagicMock()
        data_store.graph_provider = graph
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {"selectedStrategy": "MANUAL"}})
        container = MagicMock()
        container.logger = MagicMock(return_value=_LOG)
        container.connectors_map = {}
        dispatcher = AsyncMock()
        dispatcher.submit = AsyncMock(return_value=SubmitResult.ACCEPTED)

        with patch(
            "app.connectors_main.scope_org_resources",
            AsyncMock(return_value=(config_service, data_store)),
        ), patch.object(
            ConnectorFactory, "initialize_connector", AsyncMock(return_value=MagicMock())
        ), patch("app.connectors_main.get_dispatcher", return_value=dispatcher), patch(
            "app.connectors_main.sync_executor_enabled", return_value=False
        ):
            await resume_sync_services(container, data_store)
        return graph, dispatcher

    @pytest.mark.asyncio
    async def test_the_resume_is_still_published_marked_for_the_handler_to_check(self) -> None:
        """Not publishing would leave both marks set and nothing to act on them
        until the next boot. The event is marked, so the start handler reads
        the doc again before it queues or deletes anything."""
        graph, dispatcher = await self._boot_with_reread(RuntimeError("db blip"))
        assert _asked_to_raise(graph, 0)
        dispatcher.submit.assert_awaited_once()
        assert dispatcher.submit.await_args.args[0].boot_resume is True

    @pytest.mark.asyncio
    async def test_a_connector_gone_since_the_traversal_is_not_resumed(self) -> None:
        _, dispatcher = await self._boot_with_reread(_GONE)
        dispatcher.submit.assert_not_awaited()
