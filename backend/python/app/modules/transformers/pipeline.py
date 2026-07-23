import logging
from typing import Optional

from app.config.constants.arangodb import CollectionNames, EventTypes, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.reconciliation.service import ReconciliationMetadata, ReconciliationService
from app.modules.transformers.block_container_validator import BlockContainerValidator
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import ReconciliationContext, TransformContext
from app.utils.logger import create_logger


class IndexingPipeline:
    def __init__(
        self,
        document_extraction: DocumentExtraction,
        sink_orchestrator: SinkOrchestrator,
        defer_extraction: bool = False,
    ) -> None:
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator
        self.defer_extraction = defer_extraction
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
                    # backwa
                    await sink_orchestrator.vector_store.delete_embeddings(
                        record.virtual_record_id
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
        """Full pipeline: validate → index (searchable) → enrich (graph taxonomy).

        When ``ctx.settings["defer_extraction"]`` is truthy *or* the instance
        was constructed with ``defer_extraction=True``, the enrich phase is
        skipped here and callers are expected to trigger it later (e.g. via a
        Kafka event).  The index phase always runs synchronously so the
        document is immediately searchable.
        """
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
                    raise RuntimeError(
                        f"Failed to persist EMPTY status for record {record_id}"
                    )
                return

            if ctx.reconciliation_context is None:
                ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
                    ctx, self.logger, self.sink_orchestrator
                )

            # Phase 1: Index (VectorStore + BlobStorage)
            # Document becomes searchable after this call.
            await self._index(ctx)

            # Phase 2: Enrich (DocumentExtraction + GraphDB)
            # May be deferred to a background process.
            should_defer = self.defer_extraction or bool(ctx.settings.get("defer_extraction"))
            if should_defer:
                await self._publish_enrichment_event(ctx)
            else:
                await self._enrich(ctx)

        except Exception as e:
            raise e

    async def _index(self, ctx: TransformContext) -> None:
        """Phase 1: VectorStore + BlobStorage.  Sets indexingStatus=COMPLETED."""
        await self.sink_orchestrator.index(ctx)

    async def _enrich(self, ctx: TransformContext) -> None:
        """Phase 2: DocumentExtraction + GraphDB.  Sets extractionStatus=COMPLETED."""
        await self.document_extraction.apply(ctx)
        await self.sink_orchestrator.enrich(ctx)

    async def _publish_enrichment_event(self, ctx: TransformContext) -> None:
        """Stub: publish an event for deferred enrichment via Kafka.

        Future implementation should produce a message containing at minimum:
        ``{record_id, virtual_record_id, org_id}`` to a dedicated enrichment
        topic so a separate consumer can call ``_enrich()`` asynchronously.
        """
        self.logger.info(
            "📨 Deferred enrichment requested for record %s — "
            "Kafka publish not yet implemented, falling back to inline enrichment",
            ctx.record.id,
        )
        await self._enrich(ctx)
