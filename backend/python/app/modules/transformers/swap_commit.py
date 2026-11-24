"""The commit of a replacement copy and what follows it (see ``app.modules.indexing.keep_old_copy``).

``SwapCommitter`` belongs to a ``SinkOrchestrator``. The run wrote its set under a fresh virtual record (Y) while the record kept
naming the old one (X):

* :meth:`commit` moves the record to Y in one conditional write, only while it still names X, still has Y pending and is
  running or waiting (a save made mid-run stores NOT_STARTED, and must not refuse the run). A refused commit removes the run's
  own pending copy and ends the run as superseded or deleted.
* :meth:`finish` runs after a commit: Y's membership, the matching twins of a forced run, the record's cache entry, and the
  release of X after a grace.
* :meth:`release_failed` runs when a record fails for good: its pending copy is released.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.core.org_context import for_org, spawn_detached
from app.exceptions.indexing_exceptions import (
    IndexingError,
    RecordDeletedError,
    SupersededRun,
)
from app.modules.indexing import keep_old_copy
from app.modules.indexing.stray_sweep import BLOCK_ID_FIELD, sweep_max_points
from app.modules.pipeline import parser_identity
from app.services.cache.invalidation_hooks import notify_record_vrid_moved
from app.services.graph_db.common.record_visibility import (
    PENDING_VIRTUAL_RECORD_ID,
    is_live_record,
)
from app.services.graph_db.conditions import EQ, ONE_OF, Cond
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.structure_generation import leaving_vrid
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
    VIRTUAL_RECORD_ID_FIELD,
)
from app.services.vector_db.filters import org_for_filter
from app.services.vector_db.membership import (
    mark_scope_stale,
    rewrite_or_delete_virtual_record,
    sync_vector_membership,
)
from app.services.vector_db.models import ScrollWindowExceededError, VectorChunkPayload
from app.services.vector_db.strategy import RecordContext

if TYPE_CHECKING:
    from app.models.entities import Record
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.modules.transformers.transformer import TransformContext

#: Twins re-pointed at once.
TWIN_CONCURRENCY = 8
#: Writes of the undo of a move whose copy could not be shown whole, and the pause before the next one.
UNDO_ATTEMPTS = 3
UNDO_BACKOFF_SECONDS = 0.2
_RECORDS = CollectionNames.RECORDS.value
#: What a twin takes over from the record when it moves to its copy (and gives back when the move is undone).
_TWIN_IDENTITY = ("summaryDocumentId", "secretsRedacted")
_MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value


class SwapCommitter:
    def __init__(self, orchestrator: SinkOrchestrator) -> None:
        self.o = orchestrator
        self.logger = logging.getLogger("swap_commit")

    # ------------------------------------------------------------------ commit

    async def commit(self, ctx: TransformContext, swap: keep_old_copy.SwapRun, fields: dict[str, Any]) -> bool:
        """Move the record to the new copy together with ``fields`` (its status). False when the record no longer exists.

        The set is read before the write (every block that was given a point is there) and its presence is confirmed after it: once the
        record names the copy no sweep can take it, so that second read is final, and a copy that was taken in between is rolled back
        to the old one, which is still held for the grace."""
        record = ctx.record
        await self._require_points(ctx, swap)
        conditions = [
            Cond("virtualRecordId", EQ, swap.old_vrid),
            Cond(PENDING_VIRTUAL_RECORD_ID, EQ, swap.new_vrid),
            Cond("indexingStatus", ONE_OF, list(keep_old_copy.COMMITTABLE)),
        ]
        graph = self.o.graph_provider
        # Marked just before the write, so that a process that dies between the move and ``finish`` leaves the old copy with its grace: the
        # orphan sweep would otherwise take it at once, while a reader's cached map may still name it. Harmless when the write is refused
        # (the copy is still held, and a mark older than the grace delays nothing).
        # Required: without the mark a process that dies after the move would leave the old copy to the orphan sweep at once. When it cannot be
        # stored the record stays on the old copy and the run is retried.
        marked, cause = False, None
        try:
            marked = bool(await graph.update_node(swap.old_vrid, _MAPPING, {keep_old_copy.RETIRED_AT_FIELD: int(time.time() * 1000)}))
        except Exception as exc:
            cause = exc
        if not marked:
            try:
                row_exists = await graph.get_document(swap.old_vrid, _MAPPING, raise_on_error=True) is not None
            except Exception:
                row_exists = True  # unreadable: not shown to be absent
            if row_exists:  # a copy with no mapping row is found by no sweep: nothing to protect; otherwise the mark is required
                raise IndexingError(
                    "The old copy could not be marked for its grace; the record stays on it and the run is retried",
                    details={"record_id": swap.record_id, "virtual_record_id": swap.old_vrid},
                ) from cause
        # The records that keep sharing the old copy are fenced around the move, as for any other move off a shared one.
        fence = leaving_vrid(graph, record.id, swap.old_vrid, self.logger) if scope_fields_enabled() else contextlib.nullcontext()
        async with fence:
            won = await graph.update_node_fields_if(
                record.id, _RECORDS, {"virtualRecordId": swap.new_vrid}, conditions, unset=[PENDING_VIRTUAL_RECORD_ID],
            )
        if not won:
            return await self._refused(swap)
        await self._confirm_after_commit(ctx, swap)
        # Only now is the record finished: until this write its status is the run's, so a failure anywhere above leaves the record an attempt
        # in flight (the handler requeues it, or the stale sweep does) and never COMPLETED on a copy nobody has shown to be whole.
        finished = await graph.update_node_fields_if(
            record.id, _RECORDS, fields,
            [Cond("virtualRecordId", EQ, swap.new_vrid), Cond(PENDING_VIRTUAL_RECORD_ID, "is_null"),
             Cond("indexingStatus", ONE_OF, list(keep_old_copy.COMMITTABLE))],
        )  # not while another attempt has installed its own pending copy: that attempt is still running and finishes the record
        if finished:
            return True
        doc = await graph.get_document(swap.record_id, _RECORDS, raise_on_error=True)
        return doc is not None and is_live_record(doc)  # another writer finished the record: the copy it names is the one committed

    async def commit_document(self, doc: dict[str, Any], swap: keep_old_copy.SwapRun, fields: dict[str, Any]) -> bool:
        """The commit and what follows it for a run that ends without a parse of its own to hand over (an empty result), from the record's
        stored document. The new copy has no points, so nothing is read back; the record moves to it and the old copy is released."""
        ctx = SimpleNamespace(
            record=SimpleNamespace(
                id=swap.record_id, connector_id=doc.get("connectorId"), connector_name=doc.get("connectorName"),
                external_record_group_id=doc.get("externalGroupId"),
            ),
            reconciliation_context=None,
        )
        committed = await self.commit(ctx, swap, fields)  # type: ignore[arg-type]
        if committed:
            # The new copy is empty: twins keep the copy they have, or they would lose their search results
            await self.finish(ctx, swap, repoint_twins=False)  # type: ignore[arg-type]
        return committed

    async def _confirm_after_commit(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """The record names the copy now. When it is not whole in the store (a sweep, or anything else, took it between the read and the
        write) or cannot be shown to be (the read failed), the move is undone while the old copy is still there, and the run is retried.

        If the undo cannot be written either, the record names a copy nobody has verified, and its status is still the run's (the status is
        written only after this), so it is an attempt in flight: the failed attempt is requeued by the handler or, when the graph store is
        down too, by the stale-record sweep, and the rerun replaces the copy. The old copy is kept for its grace (marked before the move)."""
        failure: Exception | None = None
        try:
            if await self._complete_set(ctx, swap):
                return
        except Exception as exc:
            failure = exc
        undone = False
        for attempt in range(UNDO_ATTEMPTS):
            try:
                undone = await self.o.graph_provider.update_node_fields_if(
                    swap.record_id, _RECORDS, {"virtualRecordId": swap.old_vrid, PENDING_VIRTUAL_RECORD_ID: swap.new_vrid},
                    [Cond("virtualRecordId", EQ, swap.new_vrid), Cond(PENDING_VIRTUAL_RECORD_ID, "is_null"),
                     Cond("indexingStatus", ONE_OF, list(keep_old_copy.COMMITTABLE))],  # not over the pending copy of a newer attempt
                )
                break
            except Exception as undo_exc:
                self.logger.error("The move of record %s to its new copy could not be undone (attempt %d): %s", swap.record_id, attempt + 1, type(undo_exc).__name__)
                await asyncio.sleep(UNDO_BACKOFF_SECONDS * (attempt + 1))
        if not undone:
            self.logger.error("Record %s names a copy that was not verified; its attempt is left in flight to be rerun", swap.record_id)
            if failure is None:
                # The copy is known to be incomplete (the read answered): keep it out of search until the rerun, as far as the graph store lets
                # it be written. A record that is not an indexed copy is not served.
                with contextlib.suppress(Exception):
                    # only while the record still names the copy this attempt committed (a newer complete copy is left served)
                    await self.o.graph_provider.update_node_fields_if(
                        swap.record_id, _RECORDS, {"hasIndexedCopy": False}, [Cond("virtualRecordId", EQ, swap.new_vrid)]
                    )
        if failure is not None:
            raise failure  # the run is retried
        raise IndexingError(
            "The replacement copy was removed from the vector store while it was committed; the move is undone and the run is retried",
            details={"record_id": swap.record_id, "virtual_record_id": swap.new_vrid},
        )

    async def _refused(self, swap: keep_old_copy.SwapRun) -> bool:
        """The commit matched nothing. False: the record is gone (deleted). True: a redelivery of this very commit landed first, so the
        copy is the record's. Otherwise the run was superseded and its own copy is released."""
        doc = await self.o.graph_provider.get_document(swap.record_id, _RECORDS, raise_on_error=True)
        if doc is None or not is_live_record(doc):
            await self._drop_own_copy(swap, swap.new_vrid)
            return False
        if doc.get("virtualRecordId") == swap.new_vrid and keep_old_copy.pending_of(doc) is None:
            return True
        await self._drop_own_copy(swap, swap.new_vrid, only_if_pending=True)
        raise SupersededRun(swap.record_id)

    async def _drop_own_copy(self, swap: keep_old_copy.SwapRun, vrid: str, *, only_if_pending: bool = False) -> None:
        """Remove this run's pending field (when it is still this run's) and release its copy through the holder-checked paths."""
        graph = self.o.graph_provider
        with contextlib.suppress(Exception):
            await graph.update_node_fields_if_match(
                swap.record_id, _RECORDS, {}, {PENDING_VIRTUAL_RECORD_ID: vrid}, unset=[PENDING_VIRTUAL_RECORD_ID]
            )
        await self._release_unheld(swap.org_id, vrid)

    @staticmethod
    def _pointed(ctx: TransformContext) -> frozenset[str]:
        """The block ids the writers gave a point this run, recorded with the reconciliation metadata."""
        rc = ctx.reconciliation_context
        pointed = (rc.new_metadata or {}).get("pointedBlocks") if rc is not None and isinstance(rc.new_metadata, dict) else None
        return frozenset(pointed or ())

    async def _blocks_in_store(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> tuple[set[str], bool]:
        """The block ids the new copy has points for, and whether the listing was whole (it stops at its bound for a very large record)."""
        vs = self.o.vector_store
        db = vs.vector_db_service
        collection = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, ctx.record))
        flt = await db.filter_collection(must={"orgId": org_for_filter(swap.org_id, match_bound=True), "virtualRecordId": swap.new_vrid})
        seen: set[str] = set()
        offset: str | None = None
        limit = sweep_max_points()
        scanned = 0
        while True:
            try:
                page = await db.scroll(
                    collection_name=collection, scroll_filter=flt, limit=min(500, max(1, limit - scanned)),
                    offset=offset, with_payload=[VIRTUAL_RECORD_ID_FIELD, BLOCK_ID_FIELD],
                )
            except ScrollWindowExceededError:
                return seen, False
            for point in page.points or []:
                scanned += 1
                if block_id := VectorChunkPayload.from_dict(point.payload or {}).metadata.blockId:
                    seen.add(block_id)
            if not page.next_offset or page.next_offset == offset:
                return seen, True
            offset = page.next_offset
            if scanned >= limit:
                return seen, False

    async def _complete_set(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> bool:
        """Whether every block that was given a point has one. A listing that stops at its bound proves only that something is there."""
        pointed = self._pointed(ctx)
        if not pointed:
            return True
        seen, complete = await self._blocks_in_store(ctx, swap)
        if not seen:
            return False
        missing = pointed - seen
        if missing and not complete:
            # The listing stopped at its bound: the blocks it did not reach are asked for by id.
            missing -= await self._present_among(ctx, swap, sorted(missing))
        return not missing

    async def _present_among(self, ctx: TransformContext, swap: keep_old_copy.SwapRun, blocks: list[str]) -> set[str]:
        vs = self.o.vector_store
        db = vs.vector_db_service
        collection = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, ctx.record))
        found: set[str] = set()
        org = org_for_filter(swap.org_id, match_bound=True)
        for start in range(0, len(blocks), 200):
            chunk = blocks[start:start + 200]
            flt = await db.filter_collection(must={"orgId": org, "virtualRecordId": swap.new_vrid, "blockId": chunk})
            offset: str | None = None
            while True:
                page = await db.scroll(
                    collection_name=collection, scroll_filter=flt, limit=500, offset=offset, with_payload=[VIRTUAL_RECORD_ID_FIELD, BLOCK_ID_FIELD],
                )
                found.update(b for p in page.points or [] if (b := VectorChunkPayload.from_dict(p.payload or {}).metadata.blockId))
                if not page.next_offset or page.next_offset == offset:
                    break
                offset = page.next_offset
        return found

    async def _require_points(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """Defense in depth: a copy is committed only with a point for every block that was given one, whatever removed the rest.

        The holder rule is what keeps a running copy from being swept; this read turns a missing or partial set into a retry instead of a
        COMPLETED record that cannot be found."""
        if not await self._complete_set(ctx, swap):
            raise IndexingError(
                "The replacement copy is missing from or incomplete in the vector store; it is not committed and the run is retried",
                details={"record_id": swap.record_id, "virtual_record_id": swap.new_vrid},
            )

    # ------------------------------------------------------------------ after the commit

    async def finish(self, ctx: TransformContext, swap: keep_old_copy.SwapRun, *, repoint_twins: bool = True) -> None:
        """Everything after the commit. The record already serves the new copy, so a failure here is logged: the old copy has a
        holder (a twin still on it) or none (the orphan sweep takes it), and the membership resync is the repair path's."""
        record = ctx.record
        vs = self.o.vector_store
        try:
            await vs._resync_membership_after_write(swap.new_vrid, record.id)
        except Exception as exc:
            self.logger.warning("Membership of %s not re-applied after its commit: %s", swap.new_vrid, type(exc).__name__)
        if swap.forced and repoint_twins:
            try:
                await self._repoint_twins(ctx, swap)
            except Exception as exc:
                self.logger.warning("Twins of record %s not re-pointed: %s", record.id, type(exc).__name__)
        try:
            await self._retire_membership(swap)
        except Exception as exc:
            self.logger.warning("Membership of the old copy %s not retired: %s", swap.old_vrid, type(exc).__name__)
        await notify_record_vrid_moved(
            connector_name=getattr(record, "connector_name", None), connector_id=getattr(record, "connector_id", None),
            external_record_group_id=getattr(record, "external_record_group_id", None), org_id=swap.org_id,
        )
        with contextlib.suppress(Exception):
            await self.o.graph_provider.update_node(
                swap.old_vrid, _MAPPING, {keep_old_copy.RETIRED_AT_FIELD: int(time.time() * 1000)}
            )
        spawn_detached(self._release_after_grace(swap, record), name=f"release-old-copy-{swap.old_vrid}")
        keep_old_copy.end_swap()

    async def _retire_membership(self, swap: keep_old_copy.SwapRun) -> None:
        """Take the connectors and groups of the records that left the old copy off its points, now rather than after the grace.

        The points stay for a reader whose cached map still names the old copy (they are found by virtual record id), but a container
        search no longer matches them: they would otherwise sit beside the served copy in every search of the connector until the grace
        is over and be denied after the fetch, crowding it for as long as records are being re-indexed. A holder that remains (a twin
        not moved) keeps its membership: the arrays are recomputed from the graph then."""
        graph, vs = self.o.graph_provider, self.o.vector_store
        db = vs.vector_db_service
        if await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True):
            await sync_vector_membership(db, vs.collection_locator, graph, swap.old_vrid, self.logger)
            return
        org = org_for_filter(swap.org_id, match_bound=True)
        flt = await db.filter_collection(must={"orgId": org, "virtualRecordId": swap.old_vrid})
        empty = {CONNECTOR_IDS_FIELD: [], RECORD_GROUP_IDS_FIELD: [], ROOT_RECORD_GROUP_IDS_FIELD: []}
        cleared_failure: Exception | None = None
        try:
            for collection in await vs.collection_locator.all_collections(fresh=True, strict=True):
                await db.set_payload(collection, empty, flt)
            await mark_scope_stale(db, vs.collection_locator, swap.old_vrid, self.logger)
        except Exception as exc:  # partway: a record that attached meanwhile may have lost its membership in the collections already cleared
            cleared_failure = exc
        # A record that attached to the old copy while its membership was being taken off keeps its own: the holders are read again after
        # the write, and recomputing the arrays from them puts that record back. When that does not complete the connector's membership is
        # walked again (the backfill), as for a twin.
        try:
            holders = await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True)
            if holders:
                await sync_vector_membership(db, vs.collection_locator, graph, swap.old_vrid, self.logger)
        except Exception as exc:
            self.logger.warning("Membership of %s not restored after its retirement: %s", swap.old_vrid, type(exc).__name__)
            for key in await self._holders_connectors(swap):
                await self._request_membership_repair(None, key)
        if cleared_failure is not None:
            raise cleared_failure

    async def _holders_connectors(self, swap: keep_old_copy.SwapRun) -> set[str]:
        try:
            keys = await self.o.graph_provider.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True)
            docs = [await self.o.graph_provider.get_document(k, _RECORDS) for k in keys]
        except Exception:
            return set()
        return {d["connectorId"] for d in docs if d and d.get("connectorId")}

    async def _repoint_twins(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """Move the records that held the old copy with the same bytes, and the same write collection, to the new one.

        One compare-and-set per twin: it applies only while the twin still names the old copy with the same bytes, is
        COMPLETED and has no copy pending. A twin that changed meanwhile keeps the old copy, which then survives (it has a
        holder). A holder in another collection has its own points under the old vrid there and keeps it."""
        record, graph, vs = ctx.record, self.o.graph_provider, self.o.vector_store
        mine = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, record))
        keys = await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True)
        me = await graph.get_document(record.id, _RECORDS, raise_on_error=True) or {}
        if me.get("virtualRecordId") != swap.new_vrid:
            return  # a later run moved the record on: the copy it committed is retired, and twins are not moved onto it
        gate = asyncio.Semaphore(TWIN_CONCURRENCY)
        # With the identity written, only a twin shown to hold the same parser's copy moves (see below); the committer's own
        # identity must be known for this very commit, or the twins are moved as before and their own identity goes stale.
        committer_identity = parser_identity.known_identity(me) if parser_identity.identity_write_enabled() else None

        async def one(key: str) -> bool:
            async with gate:
                twin = await graph.get_document(key, _RECORDS, raise_on_error=True)
                twin_context = RecordContext.from_graph_document(twin or {})
                if not twin or twin_context.org_id != swap.org_id or not is_live_record(twin):
                    return False
                if twin.get("md5Checksum") is None or twin.get("md5Checksum") != me.get("md5Checksum"):
                    return False
                if twin.get("indexingStatus") != ProgressStatus.COMPLETED.value or keep_old_copy.pending_of(twin):
                    return False
                try:
                    theirs = vs.collection_registry.resolve_write_collection(twin_context)
                except Exception:
                    return False  # a collection that cannot be resolved counts as different: the twin keeps the old copy
                if theirs != mine:
                    return False
                fence = leaving_vrid(graph, key, swap.old_vrid, self.logger) if scope_fields_enabled() else contextlib.nullcontext()
                identity = {k: me[k] for k in _TWIN_IDENTITY if me.get(k) is not None}
                before = {k: twin.get(k) for k in _TWIN_IDENTITY}
                conditions = [Cond("virtualRecordId", EQ, swap.old_vrid), Cond("md5Checksum", EQ, twin["md5Checksum"]),
                              Cond("indexingStatus", EQ, ProgressStatus.COMPLETED.value),
                              Cond(PENDING_VIRTUAL_RECORD_ID, "is_null")]
                parser_fields: dict[str, Any] = {}
                if committer_identity is not None:
                    # Only a twin whose own identity is known and equals the committer's holds a copy this one replaces. Its
                    # identity is written with its own timestamp (the move does not touch it), and the write refuses a twin that
                    # was re-indexed since it was read.
                    twin_identity = parser_identity.known_identity(twin)
                    if twin_identity is None or parser_identity.key_of(twin_identity) != parser_identity.key_of(committer_identity):
                        return False
                    parser_fields = parser_identity.indexed_parser_fields(
                        committer_identity, parser_identity.source_for_copy(me.get(parser_identity.IDENTITY_SOURCE_FIELD)),
                        at=twin["lastIndexTimestamp"], vrid=swap.new_vrid,
                    )
                    conditions.append(Cond("lastIndexTimestamp", EQ, twin["lastIndexTimestamp"]))
                before.update({k: twin.get(k) for k in parser_fields})
                async with fence:
                    moved = await graph.update_node_fields_if(
                        key, _RECORDS, {"virtualRecordId": swap.new_vrid, **identity, **parser_fields}, conditions,
                    )
                if moved and ((await graph.get_document(record.id, _RECORDS)) or {}).get("virtualRecordId") != swap.new_vrid:
                    # The record moved on while this twin was moved (its copy is retired and may go): the twin goes back to the old copy.
                    await graph.update_node_fields_if(
                        key, _RECORDS, {"virtualRecordId": swap.old_vrid, **{k: v for k, v in before.items() if v is not None}},
                        [Cond("virtualRecordId", EQ, swap.new_vrid), Cond("indexingStatus", EQ, ProgressStatus.COMPLETED.value)],
                        unset=[k for k in (*identity, *parser_fields) if before[k] is None],  # what the move copied goes with it
                    )
                    return False
                if moved:
                    try:
                        await sync_vector_membership(vs.vector_db_service, vs.collection_locator, graph, swap.new_vrid, self.logger)
                    except Exception as exc:
                        # The twin names the new copy, whose points carry only this record's membership: a container search of the twin's
                        # own connector misses it until they are written. The repair is the per-connector backfill, asked for here.
                        self.logger.warning("Membership of %s not applied for the twin %s: %s", swap.new_vrid, key, type(exc).__name__)
                        await self._request_membership_repair(twin_context, twin.get("connectorId"))
                    await notify_record_vrid_moved(
                        connector_name=twin.get("connectorName"), connector_id=twin.get("connectorId"),
                        external_record_group_id=twin.get("externalGroupId"), org_id=swap.org_id,
                    )
                return bool(moved)

        # Every move is waited for (the membership of the old copy is recomputed after this), and one failing twin does not stop the others
        results = await asyncio.gather(*(one(k) for k in keys if k and k != record.id), return_exceptions=True)
        for failure in results:
            if isinstance(failure, BaseException):
                self.logger.warning("A twin of record %s was not re-pointed: %s", record.id, type(failure).__name__)

    async def _request_membership_repair(self, twin_context: RecordContext, connector_id: str | None) -> None:
        """Have the connector's vector membership walked again (the designed repair path: the backfill scanner picks up an app whose
        flag is clear), and mark the points' scope stale. Best effort: the failure to ask is logged."""
        if not connector_id:
            return
        try:
            await self.o.graph_provider.update_node(connector_id, CollectionNames.APPS.value, {"vectorMembershipBackfilled": False})
        except Exception as exc:
            self.logger.error("Could not ask for a membership repair of connector %s: %s", connector_id, type(exc).__name__)

    async def _release_after_grace(self, swap: keep_old_copy.SwapRun, record: Record) -> None:
        """Release the old copy once the accessible-records cache cannot still map a reader to it."""
        try:
            await asyncio.sleep(keep_old_copy.grace_seconds())
            # Another record may have left the same copy since (it writes a newer mark): the copy goes only when the latest mark's grace is over.
            # A read that fails raises: the copy is left to the sweep rather than released on a mark that was not read.
            for _ in range(10):
                row = await self.o.graph_provider.get_document(swap.old_vrid, _MAPPING, raise_on_error=True)
                mark = (row or {}).get(keep_old_copy.RETIRED_AT_FIELD)
                left = (mark / 1000 + keep_old_copy.grace_seconds() - time.time()) if isinstance(mark, (int, float)) else 0
                if left <= 0:
                    break
                await asyncio.sleep(left)
            else:
                return  # marks kept arriving: the sweep reclaims the copy once the last grace is over
            with for_org(swap.org_id, job="swap-release"):
                await self.release_old_copy(swap, record)
        except Exception as exc:
            # The old copy has no holder, or a twin holds it: the orphan sweep (after the same grace) or that holder's own run converges.
            self.logger.warning("Old copy %s not released: %s", swap.old_vrid, type(exc).__name__)

    async def release_old_copy(self, swap: keep_old_copy.SwapRun, record: Record) -> None:
        """Delete the old copy while nothing holds it. A holder in another collection keeps its own points under it; this record's
        collection loses them, so a multi-collection search never serves this record's stale copy."""
        graph, vs = self.o.graph_provider, self.o.vector_store
        holders = await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True)
        if not holders:
            await self._release_unheld(swap.org_id, swap.old_vrid)
            return
        await sync_vector_membership(vs.vector_db_service, vs.collection_locator, graph, swap.old_vrid, self.logger)
        mine = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, record))
        for key in holders:
            doc = await graph.get_document(key, _RECORDS, raise_on_error=True)
            try:
                theirs = vs.collection_registry.resolve_write_collection(RecordContext.from_graph_document(doc or {}))
            except Exception:
                return  # unknown: nothing is deleted
            if theirs == mine:
                return  # a holder uses this collection's points
        # Read again right before the delete: a record that attached to the old copy meanwhile keeps its points.
        for key in await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True):
            if key not in holders:
                return
        await vs.purge_record_vectors(swap.org_id, swap.old_vrid, record)

    async def _release_unheld(self, org_id: str, vrid: str) -> None:
        """Points, then stored content, of a copy that no record holds (each step re-reads the holders itself)."""
        graph, vs = self.o.graph_provider, self.o.vector_store
        await rewrite_or_delete_virtual_record(vs.vector_db_service, vs.collection_locator, graph, vrid, self.logger)
        await self.o.blob_storage.delete_storage_docs_for_vrid(org_id, vrid)

    # ------------------------------------------------------------------ failure

    async def release_failed(self, record_id: str, org_id: str, attempt_pending: str | None = None) -> None:
        """A record failed for good: drop its pending field, then release the copy it was writing.

        ``attempt_pending`` is the copy the failed attempt wrote: only that one is released, never one that another consumer's attempt named
        after the failure (the field is cleared by compare-and-set on that value)."""
        graph = self.o.graph_provider
        doc = await graph.get_document(record_id, _RECORDS)
        pending = keep_old_copy.pending_of(doc or {})
        if not pending or (attempt_pending is not None and pending != attempt_pending):
            return
        # The field goes first (the FAILED write already released the copy for every holder lookup); then the holder-checked deleters.
        await graph.update_node_fields_if_match(record_id, _RECORDS, {}, {PENDING_VIRTUAL_RECORD_ID: pending}, unset=[PENDING_VIRTUAL_RECORD_ID])
        await self._release_unheld(org_id, pending)


__all__ = ["RecordDeletedError", "SwapCommitter"]
