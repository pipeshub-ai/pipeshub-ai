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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.services.permissions.structure_generation import note_structure_change
from app.connectors.core.base.connector.connector_service import (
    ConnectorSyncSkippedError,
    IncompleteSyncError,
    connector_sync_lock,
    dispose_connector,
)
from app.connectors.core.constants import ConnectorStateKeys, SyncRunOutcome
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage, FullSyncSeen
from app.connectors.core.sync.run_context import (
    SyncRunStats,
    bind_run_stats,
    reset_run_stats,
    reset_run_sync_id,
    set_run_sync_id,
)
from app.connectors.core.sync.sync_coordinator import SyncCoordinator, SyncLease, _now_ms
from app.core.org_context import for_org, spawn_detached
from app.core.org_jobs import for_each_org
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils import error_class
from app.utils.logging_fields import bind_log_fields, log_event, reset_log_fields
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
    full_sync_started: bool = False,
    run_started: bool = False,
) -> bool:
    """Best-effort status write; a DB blip must never fail the sync itself.

    Returns whether the write landed, so a caller that reports the status back
    to a user can tell the difference between repaired and merely attempted.

    ``full_sync_owed`` sets pendingFullSync in the same write, for a caller
    clearing a full sync that never finished (see ``_finalize``).
    ``full_sync_started`` clears it in the same write: the full sync it asked
    for is now running. A separate write to the app doc at the same moment
    fails one of the two in ArangoDB (write-write conflict, or a lock timeout).
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
    elif full_sync_started:
        payload[ConnectorStateKeys.PENDING_FULL_SYNC] = False
    if run_started:
        payload[ConnectorStateKeys.LAST_SYNC_STARTED_AT] = payload["updatedAtTimestamp"]
    try:
        await graph_provider.batch_upsert_nodes([payload], CollectionNames.APPS.value)
    except Exception as e:
        # connector_id passed too: the repair route calls this outside a run.
        error_class.log_failure(
            logger, "sync_step.failed", e, level=logging.ERROR,
            step="status_write", app_status=status, connector_id=connector_id,
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
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.WARNING, step="deleting_check")
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
        }
        if outcome is not None else {}
    )
    if isinstance(action_items, list):
        # Text, as the summary is; None when nothing is left to do, which clears the card's badge.
        ended[ConnectorStateKeys.ACTION_ITEMS] = (
            json.dumps([item.model_dump(by_alias=True) for item in action_items]) if action_items else None
        )
    if lease is None or not lease.lost.is_set():
        # Not over a delete: the status is an upsert, so writing it after the
        # delete removed the doc resurrected a ghost App node, and writing it
        # during one let a second DELETE through the "already deleting" guard.
        if await _deleting_or_gone(graph_provider, logger, connector_id):
            logger.info("Not writing IDLE: the connector is being deleted")
        else:
            # A served search reads this connector with the stamps once it is IDLE, so what the sync wrote is in the cached viewer scopes first:
            # the version moves before the status does (the call after dispose stays, for the record caches).
            from app.services.cache.invalidation_hooks import notify_principals_changed

            await notify_principals_changed(org_id)
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
                error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="idle_write")
    elif owed:
        # The new owner's status is not ours to write, but the debt is: whatever
        # it runs next must be a full sync. Only ever set, so it cannot undo
        # anything the new owner wrote, and update_node cannot create the node.
        try:
            await graph_provider.update_node(connector_id, CollectionNames.APPS.value, owed)
        except Exception as e:
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="owed_full_sync")
    if lease is not None and coordinator is not None:
        await coordinator.end(lease)
    if connector is not None:
        # Built for this run only, so its HTTP sessions and producer die with it.
        try:
            await dispose_connector(connector)
        except Exception as e:
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.WARNING, step="dispose")

    # The sync may have added or removed records; drop the query service's
    # cached view of this connector so the next search sees them. Every entry
    # path (event, factory startup resume) ends here, so this is the one place
    # it has to be done.
    from app.services.cache.invalidation_hooks import notify_connector_sync_completed

    try:
        await notify_connector_sync_completed(connector_id, org_id)
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.WARNING, step="cache_invalidation")

    # A slot just freed, so anything parked at the limit can go. Failures here
    # must not escape: this runs in a detached finalizer whose real job is the
    # status write and the lease release above.
    try:
        await drain_queued_syncs(graph_provider, logger)
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="drain")

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
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="kh_scope_begin")
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
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="kh_scope_end")


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
        # One pass serves every org, and its runtime callers sit inside one org's context, where binding another raises OrgMismatch.
        return await spawn_detached(_drain_once(graph_provider, logger, dispatcher), name="drain_queued_syncs")


_DRAIN_LOCKS: dict[str, asyncio.Lock] = {}


def _drain_lock() -> asyncio.Lock:
    return _DRAIN_LOCKS.setdefault("drain", asyncio.Lock())


async def _drain_once(
    graph_provider: IGraphDBProvider, logger: logging.Logger, dispatcher: "SyncEventDispatcher"
) -> list[str]:
    """Gather every active org's queue, each under its org, then publish oldest first across orgs within one budget."""
    from app.connectors.core.sync.sync_coordinator import get_coordinator
    from app.connectors.core.sync.sync_dispatcher import SubmitResult

    owed: list[tuple[str, dict, dict]] = []     # (org id, queued doc, app doc)
    owners: dict[str, bool] = {}                # owed connector id -> claimed by an org visited so far
    failed: list[str] = []                      # orgs whose read or app listing failed this pass
    try:
        await for_each_org(
            graph_provider, "drain_queued_syncs",
            functools.partial(_read_org_queue, graph_provider, logger, owed, owners, failed), logger, active=True,
        )
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="drain")
        return []
    lost = sorted(cid for cid, mine in owners.items() if not mine)
    if not owed and not lost:
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
    # A failed org may own them (Arango and Neo4j show its queue to the next org): no ownership claim without every org's answer.
    if lost and not failed:
        logger.warning(
            "Pending resync flagged on connectors that belong to no active org; leaving them alone",
            extra={"unowned": len(lost), "target_connector_ids": ",".join(lost)},
        )

    # Oldest first, by when each connector entered the queue, across orgs.
    # Unordered, whoever the org traversal met first won every freed slot.
    def arrived(entry: tuple[str, dict, dict]) -> int:
        doc = entry[1]
        return int(doc.get("queuedAtTimestamp") or doc.get("updatedAtTimestamp") or 0)

    started: list[str] = []
    for org_id, doc, app in sorted(owed, key=arrived):
        if budget is not None and len(started) >= budget:
            break
        if len(started) >= _DRAIN_MAX_PER_PASS:
            logger.warning(
                "Drain hit its per-pass cap; the next completion or sweep takes the rest",
                extra={"cap": _DRAIN_MAX_PER_PASS, "still_queued": len(owed) - len(started)},
            )
            break
        # Published under its own org, so the event is signed for the org it names.
        with for_org(org_id, job="drain_queued_syncs"):
            if await dispatcher.submit(_resync_spec(org_id, app)) is not SubmitResult.ACCEPTED:
                continue
            started.append(doc["id"])
            # Restamp as well as clear: the staleness arm re-issues anything
            # older than the grace window, and without a fresh timestamp a row whose
            # event never arrives qualifies again on every later pass, forever.
            try:
                await graph_provider.update_node(
                    doc["id"],
                    CollectionNames.APPS.value,
                    {
                        ConnectorStateKeys.PENDING_RESYNC: False,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    },
                )
            except Exception as e:
                # Published already; the flag stays set, so a later pass re-issues it. One connector's write must not stop the pass.
                error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="drain", target_connector_id=doc["id"])

    if started:
        logger.info(
            "Released queued syncs", extra={"released": len(started), "target_connector_ids": ",".join(started)}
        )
    return started


def _is_inactive(doc: dict) -> bool:
    # A connector disabled or signed out while it waited is owed nothing: the
    # start path would skip it, so publishing it only repeats on every pass.
    return doc.get(ConnectorStateKeys.IS_ACTIVE) is False or doc.get(ConnectorStateKeys.IS_AUTHENTICATED) is False


def _is_owed(doc: dict, stale_before: int) -> bool:
    # pendingResync marks not-yet-submitted, so the next drain does not re-issue
    # a connector whose event is already in flight. Status stays QUEUED until the
    # sync starts and writes SYNCING, so anything watching still sees work owed.
    #
    # A submitted request whose event never arrives would otherwise strand the
    # connector: QUEUED, unflagged, and invisible to every later drain. After the
    # grace window it is treated as owed again. A duplicate of an event that did
    # arrive late is harmless: the start path drops a request older than the sync
    # already running for it.
    return bool(doc.get(ConnectorStateKeys.PENDING_RESYNC)) or int(doc.get("updatedAtTimestamp") or 0) < stale_before


async def _read_org_queue(
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    owed: list[tuple[str, dict, dict]],
    owners: dict[str, bool],
    failed: list[str],
    org_id: str,
) -> None:
    """One org's part of a drain pass, run under that org: drop its inactive queued connectors, note its owed ones.

    App documents do not carry their org, so a queued connector is this org's only if the org's ``orgAppRelation`` edges list it.
    """
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
        stale_before = _now_ms() - _QUEUE_GRACE_MS
        # Arango and Neo4j answer every org's queue whatever is bound: skip what an earlier org has claimed (first org wins), and list
        # this org's apps only when something is left to act on.
        actionable = [
            doc for doc in queued or []
            if doc.get("id") and not owners.get(doc["id"]) and (_is_inactive(doc) or _is_owed(doc, stale_before))
        ]
        apps = {
            app.get("_key") or app.get("id"): app
            for app in await graph_provider.get_org_apps(org_id, active_only=False, raise_on_error=True) or []
        } if actionable else {}
    except Exception as e:
        # Under for_org: the line carries this org's org_id.
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="drain")
        failed.append(org_id)
        return

    for doc in actionable:
        cid = doc["id"]
        app = apps.get(cid)
        if app is None:                             # another org's, or linked to none
            if _is_owed(doc, stale_before):
                owners.setdefault(cid, False)
        elif _is_inactive(doc):
            owners[cid] = True
            try:
                await graph_provider.update_node(
                    cid,
                    CollectionNames.APPS.value,
                    {
                        "status": AppStatus.IDLE.value,
                        ConnectorStateKeys.PENDING_RESYNC: False,
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    },
                )
                logger.info("Dropped the queued sync of an inactive connector", extra={"target_connector_id": cid})
            except Exception as e:
                error_class.log_failure(
                    logger, "sync_step.failed", e, level=logging.ERROR, step="drain", target_connector_id=cid
                )
        elif app.get(ConnectorStateKeys.IS_ACTIVE) is not True:
            # The start path skips an App with isActive unset, and get_org_apps(active_only=True) never resolved one.
            owners.setdefault(cid, False)
        else:
            owners[cid] = True
            owed.append((org_id, doc, app))


def _resync_spec(org_id: str, app: dict) -> "SyncSpec":
    from app.connectors.core.sync.sync_dispatcher import SyncSpec

    # full_sync stays False: the start path already merges the
    # connector's own pendingFullSync flag, and deciding it here too
    # would put that decision in two places.
    return SyncSpec(
        connector_id=app.get("_key") or app.get("id"),
        connector_name=(app.get("type") or app.get("name") or "").replace(" ", "").lower(),
        org_id=org_id,
    )


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
            logger.warning("Pending resync not re-issued here (no dispatcher); left flagged for the sweep")
            return
        result = await dispatcher.submit(resync_spec)
        if result is not SubmitResult.ACCEPTED:
            # Still pending by definition — leave the flag for the sweep.
            logger.warning("Re-issued resync was not accepted; left flagged", extra={"submit_result": result.value})
            return
        await graph_provider.update_node(
            connector_id,
            CollectionNames.APPS.value,
            {ConnectorStateKeys.PENDING_RESYNC: False},
        )
        logger.info("Re-issued the declined resync")
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="reissue")


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


async def _finish_full_sync_memberships(
    connector: "BaseConnector",
    connector_id: str,
    graph_provider: IGraphDBProvider,
    logger: logging.Logger,
    seen: FullSyncSeen,
) -> bool:
    """Give groups whose members came one at a time the full list, in one write
    each; only now is that list known to be complete. False when that failed
    and another full sync is owed."""
    finish = getattr(getattr(connector, "data_entities_processor", None), "finish_full_sync_memberships", None)
    if not callable(finish):
        return True
    try:
        await finish(connector_id, seen)
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="finish_memberships")
        await note_structure_change(graph_provider, connector_id, logger)  # part of it may have run
        return False
    return True


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
    if not coverage.lists_everything:
        logger.debug("Full sync: the source lists only part of itself; nothing is unlinked")
        if not coverage.complete:
            return True
        # The groups' members were still read in full, so a member removed at the source goes.
        return await _finish_full_sync_memberships(connector, connector_id, graph_provider, logger, seen)
    if not coverage.complete:
        logger.warning(
            "Full sync skipped part of the source after an error; nothing it did not see is unlinked this time"
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
        logger.warning("Full sync wrote nothing; leaving every link in place")
        return True
    if not await _finish_full_sync_memberships(connector, connector_id, graph_provider, logger, seen):
        return False
    try:
        deleted, ok = await graph_provider.unlink_nodes_missed_by_full_sync(
            connector_id,
            seen.by_collection,
            window_start_ms=coverage.window_start_ms,
            windowed_group_types=coverage.windowed_group_types,
        )
    except Exception as e:
        error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="unlink")
        await note_structure_change(graph_provider, connector_id, logger)  # part of it may have run
        return False
    if deleted or not ok:
        # a structural write made outside the transaction store (it removes hierarchy, group, inherit and grant edges)
        await note_structure_change(graph_provider, connector_id, logger)
    if ok:
        logger.info("Full sync removed the edges of nodes it did not see", extra={"removed_edges": deleted})
    return bool(ok)


def _run_outcome(*, stopped: bool, failed: bool, skipped: bool, finished: bool, lost: bool) -> SyncRunOutcome:
    # A lost lease ends _run_until_aborted without raising, so `finished` is set: the run was still cut short.
    if stopped or lost:
        return SyncRunOutcome.STOPPED
    if failed:
        return SyncRunOutcome.FAILED
    if skipped:
        return SyncRunOutcome.SKIPPED
    # Only a BaseException other than CancelledError (SystemExit) leaves every flag unset.
    return SyncRunOutcome.SUCCEEDED if finished else SyncRunOutcome.STOPPED


def _stop_cause(lease: SyncLease | None, coordinator: SyncCoordinator | None, *, lost: bool) -> str:
    """Why a stopped run stopped, for its sync_run.end line."""
    if lost:
        return "lease_lost"
    if lease is not None and lease.stop_requested.is_set():
        return "requested"
    if getattr(coordinator, "shutting_down", False) is True:
        return "shutdown"
    return "cancelled"


_START_STATUS_TRIES = 3


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
    failed = False
    finished = False
    unlink_failed = False
    skipped_code: str | None = None
    full_sync = start_status == AppStatus.FULL_SYNCING.value
    processor, seen = None, None
    scope_generation: int | None = None
    # One id per run, stamped on every record-event this sync publishes so
    # downstream processing can be correlated back to the run.
    run_sync_id = str(uuid4())
    run_sync_token = set_run_sync_id(run_sync_id)
    run_stats = SyncRunStats()
    run_stats_token = bind_run_stats(run_stats)
    # On every line of the run: the connector's, the processor's and the finalizer task's (it copies the context).
    fields_token = bind_log_fields(run_id=run_sync_id, connector_id=connector_id)
    sync_type = "full" if full_sync else "incremental"
    log_event(logger, "sync_run.start", sync_type=sync_type)
    error: Exception | None = None
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
        # Until this lands the connector is still served from its stamps (`app_is_served`), and the grants this run writes are hidden from it.
        # Retried, not required: a write that keeps failing (a schema that rejects it) must not stop every sync of the connector.
        for attempt in range(_START_STATUS_TRIES):
            if await write_app_status(
                graph_provider, logger, connector_id, start_status,
                full_sync_started=full_sync, run_started=True,
            ):
                break
            if attempt + 1 < _START_STATUS_TRIES:
                await asyncio.sleep(attempt + 1)
        else:
            logger.error("the %s status of connector %s could not be written; it is read from its stamps until the run ends", start_status, connector_id)
        # A resync asked for before this run began is served by it. Left set, the
        # flag handed the finalizer a request that was already satisfied, and
        # the connector synced twice back to back. Except a full sync owed to an
        # incremental run: then the flag must survive so the finalizer re-issues
        # it (the re-issue merges pendingFullSync and runs full).
        try:
            doc = await graph_provider.get_document(
                document_key=connector_id, collection=CollectionNames.APPS.value
            )
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
        except Exception as e:
            error_class.log_failure(logger, "sync_step.failed", e, level=logging.WARNING, step="clear_pending_resync")
        incomplete: IncompleteSyncError | None = None
        try:
            if lease is None:
                await _run_sync_alone(connector, connector_id)
            else:
                await _run_until_aborted(connector, lease)
        except IncompleteSyncError as exc:
            # It ran to the end and kept what it could not read as stored, so the removal below is still safe;
            # the run fails after it.
            incomplete = exc
        finished = True
        # Only for a sync that ran to the end: one cut off partway saw only part
        # of the source, and everything it had not reached keeps its links.
        if seen is not None and not (
            lease is not None and (lease.stop_requested.is_set() or lease.lost.is_set())
        ):
            unlink_failed = not await _unlink_what_full_sync_missed(
                connector, connector_id, graph_provider, logger, seen
            )
        if incomplete is not None:
            raise incomplete
    except asyncio.CancelledError:
        stopped = True
        raise
    except ConnectorSyncSkippedError as exc:
        # Not a crash: the connector declined to run (e.g. Local FS with no
        # desktop connected). Logged only; the UI reads live presence.
        skipped_code = exc.code
    except Exception as exc:
        failed = True
        error = exc
        raise
    finally:
        if seen is not None:
            try:
                processor.end_full_sync_run(connector_id, seen)
            except Exception as e:
                # Logged, not raised: the finalizer below must still run.
                error_class.log_failure(logger, "sync_step.failed", e, level=logging.ERROR, step="full_sync_tracking")
        reset_run_sync_id(run_sync_token)
        # Before the finalizer task copies the context: its writes are not the run's.
        reset_run_stats(run_stats_token)
        # A stop that arrives through the lease ends _run_until_aborted without
        # raising, so it has to be read here or it would log as a clean finish.
        if lease is not None and lease.stop_requested.is_set():
            stopped = True
        lease_lost = lease is not None and lease.lost.is_set()
        outcome = _run_outcome(
            stopped=stopped, failed=failed, skipped=skipped_code is not None, finished=finished, lost=lease_lost
        )
        # A stop or a lost lease returns from _run_until_aborted without
        # raising, so `finished` alone would call that run complete.
        # A failed unlink owes one too: what the sync did not see is
        # still linked, revoked access included.
        full_sync_cut_short = full_sync and (not finished or stopped or unlink_failed or lease_lost)
        stop_cause = _stop_cause(lease, coordinator, lost=lease_lost) if outcome is SyncRunOutcome.STOPPED else None
        end_fields: dict[str, Any] = {
            "sync_type": sync_type,
            "duration_ms": int((time.monotonic() - start) * 1000),
            # The next sync of this connector runs in full.
            "full_sync_cut_short": full_sync_cut_short or None,
            **run_stats.end_fields(),
        }
        # The run's one failure line; the task manager then logs the same exception at DEBUG.
        if error is not None:
            error_class.log_failure(logger, "sync_run.end", error, reason=stop_cause, **end_fields)
        elif outcome is SyncRunOutcome.STOPPED:
            log_event(
                logger, "sync_run.end", level=logging.WARNING if lease_lost else logging.INFO,
                outcome="failure", error_class="cancelled", reason=stop_cause, **end_fields,
            )
        elif outcome is SyncRunOutcome.SKIPPED:
            log_event(logger, "sync_run.end", outcome="skipped", reason=skipped_code, **end_fields)
        else:
            log_event(
                logger, "sync_run.end", level=logging.WARNING if full_sync_cut_short else logging.INFO,
                outcome="partial" if full_sync_cut_short else "success", **end_fields,
            )
        # Run the finalizer as its own task: a second cancel (another stop
        # request, appDisabled, shutdown) aimed at this task must not be able to
        # kill the IDLE write and leave the connector stuck SYNCING, nor skip
        # the lease release and leave it un-startable for a full TTL.
        cleanup = asyncio.get_running_loop().create_task(
            _finalize(
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
                full_sync_cut_short=full_sync_cut_short,
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
            name=f"sync_cleanup_{connector_id}",
        )
        _cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(_cleanup_tasks.discard)
        # shield: cancelling this task cancels whatever it awaits
        # (Task._fut_waiter), so a bare `await cleanup` would let a second
        # cancel kill the cleanup itself. Shielded, the cancel interrupts
        # only this await and the cleanup runs to completion on the loop.
        try:
            await asyncio.shield(cleanup)
        finally:
            # After the cleanup task copied the context, so its lines keep the run's fields.
            reset_log_fields(fields_token)
