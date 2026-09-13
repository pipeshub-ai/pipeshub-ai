import logging
from typing import TYPE_CHECKING, Optional

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.reconciliation.service import ReconciliationMetadata, ReconciliationService
from app.modules.transformers.block_container_validator import BlockContainerValidator
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import ReconciliationContext, TransformContext
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
        new_metadata = reconciliation_service.build_metadata(block_containers)

        if ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value) and record.virtual_record_id and record.org_id:
            prev_vrid = ctx.prev_virtual_record_id

            if prev_vrid and prev_vrid == record.virtual_record_id:
                # 1:1 case: same vrid, do diff-based reconciliation
                old_metadata_dict = await sink_orchestrator.blob_storage.get_reconciliation_metadata(
                    record.virtual_record_id, record.org_id
                )
                if old_metadata_dict:
                    old_metadata = ReconciliationMetadata.from_dict(old_metadata_dict)
                    blocks_to_index_ids, block_ids_to_delete, unchanged_id_map = reconciliation_service.compute_diff(
                        old_metadata, new_metadata
                    )

                    if unchanged_id_map:
                        reconciliation_service.apply_preserved_ids(
                            block_containers, unchanged_id_map
                        )
                        new_metadata = reconciliation_service.build_metadata(block_containers)

                    logger.info(
                        f"📊 Reconciliation (1:1): {len(blocks_to_index_ids)} to index, "
                        f"{len(block_ids_to_delete)} to delete"
                    )
                    return ReconciliationContext(
                        new_metadata=new_metadata.to_dict(),
                        blocks_to_index_ids=blocks_to_index_ids,
                        block_ids_to_delete=block_ids_to_delete,
                    )
                logger.info(
                    f"📊 No previous metadata found for {record.virtual_record_id}, "
                    f"purging stale vectors and indexing all blocks (first reconciliation pass)"
                )
                try:
                    await sink_orchestrator.vector_store.purge_record_vectors(
                        record.org_id, record.virtual_record_id, record
                    )
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
        """Validate, index (the record becomes searchable), then hand it to the stage runtime."""
        try:
            record = ctx.record
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
                    ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value)
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
                        # Save empty reconciliation metadata so future diffs start clean
                        empty_metadata = ReconciliationMetadata().to_dict()
                        await self.sink_orchestrator.blob_storage.save_reconciliation_metadata(
                            record.org_id, record_id, record.virtual_record_id, empty_metadata
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"⚠️ Failed to clean up old vectors for empty document: {str(e)}"
                        )

                status_fields = {
                    "indexingStatus": ProgressStatus.EMPTY.value,
                    "processingStartedAt": None,
                    "isDirty": False,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                }
                success = await self.document_extraction.graph_provider.update_node(
                    record_id,
                    CollectionNames.RECORDS.value,
                    status_fields,
                )
                if not success:
                    self.logger.warning(
                        "⚠️ Failed to update indexing status for record %s - record may not exist",
                        record_id,
                    )
                return

            if ctx.reconciliation_context is None:
                ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
                    ctx, self.logger, self.sink_orchestrator
                )

            # Phase 1: Index (VectorStore + BlobStorage)
            # Document becomes searchable after this call.
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
        _ = await self.stage_ingress.on_indexed(ctx.record, trigger=ctx.event_type)
