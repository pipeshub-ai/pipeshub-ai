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
from typing import Any, Optional, Sequence

from app.config.constants.arangodb import CollectionNames
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)

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


async def resolve_vector_membership(
    graph_provider,
    virtual_record_id: str,
    current_record: Any = None,
) -> tuple[list[str], list[str]]:
    """Return unique ``connectorIds`` and ``recordGroupIds`` for a VRID.

    ``recordGroupIds`` come from ``belongsTo`` → ``recordGroups`` (primary plus
    shared-with-me), not the scalar ``recordGroupId`` alone.
    """
    connector_ids: list[str] = []
    record_group_ids: list[str] = []
    seen_connectors: set[str] = set()
    seen_groups: set[str] = set()

    record_keys: list[str] = []
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

    return connector_ids, record_group_ids


async def sync_vector_membership(
    vector_db,
    collection_name: str,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> None:
    """Recompute both arrays from graph and merge them onto all chunks of a VRID."""
    if not virtual_record_id or vector_db is None or not collection_name:
        return
    lock = _vrid_lock(virtual_record_id)
    async with asyncio.timeout(MEMBERSHIP_LOCK_TIMEOUT_SECONDS):
        async with lock:
            await _sync_vector_membership_locked(
                vector_db, collection_name, graph_provider, virtual_record_id, logger
            )


async def _sync_vector_membership_locked(
    vector_db,
    collection_name: str,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> None:
    """Body of :func:`sync_vector_membership`; caller already holds the VRID lock."""
    connector_ids, record_group_ids = await resolve_vector_membership(
        graph_provider, virtual_record_id
    )

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
        return

    filt = await vector_db.filter_collection(
        must={"virtualRecordId": virtual_record_id}
    )
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
            "Rewrote vector membership for virtual_record_id %s "
            "(%d connectorIds, %d recordGroupIds)",
            virtual_record_id,
            len(connector_ids),
            len(record_group_ids),
        )
        logger.debug(
            "virtual_record_id %s connectorIds=%s recordGroupIds=%s",
            virtual_record_id,
            connector_ids,
            record_group_ids,
        )


async def rewrite_or_delete_virtual_record(
    vector_db,
    collection_name: str,
    graph_provider,
    virtual_record_id: str,
    logger,
) -> str:
    """Delete points if no graph records remain; otherwise rewrite both arrays."""
    if not virtual_record_id or vector_db is None or not collection_name:
        return "skipped"
    lock = _vrid_lock(virtual_record_id)
    async with asyncio.timeout(MEMBERSHIP_LOCK_TIMEOUT_SECONDS):
        async with lock:
            return await _rewrite_or_delete_locked(
                vector_db, collection_name, graph_provider, virtual_record_id, logger
            )


async def _rewrite_or_delete_locked(
    vector_db,
    collection_name: str,
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
        await _sync_vector_membership_locked(
            vector_db, collection_name, graph_provider, virtual_record_id, logger
        )
        return "rewritten"

    # Points first: the mapping is how an orphaned point set is found again, so it
    # must outlive the delete it describes. Dropping it first would strand the
    # vectors invisibly if the point delete then failed.
    filt = await vector_db.filter_collection(
        must={"virtualRecordId": virtual_record_id}
    )
    await vector_db.delete_points(collection_name=collection_name, filter=filt)
    if logger is not None:
        logger.info("Deleted vector points for virtual_record_id %s", virtual_record_id)

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
