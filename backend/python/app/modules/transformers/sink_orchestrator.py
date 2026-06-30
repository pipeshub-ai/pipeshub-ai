import logging

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.models.blocks import (
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class SinkOrchestrator(Transformer):
    def __init__(self, graphdb: GraphDBTransformer, blob_storage: BlobStorage, vector_store: VectorStore, graph_provider: IGraphDBProvider, logger) -> None:
        super().__init__()
        self.graphdb = graphdb
        self.logger = logging.getLogger(__name__)
        self.blob_storage = blob_storage
        self.vector_store = vector_store
        self.graph_provider = graph_provider
        self.logger = logger

    # This is not a good long-term solution and should be improved in the future.
    LIMIT_SQL_ROW_BLOCKS_TO = 10
    def _build_limited_sql_block_container(
        self, block_containers: BlocksContainer, limit: int
    ) -> BlocksContainer:
        """Build a BlocksContainer with at most `limit` row blocks for blob storage.
        """
        original_blocks = block_containers.blocks
        row_blocks = [b for b in original_blocks if b.type == BlockType.TABLE_ROW]

        if len(row_blocks) <= limit:
            return block_containers 
        limited_blocks =  row_blocks[:limit]

        kept_indices = {b.index for b in limited_blocks}

        limited_block_groups = []
        for bg in block_containers.block_groups:
            bg_copy = bg.model_copy(deep=True)
            if bg_copy.children and bg_copy.children.block_ranges:
                kept_child_indices = []
                for r in bg_copy.children.block_ranges:
                    for idx in range(r.start, r.end + 1):
                        if idx in kept_indices:
                            kept_child_indices.append(idx)
                bg_copy.children = BlockGroupChildren.from_indices(
                    block_indices=kept_child_indices,
                    block_group_indices=(
                        [idx for r in bg_copy.children.block_group_ranges for idx in range(r.start, r.end + 1)]
                        if bg_copy.children.block_group_ranges
                        else None
                    ),
                )
            limited_block_groups.append(bg_copy)

        self.logger.info(
            "📦 SQL blob-storage limit applied: %d / %d row blocks kept",
            len(limited_blocks),
            len(row_blocks),
        )
        return BlocksContainer(blocks=limited_blocks, block_groups=limited_block_groups)


    async def apply(self, ctx: TransformContext) -> None:
        """Legacy entry-point: runs both phases (index + enrich) sequentially.

        Preserved for backward compatibility with code that has not been
        migrated to the split ``index()`` / ``enrich()`` API.
        """
        await self.index(ctx)
        await self.enrich(ctx)

    # ------------------------------------------------------------------
    # Phase 1: INDEX — vector store + blob.  Document becomes searchable.
    # ------------------------------------------------------------------

    async def index(self, ctx: TransformContext) -> None:
        """Phase 1: write to BlobStorage and VectorStore.

        After this method returns, the document is queryable via the vector
        store and ``indexingStatus`` is set to ``COMPLETED``.  The graph
        enrichment (``extractionStatus``) is left unchanged here.
        """
        record = ctx.record
        full_block_containers = None
        skip_vector_store = bool(ctx.settings.get("skip_vector_store")) or bool(
            ctx.settings.get("sink_only")
        )

        is_sql = any(
            bg.sub_type in (GroupSubType.SQL_TABLE, GroupSubType.SQL_VIEW)
            for bg in record.block_containers.block_groups
        ) if record.block_containers.block_groups else False

        if is_sql and self.LIMIT_SQL_ROW_BLOCKS_TO is not None:
            full_block_containers = record.block_containers
            record.block_containers = self._build_limited_sql_block_container(
                full_block_containers, self.LIMIT_SQL_ROW_BLOCKS_TO
            )

        await self.blob_storage.apply(ctx)

        if full_block_containers is not None:
            record.block_containers = full_block_containers

        record_id = record.id
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is None:
            self.logger.error(f"❌ Record {record_id} not found in database")
            raise Exception(f"Record {record_id} not found in database")

        if skip_vector_store:
            success = await self.graph_provider.batch_update_nodes(
                [
                    {
                        "id": record_id,
                        "virtualRecordId": record.virtual_record_id,
                        "indexingStatus": ProgressStatus.NOT_STARTED.value,
                        "isDirty": False,
                    }
                ],
                CollectionNames.RECORDS.value,
            )
            if not success:
                self.logger.warning(
                    "⚠️ Failed to update record %s status - record may not exist in database",
                    record_id,
                )
                return
            self.logger.info(
                "✅ Sink-only mode completed for record %s (vector indexing skipped)",
                record_id,
            )
            return

        indexing_status = record_doc.get("indexingStatus")
        if indexing_status != ProgressStatus.COMPLETED.value:
            result = await self.vector_store.apply(ctx)
            if result is False:
                return
            self.logger.info(
                "✅ Vector store indexing succeeded for record %s", record_id
            )
            await self._update_indexing_status(ctx)
            await self._save_reconciliation_metadata(ctx)

    async def _update_indexing_status(self, ctx: TransformContext) -> None:
        """Mark indexingStatus=COMPLETED without touching extractionStatus."""
        record = ctx.record
        timestamp = get_epoch_timestamp_in_ms()
        await self.graph_provider.batch_upsert_nodes(
            [
                {
                    "id": record.id,
                    "virtualRecordId": record.virtual_record_id,
                    "indexingStatus": ProgressStatus.COMPLETED.value,
                    "lastIndexTimestamp": timestamp,
                    "isDirty": False,
                }
            ],
            CollectionNames.RECORDS.value,
        )
        self.logger.info(
            "✅ indexingStatus=COMPLETED recorded for %s", record.id
        )

    # ------------------------------------------------------------------
    # Phase 2: ENRICH — graph DB taxonomy.  Can run later (deferred).
    # ------------------------------------------------------------------

    async def enrich(self, ctx: TransformContext) -> None:
        """Phase 2: write classification metadata to the graph database.

        Calls ``graphdb.apply()`` which already sets
        ``extractionStatus=COMPLETED`` once it finishes.  Callers should
        ensure ``ctx.record.semantic_metadata`` is populated before calling
        this method.
        """
        await self.graphdb.apply(ctx)
        self.logger.info(
            "✅ Graph enrichment completed for record %s", ctx.record.id
        )

    async def _save_reconciliation_metadata(self, ctx: TransformContext) -> None:
        if ctx.reconciliation_context and ctx.reconciliation_context.new_metadata:
            record = ctx.record
            await self.blob_storage.save_reconciliation_metadata(
                record.org_id,
                record.id,
                record.virtual_record_id,
                ctx.reconciliation_context.new_metadata,
            )
