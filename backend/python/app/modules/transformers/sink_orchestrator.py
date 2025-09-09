from app.modules.transformers.arango import Arango
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore


class SinkOrchestrator(Transformer):
    def __init__(self, arango: Arango, blob_storage: BlobStorage, vector_store: VectorStore) -> None:
        super().__init__()
        self.arango = arango
        self.blob_storage = blob_storage
        self.vector_store = vector_store

    async def apply(self, ctx: TransformContext) -> TransformContext:
        await self.arango.apply(ctx)
        await self.vector_store.apply(ctx)
        await self.blob_storage.apply(ctx)
        return ctx
