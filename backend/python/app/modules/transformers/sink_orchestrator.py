import logging

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import (
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
)
from app.modules.transformers.arango import Arango
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class SinkOrchestrator(Transformer):
    def __init__(self, arango: Arango, blob_storage: BlobStorage, vector_store: VectorStore, graph_provider: IGraphDBProvider) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.arango = arango
        self.blob_storage = blob_storage
        self.vector_store = vector_store
        self.graph_provider = graph_provider

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

        record = ctx.record
        full_block_containers = None

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
        indexing_status = record_doc.get("indexingStatus")
        if indexing_status != "COMPLETED":
            result = await self.vector_store.apply(ctx)
            if result is False:
                return
            self.logger.info(f"✅ Vector store indexing succeeded for record {record_id}")
            self.logger.info(f"Saving reconciliation metadata for record {record_id}")
            await self._save_reconciliation_metadata(ctx)
            await self.arango.apply(ctx)

        return

    async def _save_reconciliation_metadata(self, ctx: TransformContext) -> None:
        if ctx.reconciliation_context and ctx.reconciliation_context.new_metadata:
            record = ctx.record
            await self.blob_storage.save_reconciliation_metadata(
                record.org_id,
                record.id,
                record.virtual_record_id,
                ctx.reconciliation_context.new_metadata,
            )
