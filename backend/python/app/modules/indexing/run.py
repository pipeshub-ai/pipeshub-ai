import asyncio
from typing import Any, Dict, List, NamedTuple, Sequence

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.exceptions.indexing_exceptions import (
    EmbeddingDeletionError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.services.vector_db.collection_locator import VirtualRecordCollectionLocator
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.membership import (
    EMPTY_CONFIRM_DELAY_SECONDS,
    MEMBERSHIP_WRITE_BATCH_SIZE,
    remaining_record_keys,
    write_membership_batch,
)
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record as _rewrite_or_delete_virtual_record,
)
from app.services.vector_db.membership import (
    sync_vector_membership as _sync_vector_membership,
)
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    VIRTUAL_RECORD_ID_FIELD,
)
from app.services.vector_db.models import VectorChunkPayload
from app.services.vector_db.strategy import (
    DeleteAction,
    DeleteContext,
    DeleteScope,
    RecordContext,
)

# Module-level stub to allow tests to patch FastEmbedSparse even though
# it is only lazily imported inside VectorStore (not used here directly).
try:
    from fastembed import SparseTextEmbedding as FastEmbedSparse  # noqa: F401
except ImportError:
    FastEmbedSparse = None  # type: ignore[assignment,misc]

# Constants for bulk deletion
QDRANT_BULK_DELETE_BATCH_SIZE = 100

# Recovery scan bounds for a purge whose producer sent no VRID list.
PURGE_SCAN_PAGE_SIZE = 500
PURGE_SCAN_MAX_POINTS = 100_000

# How many points one page of the connector-delete rewrite pass pulls back.
CONNECTOR_DELETE_SCROLL_PAGE_SIZE = 500


class ScanResult(NamedTuple):
    """VRIDs recovered by scanning, and whether the scan saw everything.

    ``complete`` is False when the point cap stopped the walk or a scroll
    failed, so callers can tell "this connector had these VRIDs" from "these
    are the ones we managed to read".
    """

    ids: List[str]
    complete: bool


class IndexingPipeline:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        graph_provider,
        collection_registry: CollectionRegistry,
        vector_db_service: IVectorDBService,
    ) -> None:
        """Initialize the indexing pipeline with necessary configurations.

        Args:
            logger: Logger instance
            config_service: Configuration service
            graph_provider: Arango service
            collection_registry: Resolves/manages collections per the active strategy
            vector_db_service: Vector DB service
        """
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider

        try:
            self.vector_db_service = vector_db_service
            self.collection_registry = collection_registry
            # VRID-scoped work knows a virtual record id, not an org or a
            # connector. The locator turns the graph records membership already
            # fetches into the collections those points actually occupy, so
            # nothing here has to assume there is only one.
            self.collection_locator = VirtualRecordCollectionLocator(
                strategy=collection_registry.strategy,
                manifest_store=collection_registry.manifest_store,
                logger=logger,
                # Deletes run here, and they are the one caller for which an
                # un-adopted pre-manifest collection is destructive: the points
                # survive while their mapping rows are dropped.
                list_managed=collection_registry.list_managed_collections,
            )

        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline: " + str(e),
                details={"error": str(e)},
            )

    async def sync_vector_membership(self, virtual_record_id: str) -> None:
        """Recompute connectorIds/recordGroupIds from graph onto every chunk of a VRID.

        Never deletes. Group membership changes (moves, relinks, duplicate attach)
        must not be able to drop embeddings for a record that still exists.
        """
        await _sync_vector_membership(
            self.vector_db_service,
            self.collection_locator,
            self.graph_provider,
            virtual_record_id,
            self.logger,
        )

    async def delete_points_for_virtual_record(
        self, virtual_record_id: str, ctx: RecordContext
    ) -> None:
        """Drop every point for a VRID in ``ctx``'s collection, whatever the graph says.

        Distinct from :meth:`bulk_delete_embeddings`, whose contract is "delete
        only if no graph record still references this VRID" — on a re-embed the
        record is still present, so that method deliberately keeps the points.
        Re-embedding mints fresh point ids, so without this the old points
        survive alongside the new ones and every pass multiplies the collection.

        Scoped to the collection *this record* writes to, deliberately: the same
        VRID can be indexed from another connector into another collection, and
        re-embedding one record must not wipe the other's points.
        """
        if not virtual_record_id:
            return
        collection_name = self.collection_registry.resolve_write_collection(ctx)
        filter_dict = await self.vector_db_service.filter_collection(
            must={"virtualRecordId": virtual_record_id}
        )
        await self.vector_db_service.delete_points(
            collection_name=collection_name,
            filter=filter_dict,
        )
        self.logger.info(
            "Deleted existing vector points for virtual_record_id %s in %s before re-embed",
            virtual_record_id,
            collection_name,
        )

    async def rewrite_or_delete_vector_membership(self, virtual_record_id: str) -> str:
        """Drop a VRID's points when no graph record references it, else rewrite.

        Only for paths where a record genuinely went away; use
        ``sync_vector_membership`` for membership-only updates.
        """
        return await _rewrite_or_delete_virtual_record(
            self.vector_db_service,
            self.collection_locator,
            self.graph_provider,
            virtual_record_id,
            self.logger,
        )

    async def purge_connector(
        self,
        ctx: DeleteContext,
        record_group_ids: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        """Remove a connector's vector data using only what the points carry.

        Two passes over the membership arrays, in this order:

        1. Delete every point whose ``connectorIds`` holds nothing but this
           connector. The array contains it, so "at most one value" means
           "exactly this one" — those points have no other owner left. This is
           the overwhelming majority of a connector's points.
        2. Whatever still matches is shared with another connector *by
           construction*, so no length test is needed: the new arrays are the
           old ones minus this connector and minus its record groups, both of
           which the point itself already tells us.

        No graph lookup in either pass. That is the whole point: the previous
        design shipped every VRID on the event because a point could not be
        told apart from a live connector's copy without asking the graph.

        Safe to re-run: pass 1 finds nothing the second time and pass 2 is a
        no-op once the ids are gone, so the caller may retry the whole event.

        ``record_group_ids`` are the connector's own groups, which the graph
        delete removed. Without them a surviving shared point keeps pointing at
        groups that no longer exist. Omitted, the group array is left untouched
        rather than blanked.
        """
        scope = await self.collection_registry.resolve_delete_scope(ctx)
        # strict: a failed enumeration and a genuinely empty one both arrive as
        # [], and they want opposite outcomes — retry versus ack.
        collections = list(
            await self.collection_locator.all_collections(fresh=True, strict=True)
        )

        dropped: List[str] = []
        if scope.action == DeleteAction.DROP_COLLECTION:
            dropped = await self._drop_scope_collections(ctx, scope)
            # Shared points in the collections that survive still carry this
            # connector, so the passes below still have work to do.
            collections = [c for c in collections if c not in dropped]

        if not collections:
            self.logger.info(
                "Nothing left to purge for connector %s%s",
                ctx.connector_id,
                f" after dropping {dropped}" if dropped else ": no managed collections",
            )
            return {
                "action": "drop_collection" if dropped else "noop",
                "collections": dropped,
                "success": True,
            }

        dead_group_ids = {g for g in (record_group_ids or []) if g}

        # Before pass 1, not after: once those points are gone there is nothing
        # left to read their VRIDs from, and pass 3 needs them to reclaim the
        # mapping rows.
        exclusive = await self._scan_exclusively_owned_vrids(
            collections, ctx.connector_id
        )

        exclusive_filter = await self.vector_db_service.filter_collection(
            must={CONNECTOR_IDS_FIELD: ctx.connector_id},
            max_values={CONNECTOR_IDS_FIELD: 1},
        )
        for name in collections:
            # refresh: the strip pass below re-reads the matched set to decide
            # when it is done, so it must not see points this loop deleted.
            await self.vector_db_service.delete_points(
                collection_name=name, filter=exclusive_filter, refresh=True
            )

        # Strictly after the delete above — these rows are the only handle the
        # orphan sweeper has on a point set, so they must outlive it — and
        # strictly *before* the strip pass, which can raise: pass A cannot be
        # re-run once B has deleted the points it reads, so a later failure
        # would leak every one of these rows permanently.
        #
        # Confirmed, not assumed: A classifies from a payload snapshot while B
        # re-evaluates server-side at delete time. A live connector that
        # deduplicates onto one of these VRIDs in between leaves the points
        # alive under its own id, and forgetting the row then strands a record
        # that is still searchable.
        reclaimable = await self._vrids_without_surviving_points(
            collections, exclusive.ids
        )
        await self._forget_virtual_record_mappings(reclaimable)

        rewritten, orphans, complete = await self._strip_connector_from_shared_points(
            collections, ctx.connector_id, dead_group_ids
        )

        if not exclusive.complete:
            self.logger.warning(
                "Scan of %s hit its bound before enumerating every exclusively "
                "owned virtual record id for connector %s; %d mapping row(s) "
                "reclaimed here and the rest left to the orphan sweeper",
                collections,
                ctx.connector_id,
                len(exclusive.ids),
            )

        if not complete:
            # The caller acks on success, so reporting True here would retire the
            # only event that can finish the job while points still carry the
            # deleted connector.
            raise EmbeddingDeletionError(
                "Connector vector cleanup could not strip the connector from "
                "every shared point",
                record_id=ctx.connector_id,
                details={
                    "connector_id": ctx.connector_id,
                    "virtual_record_ids_rewritten": rewritten,
                },
            )

        self.logger.info(
            "Purged connector %s across %s: exclusive points deleted, %d shared "
            "virtual record(s) rewritten, %d orphan(s) resolved",
            ctx.connector_id,
            collections,
            rewritten,
            orphans,
        )
        return {
            "action": "drop_collection" if dropped else "membership_delete",
            "collections": dropped or list(collections),
            "exclusive_points_deleted": True,
            "virtual_record_ids_rewritten": rewritten,
            "virtual_record_ids_deleted": orphans,
            "success": True,
        }

    async def _drop_scope_collections(
        self, ctx: DeleteContext, scope: DeleteScope
    ) -> List[str]:
        """Drop the collections a proven ``DROP_COLLECTION`` scope names.

        Enumerates the VRIDs first: afterwards there is nothing left to read
        them from, and their mapping rows would look like orphans to the
        sweeper for as long as they survive.
        """
        # Exclusively-owned only, the same predicate the delete pass uses. A
        # plain connectorIds scan would also return VRIDs shared with a live
        # connector, whose points survive in the collections that are *not*
        # dropped — forgetting their rows would strand them.
        scan = await self._scan_exclusively_owned_vrids(
            scope.collection_names, ctx.connector_id
        )
        if not scan.complete:
            # The drop still goes ahead: refusing it would leave the whole
            # collection behind, and the cap is reached exactly on the large
            # collections a drop exists to handle.
            self.logger.warning(
                "Scan of %s hit its bound before enumerating every virtual record "
                "id for connector %s; dropping anyway and leaving %d mapping "
                "row(s) beyond that point for the orphan sweeper",
                scope.collection_names,
                ctx.connector_id,
                len(scan.ids),
            )
        for name in scope.collection_names:
            await self.collection_registry.delete_collection(name)
        await self._forget_virtual_record_mappings(scan.ids)
        self.logger.info(
            "Dropped collection(s) %s for connector %s",
            scope.collection_names,
            ctx.connector_id,
        )
        return list(scope.collection_names)

    async def _scan_exclusively_owned_vrids(
        self, collections: Sequence[str], connector_id: str
    ) -> ScanResult:
        """VRIDs whose points name this connector and no other.

        Classifies on the payload rather than pushing an array-length bound
        into the query: Redis cannot express one in ``FT.SEARCH`` at all, and
        the projection makes a page cheap enough that filtering here costs
        nothing worth saving.
        """
        found: List[str] = []
        seen: set = set()
        complete = True

        for name in collections:
            try:
                filter_dict = await self.vector_db_service.filter_collection(
                    must={CONNECTOR_IDS_FIELD: connector_id}
                )
                offset = None
                scanned = 0
                while scanned < PURGE_SCAN_MAX_POINTS:
                    page = await self.vector_db_service.scroll(
                        collection_name=name,
                        scroll_filter=filter_dict,
                        limit=PURGE_SCAN_PAGE_SIZE,
                        offset=offset,
                        with_payload=[VIRTUAL_RECORD_ID_FIELD, CONNECTOR_IDS_FIELD],
                    )
                    points = list(getattr(page, "points", None) or [])
                    if not points:
                        break
                    for point in points:
                        parsed = VectorChunkPayload.from_dict(point.payload or {})
                        vrid = parsed.metadata.virtualRecordId
                        if not vrid or vrid in seen:
                            continue
                        if len(parsed.connectorIds) <= 1:
                            seen.add(vrid)
                            found.append(vrid)
                    scanned += len(points)
                    next_offset = getattr(page, "next_offset", None)
                    if next_offset is None or next_offset == offset:
                        break
                    offset = next_offset
                else:
                    complete = False
            except Exception as e:
                complete = False
                self.logger.error(
                    "Could not scan %s for exclusively owned virtual record ids: %s",
                    name,
                    e,
                )
        return ScanResult(found, complete)

    async def _vrids_without_surviving_points(
        self, collections: Sequence[str], virtual_record_ids: Sequence[str]
    ) -> List[str]:
        """Of ``virtual_record_ids``, those with no points left anywhere.

        Only these may have their mapping row forgotten. Anything still holding
        points is live under another connector and needs the row to stay
        resolvable.
        """
        if not virtual_record_ids:
            return []

        survivors: set = set()
        for start in range(0, len(virtual_record_ids), QDRANT_BULK_DELETE_BATCH_SIZE):
            batch = list(virtual_record_ids)[
                start:start + QDRANT_BULK_DELETE_BATCH_SIZE
            ]
            filter_dict = await self.vector_db_service.filter_collection(
                must={"virtualRecordId": batch}
            )
            for name in collections:
                try:
                    await self._mark_surviving_vrids(
                        name, filter_dict, batch, survivors
                    )
                except Exception as e:
                    # Unreadable means unproven, and an unproven row must be
                    # kept: the sweeper can always reclaim it later, but nothing
                    # can put it back.
                    self.logger.error(
                        "Could not confirm surviving points in %s; keeping the "
                        "mapping rows for this batch: %s",
                        name,
                        e,
                    )
                    survivors.update(batch)
                    continue

        return [v for v in virtual_record_ids if v not in survivors]

    async def _mark_surviving_vrids(
        self,
        collection_name: str,
        scroll_filter: Any,
        batch: Sequence[str],
        survivors: set,
    ) -> None:
        """Add every VRID from *batch* that still holds a point here.

        Walks the whole matched set. One page sized to the batch does not do it:
        the filter matches *points*, and a record holds many chunks, so a single
        page can be filled by a handful of VRIDs while the rest go unseen — and
        unseen reads as "no points left", which drops the mapping row of a
        record that is still searchable.

        Paging stops early once every VRID in the batch is accounted for. Any
        other early exit — the bound, or a cursor that stops advancing — leaves
        the rest *unproven*, and unproven is kept: the sweeper can always
        reclaim a row later, but nothing can put one back.

        Only two endings prove absence: a page that comes back empty, and a
        page that reports no next offset. Both mean the matched set is
        exhausted, so a VRID not seen in it genuinely has no points here.
        """
        remaining = {v for v in batch if v not in survivors}
        offset = None
        scanned = 0
        exhausted = False

        while remaining and scanned < PURGE_SCAN_MAX_POINTS:
            page = await self.vector_db_service.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                limit=PURGE_SCAN_PAGE_SIZE,
                offset=offset,
                with_payload=[VIRTUAL_RECORD_ID_FIELD],
            )
            points = list(getattr(page, "points", None) or [])
            if not points:
                exhausted = True
                break
            for point in points:
                parsed = VectorChunkPayload.from_dict(point.payload or {})
                vrid = parsed.metadata.virtualRecordId
                if vrid and vrid in remaining:
                    remaining.discard(vrid)
                    survivors.add(vrid)
            scanned += len(points)
            next_offset = getattr(page, "next_offset", None)
            if next_offset is None:
                exhausted = True
                break
            if next_offset == offset:
                # The cursor is not advancing. That is a stalled walk, not a
                # finished one, so nothing below may be treated as absent.
                break
            offset = next_offset
        else:
            # Fell out of the condition: either every VRID is accounted for, or
            # the bound stopped us with some still outstanding.
            exhausted = not remaining

        if remaining and not exhausted:
            self.logger.warning(
                "Could not walk %s to the end for %d virtual record id(s); "
                "keeping their mapping rows",
                collection_name,
                len(remaining),
            )
            survivors.update(remaining)

    async def _strip_connector_from_shared_points(
        self,
        collections: Sequence[str],
        connector_id: str,
        dead_group_ids: set,
    ) -> tuple:
        """Remove a connector (and its dead groups) from points still carrying it.

        Returns ``(rewritten, orphans, complete)``; ``complete`` is False when a
        collection stopped with addressable points still carrying the connector.
        """
        rewritten = 0
        orphans = 0
        complete = True
        orphans_handled: set = set()

        for name in collections:
            c_rewritten, c_orphans, c_complete = await self._strip_in_collection(
                name, connector_id, dead_group_ids, orphans_handled
            )
            rewritten += c_rewritten
            orphans += c_orphans
            complete = complete and c_complete
        return rewritten, orphans, complete

    async def _strip_in_collection(
        self,
        collection_name: str,
        connector_id: str,
        dead_group_ids: set,
        orphans_handled: set,
    ) -> tuple:
        """One collection's share of :meth:`_strip_connector_from_shared_points`.

        Always re-reads the first page rather than advancing a cursor: each
        rewrite stops its points matching, so the next first page is the next
        batch. Paginating would be wrong on providers whose scroll offset is a
        position rather than a key, because mutating the matched set shifts
        every later page. Termination is guaranteed by stopping as soon as a
        page yields no virtual record we have not already handled — which is
        also what stops a point that fails to rewrite from looping forever.
        """
        rewritten = 0
        orphans = 0
        handled: set = set()

        while True:
            shared_filter = await self.vector_db_service.filter_collection(
                must={CONNECTOR_IDS_FIELD: connector_id}
            )
            page = await self.vector_db_service.scroll(
                collection_name=collection_name,
                scroll_filter=shared_filter,
                limit=CONNECTOR_DELETE_SCROLL_PAGE_SIZE,
                # Only these fields are read below. Without the projection every
                # page also drags each chunk's full text across the wire.
                with_payload=[
                    VIRTUAL_RECORD_ID_FIELD,
                    CONNECTOR_IDS_FIELD,
                    RECORD_GROUP_IDS_FIELD,
                ],
            )
            points = list(getattr(page, "points", None) or [])
            if not points:
                return rewritten, orphans, True

            progressed = False
            untagged = 0
            # Every VRID here loses the same ids, so those whose arrays reduce
            # to the same pair can be written together. Grouping turns a round
            # trip per deduplicated record into one per distinct result.
            by_remaining: Dict[tuple, List[str]] = {}
            orphaned_ids: List[str] = []

            for point in points:
                parsed = VectorChunkPayload.from_dict(point.payload or {})
                virtual_record_id = parsed.metadata.virtualRecordId
                if not virtual_record_id:
                    # Membership is written per VRID, so a point without one
                    # cannot be rewritten by any amount of retrying.
                    untagged += 1
                    continue
                if virtual_record_id in handled:
                    continue
                handled.add(virtual_record_id)
                progressed = True

                remaining_connectors = tuple(
                    cid for cid in parsed.connectorIds if cid != connector_id
                )
                if not remaining_connectors:
                    orphaned_ids.append(virtual_record_id)
                    continue
                remaining_groups = (
                    tuple(
                        gid
                        for gid in parsed.recordGroupIds
                        if gid not in dead_group_ids
                    )
                    if dead_group_ids
                    else None
                )
                by_remaining.setdefault(
                    (remaining_connectors, remaining_groups), []
                ).append(virtual_record_id)

            for (
                remaining_connectors,
                remaining_groups,
            ), virtual_record_ids in by_remaining.items():
                for start in range(
                    0, len(virtual_record_ids), MEMBERSHIP_WRITE_BATCH_SIZE
                ):
                    batch = virtual_record_ids[
                        start:start + MEMBERSHIP_WRITE_BATCH_SIZE
                    ]
                    await write_membership_batch(
                        self.vector_db_service,
                        collection_name,
                        batch,
                        list(remaining_connectors),
                        None if remaining_groups is None else list(remaining_groups),
                        self.logger,
                    )
                    rewritten += len(batch)

            for virtual_record_id in orphaned_ids:
                if virtual_record_id in orphans_handled:
                    continue
                orphans_handled.add(virtual_record_id)
                # The exclusive pass claims to have taken every point whose only
                # owner was this connector, so reaching here means the payload
                # disagrees with the filter that just ran. Deciding to delete
                # from data already shown to be inconsistent is exactly when to
                # stop trusting it and ask the graph instead.
                outcome = await self.rewrite_or_delete_vector_membership(
                    virtual_record_id
                )
                self.logger.warning(
                    "Virtual record %s still carried connector %s with no other "
                    "owner after the exclusive pass; graph-confirmed outcome: %s",
                    virtual_record_id,
                    connector_id,
                    outcome,
                )
                orphans += 1

            if untagged:
                self.logger.error(
                    "%d point(s) in %s carrying connector %s have no "
                    "virtualRecordId and cannot be rewritten",
                    untagged,
                    collection_name,
                    connector_id,
                )

            if not progressed:
                if untagged == len(points):
                    # Every point here is unaddressable. Reporting this
                    # incomplete would fail the event forever, since no retry
                    # can give these points a VRID — surface it and let the
                    # delete stand for everything that could be handled.
                    self.logger.error(
                        "Connector %s membership rewrite stopped in %s: %d "
                        "remaining point(s) have no virtualRecordId, so no retry "
                        "can clear them; they keep a stale connector id",
                        connector_id,
                        collection_name,
                        untagged,
                    )
                    return rewritten, orphans, True
                self.logger.error(
                    "Stopping connector %s membership rewrite in %s: a page of "
                    "points still carries the connector but yielded no new "
                    "virtual record id (rewrites may be failing silently)",
                    connector_id,
                    collection_name,
                )
                return rewritten, orphans, False

    async def purge_connector_by_virtual_record_ids(
        self, ctx: DeleteContext, virtual_record_ids: List[str] | None = None
    ) -> Dict[str, Any]:
        """Remove a connector's vector data from an explicit list of VRIDs.

        The fallback path, for connectors whose points predate the membership
        arrays — a graph lookup per VRID is the only way to reach a point that
        carries no ``connectorIds`` to find itself by. :meth:`purge_connector`
        is the normal path and needs no list.

        ``DROP_COLLECTION`` reaches here only when the registry has *proven*
        ``ctx.is_last_writer_to_collection`` — it downgrades an unproven drop
        to a filtered delete rather than trusting the strategy — so nothing
        can still be sharing a point through deduplication. Not reachable
        under ``SingleCollectionStrategy``.

        ``FILTERED_DELETE`` is different: this connector's collection can
        still hold points deduplicated with a still-live connector (same
        ``virtualRecordId`` shared via the dedup matrix). A raw filter-delete
        on ``connectorIds`` would remove those shared points too, so this
        always routes through :meth:`bulk_delete_embeddings`, which rewrites
        membership instead of deleting when a VRID is still referenced
        elsewhere. When the producer sent no VRID list — an older publisher, or
        a genuinely enumeration-free purge — the ids are recovered by scanning
        the target collections rather than falling back to the unsafe raw
        filter, which is the one path that could take a live connector's shared
        points with it.
        """
        scope = await self.collection_registry.resolve_delete_scope(ctx)
        if scope.action == DeleteAction.DROP_COLLECTION:
            # The mapping rows outlive the points they describe, so a drop that
            # skipped them would leave every VRID looking like an orphan to the
            # sweeper for as long as the rows survive. Recover them *before* the
            # collections go: afterwards there is nothing left to enumerate, and
            # a legacy `bulkDeleteRecords` without `virtualRecordIds` arrives
            # here as an empty list. A drop scope carries no filter, so scan
            # under the membership predicate — the same one the registry
            # supplies when it downgrades a drop.
            if not virtual_record_ids:
                scan = await self._scan_virtual_record_ids(
                    DeleteScope(
                        action=scope.action,
                        collection_names=scope.collection_names,
                        filter_field=CONNECTOR_IDS_FIELD,
                        filter_values=(
                            [ctx.connector_id] if ctx.connector_id else None
                        ),
                    )
                )
                virtual_record_ids = scan.ids
                if not scan.complete:
                    # The drop still goes ahead: refusing it would leave the
                    # whole collection behind, and the cap is reached exactly
                    # on the large collections a drop exists to handle. The
                    # rows this misses are not stranded — the orphan sweeper
                    # walks virtualRecordToDocIdMapping itself, so it reaches
                    # them without needing the dropped collection. Say so,
                    # because the reclaim is then deferred rather than done.
                    self.logger.warning(
                        "Scan of %s hit its bound before enumerating every "
                        "virtual record id for connector %s; dropping anyway and "
                        "leaving %d mapping row(s) beyond that point for the "
                        "orphan sweeper to reclaim",
                        scope.collection_names,
                        ctx.connector_id,
                        len(virtual_record_ids),
                    )
            for name in scope.collection_names:
                await self.collection_registry.delete_collection(name)
            await self._forget_virtual_record_mappings(virtual_record_ids or [])
            self.logger.info(
                "Purged connector %s by dropping collection(s): %s",
                ctx.connector_id,
                scope.collection_names,
            )
            return {"action": "drop_collection", "collections": scope.collection_names}

        if not virtual_record_ids:
            virtual_record_ids = (await self._scan_virtual_record_ids(scope)).ids
            if virtual_record_ids:
                self.logger.info(
                    "Recovered %d virtual record id(s) for connector %s by scanning "
                    "%s; the producer sent none",
                    len(virtual_record_ids),
                    ctx.connector_id,
                    scope.collection_names,
                )

        if not virtual_record_ids:
            self.logger.info(
                "Nothing to purge for connector %s: no virtual record ids supplied "
                "and none found in %s",
                ctx.connector_id,
                scope.collection_names,
            )
            return {"action": "noop", "collections": scope.collection_names}

        result = await self.bulk_delete_embeddings(virtual_record_ids)
        # The scope names the connector's *own* collection(s); the delete itself
        # is keyed on virtualRecordId across every managed one, because a VRID
        # whose last record just went away must not be left behind in a
        # collection some earlier deduplication put it in. Reporting both makes
        # that difference visible rather than surprising.
        self.logger.info(
            "Purged connector %s (scope collection(s): %s) via membership-aware "
            "bulk delete: %s",
            ctx.connector_id,
            scope.collection_names,
            result,
        )
        return {
            "action": "filtered_delete",
            "scope_collections": list(scope.collection_names),
            **result,
        }

    async def _scan_virtual_record_ids(self, scope) -> ScanResult:
        """Recover the VRIDs a delete scope covers by scrolling its collections.

        Bounded by ``PURGE_SCAN_MAX_POINTS``: a purge that would need more than
        that returns what it found and the orphan sweeper finishes the rest,
        which is slower but never deletes a live connector's shared points.
        """
        if not scope.filter_field or not scope.filter_values:
            # Scanning with no predicate would enumerate every point in the
            # collection, i.e. every connector's data. Refuse rather than guess.
            self.logger.error("Delete scope resolved no filter; refusing to scan")
            return ScanResult([], False)

        found: List[str] = []
        seen: set = set()
        complete = True
        for name in scope.collection_names:
            try:
                filter_dict = await self.vector_db_service.filter_collection(
                    must={scope.filter_field: scope.filter_values}
                )
                offset = None
                scanned = 0
                while scanned < PURGE_SCAN_MAX_POINTS:
                    page = await self.vector_db_service.scroll(
                        collection_name=name,
                        scroll_filter=filter_dict,
                        limit=PURGE_SCAN_PAGE_SIZE,
                        offset=offset,
                    )
                    points = list(getattr(page, "points", None) or [])
                    if not points:
                        break
                    for point in points:
                        metadata = (point.payload or {}).get("metadata") or {}
                        vrid = metadata.get("virtualRecordId")
                        if vrid and vrid not in seen:
                            seen.add(vrid)
                            found.append(vrid)
                    scanned += len(points)
                    next_offset = getattr(page, "next_offset", None)
                    # A cursor that does not advance would re-read the same page
                    # forever; the point cap alone cannot stop that, because an
                    # empty or repeated page never increments `scanned`.
                    if next_offset is None or next_offset == offset:
                        break
                    offset = next_offset
                else:
                    # Loop ended on the cap rather than exhausting the cursor.
                    complete = False
            except Exception as e:
                complete = False
                self.logger.error(
                    "Could not scan %s for connector virtual record ids: %s", name, e
                )
        return ScanResult(found, complete)

    async def _forget_virtual_record_mappings(self, virtual_record_ids: List[str]) -> None:
        if not virtual_record_ids:
            return
        try:
            await self.graph_provider.delete_nodes(
                keys=virtual_record_ids,
                collection=CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
            )
        except Exception as e:
            self.logger.error(
                "Failed to delete %d virtualRecordToDocIdMapping entries: %s",
                len(virtual_record_ids),
                e,
            )

    async def bulk_delete_embeddings(
        self,
        virtual_record_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Bulk delete embeddings for multiple records in a single operation.
        Uses filter-based deletion for efficiency.

        This is used when deleting a connector instance and all its records.

        Args:
            virtual_record_ids: List of virtual record IDs to delete embeddings for

        Returns:
            Dict with deletion statistics:
                - virtual_record_ids_processed: Number of virtual record IDs eligible for deletion
                - success: Boolean indicating success

        Raises:
            EmbeddingDeletionError: If there's an error during the deletion process
        """
        try:
            if not virtual_record_ids:
                self.logger.info("No virtual record IDs provided for bulk deletion")
                return {"virtual_record_ids_processed": 0, "success": True}

            # Normalize IDs: remove empty values and deduplicate while preserving order
            normalized_virtual_record_ids = list(
                dict.fromkeys(
                    virtual_record_id.strip()
                    for virtual_record_id in virtual_record_ids
                    if isinstance(virtual_record_id, str) and virtual_record_id.strip()
                )
            )

            if not normalized_virtual_record_ids:
                self.logger.info("No valid virtual record IDs provided for bulk deletion")
                return {"virtual_record_ids_processed": 0, "success": True}

            self.logger.info(
                f"🗑️ Starting bulk deletion candidate evaluation for {len(normalized_virtual_record_ids)} virtual record IDs"
            )

            safe_virtual_record_ids: List[str] = []
            rewritten_virtual_record_ids: List[str] = []
            skipped_virtual_record_ids: List[str] = []

            for virtual_record_id in normalized_virtual_record_ids:
                try:
                    remaining_records = await self.graph_provider.get_records_by_virtual_record_id(
                        virtual_record_id=virtual_record_id
                    )
                    remaining_keys = remaining_record_keys(remaining_records)
                    if remaining_keys:
                        # rewrite_or_delete, not sync: a VRID that survives in
                        # another connector has still *left* the collections
                        # whose records just went away. sync_vector_membership
                        # only re-stamps where records remain, so under a
                        # multi-collection strategy the deleted connector's own
                        # collection would keep points nothing references.
                        await self.rewrite_or_delete_vector_membership(virtual_record_id)
                        rewritten_virtual_record_ids.append(virtual_record_id)
                        self.logger.debug(
                            f"Rewrote vector membership for virtual_record_id {virtual_record_id} "
                            f"(still referenced by records: {remaining_keys})"
                        )
                        continue

                    safe_virtual_record_ids.append(virtual_record_id)
                except Exception as e:
                    skipped_virtual_record_ids.append(virtual_record_id)
                    self.logger.error(
                        f"❌ Failed to validate virtual_record_id {virtual_record_id} before bulk deletion: {e}. "
                        f"Skipping this ID to avoid accidental data loss."
                    )

            if skipped_virtual_record_ids:
                self.logger.info(
                    f"⏭️ Skipped {len(skipped_virtual_record_ids)} virtual record IDs during bulk deletion safety checks"
                )

            if not safe_virtual_record_ids:
                self.logger.info(
                    "No virtual record IDs are eligible for bulk deletion after safety checks"
                )
                return {
                    "virtual_record_ids_deleted": 0,
                    "virtual_record_ids_rewritten": len(rewritten_virtual_record_ids),
                    "virtual_record_ids_processed": len(rewritten_virtual_record_ids),
                    "success": True,
                }

            # Confirming pass: "no records remain" can be a stale read on a lagging
            # follower, and deleting points is irreversible. Re-checking the whole
            # candidate set once amortises the cost over the batch instead of
            # paying a delay per VRID.
            # One pause for the whole batch, not per VRID: a large batch already
            # takes time between its two reads, but a single-VRID delete would
            # otherwise re-read milliseconds later and confirm nothing.
            await asyncio.sleep(EMPTY_CONFIRM_DELAY_SECONDS)

            confirmed_virtual_record_ids: List[str] = []
            for virtual_record_id in safe_virtual_record_ids:
                try:
                    recheck = await self.graph_provider.get_records_by_virtual_record_id(
                        virtual_record_id=virtual_record_id
                    )
                    if remaining_record_keys(recheck):
                        self.logger.warning(
                            f"Virtual record {virtual_record_id} gained records on "
                            f"re-check — rewriting membership instead of deleting"
                        )
                        await self.rewrite_or_delete_vector_membership(virtual_record_id)
                        rewritten_virtual_record_ids.append(virtual_record_id)
                        continue
                    confirmed_virtual_record_ids.append(virtual_record_id)
                except Exception as e:
                    skipped_virtual_record_ids.append(virtual_record_id)
                    self.logger.error(
                        f"❌ Failed to confirm virtual_record_id {virtual_record_id} "
                        f"before deletion: {e}. Skipping to avoid data loss."
                    )

            safe_virtual_record_ids = confirmed_virtual_record_ids
            if not safe_virtual_record_ids:
                self.logger.info(
                    "No virtual record IDs survived the deletion confirmation pass"
                )
                return {
                    "virtual_record_ids_deleted": 0,
                    "virtual_record_ids_rewritten": len(rewritten_virtual_record_ids),
                    "virtual_record_ids_processed": len(rewritten_virtual_record_ids),
                    "success": True,
                }

            self.logger.info(
                f"🗑️ Proceeding with bulk deletion for {len(safe_virtual_record_ids)} safe virtual record IDs"
            )

            # Every VRID reaching this point is provably referenced by no graph
            # record, so there is nothing left to resolve a collection from —
            # and nothing that could still want the points. Deleting across
            # every managed collection is therefore the correct scope; a VRID
            # still shared with a live record took the rewrite branch above.
            # strict: an enumeration that *failed* and a deployment that
            # genuinely holds nothing both arrive here as an empty list, and the
            # two want opposite outcomes — retry versus ack. strict raises on the
            # first, so only the second reaches the check below.
            collection_names = await self.collection_locator.all_collections(
                fresh=True, strict=True
            )

            if not collection_names:
                # Nothing to delete from, so nothing was deleted. Falling
                # through would drop the mapping rows below — and those are the
                # only handle the orphan sweeper has on these points. Stop
                # instead, and let a later run (or the sweeper) do it properly.
                self.logger.error(
                    "Refusing to purge %d virtual record id(s): no managed "
                    "collections resolved, so the points would be orphaned with "
                    "their mapping rows removed",
                    len(safe_virtual_record_ids),
                )
                return {
                    "virtual_record_ids_deleted": 0,
                    "virtual_record_ids_rewritten": len(rewritten_virtual_record_ids),
                    "virtual_record_ids_processed": len(rewritten_virtual_record_ids),
                    "success": False,
                }

            # Only VRIDs whose points were actually removed may have their
            # mapping forgotten; a failed batch keeps its rows so the orphan
            # sweeper can still find what it left behind.
            deleted_virtual_record_ids: List[str] = []

            # Process in batches to avoid filter size limits
            for i in range(0, len(safe_virtual_record_ids), QDRANT_BULK_DELETE_BATCH_SIZE):
                batch = safe_virtual_record_ids[i:i + QDRANT_BULK_DELETE_BATCH_SIZE]
                batch_num = i // QDRANT_BULK_DELETE_BATCH_SIZE + 1

                try:
                    filter_dict = await self.vector_db_service.filter_collection(
                        should={"virtualRecordId": batch}
                    )

                    for collection_name in collection_names:
                        await self.vector_db_service.delete_points(
                            collection_name=collection_name,
                            filter=filter_dict,
                        )
                    deleted_virtual_record_ids.extend(batch)
                    self.logger.info(f"✅ Deleted embeddings for batch {batch_num}")

                except Exception as e:
                    self.logger.error(f"❌ Failed to delete embeddings for batch {batch_num}: {e}")
                    # Continue with next batch even if one fails
                    continue

            # Mapping last: it is how an orphaned point set is found again, so it
            # must outlive the deletes it describes.
            if deleted_virtual_record_ids:
                try:
                    await self.graph_provider.delete_nodes(
                        keys=deleted_virtual_record_ids,
                        collection=CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                    )
                    self.logger.info(
                        f"✅ Deleted {len(deleted_virtual_record_ids)} entries from virtualRecordToDocIdMapping"
                    )
                except Exception as e:
                    self.logger.error(
                        f"❌ Failed to delete from virtualRecordToDocIdMapping: {e}. "
                        f"This may lead to orphaned entries in the graph."
                    )

            safe_virtual_record_ids = deleted_virtual_record_ids

            self.logger.info(
                f"✅ Bulk deletion complete: embeddings deleted for {len(safe_virtual_record_ids)} virtual record IDs"
            )

            return {
                "virtual_record_ids_deleted": len(safe_virtual_record_ids),
                "virtual_record_ids_rewritten": len(rewritten_virtual_record_ids),
                "virtual_record_ids_processed": len(safe_virtual_record_ids) + len(rewritten_virtual_record_ids),
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to bulk delete embeddings: {str(e)}")
            raise EmbeddingDeletionError(
                f"Bulk embedding deletion failed: {str(e)}",
                record_id="bulk_delete",
                details={"error": str(e), "count": len(virtual_record_ids) if virtual_record_ids else 0}
            )

    def _process_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and enhance document metadata.

        Args:
            metadata: Original metadata dictionary

        Returns:
            Dict[str, Any]: Enhanced metadata

        Raises:
            MetadataProcessingError: If there's an error processing the metadata
        """
        try:
            block_type = meta.get("blockType", "text")
            virtual_record_id = meta.get("virtualRecordId", "")
            record_name = meta.get("recordName", "")
            if isinstance(block_type, list):
                block_type = block_type[0]

            enhanced_metadata = {
                "orgId": meta.get("orgId", ""),
                "virtualRecordId": virtual_record_id,
                "recordName": record_name,
                "recordType": meta.get("recordType", ""),
                "recordVersion": meta.get("version", ""),
                "origin": meta.get("origin", ""),
                "connector": meta.get("connectorName", ""),
                "blockNum": meta.get("blockNum", [0]),
                "blockText": meta.get("blockText", ""),
                "blockType": str(block_type),
                "departments": meta.get("departments", ""),
                "topics": meta.get("topics", ""),
                "categories": meta.get("categories", ""),
                "subcategoryLevel1": meta.get("subcategoryLevel1", ""),
                "subcategoryLevel2": meta.get("subcategoryLevel2", ""),
                "subcategoryLevel3": meta.get("subcategoryLevel3", ""),
                "languages": meta.get("languages", ""),
                "extension": meta.get("extension", ""),
                "mimeType": meta.get("mimeType", ""),
            }

            if meta.get("bounding_box"):
                enhanced_metadata["bounding_box"] = meta.get("bounding_box")
            if meta.get("sheetName"):
                enhanced_metadata["sheetName"] = meta.get("sheetName")
            if meta.get("sheetNum"):
                enhanced_metadata["sheetNum"] = meta.get("sheetNum")
            if meta.get("pageNum"):
                enhanced_metadata["pageNum"] = meta.get("pageNum")

            return enhanced_metadata

        except MetadataProcessingError:
            raise
        except Exception as e:
            raise MetadataProcessingError(
                f"Unexpected error processing metadata: {str(e)}",
                details={"error_type": type(e).__name__},
            )
