from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext


class IndexingPipeline:
    def __init__(self, document_extraction: DocumentExtraction, sink_orchestrator: SinkOrchestrator) -> None:
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator

    async def apply(self, ctx: TransformContext) -> None:
        try:
            await self.document_extraction.apply(ctx)
            await self.sink_orchestrator.apply(ctx)
        except Exception as e:
            raise e
