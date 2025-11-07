from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService
from app.modules.transformers.arango import Arango
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore


class SinkOrchestrator(Transformer):
    def __init__(self, arango: Arango, blob_storage: BlobStorage, vector_store: VectorStore, arango_service: BaseArangoService) -> None:
        super().__init__()
        self.arango = arango
        self.blob_storage = blob_storage
        self.vector_store = vector_store
        self.arango_service = arango_service

    async def apply(self, ctx: TransformContext) -> None:
        await self.blob_storage.apply(ctx)

        record = ctx.record
        record_id = record.id
        record = await self.arango_service.get_document(
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

        extraction_status = record.get("extractionStatus")
        if extraction_status != "COMPLETED":
            await self.arango.apply(ctx)
        return
