from typing import Optional

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.reconciliation.service import ReconciliationMetadata, ReconciliationService
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import ReconciliationContext, TransformContext
from app.utils.logger import create_logger


class IndexingPipeline:
    def __init__(self, document_extraction: DocumentExtraction, sink_orchestrator: SinkOrchestrator) -> None:
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator
        self.logger = create_logger("indexing_pipeline")

    async def _build_reconciliation_context(self, ctx: TransformContext) -> Optional[ReconciliationContext]:
        """Build ReconciliationContext from ctx.record and add to ctx. Used when ctx.reconciliation_context is None."""
        record = ctx.record
        block_containers = record.block_containers
        if not block_containers:
            return None
        reconciliation_service = ReconciliationService(self.logger)
        new_metadata = reconciliation_service.build_metadata(block_containers)

        if ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value) and record.virtual_record_id and record.org_id:
            prev_vrid = ctx.prev_virtual_record_id

            if prev_vrid and prev_vrid == record.virtual_record_id:
                # 1:1 case: same vrid, do diff-based reconciliation
                old_metadata_dict = await self.sink_orchestrator.blob_storage.get_reconciliation_metadata(
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

                    self.logger.info(
                        f"📊 Reconciliation (1:1): {len(blocks_to_index_ids)} to index, "
                        f"{len(block_ids_to_delete)} to delete"
                    )
                    return ReconciliationContext(
                        new_metadata=new_metadata.to_dict(),
                        blocks_to_index_ids=blocks_to_index_ids,
                        block_ids_to_delete=block_ids_to_delete,
                    )
                self.logger.info(
                    f"📊 No previous metadata found for {record.virtual_record_id}, indexing all blocks"
                )
            elif prev_vrid and prev_vrid != record.virtual_record_id:
                # N:1 case: new vrid generated, index all blocks (no diff needed)
                self.logger.info(
                    f"📊 Reconciliation (N:1): prev_vrid={prev_vrid}, new_vrid={record.virtual_record_id}. "
                    f"Indexing all blocks with new vrid."
                )
            else:
                # No prev_vrid available, index all blocks
                self.logger.info(
                    f"📊 No prev_virtual_record_id, indexing all blocks for {record.virtual_record_id}"
                )

        return ReconciliationContext(new_metadata=new_metadata.to_dict())

    async def apply(self, ctx: TransformContext) -> None:
        try:
            record = ctx.record
            block_containers = record.block_containers
            blocks = block_containers.blocks
            block_groups = block_containers.block_groups

            if blocks is not None and len(blocks) == 0 and block_groups is not None and len(block_groups) == 0:
                record_id = record.id

                # For reconciliation-enabled 1:1 updates, clean up old vectors and metadata
                if (
                    ctx.event_type in (EventTypes.UPDATE_RECORD.value, EventTypes.REINDEX_RECORD.value)
                    and ctx.prev_virtual_record_id
                    and ctx.prev_virtual_record_id == record.virtual_record_id
                ):
                    try:
                        await self.sink_orchestrator.vector_store.delete_embeddings(
                            record.virtual_record_id
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

                record_dict = await self.document_extraction.graph_provider.get_document(
                    record_id, CollectionNames.RECORDS.value
                )

                record_dict.update(
                    {
                        "indexingStatus": ProgressStatus.EMPTY.value,
                        "isDirty": False,
                        "extractionStatus": ProgressStatus.NOT_STARTED.value,
                    }
                )

                docs = [record_dict]
                success = await self.document_extraction.graph_provider.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )
                if not success:
                    raise DocumentProcessingError(
                        "Failed to update indexing status for record id: " + record_id
                    )
                return
            if ctx.reconciliation_context is None:
                ctx.reconciliation_context = await self._build_reconciliation_context(ctx)
            await self.document_extraction.apply(ctx)
            await self.sink_orchestrator.apply(ctx)
        except Exception as e:
            raise e
