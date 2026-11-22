import logging
from typing import TYPE_CHECKING, Optional

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.modules.indexing import keep_old_copy
from app.modules.indexing.embed_resume import cleared_fields, current_attempt
from app.modules.indexing.parse_now_marker import shape_status_write
from app.modules.reconciliation.block_identity import ID_SCHEME, BlockIdentity
from app.modules.reconciliation.service import (
    ReconciliationMetadata,
    ReconciliationService,
)
from app.modules.transformers.blob_storage import StorageLookupUnavailableError
from app.modules.transformers.block_container_validator import BlockContainerValidator
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import (
    ENRICHMENT_DEFERRED,
    ENRICHMENT_FOLLOWS,
    STORE_ONLY,
    ReconciliationContext,
    TransformContext,
)
from app.modules.transformers.vectorstore import VirtualRecordBusy
from app.services.graph_db.common.record_visibility import indexed_copy_fields
from app.services.paid_cache.orphan import ensure_record_alive
from app.utils.indexing_timing import stage
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.modules.pipeline.ingress import StageIngress


class IndexingPipeline:
    def __init__(
        self,
        document_extraction: DocumentExtraction,
        sink_orchestrator: SinkOrchestrator,
        stage_ingress: "StageIngress | None" = None,
    ) -> None:
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator
        self.stage_ingress = stage_ingress
        self.logger = create_logger("indexing_pipeline")

    @staticmethod
    def assign_block_ids(reconciliation_service: ReconciliationService, record: object) -> ReconciliationMetadata:
        """Give the record's blocks content-derived ids (every parser's output goes through here) and
        return the metadata built from them. Without an org or virtual record the ids stay as parsed
        and the metadata carries no scheme, so the next diff re-keys instead of pairing them."""
        block_containers = record.block_containers
        assigned = BlockIdentity.assign(block_containers, record.org_id, record.virtual_record_id)
        metadata = reconciliation_service.build_metadata(block_containers)
        if assigned:
            metadata.id_scheme = ID_SCHEME
        return metadata

    @staticmethod
    async def build_reconciliation_context(
        ctx: TransformContext,
        logger: logging.Logger,
        sink_orchestrator: SinkOrchestrator,
    ) -> Optional[ReconciliationContext]:
        """Build ReconciliationContext from ctx.record and add to ctx. Used when ctx.reconciliation_context is None."""
        record = ctx.record
        block_containers = record.block_containers
        if not block_containers:
            return None
        reconciliation_service = ReconciliationService(logger)
        new_metadata = IndexingPipeline.assign_block_ids(reconciliation_service, record)

        if ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value) and record.virtual_record_id and record.org_id:
            prev_vrid = ctx.prev_virtual_record_id

            from app.services.paid_cache.run import current_run

            scope = current_run()
            if prev_vrid and prev_vrid == record.virtual_record_id and scope is not None and scope.refresh:
                # A user's Force reindex (a refresh run) re-embeds every block: no diff keeps an unchanged block's
                # point, and no re-key moves one. The record's old points go first, as on a first reconciliation pass.
                # The points go before the blocks are embedded again: if indexing then fails, the record has no vectors
                # until its retry (its status is FAILED, never COMPLETED, so the retry runs).
                logger.warning(
                    f"Forced reindex of {record.virtual_record_id}: purging its vectors, then indexing every block; "
                    f"the record has no vectors until this indexing completes"
                )
                try:
                    await sink_orchestrator.vector_store.purge_record_vectors(
                        record.org_id, record.virtual_record_id, record
                    )
                except VirtualRecordBusy:
                    raise  # the virtual record's lease is busy or lost: the delivery is handed back, not indexed over old points
                except Exception as e:
                    # Not purged: an unchanged block would be indexed twice, so the failure is the delivery's.
                    logger.warning(f"Failed to purge vectors for a forced reindex of {record.virtual_record_id}: {e}")
                    raise
            elif prev_vrid and prev_vrid == record.virtual_record_id:
                # 1:1 case: same vrid, do diff-based reconciliation
                old_metadata_dict = await sink_orchestrator.blob_storage.get_reconciliation_metadata(
                    record.virtual_record_id, record.org_id
                )
                if old_metadata_dict:
                    old_metadata = ReconciliationMetadata.from_dict(old_metadata_dict)
                    if old_metadata.id_scheme != new_metadata.id_scheme:
                        # Stored points carry ids of another scheme: unchanged blocks are moved to their new ids.
                        blocks_to_index_ids, block_ids_to_delete, rekey_ids = reconciliation_service.compute_rekey(
                            old_metadata, new_metadata
                        )
                        logger.info(
                            f"Reconciliation (1:1, re-key): {len(rekey_ids)} to move, "
                            f"{len(blocks_to_index_ids)} to index, {len(block_ids_to_delete)} to delete"
                        )
                        return ReconciliationContext(
                            new_metadata=new_metadata.to_dict(),
                            blocks_to_index_ids=blocks_to_index_ids,
                            block_ids_to_delete=block_ids_to_delete,
                            rekey_ids=rekey_ids,
                            recorded_points=old_metadata.pointed_blocks,
                        )
                    blocks_to_index_ids, block_ids_to_delete, unchanged_id_map = reconciliation_service.compute_diff(
                        old_metadata, new_metadata
                    )

                    if unchanged_id_map:
                        reconciliation_service.apply_preserved_ids(
                            block_containers, unchanged_id_map
                        )
                        new_metadata = reconciliation_service.build_metadata(block_containers)
                        new_metadata.id_scheme = old_metadata.id_scheme

                    logger.info(
                        f"📊 Reconciliation (1:1): {len(blocks_to_index_ids)} to index, "
                        f"{len(block_ids_to_delete)} to delete"
                    )
                    return ReconciliationContext(
                        new_metadata=new_metadata.to_dict(),
                        blocks_to_index_ids=blocks_to_index_ids,
                        block_ids_to_delete=block_ids_to_delete,
                        recorded_points=old_metadata.pointed_blocks,
                    )
                attempt = current_attempt()
                if attempt is not None and attempt.resume:
                    # The points already written by the interrupted run are what a resume keeps.
                    logger.info(
                        f"Resuming the first reconciliation pass of {record.virtual_record_id}; "
                        f"not purging its vectors"
                    )
                    return ReconciliationContext(new_metadata=new_metadata.to_dict())
                logger.info(
                    f"📊 No previous metadata found for {record.virtual_record_id}, "
                    f"purging stale vectors and indexing all blocks (first reconciliation pass)"
                )
                try:
                    await sink_orchestrator.vector_store.purge_record_vectors(
                        record.org_id, record.virtual_record_id, record
                    )
                except VirtualRecordBusy:
                    raise  # the virtual record's lease is busy or lost: the delivery is handed back, not indexed over the old points
                except Exception as e:
                    logger.warning(
                        f"⚠️ Failed to purge stale vectors during first reconciliation pass "
                        f"for {record.virtual_record_id}: {str(e)}"
                    )
            elif prev_vrid and prev_vrid != record.virtual_record_id:
                # N:1 case: new vrid generated, index all blocks (no diff needed)
                logger.info(
                    f"📊 Reconciliation (N:1): prev_vrid={prev_vrid}, new_vrid={record.virtual_record_id}. "
                    f"Indexing all blocks with new vrid."
                )
            else:
                # No prev_vrid available, index all blocks
                logger.info(
                    f"📊 No prev_virtual_record_id, indexing all blocks for {record.virtual_record_id}"
                )

        return ReconciliationContext(new_metadata=new_metadata.to_dict())

    async def apply(self, ctx: TransformContext) -> None:
        """Validate, index (the record becomes searchable), then hand it to the stage runtime.

        A store-only run (parse now, automatic indexing off) validates and stores, and stops: nothing is
        purged, reconciled or handed to the stages.
        """
        try:
            store_only = bool(ctx.settings.get(STORE_ONLY))
            record = ctx.record
            # A record deleted while its run was in flight is not embedded or written (see paid_cache/orphan.py).
            await ensure_record_alive(record.org_id, record.id, "embed")
            block_containers = record.block_containers

            if block_containers is not None:
                BlockContainerValidator(
                    logger=self.logger,
                    record_id=record.id,
                    virtual_record_id=record.virtual_record_id,
                    record_name=getattr(record, 'record_name', None),
                ).validate(block_containers)
                blocks = block_containers.blocks
                block_groups = block_containers.block_groups
            else:
                blocks = None
                block_groups = None

            if blocks is not None and len(blocks) == 0 and block_groups is not None and len(block_groups) == 0:
                record_id = record.id

                # For reconciliation-enabled 1:1 updates, clean up old vectors and metadata
                if (
                    not store_only
                    and ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value)
                    and ctx.prev_virtual_record_id
                    and ctx.prev_virtual_record_id == record.virtual_record_id
                ):
                    try:
                        await self.sink_orchestrator.vector_store.purge_record_vectors(
                            record.org_id, record.virtual_record_id, record
                        )
                        self.logger.info(
                            f"🗑️ Deleted old embeddings for empty document update (1:1): "
                            f"{record.virtual_record_id}"
                        )
                        # Save empty reconciliation metadata so future diffs
                        # start clean. document_path is derived from
                        # content's actual current location (not guessed) so
                        # metadata stays selectable by future move-tree
                        # operations -- see blob_storage.py's apply()'s
                        # actual_storage_path tracking and
                        # get_actual_content_path().
                        empty_metadata = ReconciliationMetadata().to_dict()
                        # The metadata must still be emptied, or the next update
                        # diffs against stale block hashes and skips re-embedding.
                        # With the content's path unknown, only an existing
                        # metadata document is updated: none is created at a
                        # guessed path, away from the content it describes.
                        try:
                            actual_content_path = await self.sink_orchestrator.blob_storage.get_actual_content_path(
                                record.org_id, record.virtual_record_id
                            )
                            path_known = True
                        except StorageLookupUnavailableError:
                            actual_content_path, path_known = None, False
                        await self.sink_orchestrator.blob_storage.save_reconciliation_metadata(
                            record.org_id, record_id, record.virtual_record_id, empty_metadata,
                            document_path=actual_content_path,
                            connector_id=getattr(record, "connector_id", None),
                            record_group_id=getattr(record, "record_group_id", None),
                            create_if_missing=path_known,
                        )
                    except (StorageLookupUnavailableError, VirtualRecordBusy):
                        raise  # the event is retried; stale metadata must not stay, and the old points must not stay searchable
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ Failed to clean up old vectors for empty document: {str(e)}"
                        )

                status_fields = {
                    **(cleared_fields() if current_attempt() is not None else {}),
                    "indexingStatus": ProgressStatus.EMPTY.value,
                    **indexed_copy_fields(ProgressStatus.EMPTY),
                    # The parse finished and found nothing to index.
                    "parsingStatus": ProgressStatus.COMPLETED.value,
                    "processingStartedAt": None,
                    "isDirty": False,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }
                swap_run = keep_old_copy.current_swap(record_id, record.virtual_record_id)
                if swap_run is not None:
                    # The new copy is empty: the record moves to it and the old copy is released after the commit.
                    success = await self.sink_orchestrator.swap.commit(ctx, swap_run, shape_status_write(status_fields))
                    if success:
                        await self.sink_orchestrator.swap.finish(ctx, swap_run)
                else:
                    success = await self.document_extraction.graph_provider.update_node(
                        record_id,
                        CollectionNames.RECORDS.value,
                        shape_status_write(status_fields),
                    )
                if not success:
                    self.logger.warning(
                        "⚠️ Failed to update indexing status for record %s - record may not exist",
                        record_id,
                    )
                return

            if ctx.reconciliation_context is None and not store_only:
                with stage("reconcile"):
                    ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
                        ctx, self.logger, self.sink_orchestrator
                    )

            # Phase 1: Index (VectorStore + BlobStorage)
            # Document becomes searchable after this call; enrichment follows as
            # the classify stage.
            if store_only:
                await self._index(ctx)
                return
            ctx.settings = {**ctx.settings, ENRICHMENT_FOLLOWS: True}
            await self._index(ctx)

            # Classification and later stages run on their own permits, so this
            # record's index permit is released as soon as it is searchable.
            await self._dispatch_stages(ctx)

        except Exception as e:
            raise e

    async def _index(self, ctx: TransformContext) -> None:
        """Phase 1: VectorStore + BlobStorage.  Sets indexingStatus=COMPLETED."""
        await self.sink_orchestrator.index(ctx)

    async def _dispatch_stages(self, ctx: TransformContext) -> None:
        if self.stage_ingress is None:
            raise RuntimeError("the pipeline stage runtime is not wired into this IndexingPipeline")
        _ = await self.stage_ingress.on_indexed(
            ctx.record,
            trigger=ctx.event_type,
            defer_classify=bool(ctx.settings.get(ENRICHMENT_DEFERRED)),
        )
