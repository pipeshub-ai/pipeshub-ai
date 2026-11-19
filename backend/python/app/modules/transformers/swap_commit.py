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
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.core.org_context import for_org, spawn_detached
from app.exceptions.indexing_exceptions import (
    IndexingError,
    RecordDeletedError,
    SupersededRun,
)
from app.modules.indexing import keep_old_copy
from app.services.cache.invalidation_hooks import notify_record_vrid_moved
from app.services.graph_db.common.record_visibility import (
    PENDING_VIRTUAL_RECORD_ID,
    is_live_record,
)
from app.services.graph_db.conditions import EQ, ONE_OF, Cond
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.structure_generation import leaving_vrid
from app.services.vector_db.filters import org_for_filter
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record,
    sync_vector_membership,
)
from app.services.vector_db.strategy import RecordContext

if TYPE_CHECKING:
    from app.models.entities import Record
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.modules.transformers.transformer import TransformContext

#: Twins re-pointed at once.
TWIN_CONCURRENCY = 8
_RECORDS = CollectionNames.RECORDS.value
_MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value


class SwapCommitter:
    def __init__(self, orchestrator: SinkOrchestrator) -> None:
        self.o = orchestrator
        self.logger = logging.getLogger("swap_commit")

    # ------------------------------------------------------------------ commit

    async def commit(self, ctx: TransformContext, swap: keep_old_copy.SwapRun, fields: dict[str, Any]) -> bool:
        """Move the record to the new copy together with ``fields`` (its status). False when the record no longer exists."""
        record = ctx.record
        await self._require_points(ctx, swap)
        conditions = [
            Cond("virtualRecordId", EQ, swap.old_vrid),
            Cond(PENDING_VIRTUAL_RECORD_ID, EQ, swap.new_vrid),
            Cond("indexingStatus", ONE_OF, list(keep_old_copy.COMMITTABLE)),
        ]
        graph = self.o.graph_provider
        # The records that keep sharing the old copy are fenced around the move, as for any other move off a shared one.
        fence = leaving_vrid(graph, record.id, swap.old_vrid, self.logger) if scope_fields_enabled() else contextlib.nullcontext()
        async with fence:
            won = await graph.update_node_fields_if(
                record.id, _RECORDS, {**fields, "virtualRecordId": swap.new_vrid}, conditions,
                unset=[PENDING_VIRTUAL_RECORD_ID],
            )
        if won:
            return True
        await self._refused(swap)
        return False

    async def _refused(self, swap: keep_old_copy.SwapRun) -> None:
        """The commit matched nothing: the record is gone (deleted), or the run was superseded. The run's own copy is released."""
        doc = await self.o.graph_provider.get_document(swap.record_id, _RECORDS, raise_on_error=True)
        if doc is None or not is_live_record(doc):
            await self._drop_own_copy(swap, swap.new_vrid)
            return
        if doc.get("virtualRecordId") == swap.new_vrid and keep_old_copy.pending_of(doc) is None:
            return  # a redelivery of this very commit landed first: the copy is the record's
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

    async def _require_points(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """Defense in depth: a copy with none of its points is never committed, whatever removed them.

        The holder rule is what keeps a running copy from being swept; this read turns a missing set into a retry instead of a
        COMPLETED record with nothing to find."""
        rc = ctx.reconciliation_context
        pointed = (rc.new_metadata or {}).get("pointedBlocks") if rc is not None and isinstance(rc.new_metadata, dict) else None
        if not pointed:
            return
        vs = self.o.vector_store
        collection = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, ctx.record))
        flt = await vs.vector_db_service.filter_collection(
            must={"orgId": org_for_filter(swap.org_id, match_bound=True), "virtualRecordId": swap.new_vrid}
        )
        if await vs.vector_db_service.count_points(collection, flt) <= 0:
            raise IndexingError(
                "The replacement copy has no points in the vector store; it is not committed and the run is retried",
                details={"record_id": swap.record_id, "virtual_record_id": swap.new_vrid},
            )

    # ------------------------------------------------------------------ after the commit

    async def finish(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """Everything after the commit. The record already serves the new copy, so a failure here is logged: the old copy has a
        holder (a twin still on it) or none (the orphan sweep takes it), and the membership resync is the repair path's."""
        record = ctx.record
        vs = self.o.vector_store
        try:
            await vs._resync_membership_after_write(swap.new_vrid, record.id)
        except Exception as exc:
            self.logger.warning("Membership of %s not re-applied after its commit: %s", swap.new_vrid, type(exc).__name__)
        if swap.forced:
            try:
                await self._repoint_twins(ctx, swap)
            except Exception as exc:
                self.logger.warning("Twins of record %s not re-pointed: %s", record.id, type(exc).__name__)
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

    async def _repoint_twins(self, ctx: TransformContext, swap: keep_old_copy.SwapRun) -> None:
        """Move the records that held the old copy with the same bytes, and the same write collection, to the new one.

        One compare-and-set per twin: it applies only while the twin still names the old copy with the same bytes, is
        COMPLETED and has no copy pending. A twin that changed meanwhile keeps the old copy, which then survives (it has a
        holder). A holder in another collection has its own points under the old vrid there and keeps it."""
        record, graph, vs = ctx.record, self.o.graph_provider, self.o.vector_store
        mine = vs.collection_registry.resolve_write_collection(vs._record_context(swap.org_id, record))
        keys = await graph.get_records_by_virtual_record_id(swap.old_vrid, raise_on_error=True)
        me = await graph.get_document(record.id, _RECORDS, raise_on_error=True) or {}
        gate = asyncio.Semaphore(TWIN_CONCURRENCY)

        async def one(key: str) -> bool:
            async with gate:
                twin = await graph.get_document(key, _RECORDS, raise_on_error=True)
                if not twin or twin.get("orgId") != swap.org_id or not is_live_record(twin):
                    return False
                if twin.get("md5Checksum") is None or twin.get("md5Checksum") != me.get("md5Checksum"):
                    return False
                if twin.get("indexingStatus") != ProgressStatus.COMPLETED.value or keep_old_copy.pending_of(twin):
                    return False
                try:
                    theirs = vs.collection_registry.resolve_write_collection(RecordContext.from_graph_document(twin))
                except Exception:
                    return False  # a collection that cannot be resolved counts as different: the twin keeps the old copy
                if theirs != mine:
                    return False
                fence = leaving_vrid(graph, key, swap.old_vrid, self.logger) if scope_fields_enabled() else contextlib.nullcontext()
                identity = {k: me[k] for k in ("summaryDocumentId", "secretsRedacted") if me.get(k) is not None}
                async with fence:
                    moved = await graph.update_node_fields_if(
                        key, _RECORDS, {"virtualRecordId": swap.new_vrid, **identity},
                        [Cond("virtualRecordId", EQ, swap.old_vrid), Cond("md5Checksum", EQ, twin["md5Checksum"]),
                         Cond("indexingStatus", EQ, ProgressStatus.COMPLETED.value),
                         Cond(PENDING_VIRTUAL_RECORD_ID, "is_null")],
                    )
                if moved:
                    await sync_vector_membership(vs.vector_db_service, vs.collection_locator, graph, swap.new_vrid, self.logger)
                    await notify_record_vrid_moved(
                        connector_name=twin.get("connectorName"), connector_id=twin.get("connectorId"),
                        external_record_group_id=twin.get("externalGroupId"), org_id=swap.org_id,
                    )
                return bool(moved)

        await asyncio.gather(*(one(k) for k in keys if k and k != record.id))

    async def _release_after_grace(self, swap: keep_old_copy.SwapRun, record: Record) -> None:
        """Release the old copy once the accessible-records cache cannot still map a reader to it."""
        try:
            await asyncio.sleep(keep_old_copy.grace_seconds())
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
        await vs.purge_record_vectors(swap.org_id, swap.old_vrid, record)

    async def _release_unheld(self, org_id: str, vrid: str) -> None:
        """Points, then stored content, of a copy that no record holds (each step re-reads the holders itself)."""
        graph, vs = self.o.graph_provider, self.o.vector_store
        await rewrite_or_delete_virtual_record(vs.vector_db_service, vs.collection_locator, graph, vrid, self.logger)
        await self.o.blob_storage.delete_storage_docs_for_vrid(org_id, vrid)

    # ------------------------------------------------------------------ failure

    async def release_failed(self, record_id: str, org_id: str) -> None:
        """A record failed for good: drop its pending field, then release the copy it was writing."""
        graph = self.o.graph_provider
        doc = await graph.get_document(record_id, _RECORDS)
        pending = keep_old_copy.pending_of(doc or {})
        if not pending:
            return
        # The field goes first (the FAILED write already released the copy for every holder lookup); then the holder-checked deleters.
        await graph.update_node_fields_if_match(record_id, _RECORDS, {}, {PENDING_VIRTUAL_RECORD_ID: pending}, unset=[PENDING_VIRTUAL_RECORD_ID])
        await self._release_unheld(org_id, pending)


__all__ = ["RecordDeletedError", "SwapCommitter"]
