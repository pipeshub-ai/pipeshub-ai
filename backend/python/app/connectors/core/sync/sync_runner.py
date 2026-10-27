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
import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import AppStatus, CollectionNames, Connectors
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
    connector_sync_lock,
    dispose_connector,
)
from app.connectors.core.constants import ConnectorStateKeys, SyncRunOutcome
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage, FullSyncSeen
from app.connectors.core.sync.run_context import reset_run_sync_id, set_run_sync_id
from app.connectors.core.sync.sync_errors import SyncErrorCode, classify_sync_error
from app.connectors.core.sync.sync_progress import (
    SyncPhase,
    SyncProgress,
    SyncTrigger,
    merged_run_history,
    parse_run_history,
    reset_current_sync_progress,
    set_current_sync_progress,
)
from app.connectors.core.sync.sync_coordinator import SyncCoordinator, SyncLease, _now_ms
from app.core.org_context import spawn_in_request
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import (
        ActionItem,
        BaseConnector,
    )
    from app.connectors.core.sync.sync_dispatcher import SyncEventDispatcher, SyncSpec

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
    full_sync_owed: bool = False,
    run_started: bool = False,
) -> bool:
    """Best-effort status write; a DB blip must never fail the sync itself.

    Returns whether the write landed, so a caller that reports the status back
    to a user can tell the difference between repaired and merely attempted.

    ``full_sync_owed`` sets pendingFullSync in the same write, for a caller
    clearing a full sync that never finished (see ``_finalize``).
    ``run_started`` stamps lastSyncStartedAtTimestamp, for the write that starts a run.
    """
    payload: dict[str, Any] = {
        "id": connector_id,
        "status": status,
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }
    if is_locked is not None:
        payload["isLocked"] = is_locked
    if full_sync_owed:
        payload[ConnectorStateKeys.PENDING_FULL_SYNC] = True
    if run_started:
        payload[ConnectorStateKeys.LAST_SYNC_STARTED_AT] = payload["updatedAtTimestamp"]
    try:
        await graph_provider.batch_upsert_nodes([payload], CollectionNames.APPS.value)
    except Exception as e:
        logger.error(
            f"❌ Failed to write status={status} for connector {connector_id}: {e}"
        )
        return False
    return True


async def _run_sync_alone(connector: "BaseConnector", connector_id: str) -> None:
    """run_sync, after any user-link pass on this connector has finished (see link_new_users)."""
    async with connector_sync_lock(connector_id):
        await connector.run_sync()


async def _run_until_aborted(connector: "BaseConnector", lease: SyncLease) -> None:
    """Run the sync, cutting it short if the lease is lost or a stop arrives.

    Cancellation lands at the next await point, which for an await-dense
    run_sync() is milliseconds — but a connector sitting in a long blocking
    call in a worker thread will take as long as that call takes, exactly as
    task.cancel() does today.
    """
    sync = asyncio.ensure_future(_run_sync_alone(connector, lease.connector_id))
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


def resumes_interrupted_full_sync(app: dict | None) -> bool:
    """Whether a MANUAL connector is owed the full sync a restart cut short.

    MANUAL means "not on a schedule", not "abandon a sync someone started". A
    full sync cut short by a restart or a crash leaves both pendingFullSync and
    pendingResync (``_finalize``, ``connectors_main.reset_stale_sync_state``).
    pendingFullSync alone is not enough: saving new filters sets it, and so
    does stopping a full sync, and neither of those should start a MANUAL
    connector by itself. The stop route clears pendingResync, so a stop holds.

    And only a connector that can run at all, the same states the start path
    refuses (``event_service._start_sync_with_lease``): not disabled, not
    signed out, not being deleted. A QUEUED one is left to the startup drain,
    which re-issues it after this; publishing it here too would run it twice.

    Asked twice: at boot, before publishing the resume, and by the start handler
    for the event that resume published (``bootResume``), since a stop can land
    in between and the start path does not otherwise read pendingResync.
    """
    if not app:
        return False
    return (
        bool(app.get(ConnectorStateKeys.PENDING_FULL_SYNC))
        and bool(app.get(ConnectorStateKeys.PENDING_RESYNC))
        and app.get(ConnectorStateKeys.IS_ACTIVE) is not False
        and app.get(ConnectorStateKeys.IS_AUTHENTICATED) is not False
        and app.get("status") not in (AppStatus.QUEUED.value, "DELETING")
    )


async def _deleting_or_gone(
    graph_provider: IGraphDBProvider, logger: logging.Logger, connector_id: str
) -> bool:
    try:
        doc = await graph_provider.get_document(
            document_key=connector_id, collection=CollectionNames.APPS.value
        )
    except Exception as e:
        logger.warning(f"Could not read {connector_id} before its IDLE write: {e}")
        return False
    return not doc or doc.get("status") == "DELETING"


async def _finalize(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    lease: SyncLease | None,
    coordinator: SyncCoordinator | None,
    connector: "BaseConnector | None" = None,
    resync_spec: object | None = None,
    org_id: str | None = None,
    *,
    full_sync_cut_short: bool = False,
    full_sync_still_requested: bool = False,
    scope_generation: int | None = None,
    stamp_scopes: bool = False,
    outcome: SyncRunOutcome | None = None,
    summary: dict[str, object] | None = None,
    action_items: "list[ActionItem] | None" = None,
    run_record: dict[str, object] | None = None,
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

    ``full_sync_cut_short``: a full sync that stopped before it finished — a
    restart, a stop, a crash in ``run_sync`` — or whose removal of what it did
    not see failed. Its prep already deleted the connector's sync points, and the
    links of whatever it did not see are only removed once a full sync runs to
    the end. The flag that asks for one goes in the same write as IDLE: the
    connector is never seen IDLE without it.

    ``full_sync_still_requested``: the run was cut short by this service
    shutting down, not by a person stopping it or by the sync failing. The
    request it was serving is still owed, so pendingResync goes in the same
    write, and the next boot resumes the full sync even on a MANUAL connector
    (see ``resumes_interrupted_full_sync``). Not written when
    the lease was lost: the new owner runs the connector, not the next boot.

    ``outcome`` goes in the same write too, with the finish time, so "last synced"
    means the last run that ended rather than the last write to the app doc.

    ``run_record`` is the run's progress folded into history (``syncRuns``,
    ``lastSyncError``, and ``syncProgress`` cleared), also in that write: the
    connector is never seen IDLE with a live progress snapshot still on it.
    """
    owed = {ConnectorStateKeys.PENDING_FULL_SYNC: True} if full_sync_cut_short else {}
    still_requested = (
        {ConnectorStateKeys.PENDING_RESYNC: True}
        if full_sync_cut_short and full_sync_still_requested
        else {}
    )
    finished_at = get_epoch_timestamp_in_ms()
    ended = (
        {
            ConnectorStateKeys.LAST_SYNC_FINISHED_AT: finished_at,
            ConnectorStateKeys.LAST_SYNC_OUTCOME: outcome.value,
            # Text, so every graph store can hold it; None clears an older run's summary.
            ConnectorStateKeys.LAST_SYNC_SUMMARY: json.dumps(summary, default=str) if summary else None,
            **(run_record or {}),
        }
        if outcome is not None else {}
    )
    if isinstance(action_items, list):
        # Text, as the summary is; None when nothing is left to do, which clears the card's badge.
        ended[ConnectorStateKeys.ACTION_ITEMS] = (
            json.dumps([item.model_dump(by_alias=True) for item in action_items]) if action_items else None
        )
    if full_sync_cut_short:
        logger.warning(
            f"Full sync for connector {connector_id} did not finish or skipped part of the source; the next sync "
            "will run in full"
        )
    if lease is None or not lease.lost.is_set():
        # Not over a delete: the status is an upsert, so writing it after the
        # delete removed the doc resurrected a ghost App node, and writing it
        # during one let a second DELETE through the "already deleting" guard.
        if await _deleting_or_gone(graph_provider, logger, connector_id):
            logger.info(f"Not writing IDLE for {connector_id}: it is being deleted")
        else:
            # An update, not the upsert write_app_status does: it cannot create
            # the node if the read above failed or a delete landed since.
            try:
                await graph_provider.update_node(
                    connector_id,
                    CollectionNames.APPS.value,
                    {"status": AppStatus.IDLE.value,
                     "updatedAtTimestamp": finished_at,
                     **owed, **still_requested, **ended},
                )
            except Exception as e:
                logger.error(f"❌ Failed to write status=IDLE for connector {connector_id}: {e}")
    elif owed:
        # The new owner's status is not ours to write, but the debt is: whatever
        # it runs next must be a full sync. Only ever set, so it cannot undo
        # anything the new owner wrote, and update_node cannot create the node.
        try:
            await graph_provider.update_node(connector_id, CollectionNames.APPS.value, owed)
        except Exception as e:
            logger.error(f"❌ Could not record the owed full sync for {connector_id}: {e}")
    if lease is not None and coordinator is not None:
        await coordinator.end(lease)
    if connector is not None:
        # Built for this run only, so its HTTP sessions and producer die with it.
        try:
            await dispose_connector(connector)
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

    # A slot just freed, so anything parked at the limit can go. Failures here
    # must not escape: this runs in a detached finalizer whose real job is the
    # status write and the lease release above.
    try:
        await drain_queued_syncs(graph_provider, logger)
    except Exception as e:
        logger.error(f"Could not release queued syncs: {e}")

    # Re-issue any resync that was declined while this one held the lease.
    # After the release, so it can take the lease; after the drain, so it
    # queues behind connectors that were already waiting instead of taking the
    # slot it just gave up -- a connector whose schedule is shorter than its
    # sync would otherwise hold a slot for ever. Not during shutdown: the next
    # boot resumes every connector anyway.
    if resync_spec is not None and getattr(coordinator, "shutting_down", False) is not True:
        await _reissue_pending_resync(graph_provider, logger, connector_id, resync_spec)

    # Last: a stamp of a large connector takes minutes, and nothing above may wait for it.
    await _kh_scope_end(graph_provider, logger, connector_id, scope_generation, stamp=stamp_scopes)


async def _kh_scope_begin(
    graph_provider: IGraphDBProvider, logger: logging.Logger, connector_id: str
) -> int | None:
    """Mark the connector's knowledge hub scopes stale before a sync writes; the generation this run owns."""
    try:
        return await graph_provider.kh_scope_mark_stale(connector_id)
    except Exception as e:
        logger.error(f"❌ Could not mark knowledge hub scopes stale for {connector_id}: {e}")
        return None


async def _kh_scope_end(
    graph_provider: IGraphDBProvider, logger: logging.Logger, connector_id: str,
    generation: int | None, *, stamp: bool,
) -> None:
    """End this run's mark and, when the scope listing is enabled, re-stamp from what it left."""
    if generation is None:
        return
    try:
        await graph_provider.kh_scope_sync_ended(connector_id, generation)
        if stamp and await graph_provider.kh_scope_enabled():
            await graph_provider.kh_scope_stamp(connector_id)
    except Exception as e:
        logger.error(f"❌ Knowledge hub scope stamp failed for {connector_id}: {e}")


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
    from app.connectors.core.sync.sync_dispatcher import get_dispatcher

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
    if not await coordinator.try_claim_once("drain", 5_000):
        return []

    # try_claim_once only separates processes. Finalizers in one process run
    # concurrently too (a shutdown fires them all at once), and two passes over
    # the same rows each published every queued connector.
    async with _drain_lock():
        return await _drain_once(graph_provider, logger, dispatcher)


_DRAIN_LOCKS: dict[str, asyncio.Lock] = {}


def _drain_lock() -> asyncio.Lock:
    return _DRAIN_LOCKS.setdefault("drain", asyncio.Lock())


async def _drain_once(
    graph_provider: IGraphDBProvider, logger: logging.Logger, dispatcher: "SyncEventDispatcher"
) -> list[str]:
    from app.connectors.core.sync.sync_coordinator import get_coordinator
    from app.connectors.core.sync.sync_dispatcher import SubmitResult

    try:
        queued = await graph_provider.get_nodes_by_field_in(
            CollectionNames.APPS.value,
            "status",
            [AppStatus.QUEUED.value],
            [
                "id", ConnectorStateKeys.PENDING_RESYNC, "updatedAtTimestamp", "queuedAtTimestamp",
                ConnectorStateKeys.IS_ACTIVE, ConnectorStateKeys.IS_AUTHENTICATED,
            ],
        )
    except Exception as e:
        logger.error(f"Could not read the queued syncs: {e}")
        return []

    # A connector disabled or signed out while it waited is owed nothing: the
    # start path would skip it, so publishing it only repeats on every pass.
    runnable = []
    for doc in queued or []:
        if doc.get(ConnectorStateKeys.IS_ACTIVE) is False or doc.get(
            ConnectorStateKeys.IS_AUTHENTICATED
        ) is False:
            try:
                await graph_provider.update_node(
                    doc["id"],
                    CollectionNames.APPS.value,
                    {
                        "status": AppStatus.IDLE.value,
                        ConnectorStateKeys.PENDING_RESYNC: False,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    },
                )
                logger.info(f"Dropped queued sync of inactive connector {doc['id']}")
            except Exception as e:
                logger.error(f"Could not clear queued inactive connector {doc.get('id')}: {e}")
            continue
        runnable.append(doc)
    queued = runnable

    # pendingResync marks not-yet-submitted, so the next drain does not re-issue
    # a connector whose event is already in flight. Status stays QUEUED until the
    # sync starts and writes SYNCING, so anything watching still sees work owed.
    #
    # A submitted request whose event never arrives would otherwise strand the
    # connector: QUEUED, unflagged, and invisible to every later drain. After the
    # grace window it is treated as owed again. A duplicate of an event that did
    # arrive late is harmless: the start path drops a request older than the sync
    # already running for it.
    stale_before = _now_ms() - _QUEUE_GRACE_MS
    # Oldest first, by when each connector entered the queue. Unordered, whoever
    # the org traversal met first won every freed slot.
    def arrived(d: dict) -> int:
        return int(d.get("queuedAtTimestamp") or d.get("updatedAtTimestamp") or 0)

    ids = [
        doc["id"]
        for doc in sorted(queued or [], key=arrived)
        if doc.get("id")
        and (
            doc.get(ConnectorStateKeys.PENDING_RESYNC)
            or int(doc.get("updatedAtTimestamp") or 0) < stale_before
        )
    ]
    if not ids:
        return []

    # A coordinator that can count free slots exactly (one process) gets only as
    # many releases as it can admit. Publishing the whole queue on every
    # completion made each of those events bounce back to QUEUED -- a read and a
    # write per queued connector per completion. One that cannot (a fleet, where
    # another worker may have room) keeps publishing everything.
    budget_fn = getattr(get_coordinator(), "drain_budget", None)
    budget = budget_fn() if callable(budget_fn) else None
    budget = budget if isinstance(budget, int) else None
    if budget is not None and budget <= 0:
        return []

    order = {cid: i for i, cid in enumerate(ids)}
    specs = sorted(
        await resolve_resync_specs(graph_provider, ids, logger),
        key=lambda spec: order.get(spec.connector_id, len(order)),
    )
    started: list[str] = []
    for spec in specs:
        if budget is not None and len(started) >= budget:
            break
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
) -> list["SyncSpec"]:
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


def _begin_full_sync_run(
    connector: "BaseConnector", connector_id: str
) -> tuple[Any, FullSyncSeen | None]:
    """Have the connector's processor note what this full sync writes."""
    processor = getattr(connector, "data_entities_processor", None)
    begin = getattr(processor, "begin_full_sync_run", None)
    run = begin(connector_id) if callable(begin) else None
    return (processor, run) if isinstance(run, FullSyncSeen) else (None, None)


# Connectors whose next full sync is the retry owed by one that skipped part of the source.
# shortcut: in memory, so a restart or another sync process may owe one more retry; persist it if that matters.
_INCOMPLETE_FULL_SYNC_RETRYING: set[str] = set()


async def _unlink_what_full_sync_missed(
    connector: "BaseConnector",
    connector_id: str,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    seen: FullSyncSeen,
) -> bool:
    """After a full sync ran to the end, unlink the nodes it did not see.

    This is the removal the full sync used to do before it started: stale links
    and revoked access go, but only once the sync has seen everything it is
    going to see. Returns False when another full sync is owed: the removal
    failed, or the sync skipped part of the source (one retry, not a chain of them).
    """
    coverage = getattr(connector, "full_sync_coverage", None)
    coverage = coverage() if callable(coverage) else None
    if not isinstance(coverage, FullSyncCoverage):
        coverage = FullSyncCoverage()
    if not coverage.complete:
        logger.warning(
            f"Full sync of {connector_id} skipped part of the source after an error; "
            "nothing it did not see is unlinked this time"
        )
        # Owe one retry, not one per retry: a source that always fails part of the
        # way (a quota, one unreadable item) would otherwise run in full forever.
        if connector_id in _INCOMPLETE_FULL_SYNC_RETRYING:
            _INCOMPLETE_FULL_SYNC_RETRYING.discard(connector_id)
            return True
        _INCOMPLETE_FULL_SYNC_RETRYING.add(connector_id)
        return False
    _INCOMPLETE_FULL_SYNC_RETRYING.discard(connector_id)
    if seen.count() == 0:
        # A sync that wrote nothing at all most likely failed without saying so.
        logger.warning(f"Full sync of {connector_id} wrote nothing; leaving every link in place")
        return True
    try:
        # Groups whose members came one at a time get the full list now, in one
        # write each; only now is that list known to be complete.
        finish = getattr(getattr(connector, "data_entities_processor", None), "finish_full_sync_memberships", None)
        if callable(finish):
            await finish(connector_id, seen)
        deleted, ok = await graph_provider.unlink_nodes_missed_by_full_sync(
            connector_id,
            seen.by_collection,
            window_start_ms=coverage.window_start_ms,
            windowed_group_types=coverage.windowed_group_types,
        )
    except Exception as e:
        logger.error(f"Could not unlink what the full sync of {connector_id} missed: {e}")
        return False
    if ok:
        logger.info(f"Full sync of {connector_id}: removed {deleted} edge(s) of nodes it did not see")
    return bool(ok)


# How often a running sync's snapshot is written to its app doc. The UI polls at
# about this pace while a sync runs; writes are skipped when nothing changed.
_PROGRESS_WRITE_INTERVAL_S = 5.0
_PROGRESS_WRITER_STOP_TIMEOUT_S = 5.0
# Bounded: the IDLE write waits on it.
_HISTORY_REREAD_TIMEOUT_S = 10.0


# get_document answers None for a failed read as well as a missing doc.
HISTORY_UNREAD = object()


async def _write_progress_while_running(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    progress: SyncProgress,
    lease: SyncLease | None,
    run_mark: dict[str, Any],
) -> None:
    """Keep ``syncProgress`` on the app doc current until cancelled.

    An update, never an upsert, so it cannot resurrect a connector deleted
    mid-run, and only while the doc still carries ``run_mark`` (this run's status
    and start time): a write that outlives the finalizer's cancel wait is dropped,
    on an IDLE doc and on the next run's alike.
    Failures are logged and retried on the next tick: progress is for people to
    look at, and must never fail the sync.
    """
    written = -1
    while True:
        if lease is not None and lease.lost.is_set():
            # Someone else owns the connector now; its doc is theirs to write.
            return
        version = progress.version
        if version != written:
            try:
                if await graph_provider.update_node_fields_if_match(
                    connector_id,
                    CollectionNames.APPS.value,
                    {ConnectorStateKeys.SYNC_PROGRESS: json.dumps(progress.snapshot(), default=str)},
                    run_mark,
                ):
                    written = version
            except Exception as e:
                logger.debug(f"Could not write sync progress for {connector_id}: {e}")
        await asyncio.sleep(_PROGRESS_WRITE_INTERVAL_S)


def _run_record(
    progress: SyncProgress,
    outcome: SyncRunOutcome,
    stored_runs: object,
    *,
    error: dict[str, object] | None,
    skip_code: str | None,
    started_by: str | None,
) -> dict[str, object]:
    """The app-doc fields that close a run: history, the failure reason, and no live snapshot.

    When the history could not be read, it is left alone, and so is the last
    failure: writing this run alone would replace the stored runs, and the card
    reads the latest run from the history, so the two must move together.
    """
    entry = progress.run_entry(
        outcome=outcome.value,
        finished_at=get_epoch_timestamp_in_ms(),
        error=error if outcome is SyncRunOutcome.FAILED else None,
        skip_code=skip_code,
        started_by=started_by,
    )
    progress.close()
    failure = (
        json.dumps({**(error or {"code": "unknown"}), "runId": progress.run_id, "at": entry["finishedAt"]})
        if outcome is SyncRunOutcome.FAILED
        else None
    )
    if stored_runs is HISTORY_UNREAD:
        return {ConnectorStateKeys.SYNC_PROGRESS: None}
    return {
        ConnectorStateKeys.SYNC_PROGRESS: None,
        ConnectorStateKeys.SYNC_RUNS: merged_run_history(stored_runs, entry),
        # A stopped or skipped run says nothing about whether the source works,
        # so only a success clears the last failure.
        **(
            {ConnectorStateKeys.LAST_SYNC_ERROR: failure}
            if outcome in (SyncRunOutcome.FAILED, SyncRunOutcome.SUCCEEDED)
            else {}
        ),
    }


async def _finalize_with_history(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    stored_runs: object,
    record_for: Callable[[object], dict[str, object]],
    finalize: Callable[..., Awaitable[None]],
) -> None:
    """Build the run's closing fields, then finalize; inside the shielded cleanup task.

    The history re-read happens here, not in the sync task, so a second cancel
    aimed at the sync task cannot skip the IDLE write or the lease release.
    """
    if stored_runs is HISTORY_UNREAD:
        stored_runs = await _read_history_again(graph_provider, logger, connector_id)
    await finalize(run_record=record_for(stored_runs))


async def _read_history_again(
    graph_provider: IGraphDBProvider, logger: logging.Logger, connector_id: str
) -> object:
    """One more try at the stored runs, for a run whose start-of-run read failed."""
    try:
        doc = await asyncio.wait_for(
            graph_provider.get_document(document_key=connector_id, collection=CollectionNames.APPS.value),
            _HISTORY_REREAD_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"Could not read sync history for {connector_id}: {e}")
        return HISTORY_UNREAD
    return HISTORY_UNREAD if doc is None else doc.get(ConnectorStateKeys.SYNC_RUNS)


async def record_failed_start(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    error: BaseException | None,
    *,
    trigger: str,
) -> None:
    """Record a sync that never began because the connector could not start.

    No run starts, so nothing else tells the card: it would keep showing the
    last good run, or "not synced yet", while an expired sign-in fails every
    sync. The same failure again replaces its entry rather than pushing real
    runs out of the history. ``error`` is what ``init`` raised, or None when
    it returned False. Never raises.
    """
    try:
        doc = await graph_provider.get_document(document_key=connector_id, collection=CollectionNames.APPS.value)
        status = (doc or {}).get("status")
        # A run in progress, or a connector being removed, says more than this does.
        if not doc or status in (AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, "DELETING"):
            return
        stored_runs = doc.get(ConnectorStateKeys.SYNC_RUNS)
        failure = (
            classify_sync_error(error)
            if error is not None
            else {"code": SyncErrorCode.UNKNOWN.value, "detail": None}
        )
        latest = next(iter(parse_run_history(stored_runs)), {})
        repeat = (
            latest.get("outcome") == SyncRunOutcome.FAILED.value
            and (latest.get("error") or {}).get("code") == failure["code"]
            and not (latest.get("counts") or {}).get("found")
        )
        progress = SyncProgress(
            connector_id=connector_id,
            run_id=str(latest.get("runId")) if repeat and latest.get("runId") else str(uuid4()),
            full=False,
            trigger=SyncTrigger.parse(trigger),
        )
        record = _run_record(
            progress, SyncRunOutcome.FAILED, stored_runs, error=failure, skip_code=None, started_by=None
        )
        # Matched on the history read above, so a run that finished meanwhile is not overwritten.
        await graph_provider.update_node_fields_if_match(
            connector_id,
            CollectionNames.APPS.value,
            record,
            {"status": status, ConnectorStateKeys.SYNC_RUNS: stored_runs},
        )
    except Exception as e:
        logger.warning(f"Could not record the failed start of {connector_id}: {e}")


async def _mark_first_sync(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    connector_id: str,
    doc: dict | None,
) -> None:
    """Flag a connector whose content has never been searchable, so people hear when it is.

    Decided at the start of a run, from the doc read just before. A connector
    that has finished a run before, or already has indexed content, is not
    new: lastSyncFinishedAtTimestamp only exists since October 2026, so the
    indexed-content check is what keeps installs upgraded from older versions
    from announcing every connector they already have.
    """
    if not doc or doc.get(ConnectorStateKeys.AWAITING_FIRST_SYNC) or doc.get(ConnectorStateKeys.READY_AT):
        return
    if doc.get(ConnectorStateKeys.LAST_SYNC_FINISHED_AT):
        return
    if doc.get("type") == Connectors.KNOWLEDGE_BASE.value:
        return
    try:
        if await graph_provider.has_records_indexed_since(connector_id, 0):
            return
        await graph_provider.update_node(
            connector_id, CollectionNames.APPS.value, {ConnectorStateKeys.AWAITING_FIRST_SYNC: True}
        )
    except Exception as e:
        logger.debug(f"Could not mark the first sync of {connector_id}: {e}")


def _run_outcome(*, stopped: bool, failed: bool, skipped: bool, finished: bool) -> SyncRunOutcome:
    if stopped:
        return SyncRunOutcome.STOPPED
    if failed:
        return SyncRunOutcome.FAILED
    if skipped:
        return SyncRunOutcome.SKIPPED
    # Not finished without an error: the lease was lost and the run cut short.
    return SyncRunOutcome.SUCCEEDED if finished else SyncRunOutcome.STOPPED


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
    trigger: str | None = None,
    started_by: str | None = None,
) -> None:
    start = time.monotonic()
    stopped = False
    failed = False
    finished = False
    unlink_failed = False
    skipped_code: str | None = None
    error: dict[str, object] | None = None
    full_sync = start_status == AppStatus.FULL_SYNCING.value
    processor, seen = None, None
    scope_generation: int | None = None
    stored_runs: object = HISTORY_UNREAD
    run_mark: dict[str, Any] = {"status": start_status}
    progress_writer: asyncio.Task | None = None
    # One id per run, stamped on every record-event this sync publishes so
    # downstream processing can be correlated back to the run.
    run_sync_id = str(uuid4())
    run_sync_token = set_run_sync_id(run_sync_id)
    progress = SyncProgress(
        connector_id=connector_id,
        run_id=run_sync_id,
        full=full_sync,
        trigger=SyncTrigger.parse(trigger),
    )
    progress_token = set_current_sync_progress(progress)
    logger.info(f"▶️ Starting sync run {run_sync_id} for connector {connector_id}")
    try:
        # Inside the try: if it raised before it, the finalizer below would never
        # write IDLE or release the lease.
        scope_generation = await _kh_scope_begin(graph_provider, logger, connector_id)
        # A cached connector still holds the last run's summary until run_sync starts;
        # a stop before then must not file it under this run.
        connector.last_sync_summary = None
        connector.last_action_items = None
        if full_sync:
            processor, seen = _begin_full_sync_run(connector, connector_id)
        await write_app_status(graph_provider, logger, connector_id, start_status, run_started=True)
        # A resync asked for before this run began is served by it. Left set, the
        # flag handed the finalizer a request that was already satisfied, and
        # the connector synced twice back to back. Except a full sync owed to an
        # incremental run: then the flag must survive so the finalizer re-issues
        # it (the re-issue merges pendingFullSync and runs full).
        try:
            doc = await graph_provider.get_document(
                document_key=connector_id, collection=CollectionNames.APPS.value
            )
            if doc is not None:
                stored_runs = doc.get(ConnectorStateKeys.SYNC_RUNS)
                if doc.get(ConnectorStateKeys.LAST_SYNC_STARTED_AT) is not None:
                    run_mark[ConnectorStateKeys.LAST_SYNC_STARTED_AT] = doc[ConnectorStateKeys.LAST_SYNC_STARTED_AT]
            full_owed = bool((doc or {}).get(ConnectorStateKeys.PENDING_FULL_SYNC))
            # Only when set: writing the field on every sync put it on every app
            # doc, and main's strict Arango app schema rejects any doc carrying it.
            flagged = bool((doc or {}).get(ConnectorStateKeys.PENDING_RESYNC))
            if flagged and (start_status == AppStatus.FULL_SYNCING.value or not full_owed):
                await graph_provider.update_node(
                    connector_id,
                    CollectionNames.APPS.value,
                    {ConnectorStateKeys.PENDING_RESYNC: False},
                )
            await _mark_first_sync(graph_provider, logger, connector_id, doc)
        except Exception as e:
            logger.warning(f"Could not clear pendingResync for {connector_id}: {e}")
        progress_writer = spawn_in_request(
            _write_progress_while_running(graph_provider, logger, connector_id, progress, lease, run_mark),
            name=f"sync_progress_{connector_id}",
        )
        if lease is None:
            await _run_sync_alone(connector, connector_id)
        else:
            await _run_until_aborted(connector, lease)
        finished = True
        # Only for a sync that ran to the end: one cut off partway saw only part
        # of the source, and everything it had not reached keeps its links.
        if seen is not None and not (
            lease is not None and (lease.stop_requested.is_set() or lease.lost.is_set())
        ):
            progress.enter_phase(SyncPhase.FINISHING)
            unlink_failed = not await _unlink_what_full_sync_missed(
                connector, connector_id, graph_provider, logger, seen
            )
    except asyncio.CancelledError:
        stopped = True
        raise
    except ConnectorSyncSkippedError as exc:
        # Not a crash: the connector declined to run (e.g. Local FS with no
        # desktop connected). Logged only; the UI reads live presence.
        skipped_code = exc.code
    except Exception as exc:
        failed = True
        error = classify_sync_error(exc)
        raise
    finally:
        if progress_writer is not None:
            # Waited for, briefly: a snapshot write landing after the IDLE write
            # below would leave a live-looking run on an idle connector. Readers
            # ignore a snapshot on an idle connector too, so a write that
            # outlives this wait is harmless.
            progress_writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait({progress_writer}, timeout=_PROGRESS_WRITER_STOP_TIMEOUT_S)
        if seen is not None:
            try:
                processor.end_full_sync_run(connector_id, seen)
            except Exception as e:
                # Logged, not raised: the finalizer below must still run.
                logger.error(f"Could not end full-sync tracking for {connector_id}: {e}")
        reset_run_sync_id(run_sync_token)
        reset_current_sync_progress(progress_token)
        # A stop that arrives through the lease ends _run_until_aborted without
        # raising, so it has to be read here or it would log as a clean finish.
        if lease is not None and lease.stop_requested.is_set():
            stopped = True
        outcome = _run_outcome(stopped=stopped, failed=failed, skipped=skipped_code is not None, finished=finished)
        record_for = functools.partial(
            _run_record, progress, outcome, error=error, skip_code=skipped_code, started_by=started_by
        )
        elapsed = time.monotonic() - start
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{int(mins)}m {secs:.1f}s" if mins else f"{secs:.1f}s"
        if stopped:
            logger.warning(f"⚠️ Sync cancelled for connector {connector_id} after {elapsed_str}")
        elif failed:
            logger.error(f"❌ Sync failed for connector {connector_id} after {elapsed_str}")
        elif skipped_code:
            logger.info(f"Sync skipped for connector {connector_id} ({skipped_code}, {elapsed_str})")
        else:
            logger.info(
                f"✅ Sync finished for connector {connector_id} — total time: {elapsed_str}"
            )
        # Run the finalizer as its own task: a second cancel (another stop
        # request, appDisabled, shutdown) aimed at this task must not be able to
        # kill the IDLE write and leave the connector stuck SYNCING, nor skip
        # the lease release and leave it un-startable for a full TTL.
        cleanup = asyncio.get_running_loop().create_task(
            _finalize_with_history(
                graph_provider,
                logger,
                connector_id,
                stored_runs,
                record_for,
                finalize=functools.partial(
                    _finalize,
                    graph_provider,
                    logger,
                    connector_id,
                    lease,
                    coordinator,
                    connector if close_connector else None,
                    # Re-issued even after a stop: /sync/stop clears the flags before
                    # it stops anything, so a request still flagged now arrived after
                    # the stop -- a re-enable, say -- and dropping it left the
                    # connector enabled and never synced.
                    resync_spec,
                    # Derived here, not in _finalize: that only receives the
                    # connector when it is also responsible for closing it.
                    getattr(
                        getattr(connector, "data_entities_processor", None), "org_id", None
                    ),
                    outcome=outcome,
                    summary=getattr(connector, "last_sync_summary", None),
                    action_items=getattr(connector, "last_action_items", None),
                    # A stop or a lost lease returns from _run_until_aborted without
                    # raising, so `finished` alone would call that run complete.
                    # A failed unlink owes one too: what the sync did not see is
                    # still linked, revoked access included.
                    full_sync_cut_short=(
                        full_sync
                        and (
                            not finished
                            or stopped
                            or unlink_failed
                            or (lease is not None and lease.lost.is_set())
                        )
                    ),
                    # Cut short by the shutdown itself. A stop someone asked for
                    # (stop_requested, which also covers one still winding down when
                    # the shutdown arrived) and a run that raised are not: neither
                    # should start again on its own at the next boot.
                    full_sync_still_requested=(
                        stopped
                        and not failed
                        and getattr(coordinator, "shutting_down", False) is True
                        and not (lease is not None and lease.stop_requested.is_set())
                    ),
                    scope_generation=scope_generation,
                    # A stopped sync is being replaced, or its connector disabled: it ends
                    # only its own mark, and the replacement or the scope keeper stamps.
                    stamp_scopes=not stopped,
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
