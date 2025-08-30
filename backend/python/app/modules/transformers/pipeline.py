from app.modules.transformers.transformer import Transformer, TransformContext
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator

class IndexingPipeline:
    def __init__(self, document_extraction: DocumentExtraction, sink_orchestrator: SinkOrchestrator):
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator

    async def apply(self, ctx: TransformContext) -> TransformContext:
        
        await self.document_extraction.apply(ctx)
        print("document extracted")
        await self.sink_orchestrator.apply(ctx)
        return ctx