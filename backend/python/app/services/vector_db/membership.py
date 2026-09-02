"""Resolve and rewrite VRID-level connector/group membership on vector points.

Graph records are the source of truth. Vector points are keyed by virtual
record ID (content identity), so one VRID can attach to several connector
instances and record groups. Callers always recompute both arrays from the
graph rather than patching a single id in the vector DB.

Concurrency boundary
--------------------
Recompute-then-write is not atomic, so two writers for one VRID can interleave
and let the staler read win. ``_vrid_lock`` closes that within a process, which
is where the overlap actually happens: one indexing consumer runs many events
concurrently under MAX_CONCURRENT_INDEXING.

It is deliberately **not** a distributed lock. Across replicas the race is left
open, because:

* No vector provider offers compare-and-set on a payload, so a correct
  cross-process guard means real mutual exclusion — Redis lock plus TTL,
  renewal, and fencing tokens, since a lease can expire while its holder is
  still writing. That is a new failure mode (stranded locks, split-brain
  writers) in exchange for closing one.
* The broker cannot serialise it either: record lifecycle events cannot be keyed
  by VRID (a VRID is minted during processing and changes on an N:1 split), and
  Redis Streams — the default broker — has no key affinity at all.
* The blast radius is small and self-healing. Retrieval is graph-first, so these
  arrays are not on the read path today; a lost update means a point misses one
  connectorId until the next write or a backfill pass, and
  ``IndexingPipeline.sync_vector_membership`` is idempotent by construction.

The repair path is the per-connector backfill: clear ``vectorMembershipBackfilled``
on the app document and the scanner re-walks that connector. Revisit this if
retrieval ever becomes vector-first, because then a stale array becomes a missing
search result rather than a cosmetic one.
"""
from __future__ import annotations

import asyncio
import weakref
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from app.config.constants.arangodb import CollectionNames
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)


@runtime_checkable
class CollectionLocator(Protocol):
    """Where a virtual record's points live.

    Declared here, by the consumer, rather than in the strategy layer: this
    module must not depend on *how* collection names are chosen, only on being
    told which ones to write to. ``VirtualRecordCollectionLocator`` in
    ``collection_locator.py`` satisfies it structurally.
    """

    def collections_for_records(
        self, records: Sequence[Mapping[str, Any]]
    ) -> Sequence[str]:
        """Collections holding points for these graph records. Pure and sync.

        Callers pass the documents membership resolution already fetched, so
        this costs no extra round trip.
        """
        ...

    async def all_collections(self, *, fresh: bool = False) -> Sequence[str]:
        """Every managed collection.

        ``fresh=True`` asks for an uncached view; delete paths use it, because
        acting on a stale enumeration leaves points in collections it missed.

        The only correct target when a VRID has no graph record left to
        resolve from — which is exactly when deleting everywhere is safe: a
        VRID still shared with a live record would have taken the per-record
        path instead.
        """
        ...


_cv_connector_ids: ContextVar[tuple[str, ...]] = ContextVar(
    "vector_connector_ids", default=()
)
_cv_record_group_ids: ContextVar[tuple[str, ...]] = ContextVar(
    "vector_record_group_ids", default=()
)

_RECORD_GROUPS_PREFIX = f"{CollectionNames.RECORD_GROUPS.value}/"


def set_membership_context(
    connector_ids: Sequence[str],
    record_group_ids: Sequence[str],
) -> tuple[Token, Token]:
    return (
        _cv_connector_ids.set(tuple(connector_ids)),
        _cv_record_group_ids.set(tuple(record_group_ids)),
    )


def reset_membership_context(tokens: tuple[Token, Token]) -> None:
    _cv_connector_ids.reset(tokens[0])
    _cv_record_group_ids.reset(tokens[1])


def vector_point_payload(metadata: dict, page_content: str) -> dict[str, Any]:
    """Build a VectorPoint payload including current VRID membership."""
    return {
        "page_content": page_content,
        "metadata": metadata,
        CONNECTOR_IDS_FIELD: list(_cv_connector_ids.get()),
        RECORD_GROUP_IDS_FIELD: list(_cv_record_group_ids.get()),
    }


# Membership writes are read-graph → recompute → overwrite, with no CAS in any
# vector provider. Two concurrent writers for one VRID would otherwise let the
# staler read win and drop an instance permanently. Locks are weakly held so the
# map does not grow with every VRID ever touched; a caller keeps its lock alive
# for the duration of the critical section.
_vrid_locks: "weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)

# A held lock blocks every later write for that VRID, so the critical section is
# bounded rather than trusting every graph/vector call to time out on its own.
# On expiry membership is left stale — the post-index resync and subsequent
# events re-apply it — which is strictly better than stalling the VRID forever.
# Cap on simultaneous graph calls when resolving one VRID's membership.
MEMBERSHIP_RESOLVE_CONCURRENCY = 32

MEMBERSHIP_LOCK_TIMEOUT_SECONDS = 120

# Deleting points is irreversible, and "no records remain" can be a stale read on
# a cluster whose followers lag the commit that published this event. One
# confirming re-read after a short pause costs nothing on a genuine delete and
# turns a lagging replica from data loss into a no-op.
EMPTY_CONFIRM_DELAY_SECONDS = 0.5


def _vrid_lock(virtual_record_id: str) -> asyncio.Lock:
    """Get-or-create the lock for a VRID on the running event loop.

    No guard lock is needed: there is no await between the lookup and the insert,
    so this is atomic on a single-threaded event loop.

    The key includes the loop id because indexing runs some work on the record
    consumer's worker-thread loop and some on the main loop. An ``asyncio.Lock``
    binds to the first loop that awaits it and raises on any other, so a single
    shared lock per VRID would turn a cross-loop call into a hard error. Locks
    are per (loop, VRID): each loop serialises its own writers, which is what
    actually prevents the lost update.
    """
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    key = (loop_id, virtual_record_id)
    lock = _vrid_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _vrid_locks[key] = lock
    return lock


def _record_key(item: Any) -> Optional[str]:
    if isinstance(item, str) and item:
        return item
    if isinstance(item, dict):
        key = item.get("_key") or item.get("id")
        return str(key) if key else None
    return None


def remaining_record_keys(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = _record_key(item)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _add_unique(values: list[str], seen: set[str], candidate: Any) -> None:
    if not candidate:
        return
    value = str(candidate)
    if value and value not in seen:
        seen.add(value)
        values.append(value)


def _connector_id_from_record(record: Any) -> Optional[str]:
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get("connectorId") or record.get("connector_id")
    return getattr(record, "connector_id", None) or getattr(record, "connectorId", None)


def _record_group_id_from_record(record: Any) -> Optional[str]:
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get("recordGroupId") or record.get("record_group_id")
    return getattr(record, "record_group_id", None) or getattr(
        record, "recordGroupId", None
    )


def _record_group_id_from_edge(edge: dict) -> Optional[str]:
    """Group id for a ``belongsTo`` edge, or None if it points somewhere else.

    Collections (connector type "KB") deliberately yield None. Their records
    carry no ``recordGroupId`` and their ``belongsTo`` edge targets
    ``apps/<kbId>`` rather than a record group, because a Collection has no
    container below itself — the Collection *is* the container, and its id is
    already in ``connectorIds``.

    So an all-Collection VRID ends up with an empty ``recordGroupIds``, and that
    is the intended state, not a backfill that missed it. Anything filtering by
    container has to read ``connectorIds`` for Collections. Do not widen this to
    accept ``apps/`` targets without changing that contract first.
    """
    to_id = edge.get("_to") or ""
    if isinstance(to_id, str) and to_id.startswith(_RECORD_GROUPS_PREFIX):
        return to_id[len(_RECORD_GROUPS_PREFIX):]
    return None


@dataclass(frozen=True)
class VirtualRecordState:
    """Everything the graph knows about one VRID, resolved in a single pass.

    ``records`` is carried alongside the two id arrays because resolving them
    already fetches every record document. A caller that needs to know which
    collections a VRID occupies maps these through the active strategy — a pure
    function over data already in hand, rather than a second graph walk.
    """

    connector_ids: list[str]
    record_group_ids: list[str]
    records: list[Any]
    #: False when the graph did not return a document for every record key it
    #: reported for this VRID. Both providers' ``get_document`` swallow every
    #: exception and return ``None``, so a dropped connection is otherwise
    #: indistinguishable from "this record does not exist" — and a shorter
    #: ``records`` list makes a collection look abandoned when it is not.
    #: Destructive callers must refuse to act on an incomplete read.
    complete: bool = True


async def resolve_vector_membership(
    graph_provider,
    virtual_record_id: str,
    current_record: Any = None,
) -> tuple[list[str], list[str]]:
    """Return unique ``connectorIds`` and ``recordGroupIds`` for a VRID.

    Thin view over :func:`resolve_virtual_record_state` for callers that only
    need the membership arrays.
    """
    state = await resolve_virtual_record_state(
        graph_provider, virtual_record_id, current_record
    )
    return state.connector_ids, state.record_group_ids


async def resolve_virtual_record_state(
    graph_provider,
    virtual_record_id: str,
    current_record: Any = None,
) -> VirtualRecordState:
    """Resolve a VRID's membership arrays and the records they came from.

    ``recordGroupIds`` come from ``belongsTo`` → ``recordGroups`` (primary plus
    shared-with-me), not the scalar ``recordGroupId`` alone.
    """
    connector_ids: list[str] = []
    record_group_ids: list[str] = []
    seen_connectors: set[str] = set()
    seen_groups: set[str] = set()

    record_keys: list[str] = []
    docs: list[Any] = []
    if virtual_record_id:
        raw = await graph_provider.get_records_by_virtual_record_id(virtual_record_id)
        record_keys = remaining_record_keys(raw)

    if record_keys:
        # Fan out rather than walking record-by-record: a deduped VRID otherwise
        # costs 1 + 2N serial round trips, and the backfill pays that per VRID for
        # every record in the corpus. Results are consumed in key order so the
        # resulting arrays stay deterministic.
        # Bounded: record_keys is every record sharing this content, so a piece
        # of boilerplate duplicated across the corpus would otherwise open 2N
        # simultaneous graph calls and saturate the connection pool for
        # everything else — and the backfill repeats it for every VRID.
        semaphore = asyncio.Semaphore(MEMBERSHIP_RESOLVE_CONCURRENCY)

        async def _get_document(key: str):
            async with semaphore:
                return await graph_provider.get_document(
                    key, CollectionNames.RECORDS.value
                )

        async def _get_edges(key: str):
            async with semaphore:
                return await graph_provider.get_edges_from_node(
                    f"{CollectionNames.RECORDS.value}/{key}",
                    CollectionNames.BELONGS_TO.value,
                )

        docs, edge_lists = await asyncio.gather(
            asyncio.gather(*(_get_document(key) for key in record_keys)),
            asyncio.gather(*(_get_edges(key) for key in record_keys)),
        )

        for rec, edges in zip(docs, edge_lists):
            _add_unique(connector_ids, seen_connectors, _connector_id_from_record(rec))
            _add_unique(record_group_ids, seen_groups, _record_group_id_from_record(rec))
            if not isinstance(edges, (list, tuple)):
                continue
            for edge in edges:
                if isinstance(edge, dict):
                    _add_unique(
                        record_group_ids, seen_groups, _record_group_id_from_edge(edge)
                    )

    if not connector_ids and current_record is not None:
        _add_unique(
            connector_ids, seen_connectors, _connector_id_from_record(current_record)
        )
        _add_unique(
            record_group_ids, seen_groups, _record_group_id_from_record(current_record)
        )

    # Only documents the graph actually returned: a None from a failed
    # get_document must not reach a strategy as if it were a record.
    resolved = [doc for doc in docs if isinstance(doc, dict)]
    return VirtualRecordState(
        connector_ids=connector_ids,
        record_group_ids=record_group_ids,
        records=resolved,
        # A key that resolved to nothing is either a read that failed or a
        # record deleted mid-flight. Neither is safe to read as "no record
        # here" on a destructive path, so both count as incomplete.
        complete=len(resolved) == len(record_keys),
    )


async def sync_vector_membership(
    vector_db,
    locator: CollectionLocator,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> None:
    """Recompute both arrays from graph and merge them onto all chunks of a VRID."""
    if not virtual_record_id or vector_db is None or locator is None:
        return
    lock = _vrid_lock(virtual_record_id)
    async with asyncio.timeout(MEMBERSHIP_LOCK_TIMEOUT_SECONDS):
        async with lock:
            await _sync_vector_membership_locked(
                vector_db, locator, graph_provider, virtual_record_id, logger
            )


async def _sync_vector_membership_locked(
    vector_db,
    locator: CollectionLocator,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> Optional[list[str]]:
    """Body of :func:`sync_vector_membership`; caller already holds the VRID lock.

    Returns the collections written, or None when it declined to write — the
    delete-aware caller uses that to tell "this VRID now lives here and nowhere
    else" apart from "the graph did not give me a usable answer".
    """
    state = await resolve_virtual_record_state(graph_provider, virtual_record_id)
    connector_ids, record_group_ids = state.connector_ids, state.record_group_ids

    # Every record carries a connectorId, so an empty result means the graph
    # returned nothing for this VRID — a lagging read or a record mid-delete, not
    # a record that genuinely belongs to no connector. Writing it would replace
    # good membership with [] and make those points invisible to instance-scoped
    # filters, with nothing to trigger a repair. Same reasoning as the confirming
    # re-read on the delete path.
    if not connector_ids:
        if logger is not None:
            logger.warning(
                "Skipping vector membership write for %s: graph resolved no "
                "connectorIds, which would blank existing membership",
                virtual_record_id,
            )
        return None

    # Derived from the records just fetched, so under a strategy that splits by
    # org or connector type this rewrites every collection the VRID occupies.
    # Writing only one would leave the others advertising membership the graph
    # no longer agrees with.
    collections = locator.collections_for_records(state.records)
    if not collections:
        if logger is not None:
            logger.warning(
                "Skipping vector membership write for %s: resolved no collections",
                virtual_record_id,
            )
        return None

    filt = await vector_db.filter_collection(
        must={"virtualRecordId": virtual_record_id}
    )
    for collection_name in collections:
        await vector_db.set_payload(
            collection_name,
            {
                CONNECTOR_IDS_FIELD: connector_ids,
                RECORD_GROUP_IDS_FIELD: record_group_ids,
            },
            filt,
        )
    if logger is not None:
        # Counts at info, contents at debug: the backfill runs this for every
        # VRID in the corpus, and full arrays at info would drown the log.
        logger.debug(
            "Rewrote vector membership for virtual_record_id %s across %d "
            "collection(s) (%d connectorIds, %d recordGroupIds)",
            virtual_record_id,
            len(collections),
            len(connector_ids),
            len(record_group_ids),
        )
        logger.debug(
            "virtual_record_id %s collections=%s connectorIds=%s recordGroupIds=%s",
            virtual_record_id,
            collections,
            connector_ids,
            record_group_ids,
        )
    return list(collections)


async def _drop_points_where_no_record_remains(
    vector_db,
    locator: CollectionLocator,
    graph_provider,
    virtual_record_id: str,
    live_collections: Sequence[str],
    logger,
) -> None:
    """Remove a VRID's points from collections nothing references any more.

    Only reachable under a multi-collection strategy, and only because
    deduplication lets one VRID be indexed into several collections: the same
    file reaching PipesHub through Drive and through Slack shares a content
    identity but gets vectors in each connector type's own collection.

    When the Drive record is then deleted, the VRID still has a Slack record,
    so the rewrite branch above runs rather than the delete branch — and
    without this, the Drive collection would keep points for a record that no
    longer exists. They stay searchable, their membership stops matching the
    graph, and their metadata cites a deleted record.

    Deleting is irreversible, so a *non-empty* stale set is confirmed against a
    second graph read before acting. The re-read costs nothing in the common
    case because the set is empty — always, under ``single``, and under any
    strategy until a VRID's collections actually shrink.

    The sweep cannot tell a collection the VRID *left* from one it was never
    in, so under a multi-collection strategy it issues a filtered delete
    against each — a no-op where nothing matches. That is bounded by the number
    of managed collections (a handful in practice, ~30 at the ceiling for
    per-connector-type) and only happens on the rewrite path, so it is paid per
    departing record rather than per search. Narrowing it would need the VRID's
    previous collection set, which nothing records.
    """
    managed = set(await locator.all_collections(fresh=True))
    stale = managed - set(live_collections)
    if not stale:
        return

    # A partial read would name collections as stale that still have records.
    # Confirming costs one query on a path that is already rare.
    confirmed_state = await resolve_virtual_record_state(
        graph_provider, virtual_record_id
    )
    if not confirmed_state.complete:
        # Fail closed: an unresolved record document would drop its collection
        # out of `still_live` and leave it in `stale`, deleting points a live
        # record still needs. Keeping them is recoverable — the sweep runs
        # again on the next rewrite, and the orphan sweeper is the backstop.
        if logger is not None:
            logger.warning(
                "Skipping stale-collection cleanup for virtual record %s: the "
                "graph did not return every record document, so a collection "
                "that still holds records could look abandoned",
                virtual_record_id,
            )
        return

    still_live = set(locator.collections_for_records(confirmed_state.records))
    stale &= managed - still_live
    if not stale:
        if logger is not None:
            logger.info(
                "Virtual record %s looked stale in some collections but the "
                "re-read disagreed — keeping their points",
                virtual_record_id,
            )
        return

    filt = await vector_db.filter_collection(
        must={"virtualRecordId": virtual_record_id}
    )
    for collection_name in sorted(stale):
        await vector_db.delete_points(collection_name=collection_name, filter=filt)
    if logger is not None:
        # Deliberately describes the *action*, not an effect it cannot observe:
        # `delete_points` reports no count, and most of these collections never
        # held the VRID at all, so the filtered delete matches nothing. Saying
        # "removed from N collections" reads as data loss on what is usually a
        # no-op. Debug rather than info because it fires per rewritten VRID —
        # one line per shared record on a large connector delete — and the
        # caller already logs the aggregate.
        logger.debug(
            "Cleared virtual_record_id %s from collection(s) it no longer "
            "belongs to: %s (a no-op in any it never had points in)",
            virtual_record_id,
            sorted(stale),
        )


async def rewrite_or_delete_virtual_record(
    vector_db,
    locator: CollectionLocator,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> str:
    """Delete points if no graph records remain; otherwise rewrite both arrays."""
    if not virtual_record_id or vector_db is None or locator is None:
        return "skipped"
    lock = _vrid_lock(virtual_record_id)
    async with asyncio.timeout(MEMBERSHIP_LOCK_TIMEOUT_SECONDS):
        async with lock:
            return await _rewrite_or_delete_locked(
                vector_db, locator, graph_provider, virtual_record_id, logger
            )


async def _rewrite_or_delete_locked(
    vector_db,
    locator: CollectionLocator,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> str:
    raw = await graph_provider.get_records_by_virtual_record_id(virtual_record_id)
    remaining = remaining_record_keys(raw)

    if not remaining:
        await asyncio.sleep(EMPTY_CONFIRM_DELAY_SECONDS)
        raw = await graph_provider.get_records_by_virtual_record_id(virtual_record_id)
        remaining = remaining_record_keys(raw)
        if remaining and logger is not None:
            logger.warning(
                "Virtual record %s looked unreferenced but records %s appeared on "
                "re-read — keeping its vectors",
                virtual_record_id,
                remaining,
            )

    if remaining:
        live_collections = await _sync_vector_membership_locked(
            vector_db, locator, graph_provider, virtual_record_id, logger
        )
        if live_collections is not None:
            await _drop_points_where_no_record_remains(
                vector_db,
                locator,
                graph_provider,
                virtual_record_id,
                live_collections,
                logger,
            )
        return "rewritten"

    # No record anywhere references this VRID, so there is nothing left to
    # resolve a collection from — and nothing that could still want these
    # points. A VRID shared with a live record would have taken the rewrite
    # branch above, which is what makes deleting across every managed
    # collection the correct scope here rather than an overreach.
    collections = await locator.all_collections(fresh=True)

    # Points first: the mapping is how an orphaned point set is found again, so it
    # must outlive the delete it describes. Dropping it first would strand the
    # vectors invisibly if the point delete then failed.
    filt = await vector_db.filter_collection(
        must={"virtualRecordId": virtual_record_id}
    )
    for collection_name in collections:
        await vector_db.delete_points(collection_name=collection_name, filter=filt)
    if logger is not None:
        logger.info(
            "Deleted vector points for virtual_record_id %s from %d collection(s)",
            virtual_record_id,
            len(collections),
        )

    try:
        await graph_provider.delete_nodes(
            keys=[virtual_record_id],
            collection=CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
        )
    except Exception as e:
        if logger is not None:
            logger.error(
                "Failed to delete virtualRecordToDocIdMapping for %s: %s",
                virtual_record_id,
                e,
            )
    return "deleted"
