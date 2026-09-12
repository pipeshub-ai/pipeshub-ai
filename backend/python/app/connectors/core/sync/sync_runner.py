"""Single body for every connector sync task, regardless of trigger path.

Both the event path (EventService._handle_start_sync) and the startup path
(ConnectorFactory.create_and_start_sync) wrap run_sync() in this coroutine, so
the SYNCING/FULL_SYNCING start status and the IDLE end status are written in
exactly one place — inside the task itself. Writing the start status as the
task's first act (rather than before spawning) is what keeps the stored status
truthful: a task cancelled by a newer one can no longer overwrite the newer
task's status, and startup-resumed syncs become visible.
"""

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync.sync_coordinator import SyncCoordinator, SyncLease, _now_ms
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import BaseConnector

# Strong refs to detached cleanup tasks; the loop holds only weak ones.
_cleanup_tasks: set[asyncio.Task] = set()


async def join_cleanup_tasks(timeout: float = 30.0) -> int:
    """Wait for finalizers that a second cancel detached.

    `run_sync_task` awaits its finalizer under `asyncio.shield`, so ONE cancel
    still waits — but the timeout-driven second cancel in a shutdown drain
    breaks that await and leaves `_finalize` running alone. It is then racing
    teardown: it still has to write IDLE, release the lease and publish. Closing
    Redis and the producer first is what leaves connectors SYNCING with a held
    lease after every restart.

    Returns how many were still outstanding.
    """
    pending = [t for t in _cleanup_tasks if not t.done()]
    if not pending:
        return 0
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=timeout
        )
    return len(pending)


async def write_app_status(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    status: str,
    *,
    is_locked: bool | None = None,
) -> bool:
    """Best-effort status write; a DB blip must never fail the sync itself.

    Returns whether the write landed, so a caller that reports the status back
    to a user can tell the difference between repaired and merely attempted.
    """
    payload: dict[str, Any] = {
        "id": connector_id,
        "status": status,
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }
    if is_locked is not None:
        payload["isLocked"] = is_locked
    try:
        await graph_provider.batch_upsert_nodes([payload], CollectionNames.APPS.value)
    except Exception as e:
        logger.error(
            f"❌ Failed to write status={status} for connector {connector_id}: {e}"
        )
        return False
    return True


async def _run_until_aborted(connector: "BaseConnector", lease: SyncLease) -> None:
    """Run the sync, cutting it short if the lease is lost or a stop arrives.

    Cancellation lands at the next await point, which for an await-dense
    run_sync() is milliseconds — but a connector sitting in a long blocking
    call in a worker thread will take as long as that call takes, exactly as
    task.cancel() does today.
    """
    sync = asyncio.ensure_future(connector.run_sync())
    abort = asyncio.ensure_future(lease.wait_aborted())
    try:
        await asyncio.wait({sync, abort}, return_when=asyncio.FIRST_COMPLETED)
        if sync.done():
            await sync  # re-raise whatever the sync itself raised
            return
    finally:
        abort.cancel()
        # Also reached when this task is cancelled from outside (/sync/stop,
        # shutdown). asyncio.wait leaves the futures it waited on running, so
        # without this the sync outlives the lease release and the cleanup.
        if not sync.done():
            sync.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sync


async def _finalize(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    lease: SyncLease | None,
    coordinator: SyncCoordinator | None,
    connector: "BaseConnector | None" = None,
    resync_spec: object | None = None,
    org_id: str | None = None,
) -> None:
    """Write IDLE, then release — in that order, in one detached task.

    Order is load-bearing: releasing first would let the next owner write
    SYNCING and have our IDLE land on top of it.

    Both steps live in the same task because `await asyncio.shield(...)` raises
    on a second cancel while the shielded task keeps running. Anything placed
    after that await — the release in particular — would simply never run, and
    the lease would then pin the connector for a full TTL.

    A lost lease means someone else owns the connector now, so we must not write
    its status; the CAS release is still safe (it no-ops) and keeps bookkeeping
    tidy.
    """
    if lease is None or not lease.lost.is_set():
        await write_app_status(
            graph_provider, logger, connector_id, AppStatus.IDLE.value
        )
    if lease is not None and coordinator is not None:
        await coordinator.end(lease)
    if connector is not None:
        # Built for this run only, so its HTTP sessions die with it.
        try:
            await connector.cleanup()
        except Exception as e:
            logger.warning(f"Connector cleanup failed for {connector_id}: {e}")

    # The sync may have added or removed records; drop the query service's
    # cached view of this connector so the next search sees them. Every entry
    # path (event, factory startup resume) ends here, so this is the one place
    # it has to be done.
    from app.services.cache.invalidation_hooks import notify_connector_sync_completed

    try:
        await notify_connector_sync_completed(connector_id, org_id)
    except Exception as e:
        logger.warning(f"Cache invalidation failed for {connector_id}: {e}")

    # Re-issue any resync that was declined while this one held the lease.
    # Deliberately after the release, so the re-issued request can actually
    # take the lease instead of being declined all over again.
    if resync_spec is not None:
        await _reissue_pending_resync(graph_provider, logger, connector_id, resync_spec)

    # A slot just freed, so anything parked at the limit can go. Failures here
    # must not escape: this runs in a detached finalizer whose real job is the
    # status write and the lease release above.
    try:
        await drain_queued_syncs(graph_provider, logger)
    except Exception as e:
        logger.error(f"Could not release queued syncs: {e}")


# How long a queued connector may sit with its request already submitted before
# the drain assumes the event was lost and re-issues it.
_QUEUE_GRACE_MS = 120_000

#: Sanity bound on one drain pass. Not a concurrency limit — `begin()` is that.
_DRAIN_MAX_PER_PASS = 500


async def drain_queued_syncs(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
) -> list[str]:
    """Start syncs parked at the concurrency limit, now that a slot has freed.

    Edge-triggered off the end of a sync, which is exactly when capacity
    changes. The status write is what the UI reads; pendingResync is what makes
    the request survive a restart, so both are cleared only once the re-issue
    is actually accepted.
    """
    from app.connectors.core.sync.sync_coordinator import get_coordinator
    from app.connectors.core.sync.sync_dispatcher import SubmitResult, get_dispatcher

    dispatcher = get_dispatcher()
    coordinator = get_coordinator()
    if dispatcher is None or coordinator is None:
        return []

    # Deliberately NOT bounded by this worker's free slots. Publishing is not
    # admission: submit() puts an event on the topic, and `begin()` at consume
    # time is what grants a slot — on whichever worker picks it up. Budgeting by
    # the draining worker's own running_count() was wrong twice over: that worker
    # can be full while the rest of the fleet is idle, and it does not run the
    # syncs it releases. Measured at n=70 it released ~7 connectors per 87-second
    # burst instead of keeping the pipeline full — 456s against 115s, a 4x loss.
    #
    # Over-publishing is cheap and self-correcting: the consumer's capacity gate
    # meters arrivals, and anything admitted past the limit is answered
    # AT_CAPACITY and stays QUEUED. The cap is a sanity bound on a pathological
    # queue, not a throttle.

    # Every worker's finalizer fires this, and they would all publish for the
    # same rows: one submission is accepted and the losers re-flag what the
    # winner just cleared. One worker per pass is enough — the next completion
    # takes the next turn.
    manager = get_coordinator()
    if manager is not None and not await manager.try_claim_once("drain", 5_000):
        return []

    try:
        queued = await graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value,
            "status",
            [AppStatus.QUEUED.value],
            ["id", ConnectorStateKeys.PENDING_RESYNC, "updatedAtTimestamp"],
        )
    except Exception as e:
        logger.error(f"Could not read the queued syncs: {e}")
        return []

    # pendingResync marks not-yet-submitted, so the next drain does not re-issue
    # a connector whose event is already in flight. Status stays QUEUED until the
    # sync starts and writes SYNCING, so anything watching still sees work owed.
    #
    # A submitted request whose event never arrives would otherwise strand the
    # connector: QUEUED, unflagged, and invisible to every later drain. After the
    # grace window it is treated as owed again — re-issuing is cheap and idempotent
    # (the lease declines a duplicate, record ids are deterministic), whereas a
    # lost request never comes back.
    stale_before = _now_ms() - _QUEUE_GRACE_MS
    ids = [
        doc["id"]
        for doc in (queued or [])
        if doc.get("id")
        and (
            doc.get(ConnectorStateKeys.PENDING_RESYNC)
            or int(doc.get("updatedAtTimestamp") or 0) < stale_before
        )
    ]
    if not ids:
        return []

    started: list[str] = []
    for spec in await resolve_resync_specs(graph_provider, ids, logger):
        if len(started) >= _DRAIN_MAX_PER_PASS:
            logger.warning(
                "Drain hit its %d-per-pass cap with %d still queued; the next "
                "completion or sweep takes the rest",
                _DRAIN_MAX_PER_PASS, len(ids) - len(started),
            )
            break
        if await dispatcher.submit(spec) is not SubmitResult.ACCEPTED:
            continue
        # Restamp as well as clear: the staleness arm above re-issues anything
        # older than the grace window, and without a fresh timestamp a row whose
        # event never arrives qualifies again on every later pass, forever.
        await graph_provider.update_node(
            spec.connector_id,
            CollectionNames.APPS.value,
            {
                ConnectorStateKeys.PENDING_RESYNC: False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            },
        )
        started.append(spec.connector_id)

    if started:
        logger.info("Released %d queued sync(s): %s", len(started), started)
    return started


async def resolve_resync_specs(
    graph_provider: IGraphDBProvider,
    connector_ids: list[str],
    logger: logging.Logger,
) -> list[object]:
    """App documents do not carry their org, so walk orgs down to their apps.

    The same traversal `resume_sync_services` uses, and it only runs on a pass
    that actually found something flagged.
    """
    from app.connectors.core.sync.sync_dispatcher import SyncSpec

    wanted = set(connector_ids)
    specs = []
    for org in (await graph_provider.get_all_orgs(active=True)) or []:
        if not wanted:
            break
        org_id = org.get("_key") or org.get("id")
        if not org_id:
            continue
        for app in (await graph_provider.get_org_apps(org_id)) or []:
            cid = app.get("_key") or app.get("id")
            if cid not in wanted:
                continue
            wanted.discard(cid)
            # full_sync stays False: the start path already merges the
            # connector's own pendingFullSync flag, and deciding it here too
            # would put that decision in two places.
            specs.append(
                SyncSpec(
                    connector_id=cid,
                    connector_name=(app.get("type") or app.get("name") or "")
                    .replace(" ", "")
                    .lower(),
                    org_id=org_id,
                )
            )
    if wanted:
        logger.warning(
            "Pending resync flagged on %d connector(s) that belong to no active "
            "org; leaving them alone: %s", len(wanted), sorted(wanted),
        )
    return specs


async def _reissue_pending_resync(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    resync_spec: object,
) -> None:
    """Hand back a request that arrived while this connector was busy.

    A fast path, not the guarantee. It only fires if this process survives to
    its finalizer, and it reads the flag at a moment the declining request may
    not have written it yet. A later pass over connectors still flagged
    `pendingResync` is what makes the request durable; anything missed here is
    picked up there.

    So the flag is cleared only once the resync is actually accepted. Clearing
    it first — as this did — turned every failed publish into a silently lost
    request, with nothing left in the document for the sweep to find.
    """
    from app.connectors.core.sync.sync_dispatcher import SubmitResult, get_dispatcher

    try:
        doc = await graph_provider.get_document(
            document_key=connector_id, collection=CollectionNames.APPS.value
        )
        if not doc or not doc.get(ConnectorStateKeys.PENDING_RESYNC):
            return
        dispatcher = get_dispatcher()
        if dispatcher is None:
            logger.warning(
                "Pending resync for %s not re-issued here (no dispatcher); left "
                "flagged for the sweep", connector_id,
            )
            return
        result = await dispatcher.submit(resync_spec)
        if result is not SubmitResult.ACCEPTED:
            # Still pending by definition — leave the flag for the sweep.
            logger.warning(
                "Re-issued resync for %s was not accepted (%s); left flagged",
                connector_id, result,
            )
            return
        await graph_provider.update_node(
            connector_id,
            CollectionNames.APPS.value,
            {ConnectorStateKeys.PENDING_RESYNC: False},
        )
        logger.info("Re-issued declined resync for %s", connector_id)
    except Exception as e:
        logger.error(f"Could not re-issue pending resync for {connector_id}: {e}")


async def run_sync_task(
    connector: "BaseConnector",
    connector_id: str,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    *,
    start_status: str = AppStatus.SYNCING.value,
    lease: SyncLease | None = None,
    coordinator: SyncCoordinator | None = None,
    close_connector: bool = False,
    resync_spec: object | None = None,
) -> None:
    start = time.monotonic()
    stopped = False
    try:
        await write_app_status(graph_provider, logger, connector_id, start_status)
        if lease is None:
            await connector.run_sync()
        else:
            await _run_until_aborted(connector, lease)
    except asyncio.CancelledError:
        # Stopped on request, or shutting down. Either way a resync queued
        # behind this run must not be handed straight back, or a stop that
        # the user asked for undoes itself.
        stopped = True
        raise
    finally:
        elapsed = time.monotonic() - start
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{int(mins)}m {secs:.1f}s" if mins else f"{secs:.1f}s"
        logger.info(
            f"✅ Sync finished for connector {connector_id} — total time: {elapsed_str}"
        )
        # Run the finalizer as its own task: a second cancel (another stop
        # request, appDisabled, shutdown) aimed at this task must not be able to
        # kill the IDLE write and leave the connector stuck SYNCING, nor skip
        # the lease release and leave it un-startable for a full TTL.
        if lease is not None and lease.stop_requested.is_set():
            stopped = True
        cleanup = asyncio.get_running_loop().create_task(
            _finalize(
                graph_provider,
                logger,
                connector_id,
                lease,
                coordinator,
                connector if close_connector else None,
                None if stopped else resync_spec,
                # Derived here, not in _finalize: that only receives the
                # connector when it is also responsible for closing it.
                getattr(
                    getattr(connector, "data_entities_processor", None), "org_id", None
                ),
            ),
            name=f"sync_cleanup_{connector_id}",
        )
        _cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(_cleanup_tasks.discard)
        # shield: cancelling this task cancels whatever it awaits
        # (Task._fut_waiter), so a bare `await cleanup` would let a second
        # cancel kill the cleanup itself. Shielded, the cancel interrupts
        # only this await and the cleanup runs to completion on the loop.
        await asyncio.shield(cleanup)
