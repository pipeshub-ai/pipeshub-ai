import logging

from app.config.constants.arangodb import CollectionNames
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

    async def apply(self, ctx: TransformContext) -> None:
        
        await self.blob_storage.apply(ctx)

        record = ctx.record
        record_id = record.id
        record = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
        if record is None:
            self.logger.error(f"❌ Record {record_id} not found in database")
            raise Exception(f"Record {record_id} not found in database")
        indexing_status = record.get("indexingStatus")
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
