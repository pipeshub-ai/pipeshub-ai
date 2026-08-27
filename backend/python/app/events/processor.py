import asyncio
import io
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.config.constants.ai_models import AzureDocIntelligenceModel, OCRProvider
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ExtensionTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    IndexingError,
    RecordStatusUpdateError,
)
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData
from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    Point,
)
from app.models.entities import Record, RecordType
from app.modules.parsers.code_parser.lang_config import config_for_extension, detect_language
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.pdf.docling_processor import DoclingProcessor
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.modules.parsers.pdf.pdfplumber_opencv_processor import PDFPlumberOpenCVProcessor
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext
from app.services.docling.client import DoclingClient
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import is_multimodal_llm
from app.utils.llm import get_embedding_model_config, get_llm, get_llm_for_role
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.concurrency import MAX_CONCURRENT_PAGE_BUILDS
from app.utils.table_enrichment import enhance_tables_with_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms


SCANNED_PDF_NO_OCR_MESSAGE = "Scanned document, add Multimodal"


def convert_record_dict_to_record(record_dict: dict) -> Record:
    conn_name_value = record_dict.get("connectorName")
    try:
        connector_name = (
            Connectors(conn_name_value)
            if conn_name_value is not None
            else Connectors.KNOWLEDGE_BASE
        )
    except ValueError:
        connector_name = Connectors.KNOWLEDGE_BASE
    origin_value = record_dict.get("origin", OriginTypes.UPLOAD.value)
    try:
        origin = OriginTypes(origin_value)
    except ValueError:
        origin = OriginTypes.UPLOAD

    mime_type = record_dict.get("mimeType")

    return Record(
        id=record_dict.get("_key") or record_dict.get("id"),
        org_id=record_dict.get("orgId"),
        record_name=record_dict.get("recordName"),
        record_type=RecordType(record_dict.get("recordType", "FILE")),
        record_status=ProgressStatus(record_dict.get("indexingStatus", "NOT_STARTED")),
        external_record_id=record_dict.get("externalRecordId"),
        version=record_dict.get("version", 1),
        origin=origin,
        summary_document_id=record_dict.get("summaryDocumentId"),
        created_at=(
            record_dict["createdAtTimestamp"]
            if record_dict.get("createdAtTimestamp") is not None
            else get_epoch_timestamp_in_ms()
        ),
        updated_at=(
            record_dict["updatedAtTimestamp"]
            if record_dict.get("updatedAtTimestamp") is not None
            else get_epoch_timestamp_in_ms()
        ),
        source_created_at=record_dict.get("sourceCreatedAtTimestamp"),
        source_updated_at=record_dict.get("sourceLastModifiedTimestamp"),
        weburl=record_dict.get("webUrl"),
        mime_type=mime_type,
        external_revision_id=record_dict.get("externalRevisionId"),
        connector_name=connector_name,
        is_vlm_ocr_processed=record_dict.get("isVLMOcrProcessed", False),
        connector_id=record_dict.get("connectorId"),
        md5_hash=record_dict.get("md5Checksum"),
        record_group_id=record_dict.get("recordGroupId"),
        external_record_group_id=record_dict.get("externalGroupId"),
    )

class Processor:
    def __init__(
        self,
        logger,
        config_service,
        indexing_pipeline,
        graph_provider: IGraphDBProvider,
        parsers,
        document_extractor,
        sink_orchestrator,
    ) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing Processor")
        self.indexing_pipeline = indexing_pipeline
        self.graph_provider = graph_provider
        self.parsers = parsers
        self.config_service = config_service
        self.document_extraction = document_extractor
        self.sink_orchestrator = sink_orchestrator

        # Initialize Docling client for external service
        self.docling_client = DoclingClient()
        # Shared local block-builder: parsing (DoclingDocument) is fetched either
        # from the external Docling service (PDF) or parsed in-process (DOCX/PPTX/OCR),
        # but block construction (incl. LLM table enrichment) always happens here.
        self.docling_processor = DoclingProcessor(logger=self.logger, config=self.config_service)

    async def _get_llm_for_role(self, role: str, *, reasoning_effort: str | None = None):
        """Resolve LLM for a role."""
        return await get_llm_for_role(
            self.config_service, role, reasoning_effort=reasoning_effort
        )

    def _convert_record(self, record_dict: dict) -> Record:
        """Map a record document to a Record model."""
        return convert_record_dict_to_record(record_dict)

    def _create_transform_context(
        self,
        record,
        event_type: Optional[str] = None,
        prev_virtual_record_id: Optional[str] = None,
    ) -> TransformContext:
        """Create TransformContext with per-invocation reconciliation context."""
        return TransformContext(
            record=record,
            event_type=event_type,
            prev_virtual_record_id=prev_virtual_record_id,
        )

    async def process_image(self, record_id, content, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Process image content, yielding phase completion events."""
        try:
            # Initialize image parser
            self.logger.debug("📸 Processing image content")
            if not content:
                raise Exception("No image data provided")

            record = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
                # Must yield both events to release semaphores properly
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            _ , config = await self._get_llm_for_role("indexing", reasoning_effort="low")
            is_multimodal_llm = config.get("isMultimodal")

            embedding_config = await get_embedding_model_config(self.config_service)
            is_multimodal_embedding = embedding_config.get("isMultimodal") if embedding_config else False
            if not is_multimodal_embedding and not is_multimodal_llm:
                try:
                    status_fields = {
                        "indexingStatus": ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
                        "extractionStatus": ProgressStatus.NOT_STARTED.value,
                        "processingStartedAt": None,
                    }
                    success = await self.graph_provider.update_node(
                        record_id,
                        CollectionNames.RECORDS.value,
                        status_fields,
                    )
                    if not success:
                        self.logger.warning(
                            "⚠️ Failed to update indexing status for record %s - record may not exist",
                            record_id,
                        )
                    # Yield both events since we're skipping processing
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    return
                except IndexingError:
                    raise
                except Exception as e:
                    raise DocumentProcessingError(
                        "Error updating record status: " + str(e),
                        doc_id=record_id,
                        details={"error": str(e)},
                    ) from e

            mime_type = record.get("mimeType")
            if mime_type is None:
                raise Exception("No mime type present in the record from graph db")
            extension = get_extension_from_mimetype(mime_type)

            parser = self.parsers.get(extension)
            if not parser:
                raise Exception(f"Unsupported extension: {extension}")

            block_containers = parser.parse_image(content, extension)
            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            # Signal parsing complete
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))

            self.logger.info("✅ Image processing completed successfully")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing image: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=record_id,
                details={"error": str(e)},
            ) from e


    async def process_gmail_message(
        self,
        recordName: str,
        recordId: str,
        version: int | str,
        source: str,
        orgId: str,
        mail_content: bytes | str,
        virtual_record_id: str,
        event_type: Optional[str] = None,
        prev_virtual_record_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Gmail message, yielding phase completion events."""

        try:
            async for event in self.process_html_document(
                recordName=recordName,
                recordId=recordId,
                version=version,
                source=source,
                orgId=orgId,
                html_binary=mail_content,
                virtual_record_id=virtual_record_id,
                event_type=event_type,
                prev_virtual_record_id=prev_virtual_record_id,
            ):
                yield event

            self.logger.info("✅ Gmail Message processing completed successfully using HTML processing.")

        except Exception as e:
            self.logger.error(f"❌ Error processing Gmail Message document: {str(e)}")
            raise

    async def process_pdf_with_pdf_plumber(self, recordName, recordId, pdf_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PDF using PdfPlumber+OpenCV processor, yielding phase completion events."""
        self.logger.info(f"🚀 Starting PDF document processing for record: {recordId}")
        try:
            self.logger.debug("📄 Processing PDF binary content using PdfPlumber+OpenCV processor")

            record_name = recordName if recordName.endswith(".pdf") else f"{recordName}.pdf"

            processor = PDFPlumberOpenCVProcessor(
                logger=self.logger,
                config=self.config_service,
            )

            # Phase 1: Parse PDF layout (no LLM calls)
            parsed_data = await processor.parse_document(record_name, pdf_binary)

            # Signal parsing complete
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(parsed_data)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info(f"✅ PDF processing completed for record: {recordName}, using PdfPlumber+OpenCV processor")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document with PdfPlumber+OpenCV: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_pdf_with_docling(self, recordName, recordId, pdf_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PDF with Docling, yielding phase completion events."""
        self.logger.info(f"🚀 Starting PDF document processing for record: {recordName}")
        try:
            self.logger.debug("📄 Processing PDF binary content using external Docling service")

            record_name = recordName if recordName.endswith(".pdf") else f"{recordName}.pdf"

            # Phase 1: Parse PDF via the external Docling service (no LLM calls)
            doc = await self.docling_client.parse_pdf_batched(record_name, pdf_binary)
            if doc is None:
                self.logger.error(f"❌ External Docling service failed to parse {recordName}")
                yield PipelineEvent(event=IndexingEvent.DOCLING_FAILED, data=PipelineEventData(record_id=recordId))
                return

            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Phase 2: Create blocks locally (involves LLM calls for tables)
            block_containers = await self.docling_processor.create_blocks(doc)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info(f"✅ PDF processing completed for record: {recordName}, using external Docling service")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document with external Docling service: {str(e)}")
            yield PipelineEvent(event=IndexingEvent.DOCLING_FAILED, data=PipelineEventData(record_id=recordId))

    async def process_pdf_document_with_ocr(
        self, recordName, recordId, version, source, orgId, pdf_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PDF document with OCR, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting PDF document processing for record: {recordName}"
        )

        try:
            self.logger.debug("📄 Processing PDF binary content")
            # Get OCR configurations
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value
            )
            ocr_configs = ai_models["ocr"]

            # Configure OCR handler
            self.logger.debug("🛠️ Configuring OCR handler")
            handler = None

            provider = None
            for config in ocr_configs:
                provider = config["provider"]
                self.logger.info(f"🔧 Checking OCR provider: {provider}")

                if provider == OCRProvider.VLM_OCR.value:
                    self.logger.debug("🤖 Setting up VLM OCR handler")
                    handler = OCRHandler(
                        self.logger,
                        OCRProvider.VLM_OCR.value,
                        config=self.config_service
                    )
                    break

            if not handler:
                # Check if multimodal LLM is available
                self.logger.debug("🔍 Checking for multimodal LLM availability")
                has_multimodal_llm = False

                try:
                    llm_configs = ai_models.get("llm", [])
                    for llm_config in llm_configs:
                        if is_multimodal_llm(llm_config):
                            has_multimodal_llm = True
                            self.logger.info(f"✅ Found multimodal LLM: {llm_config.get('provider')}")
                            break
                except Exception as e:
                    self.logger.warning(f"⚠️ Error checking for multimodal LLM: {str(e)}")

                if has_multimodal_llm:
                    self.logger.debug("🤖 Setting up VLM OCR handler (multimodal LLM detected)")
                    handler = OCRHandler(self.logger, OCRProvider.VLM_OCR.value, config=self.config_service)
                    provider = OCRProvider.VLM_OCR.value
                else:
                    self.logger.warning("⚠️ Scanned PDF detected but no OCR provider (Azure DI or multimodal LLM) is configured")
                    raise IndexingError(SCANNED_PDF_NO_OCR_MESSAGE, record_id=recordId)

            # Process document
            self.logger.info("🔄 Processing document with OCR handler")
            ocr_result = await handler.process_document(pdf_binary)

            self.logger.debug("✅ OCR processing completed")



            if provider == OCRProvider.VLM_OCR.value:
                pages = ocr_result.get("pages", [])
                self.logger.info(f"📄 Processing {len(pages)} pages from VLM OCR")

                # Phase 1: Parse all pages with Docling (no LLM calls yet)
                all_conv_results = []
                processor = self.docling_processor

                for page in pages:
                    page_number = page.get("page_number")
                    page_markdown = page.get("markdown", "")

                    if not page_markdown.strip():
                        self.logger.debug(f"⏭️ Skipping empty page {page_number}")
                        continue

                    # Parse each page through DoclingProcessor (no LLM calls)
                    page_filename = f"{Path(recordName).stem}_page_{page_number}.md"
                    md_bytes = page_markdown.encode('utf-8')

                    try:
                        conv_res = await processor.parse_document(page_filename, md_bytes)
                        all_conv_results.append((page_number, conv_res))
                    except Exception as e:
                        self.logger.error(f"❌ Failed to parse page {page_number}: {str(e)}")
                        raise

                # Signal parsing complete after all pages are parsed
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

                # Phase 2: Create blocks for all pages (involves LLM calls for tables).
                # Fan out create_blocks with a cap; cancel stragglers on the first failure
                # so we keep today's fail-fast. Results stay in page order for the
                # sequential index-offset merge below.
                page_build_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGE_BUILDS)

                async def _create_page_blocks(
                    page_number: int, conv_res: Any
                ) -> Optional[BlocksContainer]:
                    async with page_build_semaphore:
                        try:
                            return await processor.create_blocks(
                                conv_res, page_number=page_number
                            )
                        except Exception as e:
                            self.logger.error(
                                f"❌ Failed to create blocks for page {page_number}: {str(e)}"
                            )
                            raise

                page_build_tasks = [
                    asyncio.create_task(_create_page_blocks(page_number, conv_res))
                    for page_number, conv_res in all_conv_results
                ]
                try:
                    page_block_results = await asyncio.gather(*page_build_tasks)
                except Exception:
                    self.logger.error(
                        "❌ Cancelling remaining page block builds due to failure"
                    )
                    for task in page_build_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*page_build_tasks, return_exceptions=True)
                    raise

                combined_block_containers = BlocksContainer()
                for page_block_containers in page_block_results:
                    if page_block_containers:
                        combined_block_containers.extend(page_block_containers)

                self.logger.info(
                    f"📦 Combined {len(combined_block_containers.blocks)} blocks and "
                    f"{len(combined_block_containers.block_groups)} block groups from all pages"
                )

                # Get record and run indexing pipeline
                record = await self.graph_provider.get_document(recordId, CollectionNames.RECORDS.value)
                if record is None:
                    self.logger.error(f"❌ Record {recordId} not found in database")
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                    return

                record = self._convert_record(record)
                record.block_containers = combined_block_containers
                record.virtual_record_id = virtual_record_id
                record.is_vlm_ocr_processed = True

                ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
                pipeline = IndexingPipeline(
                    document_extraction=self.document_extraction,
                    sink_orchestrator=self.sink_orchestrator
                )
                await pipeline.apply(ctx)

                # Signal indexing complete
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

                self.logger.info("✅ PDF processing completed successfully using VLM OCR")
                return
            else:
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            blocks_from_ocr = ocr_result.get("blocks", [])
            blocks = []
            index = 0
            table_rows = {}
            if blocks_from_ocr:
                for block in blocks_from_ocr:
                    if isinstance(block, Block):
                        block.index = index
                        blocks.append(block)
                        block_type = block.type
                        if block_type == BlockType.TABLE_ROW:
                            if block.parent_index not in table_rows:
                                table_rows[block.parent_index] = []
                            table_rows[block.parent_index].append(BlockContainerIndex(block_index=index))
                        index += 1

                    else:
                        paragraph = block
                        if paragraph and paragraph.get("content"):
                            bounding_boxes = None
                            if paragraph.get("bounding_box"):
                                try:
                                    bounding_boxes = [Point(x=p["x"], y=p["y"]) for p in paragraph["bounding_box"]]
                                except (TypeError, KeyError) as e:
                                    self.logger.warning(f"Failed to process bounding boxes: {e}")
                                    bounding_boxes = None

                            blocks.append(
                                Block(
                                    index=index,
                                    type=BlockType.TEXT,
                                    format=DataFormat.TXT,
                                    data=paragraph["content"],
                                    comments=[],
                                    citation_metadata=CitationMetadata(
                                        page_number=paragraph.get("page_number"),
                                        bounding_boxes=bounding_boxes,
                                    ),
                                )
                            )
                            index += 1

            block_groups = ocr_result.get("tables", [])
            for block_group in block_groups:
                # Convert list of BlockContainerIndex to BlockGroupChildren
                block_container_indices = table_rows.get(block_group.index, [])
                if block_container_indices:
                    block_indices = [child.block_index for child in block_container_indices if child.block_index is not None]
                    block_group.children = BlockGroupChildren.from_indices(block_indices=block_indices)
                else:
                    block_group.children = None
            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)
            record.block_containers = BlocksContainer(blocks=blocks, block_groups=block_groups)
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ PDF processing completed successfully")
            return

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_doc_document(
        self, recordName, recordId, version, source, orgId, doc_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process DOC document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting DOC document processing for record: {recordName}"
        )
        # Convert DOC to DOCX and delegate
        parser = self.parsers[ExtensionTypes.DOC.value]
        doc_result = parser.convert_doc_to_docx(doc_binary)
        async for event in self.process_docx_document(
            recordName, recordId, version, source, orgId, doc_result, virtual_record_id, event_type, prev_virtual_record_id
        ):
            yield event

    async def process_docx_document(
        self, recordName, recordId, version, source, orgId, docx_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process DOCX document, yielding phase completion events.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            docx_binary (bytes): Binary content of the DOCX file
        """
        self.logger.info(
            f"🚀 Starting DOCX document processing for record: {recordName}"
        )

        try:
            # Convert binary to string if necessary
            # Initialize DocxParser and parse content
            self.logger.debug("📄 Processing DOCX content")

            processor = self.docling_processor

            # Phase 1: Parse document with Docling (no LLM calls)
            conv_res = await processor.parse_document(recordName, docx_binary)

            # Signal parsing complete after Docling parsing
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(conv_res)


            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ Docx/Doc processing completed successfully using docling")

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing DOCX document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_blocks(
        self, recordName, recordId, version, source, orgId, blocks_data, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process BlocksContainer and attach to record for indexing, yielding phase completion events.

        For BlockGroups with requires_processing=True, processes their markdown data
        through the configured markdown parser and merges the resulting blocks back
        into the container.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            blocks_data (bytes|str|dict): BlocksContainer data (JSON string, bytes, or dict)
            virtual_record_id (str): Virtual record ID
        """
        self.logger.info(
            f"🚀 Starting Blocks Container processing for record: {recordName}"
        )

        try:
            # Deserialize blocks_data to BlocksContainer
            if isinstance(blocks_data, bytes):
                blocks_data = blocks_data.decode('utf-8')

            if isinstance(blocks_data, str):
                blocks_dict = json.loads(blocks_data)
            elif isinstance(blocks_data, dict):
                blocks_dict = blocks_data
            else:
                raise ValueError(f"Invalid blocks_data type: {type(blocks_data)}")

            # Convert dict to BlocksContainer
            block_containers = BlocksContainer(**blocks_dict)

            # Process BlockGroups with requires_processing=True via markdown parser
            block_containers = await self._process_blockgroups(
                block_containers, recordName
            )

            # Signal parsing complete after blocks are processed
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Enhance TABLE BlockGroups with LLM summaries and row descriptions
            await enhance_tables_with_llm(block_containers, self.config_service, self.logger)

            # Get record from database
            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            # Convert to Record entity and attach blocks
            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            # Apply indexing pipeline
            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(
                document_extraction=self.document_extraction,
                sink_orchestrator=self.sink_orchestrator
            )
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ Blocks Container processing completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error processing Blocks Container: {str(e)}")
            raise

    def _separate_block_groups_by_index(
        self, block_groups: List[BlockGroup]
    ) -> Tuple[List[BlockGroup], List[BlockGroup]]:
        """
        Separate block groups into those with valid index and those without.

        Args:
            block_groups: List of block groups to separate

        Returns:
            Tuple of (block_groups_with_index, block_groups_without_index)
        """
        block_groups_with_index: List[BlockGroup] = []
        block_groups_without_index: List[BlockGroup] = []

        for bg in block_groups:
            if bg.index is not None:
                block_groups_with_index.append(bg)
            else:
                block_groups_without_index.append(bg)

        return block_groups_with_index, block_groups_without_index

    async def _process_blockgroup_images(
        self, markdown_data: str, block_group_index: int
    ) -> Tuple[str, Dict[str, str]]:
        """
        Extract images from markdown and convert URLs to base64.

        Args:
            markdown_data: Markdown content to process
            block_group_index: Index of the block group (for logging)

        Returns:
            Tuple of (modified_markdown, caption_map) where caption_map maps alt text to base64 URIs
        """
        caption_map: Dict[str, str] = {}
        modified_markdown = markdown_data

        md_parser = self.parsers.get(ExtensionTypes.MD.value)
        image_parser = self.parsers.get(ExtensionTypes.PNG.value)

        if md_parser and image_parser:
            modified_markdown, images = md_parser.extract_and_replace_images(markdown_data)

            if images:
                # Collect all image URLs
                urls_to_convert = [image["url"] for image in images]

                # Convert URLs to base64
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)

                # Create caption map with base64 URLs
                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

                self.logger.debug(
                    f"📷 Extracted {len(images)} images from BlockGroup {block_group_index}, "
                    f"converted {len([u for u in base64_urls if u])} to base64"
                )

        return modified_markdown, caption_map

    async def _process_single_blockgroup(
        self,
        block_group: BlockGroup,
        record_name: str,
        md_parser: MarkdownParser,
    ) -> Tuple[List[BlockGroup], List[Block]]:
        """
        Process a single block group's markdown into blocks.

        Args:
            block_group: Block group to process
            record_name: Name of the record (for filename generation)
            md_parser: Markdown parser instance

        Returns:
            Tuple of (new_block_groups, new_blocks) from processing

        Raises:
            ValueError: If block group has no valid markdown data
        """
        # Extract markdown data from BlockGroup
        markdown_data = block_group.data
        if not markdown_data or not isinstance(markdown_data, str):
            raise ValueError(
                f"BlockGroup {block_group.index} has no valid markdown data"
            )

        # Extract and replace images from markdown, then convert URLs to base64
        modified_markdown, caption_map = await self._process_blockgroup_images(
            markdown_data, block_group.index
        )

        self.logger.debug(
            f"📄 Processing BlockGroup {block_group.index} ({block_group.name})"
        )
        processed_blocks_container = await md_parser.parse_to_blocks(
            modified_markdown,
            caption_map=caption_map or None,
            name=block_group.name or record_name,
        )

        self.logger.debug(
            f"✅ Processed BlockGroup {block_group.index}: "
            f"collected {len(processed_blocks_container.blocks)} blocks, "
            f"{len(processed_blocks_container.block_groups)} block_groups"
        )

        return processed_blocks_container.block_groups, processed_blocks_container.blocks

    async def _process_single_blockgroup_html(
        self,
        block_group: BlockGroup,
        record_name: str,
    ) -> Tuple[List[BlockGroup], List[Block]]:
        """
        Process a single block group's HTML data into blocks.

        Mirrors ``_process_single_blockgroup`` but uses the HTML parser.
        Images should already be base64-inlined by the connector; any remaining
        remote URLs are fetched as a safety net.
        """
        html_data = block_group.data
        if not html_data or not isinstance(html_data, str):
            raise ValueError(
                f"BlockGroup {block_group.index} has no valid HTML data"
            )

        html_parser = self.parsers.get(ExtensionTypes.HTML.value)
        if not html_parser:
            raise ValueError("HTML parser is not configured")
        html_content = html_parser.clean_html(html_data)

        caption_map: Dict[str, str] = {}
        modified_html, images = html_parser.extract_and_replace_images(html_content)

        if images:
            image_parser = self.parsers.get(ExtensionTypes.PNG.value)
            if image_parser:
                urls_to_convert = [image["url"] for image in images]
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)
                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

                self.logger.debug(
                    f"📷 Extracted {len(images)} images from HTML BlockGroup {block_group.index}, "
                    f"converted {len([u for u in base64_urls if u])} to base64"
                )

        processed_blocks_container = await html_parser.parse_to_blocks(
            modified_html,
            caption_map=caption_map or None,
            name=block_group.name or record_name,
        )

        self.logger.debug(
            f"✅ Processed HTML BlockGroup {block_group.index}: "
            f"collected {len(processed_blocks_container.blocks)} blocks, "
            f"{len(processed_blocks_container.block_groups)} block_groups"
        )

        return processed_blocks_container.block_groups, processed_blocks_container.blocks

    def _calculate_index_shift_map(
        self,
        block_groups_with_index: List[BlockGroup],
        processing_results: Dict[int, Tuple[List[BlockGroup], List[Block]]]
    ) -> Dict[int, int]:
        """
        Calculate index shift mappings for block groups.

        Builds a map of original_index -> cumulative_shift_amount where
        cumulative_shift = sum of new_block_groups from all parents with index < original_index.

        Args:
            block_groups_with_index: List of block groups with valid indices
            processing_results: Map of parent_index -> (new_block_groups, new_blocks)

        Returns:
            Dictionary mapping original_index to shift amount
        """
        index_shift_map: Dict[int, int] = {}
        cumulative_shift = 0

        for bg in block_groups_with_index:
            original_index = bg.index
            index_shift_map[original_index] = cumulative_shift

            # If this block_group was processed, add its new block_groups to the shift
            if original_index in processing_results:
                num_new_block_groups = len(processing_results[original_index][0])
                cumulative_shift += num_new_block_groups

        return index_shift_map

    def _build_updated_blocks_container(
        self,
        block_containers: BlocksContainer,
        block_groups_with_index: List[BlockGroup],
        block_groups_without_index: List[BlockGroup],
        processing_results: Dict[int, Tuple[List[BlockGroup], List[Block]]],
        index_shift_map: Dict[int, int],
        initial_block_count: int
    ) -> BlocksContainer:
        """
        Build the final BlocksContainer with updated indices.

        Handles both:
        - BlockGroups with requires_processing=True: blocks from docling processing
        - BlockGroups with requires_processing=False: existing blocks from connector

        All blocks are assigned sequential indices in BlockGroup order.

        Args:
            block_containers: Original BlocksContainer
            block_groups_with_index: Block groups with valid indices
            block_groups_without_index: Block groups without indices
            processing_results: Map of parent_index -> (new_block_groups, new_blocks)
            index_shift_map: Map of original_index to shift amount
            initial_block_count: Initial count of blocks (unused, kept for compatibility)

        Returns:
            New BlocksContainer with processed blocks merged in
        """
        new_block_groups: List[BlockGroup] = []
        new_blocks: List[Block] = []
        processed_indices = set(processing_results.keys())

        # Group existing blocks by their original parent_index
        # (before any shifting is applied to BlockGroup indices)
        existing_blocks_by_parent: Dict[int, List[Block]] = {}
        for block in block_containers.blocks:
            parent_idx = block.parent_index
            if parent_idx is not None:
                if parent_idx not in existing_blocks_by_parent:
                    existing_blocks_by_parent[parent_idx] = []
                existing_blocks_by_parent[parent_idx].append(block)

        # Sort blocks within each parent group by their original index to maintain relative order
        for parent_idx in existing_blocks_by_parent:
            existing_blocks_by_parent[parent_idx].sort(
                key=lambda b: b.index if b.index is not None else float('inf')
            )

        # Track current block index for sequential assignment
        current_block_index = 0

        # Build new block_groups list and assign block indices in BlockGroup order
        for bg in block_groups_with_index:
            original_index = bg.index
            shift_amount = index_shift_map[original_index]
            final_index = original_index + shift_amount

            # Update block_group's index
            bg.index = final_index

            # Update parent_index if it references a shifted block_group
            if bg.parent_index is not None and bg.parent_index in index_shift_map:
                bg.parent_index += index_shift_map[bg.parent_index]

            # Update children.block_group_ranges references
            if bg.children and bg.children.block_group_ranges:
                shifted_indices = []
                for range_obj in bg.children.block_group_ranges:
                    for idx in range(range_obj.start, range_obj.end + 1):
                        if idx in index_shift_map:
                            shifted_indices.append(idx + index_shift_map[idx])
                        else:
                            shifted_indices.append(idx)
                # Reconstruct ranges from shifted indices
                bg.children.block_group_ranges = BlockGroupChildren.from_indices(
                    block_group_indices=shifted_indices
                ).block_group_ranges

            # Add the block_group to the result
            new_block_groups.append(bg)

            # Handle blocks for this BlockGroup
            if original_index in processed_indices:
                # Case 1: BlockGroup was processed by docling - use new blocks
                bg.requires_processing = False

                # Get processing results
                new_block_groups_list, new_blocks_list = processing_results[original_index]
                insertion_index = final_index + 1

                # Initialize children if needed
                if bg.children is None:
                    bg.children = BlockGroupChildren()

                # Clear existing block_ranges since we're replacing with processed blocks
                bg.children.block_ranges = []

                # First, assign indices to all blocks (docling gives proper order)
                # This ensures we know the final indices before updating nested block_group ranges
                block_start_index = current_block_index
                for new_block in new_blocks_list:
                    # Assign sequential block index
                    new_block.index = current_block_index

                    # Set parent_index
                    if new_block.parent_index is None:
                        new_block.parent_index = final_index
                    else:
                        # If parent_index exists, it's a relative index from docling
                        new_block.parent_index = new_block.parent_index + insertion_index

                    new_blocks.append(new_block)

                    # Add blocks that directly belong to the parent BlockGroup
                    if new_block.parent_index == final_index:
                        bg.children.add_block_index(new_block.index)

                    current_block_index += 1

                # Now assign indices to new block_groups and update their ranges
                # (ranges can now reference the correctly assigned block indices)
                for i, new_bg in enumerate(new_block_groups_list):
                    new_bg.index = insertion_index + i

                    # Set parent_index to parent's final index if not set
                    if new_bg.parent_index is None:
                        new_bg.parent_index = final_index
                    else:
                        # If parent_index exists, it's a relative index from docling
                        new_bg.parent_index = new_bg.parent_index + insertion_index

                    # Update children indices in the new block_group
                    # Since blocks are already assigned, shift ranges by block_start_index
                    if new_bg.children:
                        # Shift block_ranges (docling returns ranges relative to its output starting at 0)
                        for range_obj in new_bg.children.block_ranges:
                            range_obj.start += block_start_index
                            range_obj.end += block_start_index

                        # Shift block_group_ranges
                        for range_obj in new_bg.children.block_group_ranges:
                            range_obj.start += insertion_index
                            range_obj.end += insertion_index

                    new_block_groups.append(new_bg)

                    # Add to parent's children
                    bg.children.add_block_group_index(new_bg.index)

                bg.data = None

            elif original_index in existing_blocks_by_parent:
                # Case 2: BlockGroup has existing blocks from connector - reassign indices
                existing_blocks = existing_blocks_by_parent[original_index]

                # Initialize children if needed
                if bg.children is None:
                    bg.children = BlockGroupChildren()

                # Clear and rebuild block_ranges with new indices
                bg.children.block_ranges = []

                for block in existing_blocks:
                    # Update parent_index to the shifted BlockGroup index
                    block.parent_index = final_index

                    # Assign new sequential block index
                    block.index = current_block_index
                    new_blocks.append(block)

                    # Add to parent's children
                    bg.children.add_block_index(block.index)

                    current_block_index += 1

        # Append block_groups with None index at end
        new_block_groups.extend(block_groups_without_index)

        # Sort block_groups by index to ensure list position matches index value
        sorted_block_groups = sorted(
            new_block_groups,
            key=lambda bg: bg.index if bg.index is not None else float('inf')
        )

        # Sort blocks by index to ensure list position matches index value
        sorted_blocks = sorted(
            new_blocks,
            key=lambda b: b.index if b.index is not None else float('inf')
        )

        # Build final BlocksContainer
        return BlocksContainer(
            block_groups=sorted_block_groups,
            blocks=sorted_blocks
        )

    async def _process_blockgroups(
        self, block_containers: BlocksContainer, record_name: str
    ) -> BlocksContainer:
        """
        Process BlockGroups with requires_processing=True via the markdown parser.

        Uses a functional approach:
        1. Process all BlockGroups that need processing, collecting results
        2. Calculate index mappings upfront
        3. Build new BlocksContainer in a single pass

        Args:
            block_containers: BlocksContainer to process
            record_name: Name of the record (for parser context)

        Returns:
            BlocksContainer with processed blocks merged in
        """
        if not block_containers.block_groups:
            return block_containers

        # Separate block_groups with valid index from those with None index
        block_groups_with_index, block_groups_without_index = self._separate_block_groups_by_index(
            block_containers.block_groups
        )

        # Filter BlockGroups that need processing (already in sequence from connector)
        block_groups_to_process = [
            bg for bg in block_groups_with_index
            if bg.requires_processing and bg.data
        ]

        if not block_groups_to_process:
            self.logger.debug("No BlockGroups require processing")
            return block_containers

        self.logger.info(
            f"🔄 Processing {len(block_groups_to_process)} BlockGroups"
        )

        # ========== PHASE 1: Process all BlockGroups and collect results ==========
        # Map: parent_index -> (new_block_groups, new_blocks)
        processing_results: Dict[int, Tuple[List[BlockGroup], List[Block]]] = {}
        initial_block_count = len(block_containers.blocks)

        md_parser = self.parsers.get(ExtensionTypes.MD.value)
        if md_parser is None:
            raise ValueError("Markdown parser is not configured")

        for block_group in block_groups_to_process:
            try:
                if block_group.format == DataFormat.HTML:
                    new_block_groups, new_blocks = await self._process_single_blockgroup_html(
                        block_group, record_name
                    )
                else:
                    new_block_groups, new_blocks = await self._process_single_blockgroup(
                        block_group, record_name, md_parser
                    )

                # Store results for later merging
                processing_results[block_group.index] = (new_block_groups, new_blocks)

            except Exception as e:
                self.logger.error(
                    f"❌ Error processing BlockGroup {block_group.index}: {e}",
                    exc_info=True
                )
                # Stop processing if any BlockGroup fails
                raise

        if not processing_results:
            self.logger.debug("No BlockGroups were successfully processed")
            return block_containers

        # ========== PHASE 2: Calculate index mappings upfront ==========
        index_shift_map = self._calculate_index_shift_map(
            block_groups_with_index, processing_results
        )

        # ========== PHASE 3: Build new BlocksContainer in a single pass ==========
        result = self._build_updated_blocks_container(
            block_containers,
            block_groups_with_index,
            block_groups_without_index,
            processing_results,
            index_shift_map,
            initial_block_count
        )

        self.logger.info(
            f"✅ Processed {len(processing_results)} BlockGroups. "
            f"Total blocks: {len(result.blocks)}, "
            f"Total block_groups: {len(result.block_groups)}"
        )

        return result

    async def process_excel_document(
        self, recordName, recordId, version, source, orgId, excel_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Excel document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting Excel document processing for record: {recordName}"
        )

        try:
            self.logger.debug("📊 Processing Excel content")
            llm, _ = await self._get_llm_for_role("indexing", reasoning_effort="low")
            parser = self.parsers[ExtensionTypes.XLSX.value]
            if not excel_binary:
                self.logger.info(f"No Excel binary found for record: {recordName}")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            # Phase 1: Load workbook (no LLM calls)
            parser.load_workbook_from_binary(excel_binary)

            # Signal parsing complete after workbook is loaded
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Phase 2: Create blocks (involves LLM calls for summaries)
            blocks_containers = await parser.create_blocks(llm)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)
            record.block_containers = blocks_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ Excel processing completed successfully.")
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing Excel document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_xls_document(
        self, recordName, recordId, version, source, orgId, xls_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process XLS document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting XLS document processing for record: {recordName}"
        )

        try:
            # Convert XLS to XLSX binary
            xls_parser = self.parsers[ExtensionTypes.XLS.value]
            xlsx_binary = xls_parser.convert_xls_to_xlsx(xls_binary)

            # Process the converted XLSX using the Excel parser
            async for event in self.process_excel_document(
                recordName, recordId, version, source, orgId, xlsx_binary, virtual_record_id, event_type, prev_virtual_record_id
            ):
                yield event
            self.logger.debug("📑 XLS document processed successfully")

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing XLS document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_delimited_document(
        self, recordName, recordId, file_binary, virtual_record_id, extension=None, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process delimited document (CSV/TSV), yielding phase completion events.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            file_binary (bytes): Binary content of the delimited file (CSV/TSV)
            virtual_record_id (str): Virtual record ID
            extension (str): File extension type (defaults to CSV if None)
        """
        self.logger.info(
            f"🚀 Starting delimited document processing for record: {recordName}"
        )

        try:
            # Initialize parser
            self.logger.debug("📊 Processing delimited file content")
            if extension is None:
                parser = self.parsers[ExtensionTypes.CSV.value]
            else:
                parser = self.parsers[extension]

            llm, _ = await self._get_llm_for_role("indexing", reasoning_effort="low")

            # Try different encodings to decode binary data
            encodings = ["utf-8", "latin1", "cp1252", "iso-8859-1"]
            all_rows = None
            for encoding in encodings:
                try:
                    self.logger.debug(
                        f"Attempting to decode delimited file with {encoding} encoding"
                    )
                    # Decode binary data to string
                    csv_text = file_binary.decode(encoding)

                    # Create string stream from decoded text
                    csv_stream = io.StringIO(csv_text)

                    # Read raw rows for table detection
                    all_rows = parser.read_raw_rows(csv_stream)


                    self.logger.info(
                        f"✅ Successfully parsed delimited file with {encoding} encoding. Rows: {len(all_rows)}"
                    )
                    break
                except UnicodeDecodeError:
                    self.logger.debug(f"Failed to decode with {encoding} encoding")
                    continue
                except Exception as e:
                    self.logger.debug(f"Failed to process delimited file with {encoding} encoding: {str(e)}")
                    continue


            if all_rows is None or not all_rows:
                self.logger.info(f"Unable to decode delimited file with any supported encoding or it is empty for record: {recordName}. Setting indexing status to EMPTY.")

                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                await self._mark_record(recordId, ProgressStatus.EMPTY)

                return

            self.logger.debug("📑 Delimited file result processed")

            # Detect multiple tables
            tables = parser.find_tables_in_csv(all_rows)
            self.logger.info(f"🔍 Detected {len(tables)} table(s) in delimited file")

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)
            record.virtual_record_id = virtual_record_id

            # Signal parsing complete after delimited file is parsed (before LLM block creation)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Process all tables using unified multi-table logic
            self.logger.info(f"📊 Processing {len(tables)} table(s)")
            block_containers = await parser.get_blocks_from_csv_with_multiple_tables(tables, llm)

            record.block_containers = block_containers

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ Delimited file processing completed successfully")

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing delimited document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def _mark_record(self, record_id, indexing_status: ProgressStatus) -> None:
        record = await self.graph_provider.get_document(
                        record_id, CollectionNames.RECORDS.value
                    )
        if not record:
            raise DocumentProcessingError(
                "Record not found in database",
                doc_id=record_id,
            )
        timestamp = get_epoch_timestamp_in_ms()
        status_update: dict[str, Any] = {
            "indexingStatus": indexing_status.value,
            "processingStartedAt": None,
            "isDirty": False,
            "lastIndexTimestamp": timestamp,
            "extractionStatus": ProgressStatus.EMPTY.value,
            "lastExtractionTimestamp": timestamp,
        }
        if indexing_status == ProgressStatus.EMPTY:
            status_update["reason"] = ""

        success = await self.graph_provider.update_node(
            record_id,
            CollectionNames.RECORDS.value,
            status_update,
        )
        if not success:
            self.logger.warning(
                "⚠️ Failed to update indexing status for record %s - record may not exist",
                record_id,
            )
            return

    async def process_html_document(
        self, recordName, recordId, version, source, orgId, html_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process HTML document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting HTML document processing for record: {recordName}"
        )

        try:
            # Convert binary to string
            if isinstance(html_binary, bytes):
                html_content = html_binary.decode("utf-8")
            else:
                html_content = html_binary

            html_content = html_content.strip()

            if not html_content:
                try:
                    await self._mark_record(recordId, ProgressStatus.EMPTY)
                    self.logger.info("✅ HTML processing completed - empty content.")
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                    return
                except IndexingError:
                    raise
                except Exception as e:
                    raise DocumentProcessingError(
                        "Error updating record status: " + str(e),
                        doc_id=recordId,
                        details={"error": str(e)},
                    ) from e

            # Use the unified HTML parser interface
            self.logger.debug("📄 Processing HTML content")
            html_parser = self.parsers[ExtensionTypes.HTML.value]
            html_content = html_parser.clean_html(html_content)
            html_content = html_parser.replace_relative_image_urls(html_content)

            # Extract image URLs and convert to base64 (mirrors the Markdown flow)
            caption_map: Dict[str, str] = {}
            modified_html, images = html_parser.extract_and_replace_images(html_content)

            if images:
                image_parser = self.parsers[ExtensionTypes.PNG.value]
                urls_to_convert = [image["url"] for image in images]
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)

                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

                self.logger.debug(
                    f"📷 Extracted {len(images)} images from HTML, "
                    f"converted {len([u for u in base64_urls if u])} to base64"
                )

            block_containers = await html_parser.parse_to_blocks(
                modified_html,
                caption_map=caption_map if caption_map else None,
                name=recordName,
            )

            # Signal parsing complete
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)

            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ HTML processing completed successfully.")

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing HTML document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_mdx_document(
        self, recordName: str, recordId: str, version: str, source: str, orgId: str, mdx_content, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process MDX document, yielding phase completion events.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the record
            orgId (str): Organization ID
            mdx_content (str): Content of the MDX file
        """
        self.logger.info(
            f"🚀 Starting MDX document processing for record: {recordName}"
        )

        # Convert MDX to MD using our parser
        parser = self.parsers[ExtensionTypes.MDX.value]
        md_content = parser.convert_mdx_to_md(mdx_content)

        # Process the converted markdown content
        async for event in self.process_md_document(
            recordName, recordId, md_content, virtual_record_id, event_type, prev_virtual_record_id
        ):
            yield event

    async def process_md_document(
        self, recordName, recordId, md_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Markdown document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting Markdown document processing for record: {recordName}"
        )

        try:
            # Convert binary to string
            if isinstance(md_binary, bytes):
                md_content = md_binary.decode("utf-8")
            else:
                md_content = md_binary

            markdown = md_content.strip()

            if markdown is None or markdown == "":
                try:
                    await self._mark_record(recordId, ProgressStatus.EMPTY)
                    self.logger.info("✅ HTML processing completed successfully using markdown conversion.")
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                    return
                except IndexingError:
                    raise
                except Exception as e:
                    raise DocumentProcessingError(
                        "Error updating record status: " + str(e),
                        doc_id=recordId,
                        details={"error": str(e)},
                    ) from e

            # Initialize Markdown parser
            self.logger.debug("📄 Processing Markdown content")
            parser = self.parsers[ExtensionTypes.MD.value]

            modified_markdown, images = parser.extract_and_replace_images(markdown)
            caption_map = {}

            # Collect all image URLs
            urls_to_convert = [image["url"] for image in images]

            # Convert URLs to base64 if there are any images
            if urls_to_convert:
                image_parser = self.parsers[ExtensionTypes.PNG.value]
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)

                # Create caption map with base64 URLs
                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

            block_containers = await parser.parse_to_blocks(
                modified_markdown,
                caption_map=caption_map or None,
                name=recordName,
            )

            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)

            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ MD processing completed successfully")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing Markdown document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def _lookup_code_file_path(self, record_id: str) -> Optional[str]:
        """Read filePath from the codeFiles node when the event omits it."""
        try:
            doc = await self.graph_provider.get_document(
                record_id, CollectionNames.CODE_FILES.value
            )
            return (doc or {}).get("filePath")
        except Exception as e:
            self.logger.warning(f"Could not read filePath for {record_id}: {e}")
            return None

    async def process_code_document(
        self, recordName, recordId, code_binary, virtual_record_id, extension=None,
        file_path: Optional[str] = None,
        event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process a source file into code blocks, yielding phase events."""
        self.logger.info(f"🚀 Starting code document processing for record: {recordName}")

        try:
            if isinstance(code_binary, str):
                code_binary = code_binary.encode("utf-8")

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = convert_record_dict_to_record(record)

            # Preserve the repo-relative path so block metadata remains unique
            # when different directories contain files with the same basename.
            if not file_path:
                file_path = await self._lookup_code_file_path(recordId)
            file_path = file_path or recordName
            language = detect_language(recordName) or detect_language(file_path)
            if not language and extension:
                cfg = config_for_extension(extension)
                if cfg:
                    language = cfg.name
            if not language:
                self.logger.info(
                    f"No code grammar for {recordName}; falling back to text parsing"
                )
                async for event in self.process_md_document(
                    recordName=recordName,
                    recordId=recordId,
                    md_binary=code_binary.decode("utf-8", errors="replace"),
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event
                return

            parser = self.parsers[ExtensionTypes.CODE.value]
            block_containers = parser.parse_to_blocks(
                code_binary, recordName, file_path, language
            )

            if block_containers is None:
                self.logger.info(
                    f"Code parser skipped {recordName} (oversized); marking as not supported"
                )
                await self._mark_record(recordId, ProgressStatus.FILE_TYPE_NOT_SUPPORTED)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            if not block_containers.blocks and not block_containers.block_groups:
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
            self.logger.info("✅ Code processing completed successfully")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing code document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_txt_document(
        self, recordName, recordId, version, source, orgId, txt_binary, virtual_record_id, recordType, connectorName, origin, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process TXT document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting TXT document processing for record: {recordName}"
        )

        try:
            # Try different encodings to decode the binary content
            encodings = ["utf-8", "utf-8-sig", "latin-1", "iso-8859-1"]
            text_content = None

            for encoding in encodings:
                try:
                    text_content = txt_binary.decode(encoding)
                    self.logger.debug(
                        f"Successfully decoded text with {encoding} encoding"
                    )
                    break
                except UnicodeDecodeError:
                    continue

            if text_content is None:
                raise ValueError(
                    "Unable to decode text file with any supported encoding"
                )

            async for event in self.process_md_document(
                recordName=recordName,
                recordId=recordId,
                md_binary=text_content,
                virtual_record_id=virtual_record_id,
                event_type=event_type,
                prev_virtual_record_id=prev_virtual_record_id,
            ):
                yield event
            self.logger.info("✅ TXT processing completed successfully")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing TXT document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_pptx_document(
        self, recordName, recordId, version, source, orgId, pptx_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PPTX document, yielding phase completion events.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            pptx_binary (bytes): Binary content of the PPTX file
        """
        self.logger.info(
            f"🚀 Starting PPTX document processing for record: {recordName}"
        )

        try:
            # Initialize PPTX parser
            self.logger.debug("📄 Processing PPTX content")

            processor = self.docling_processor

            # Phase 1: Parse document with Docling (no LLM calls)
            if not recordName.lower().endswith(".pptx"):
                recordName = f"{recordName}.pptx"
            conv_res = await processor.parse_document(recordName, pptx_binary)

            # Signal parsing complete after Docling parsing
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(conv_res)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))

            self.logger.info("✅ PPTX processing completed successfully using docling")
            return
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing PPTX document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_ppt_document(
        self, recordName, recordId, version, source, orgId, ppt_binary, virtual_record_id, event_type: Optional[str] = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PPT document, yielding phase completion events.

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            ppt_binary (bytes): Binary content of the PPT file
        """
        self.logger.info(
            f"🚀 Starting PPT document processing for record: {recordName}"
        )
        parser = self.parsers[ExtensionTypes.PPT.value]
        ppt_result = parser.convert_ppt_to_pptx(ppt_binary)
        async for event in self.process_pptx_document(
            recordName, recordId, version, source, orgId, ppt_result, virtual_record_id, event_type, prev_virtual_record_id
        ):
            yield event

    async def process_sql_structured_data(
        self, recordName: str, recordId: str, json_content: bytes, virtual_record_id: str,
        record_type: str = "SQL_TABLE", event_type: str = None, prev_virtual_record_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process SQL Table or View data, yielding phase completion events.

        Uses SQLTableParser or SQLViewParser to create:
        - 1 block group containing DDL/schema metadata (useful for text-to-SQL context)
        - 1 block per data row as children of the block group

        For update events on reconciliation-enabled types, performs incremental
        indexing using content hashes instead of full re-index.

        Args:
            recordName (str): Name of the record (table/view name)
            recordId (str): ID of the record
            json_content (bytes): JSON content with table/view metadata
            virtual_record_id (str): Virtual record ID for indexing
            record_type (str): Either "SQL_TABLE" or "SQL_VIEW"
            event_type (str): Event type (newRecord, updateRecord, etc.)
        """
        self.logger.info(f"🚀 Starting {record_type} processing for record: {recordName}")
        
        try:
            # Get the appropriate parser based on record type
            if record_type == "SQL_TABLE":
                parser = self.parsers.get(ExtensionTypes.SQL_TABLE.value)
            elif record_type == "SQL_VIEW":
                parser = self.parsers.get(ExtensionTypes.SQL_VIEW.value)
            else:
                self.logger.error(f"❌ Unknown record type: {record_type}")
                await self._mark_record(recordId, ProgressStatus.FAILED)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            
            if not parser:
                self.logger.error(f"❌ No parser found for {record_type}")
                await self._mark_record(recordId, ProgressStatus.FAILED)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            
            # Create a file-like stream from the JSON content
            if isinstance(json_content, bytes):
                file_stream = io.BytesIO(json_content)
            else:
                file_stream = io.BytesIO(json_content.encode("utf-8"))
            
            # Parse using the dedicated SQL parser (handles DDL, rows, etc.)
            block_containers = parser.parse_stream(file_stream)
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            if not block_containers.block_groups and not block_containers.blocks:
                self.logger.info(f"No content to index for {record_type}: {recordName}")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return
            
            self.logger.info(f"📊 Created {len(block_containers.block_groups)} block group(s) and {len(block_containers.blocks)} block(s) for {record_type}: {recordName}")
            
            # Get record from database
            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise DocumentProcessingError(
                    "Record not found in database", doc_id=recordId
                )
            
            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            
            # Signal indexing complete
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
            
            self.logger.info(f"✅ {record_type} processing completed successfully for: {recordName} ({len(block_containers.block_groups)} block group(s), {len(block_containers.blocks)} block(s))")
            
        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing {record_type} document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

    async def process_structured_document(
        self,
        recordName: str,
        recordId: str,
        file_content: bytes | str | dict | list,
        virtual_record_id: str,
        extension: str,
        event_type: Optional[str] = None,
        prev_virtual_record_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process a JSON or YAML file using its registered parser."""
        self.logger.info(f"🚀 Starting {extension.upper()} processing for record: {recordName}")

        try:
            parser = self.parsers.get(extension)
            if not parser:
                self.logger.error(f"❌ No parser found for extension: {extension}")
                await self._mark_record(recordId, ProgressStatus.FAILED)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            if isinstance(file_content, (dict, list)):
                file_content = json.dumps(file_content, default=str, ensure_ascii=False).encode("utf-8")
            elif isinstance(file_content, str):
                file_content = file_content.encode("utf-8")

            result = await parser.parse(file_content, recordName)
            block_containers = result.block_container
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=recordId))

            if not block_containers.block_groups and not block_containers.blocks:
                self.logger.info(f"No content to index for {extension}: {recordName}")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
                return

            self.logger.info(
                f"📊 Created {len(block_containers.block_groups)} block group(s) "
                f"and {len(block_containers.blocks)} block(s) for {extension}: {recordName}"
            )

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise DocumentProcessingError(
                    "Record not found in database", doc_id=recordId
                )

            record = self._convert_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type, prev_virtual_record_id)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=recordId))
            self.logger.info(f"✅ {extension.upper()} processing completed for: {recordName}")

        except IndexingError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error processing {extension} document: {str(e)}")
            raise DocumentProcessingError(
                f"Failed to process document: {str(e)}",
                doc_id=recordId,
                details={"error": str(e)},
            ) from e

