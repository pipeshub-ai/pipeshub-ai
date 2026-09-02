import asyncio
from typing import Any, Dict, List, NamedTuple

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
    remaining_record_keys,
)
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record as _rewrite_or_delete_virtual_record,
)
from app.services.vector_db.membership import (
    sync_vector_membership as _sync_vector_membership,
)
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
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
        self, ctx: DeleteContext, virtual_record_ids: List[str] | None = None
    ) -> Dict[str, Any]:
        """Remove all of a connector's vector data per the active collection strategy.

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
            collection_names = await self.collection_locator.all_collections(fresh=True)

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
