from app.config.constants.arangodb import CollectionNames
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class SinkOrchestrator(Transformer):
    def __init__(self, graphdb: GraphDBTransformer, blob_storage: BlobStorage, vector_store: VectorStore, graph_provider: IGraphDBProvider) -> None:
        super().__init__()
        self.graphdb = graphdb
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

            await self.graphdb.apply(ctx)

        return
