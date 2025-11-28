from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext


class IndexingPipeline:
    def __init__(self, document_extraction: DocumentExtraction, sink_orchestrator: SinkOrchestrator) -> None:
        self.document_extraction = document_extraction
        self.sink_orchestrator = sink_orchestrator

    async def apply(self, ctx: TransformContext) -> None:
        try:
            record = ctx.record
            block_containers = record.block_containers
            blocks = block_containers.blocks
            block_groups = block_containers.block_groups

            if blocks is not None and len(blocks) == 0 and block_groups is not None and len(block_groups) == 0:
                record_id = record.id
                record_dict = await self.document_extraction.arango_service.get_document(
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
                success = await self.document_extraction.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )
                if not success:
                    raise DocumentProcessingError(
                        "Failed to update indexing status for record id: " + record_id
                    )
                return
            await self.document_extraction.apply(ctx)
            await self.sink_orchestrator.apply(ctx)
        except Exception as e:
            raise e
