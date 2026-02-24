import io
import json
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from html_to_markdown import convert

from app.config.constants.ai_models import AzureDocIntelligenceModel, OCRProvider
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ExtensionTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import DocumentProcessingError
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
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.pdf.docling import DoclingProcessor
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext
from app.services.docling.client import DoclingClient
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.aimodels import is_multimodal_llm
from app.utils.llm import get_embedding_model_config, get_llm
from app.utils.mimetype_to_extension import get_extension_from_mimetype
from app.utils.time_conversion import get_epoch_timestamp_in_ms


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

    mime_type = record_dict.get("mimeType", None)

    record = Record(
        id=record_dict.get("_key") or record_dict.get("id"),
        org_id=record_dict.get("orgId"),
        record_name=record_dict.get("recordName"),
        record_type=RecordType(record_dict.get("recordType", "FILE")),
        record_status=ProgressStatus(record_dict.get("indexingStatus", "NOT_STARTED")),
        external_record_id=record_dict.get("externalRecordId"),
        version=record_dict.get("version", 1),
        origin=origin,
        summary_document_id=record_dict.get("summaryDocumentId"),
        created_at=record_dict.get("createdAtTimestamp"),
        updated_at=record_dict.get("updatedAtTimestamp"),
        source_created_at=record_dict.get("sourceCreatedAtTimestamp"),
        source_updated_at=record_dict.get("sourceLastModifiedTimestamp"),
        weburl=record_dict.get("webUrl"),
        mime_type=mime_type,
        external_revision_id=record_dict.get("externalRevisionId"),
        connector_name=connector_name,
        is_vlm_ocr_processed=record_dict.get("isVLMOcrProcessed", False),
        connector_id=record_dict.get("connectorId"),
    )
    return record

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

        # Set by EventProcessor before calling process methods, used for reconciliation
        self._prev_virtual_record_id: Optional[str] = None

    def _create_transform_context(self, record, event_type: Optional[str] = None) -> TransformContext:
        """Create TransformContext with prev_virtual_record_id from EventProcessor."""
        return TransformContext(
            record=record,
            event_type=event_type,
            prev_virtual_record_id=self._prev_virtual_record_id,
        )

    async def process_image(self, record_id, content, virtual_record_id, event_type: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
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
                yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                return

            _ , config = await get_llm(self.config_service)
            is_multimodal_llm = config.get("isMultimodal")

            embedding_config = await get_embedding_model_config(self.config_service)
            is_multimodal_embedding = embedding_config.get("isMultimodal") if embedding_config else False
            if not is_multimodal_embedding and not is_multimodal_llm:
                try:
                    record.update(
                        {
                            "indexingStatus": ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
                            "extractionStatus": ProgressStatus.NOT_STARTED.value,
                        })

                    docs = [record]
                    success = await self.graph_provider.batch_upsert_nodes(
                        docs, CollectionNames.RECORDS.value
                    )
                    if not success:
                        raise DocumentProcessingError(
                            "Failed to update indexing status", doc_id=record_id
                        )

                    # Yield both events since we're skipping processing
                    yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                    yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                    return

                except DocumentProcessingError:
                    raise
                except Exception as e:
                    raise DocumentProcessingError(
                        "Error updating record status: " + str(e),
                        doc_id=record_id,
                        details={"error": str(e)},
                    )

            mime_type = record.get("mimeType")
            if mime_type is None:
                raise Exception("No mime type present in the record from graph db")
            extension = get_extension_from_mimetype(mime_type)

            parser = self.parsers.get(extension)
            if not parser:
                raise Exception(f"Unsupported extension: {extension}")

            block_containers = parser.parse_image(content, extension)
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            # Signal parsing complete
            yield {"event": "parsing_complete", "data": {"record_id": record_id}}

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": record_id}}

            self.logger.info("✅ Image processing completed successfully")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing image: {str(e)}")
            raise



    async def process_gmail_message(
        self, recordName, recordId, version, source, orgId, html_content, virtual_record_id, event_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Gmail message, yielding phase completion events."""
        self.logger.info("🚀 Processing Gmail Message")

        try:
            async for event in self.process_html_document(
                recordName=recordName,
                recordId=recordId,
                version=version,
                source=source,
                orgId=orgId,
                html_binary=html_content,
                virtual_record_id=virtual_record_id,
                event_type=event_type
            ):
                yield event

            self.logger.info("✅ Gmail Message processing completed successfully using markdown conversion.")

        except Exception as e:
            self.logger.error(f"❌ Error processing Gmail Message document: {str(e)}")
            raise

    async def process_pdf_with_docling(self, recordName, recordId, pdf_binary, virtual_record_id, event_type: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Process PDF with Docling, yielding phase completion events."""
        self.logger.info(f"🚀 Starting PDF document processing for record: {recordName}")
        try:
            self.logger.debug("📄 Processing PDF binary content using external Docling service")

            # Use external Docling service
            record_name = recordName if recordName.endswith(".pdf") else f"{recordName}.pdf"

            # Phase 1: Parse PDF (no LLM calls)
            parse_result = await self.docling_client.parse_pdf(record_name, pdf_binary)
            if parse_result is None:
                self.logger.error(f"❌ External Docling service failed to parse {recordName}")
                yield {"event": "docling_failed", "data": {"record_id": recordId}}
                return

            # Signal parsing complete after Docling parsing
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}


            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await self.docling_client.create_blocks(parse_result)
            if block_containers is None:
                self.logger.error(f"❌ External Docling service failed to create blocks for {recordName}")
                raise Exception(f"External Docling service failed to create blocks for {recordName}")

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return

            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info(f"✅ PDF processing completed for record: {recordName}, using external Docling service")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document with external Docling service: {str(e)}")
            raise

    async def process_pdf_document_with_ocr(
        self, recordName, recordId, version, source, orgId, pdf_binary, virtual_record_id, event_type: Optional[str] = None
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

                elif provider == OCRProvider.AZURE_DI.value:
                    self.logger.debug("☁️ Setting up Azure OCR handler")
                    handler = OCRHandler(
                        self.logger,
                        OCRProvider.AZURE_DI.value,
                        endpoint=config["configuration"]["endpoint"],
                        key=config["configuration"]["apiKey"],
                        model_id=AzureDocIntelligenceModel.PREBUILT_DOCUMENT.value,
                    )
                    break
                elif provider == OCRProvider.OCRMYPDF.value:
                    self.logger.debug("📚 Setting up PyMuPDF OCR handler")
                    handler = OCRHandler(
                        self.logger, OCRProvider.OCRMYPDF.value, config=self.config_service
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
                    self.logger.debug("📚 Setting up OCRmyPDF handler (no multimodal LLM available)")
                    handler = OCRHandler(self.logger, OCRProvider.OCRMYPDF.value, config=self.config_service)
                    provider = OCRProvider.OCRMYPDF.value

            # Process document
            self.logger.info("🔄 Processing document with OCR handler")
            try:
                ocr_result = await handler.process_document(pdf_binary)
            except Exception:
                if provider == OCRProvider.AZURE_DI.value or provider == OCRProvider.VLM_OCR.value:
                    self.logger.info(f"🔄 Switching to OCRmyPDF handler as {provider} failed")
                    provider = OCRProvider.OCRMYPDF.value
                    handler = OCRHandler(self.logger, provider, config=self.config_service)
                    ocr_result = await handler.process_document(pdf_binary)
                else:
                    raise

            self.logger.debug("✅ OCR processing completed")



            if provider == OCRProvider.VLM_OCR.value:
                pages = ocr_result.get("pages", [])
                self.logger.info(f"📄 Processing {len(pages)} pages from VLM OCR")

                # Phase 1: Parse all pages with Docling (no LLM calls yet)
                all_conv_results = []
                processor = DoclingProcessor(logger=self.logger, config=self.config_service)

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
                yield {"event": "parsing_complete", "data": {"record_id": recordId}}

                # Phase 2: Create blocks for all pages (involves LLM calls for tables)
                all_blocks = []
                all_block_groups = []
                block_index_offset = 0
                block_group_index_offset = 0

                for page_number, conv_res in all_conv_results:
                    try:
                        page_block_containers = await processor.create_blocks(conv_res, page_number=page_number)
                    except Exception as e:
                        self.logger.error(f"❌ Failed to create blocks for page {page_number}: {str(e)}")
                        raise

                    if page_block_containers:
                        # Adjust block indices to be unique across all pages
                        for block in page_block_containers.blocks:
                            block.index = block.index + block_index_offset
                            if block.parent_index is not None:
                                block.parent_index = block.parent_index + block_group_index_offset
                            all_blocks.append(block)

                        for block_group in page_block_containers.block_groups:
                            block_group.index = block_group.index + block_group_index_offset
                            if block_group.parent_index is not None:
                                block_group.parent_index = block_group.parent_index + block_group_index_offset
                            # Adjust children indices
                            if block_group.children:
                                # Adjust ranges by adding offsets
                                for range_obj in block_group.children.block_ranges:
                                    range_obj.start += block_index_offset
                                    range_obj.end += block_index_offset
                                for range_obj in block_group.children.block_group_ranges:
                                    range_obj.start += block_group_index_offset
                                    range_obj.end += block_group_index_offset
                            all_block_groups.append(block_group)

                        block_index_offset = len(all_blocks)
                        block_group_index_offset = len(all_block_groups)

                # Create combined BlocksContainer
                combined_block_containers = BlocksContainer(blocks=all_blocks, block_groups=all_block_groups)
                self.logger.info(f"📦 Combined {len(all_blocks)} blocks and {len(all_block_groups)} block groups from all pages")

                # Get record and run indexing pipeline
                record = await self.graph_provider.get_document(recordId, CollectionNames.RECORDS.value)
                if record is None:
                    self.logger.error(f"❌ Record {recordId} not found in database")
                    yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                    return

                record = convert_record_dict_to_record(record)
                record.block_containers = combined_block_containers
                record.virtual_record_id = virtual_record_id
                record.is_vlm_ocr_processed = True

                ctx = self._create_transform_context(record, event_type)
                pipeline = IndexingPipeline(
                    document_extraction=self.document_extraction,
                    sink_orchestrator=self.sink_orchestrator
                )
                await pipeline.apply(ctx)

                # Signal indexing complete
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}

                self.logger.info("✅ PDF processing completed successfully using VLM OCR")
                return
            else:
                yield {"event": "parsing_complete", "data": {"record_id": recordId}}

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
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)
            record.block_containers = BlocksContainer(blocks=blocks, block_groups=block_groups)
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ PDF processing completed successfully")
            return

        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document: {str(e)}")
            raise

    async def process_doc_document(
        self, recordName, recordId, version, source, orgId, doc_binary, virtual_record_id, event_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process DOC document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting DOC document processing for record: {recordName}"
        )
        # Convert DOC to DOCX and delegate
        parser = self.parsers[ExtensionTypes.DOC.value]
        doc_result = parser.convert_doc_to_docx(doc_binary)
        async for event in self.process_docx_document(
            recordName, recordId, version, source, orgId, doc_result, virtual_record_id, event_type
        ):
            yield event

    async def process_docx_document(
        self, recordName, recordId, version, source, orgId, docx_binary, virtual_record_id, event_type: Optional[str] = None
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

            processor = DoclingProcessor(logger=self.logger, config=self.config_service)

            # Phase 1: Parse document with Docling (no LLM calls)
            conv_res = await processor.parse_document(recordName, docx_binary)

            # Signal parsing complete after Docling parsing
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(conv_res)


            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ Docx/Doc processing completed successfully using docling")

        except Exception as e:
            self.logger.error(f"❌ Error processing DOCX document: {str(e)}")
            raise

    async def _enhance_tables_with_llm(self, block_containers: BlocksContainer) -> None:
        """
        Enhance TABLE BlockGroups with LLM-generated summaries and row descriptions.

        This method processes all TABLE BlockGroups in the container:
        - Generates table summary and enhanced column headers using LLM
        - Generates natural language descriptions for each row
        - Updates BlockGroup and Block data with enhanced content

        Args:
            block_containers: The BlocksContainer to enhance in-place
        """
        from app.utils.indexing_helpers import (
            get_rows_text,
            get_table_summary_n_headers,
        )

        # Find all TABLE BlockGroups
        table_groups = [
            bg for bg in block_containers.block_groups
            if bg.type == GroupType.TABLE
        ]

        if not table_groups:
            self.logger.debug("No TABLE BlockGroups found, skipping LLM enhancement")
            return

        self.logger.info(f"🤖 Enhancing {len(table_groups)} tables with LLM summaries")

        for table_group in table_groups:
            try:
                # Get table markdown from data
                table_markdown = table_group.data.get("table_markdown") if table_group.data else None
                if not table_markdown:
                    self.logger.warning(f"No table_markdown found for table group {table_group.index}")
                    continue

                # Get LLM-enhanced summary and column headers
                response = await get_table_summary_n_headers(self.config_service, table_markdown)

                if response:
                    table_summary = response.summary or ""
                    column_headers = response.headers or []

                    # Update BlockGroup with enhanced data
                    table_group.description = table_summary
                    if table_group.data is None:
                        table_group.data = {}
                    table_group.data["table_summary"] = table_summary
                    table_group.data["column_headers"] = column_headers

                    # Update TableMetadata if column headers are available
                    if column_headers and table_group.table_metadata:
                        table_group.table_metadata.column_names = column_headers

                    self.logger.debug(f"Enhanced table {table_group.index} with summary: {table_summary[:100]}...")

                    # Get all child row blocks for this table
                    row_blocks = []
                    row_dicts = []

                    if table_group.children:
                        # Handle new BlockGroupChildren format (range-based)
                        if isinstance(table_group.children, BlockGroupChildren):
                            # Iterate over block ranges and expand to individual indices
                            for range_obj in table_group.children.block_ranges:
                                for block_index in range(range_obj.start, range_obj.end + 1):
                                    if 0 <= block_index < len(block_containers.blocks):
                                        block = block_containers.blocks[block_index]
                                        if block.type == BlockType.TABLE_ROW:
                                            row_blocks.append(block)
                                            # Extract row dict from block data
                                            if block.data and "cells" in block.data:
                                                # Create row dict mapping column headers to cell values
                                                cells = block.data["cells"]
                                                if isinstance(cells, list) and column_headers:
                                                    row_dict = {
                                                        col: cells[i] if i < len(cells) else ""
                                                        for i, col in enumerate(column_headers)
                                                    }
                                                    row_dicts.append(row_dict)
                                                else:
                                                    row_dicts.append({})
                        # Handle old format (list of BlockContainerIndex) for backward compatibility
                        elif isinstance(table_group.children, list):
                            for child_idx in table_group.children:
                                if isinstance(child_idx, BlockContainerIndex) and child_idx.block_index is not None:
                                    block_index = child_idx.block_index
                                    if 0 <= block_index < len(block_containers.blocks):
                                        block = block_containers.blocks[block_index]
                                        if block.type == BlockType.TABLE_ROW:
                                            row_blocks.append(block)
                                            # Extract row dict from block data
                                            if block.data and "cells" in block.data:
                                                # Create row dict mapping column headers to cell values
                                                cells = block.data["cells"]
                                                if isinstance(cells, list) and column_headers:
                                                    row_dict = {
                                                        col: cells[i] if i < len(cells) else ""
                                                        for i, col in enumerate(column_headers)
                                                    }
                                                    row_dicts.append(row_dict)
                                                else:
                                                    row_dicts.append({})

                    # Generate LLM row descriptions (skip header rows)
                    # Filter out header rows using is_header flag from table_row_metadata
                    non_header_row_dicts = []
                    non_header_row_indices = []  # Track original indices for updating blocks

                    for i, (row_dict, row_block) in enumerate(zip(row_dicts, row_blocks)):
                        # Check if this row is a header using the is_header flag from table_row_metadata
                        is_header = (
                            row_block
                            and row_block.table_row_metadata
                            and row_block.table_row_metadata.is_header
                        )

                        if not is_header:
                            non_header_row_dicts.append(row_dict)
                            non_header_row_indices.append(i)

                    if non_header_row_dicts:
                        try:
                            table_data = {"grid": [[row] for row in non_header_row_dicts]}
                            row_descriptions, _ = await get_rows_text(
                                self.config_service, table_data, table_summary, column_headers
                            )

                            # Update row blocks with LLM descriptions (only non-header rows)
                            for description_idx, original_idx in enumerate(non_header_row_indices):
                                if description_idx < len(row_descriptions) and original_idx < len(row_blocks):
                                    row_block = row_blocks[original_idx]
                                    if row_block.data:
                                        row_block.data["row_natural_language_text"] = row_descriptions[description_idx]

                            self.logger.debug(f"Enhanced {len(row_descriptions)} rows with LLM descriptions")
                        except Exception as e:
                            self.logger.warning(f"Failed to generate row descriptions: {e}")
                else:
                    self.logger.warning(f"No LLM response for table {table_group.index}")

            except Exception as e:
                self.logger.error(f"Error enhancing table {table_group.index}: {e}")
                # Continue with other tables even if one fails

    async def process_blocks(
        self, recordName, recordId, version, source, orgId, blocks_data, virtual_record_id, event_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process BlocksContainer and attach to record for indexing, yielding phase completion events.

        For BlockGroups with requires_processing=True, processes their data through docling
        and merges the resulting blocks back into the container.

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

            # Process BlockGroups with requires_processing=True through docling
            block_containers = await self._process_blockgroups_through_docling(
                block_containers, recordName
            )

            # Signal parsing complete after blocks are processed
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Enhance TABLE BlockGroups with LLM summaries and row descriptions
            await self._enhance_tables_with_llm(block_containers)

            # Get record from database
            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return

            # Convert to Record entity and attach blocks
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            # Apply indexing pipeline
            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(
                document_extraction=self.document_extraction,
                sink_orchestrator=self.sink_orchestrator
            )
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

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

    def _map_base64_images_to_blocks(
        self, blocks: List[Block], caption_map: Dict[str, str], block_group_index: int
    ) -> None:
        """
        Map base64 images to image blocks using captions.

        Args:
            blocks: List of blocks to process
            caption_map: Map of alt text to base64 URIs
            block_group_index: Index of the block group (for logging)
        """
        if not caption_map:
            return

        for block in blocks:
            if block.type == BlockType.IMAGE.value and block.image_metadata:
                caption = block.image_metadata.captions
                if caption:
                    caption = caption[0] if isinstance(caption, list) else caption
                    if caption in caption_map and caption_map[caption]:
                        if block.data is None:
                            block.data = {}
                        if isinstance(block.data, dict):
                            block.data["uri"] = caption_map[caption]
                        else:
                            # If data is not a dict, create a new dict with the uri
                            block.data = {"uri": caption_map[caption]}
                    else:
                        self.logger.warning(
                            f"⚠️ Skipping image with caption '{caption}' in BlockGroup {block_group_index} - no valid base64 data available"
                        )

    async def _process_single_blockgroup_through_docling(
        self,
        block_group: BlockGroup,
        record_name: str,
        processor: DoclingProcessor,
        md_parser: Optional[MarkdownParser]
    ) -> Tuple[List[BlockGroup], List[Block]]:
        """
        Process a single block group through docling.

        Args:
            block_group: Block group to process
            record_name: Name of the record (for filename generation)
            processor: DoclingProcessor instance
            md_parser: Markdown parser instance

        Returns:
            Tuple of (new_block_groups, new_blocks) from processing

        Raises:
            ValueError: If block group has no valid markdown data or docling returns empty result
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

        # Parse the modified markdown to bytes
        if md_parser:
            md_bytes = md_parser.parse_string(modified_markdown)
        else:
            md_bytes = modified_markdown.encode('utf-8')

        # Create filename from BlockGroup name or use default
        filename = block_group.name or f"{Path(record_name).stem}_blockgroup_{block_group.index}.md"
        if not filename.endswith('.md'):
            filename = f"{filename}.md"

        # Process through docling
        self.logger.debug(
            f"📄 Processing BlockGroup {block_group.index} ({block_group.name}) through docling"
        )
        processed_blocks_container = await processor.load_document(filename, md_bytes)

        if not processed_blocks_container:
            raise ValueError(
                f"Docling returned empty result for BlockGroup {block_group.index}"
            )

        # Map base64 images to image blocks using captions
        self._map_base64_images_to_blocks(
            processed_blocks_container.blocks, caption_map, block_group.index
        )

        self.logger.debug(
            f"✅ Processed BlockGroup {block_group.index}: "
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

    async def _process_blockgroups_through_docling(
        self, block_containers: BlocksContainer, record_name: str
    ) -> BlocksContainer:
        """
        Process BlockGroups with requires_processing=True through docling.

        Uses a functional approach:
        1. Process all BlockGroups that need processing, collecting results
        2. Calculate index mappings upfront
        3. Build new BlocksContainer in a single pass

        Args:
            block_containers: BlocksContainer to process
            record_name: Name of the record (for docling processing)

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
            f"🔄 Processing {len(block_groups_to_process)} BlockGroups through docling"
        )

        # ========== PHASE 1: Process all BlockGroups and collect results ==========
        # Map: parent_index -> (new_block_groups, new_blocks)
        processing_results: Dict[int, Tuple[List[BlockGroup], List[Block]]] = {}
        processor = DoclingProcessor(logger=self.logger, config=self.config_service)
        initial_block_count = len(block_containers.blocks)

        # Get markdown parser for processing
        md_parser = self.parsers.get(ExtensionTypes.MD.value)

        for block_group in block_groups_to_process:
            try:
                new_block_groups, new_blocks = await self._process_single_blockgroup_through_docling(
                    block_group, record_name, processor, md_parser
                )

                # Store results for later merging
                processing_results[block_group.index] = (new_block_groups, new_blocks)

            except Exception as e:
                self.logger.error(
                    f"❌ Error processing BlockGroup {block_group.index} through docling: {e}",
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
        self, recordName, recordId, version, source, orgId, excel_binary, virtual_record_id, event_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Excel document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting Excel document processing for record: {recordName}"
        )

        try:
            self.logger.debug("📊 Processing Excel content")
            llm, _ = await get_llm(self.config_service)
            parser = self.parsers[ExtensionTypes.XLSX.value]
            if not excel_binary:
                self.logger.info(f"No Excel binary found for record: {recordName}")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield {"event": "parsing_complete", "data": {"record_id": recordId}}
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return

            # Phase 1: Load workbook (no LLM calls)
            parser.load_workbook_from_binary(excel_binary)

            # Signal parsing complete after workbook is loaded
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Phase 2: Create blocks (involves LLM calls for summaries)
            blocks_containers = await parser.create_blocks(llm)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)
            record.block_containers = blocks_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ Excel processing completed successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error processing Excel document: {str(e)}")
            raise

    async def process_xls_document(
        self, recordName, recordId, version, source, orgId, xls_binary, virtual_record_id, event_type: Optional[str] = None
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
                recordName, recordId, version, source, orgId, xlsx_binary, virtual_record_id, event_type
            ):
                yield event
            self.logger.debug("📑 XLS document processed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error processing XLS document: {str(e)}")
            raise

    async def process_delimited_document(
        self, recordName, recordId, file_binary, virtual_record_id, extension=None, event_type: Optional[str] = None
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

            llm, _ = await get_llm(self.config_service)

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

                yield {"event": "parsing_complete", "data": {"record_id": recordId}}
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
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
                yield {"event": "parsing_complete", "data": {"record_id": recordId}}
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)
            record.virtual_record_id = virtual_record_id

            # Signal parsing complete after delimited file is parsed (before LLM block creation)
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Process all tables using unified multi-table logic
            self.logger.info(f"📊 Processing {len(tables)} table(s)")
            block_containers = await parser.get_blocks_from_csv_with_multiple_tables(tables, llm)

            record.block_containers = block_containers

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ Delimited file processing completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error processing delimited document: {str(e)}")
            raise



    async def _mark_record(self, record_id, indexing_status: ProgressStatus) -> None:
        record = await self.graph_provider.get_document(
                        record_id, CollectionNames.RECORDS.value
                    )
        if not record:
            raise DocumentProcessingError(
                "Record not found in database",
                doc_id=record_id,
            )
        doc = dict(record)
        timestamp = get_epoch_timestamp_in_ms()
        doc.update(
            {
                "indexingStatus": indexing_status.value,
                "isDirty": False,
                "lastIndexTimestamp": timestamp,
                "extractionStatus": ProgressStatus.EMPTY.value,
                "lastExtractionTimestamp": timestamp,
            }
        )

        docs = [doc]

        success = await self.graph_provider.batch_upsert_nodes(
            docs, CollectionNames.RECORDS.value
        )
        if not success:
            raise DocumentProcessingError(
                "Failed to update indexing status", doc_id=record_id
            )
        return

    async def process_html_document(
        self, recordName, recordId, version, source, orgId, html_binary, virtual_record_id, event_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process HTML document, yielding phase completion events."""
        self.logger.info(
            f"🚀 Starting HTML document processing for record: {recordName}"
        )

        try:
            html_content = None
            try:
                soup = BeautifulSoup(html_binary, 'html.parser')

                # Remove script, style, and other non-content elements
                for element in soup(["script", "style", "noscript", "iframe", "nav", "footer", "header"]):
                    element.decompose()

                html_content = str(soup)

            except Exception as e:
                self.logger.warning(f"⚠️ Failed to clean HTML: {e}")

            if html_content is None:
                if isinstance(html_binary, bytes):
                    html_content = html_binary.decode("utf-8")
                else:
                    html_content = html_binary
            html_parser = self.parsers[ExtensionTypes.HTML.value]
            html_content = html_parser.replace_relative_image_urls(html_content)
            markdown = convert(html_content)
            md_binary = markdown.encode("utf-8")

            # Use the existing markdown processing function
            async for event in self.process_md_document(
                recordName=recordName,
                recordId=recordId,
                md_binary=md_binary,
                virtual_record_id=virtual_record_id,
                event_type=event_type
            ):
                yield event

            self.logger.info("✅ HTML processing completed successfully using markdown conversion.")

        except Exception as e:
            self.logger.error(f"❌ Error processing HTML document: {str(e)}")
            raise

    async def process_mdx_document(
        self, recordName: str, recordId: str, version: str, source: str, orgId: str, mdx_content: str, virtual_record_id, event_type: Optional[str] = None
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
            recordName, recordId, md_content, virtual_record_id, event_type
        ):
            yield event

    async def process_md_document(
        self, recordName, recordId, md_binary, virtual_record_id, event_type: Optional[str] = None
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
                    yield {"event": "parsing_complete", "data": {"record_id": recordId}}
                    yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                    return
                except DocumentProcessingError:
                    raise
                except Exception as e:
                    raise DocumentProcessingError(
                        "Error updating record status: " + str(e),
                        doc_id=recordId,
                        details={"error": str(e)},
                    )

            # Initialize Markdown parser
            self.logger.debug("📄 Processing Markdown content")
            parser = self.parsers[ExtensionTypes.MD.value]

            modified_markdown, images = parser.extract_and_replace_images(markdown)
            caption_map = {}
            urls_to_convert = []

            # Collect all image URLs
            for image in images:
                urls_to_convert.append(image["url"])

            # Convert URLs to base64 if there are any images
            if urls_to_convert:
                image_parser = self.parsers[ExtensionTypes.PNG.value]
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)

                # Create caption map with base64 URLs
                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

            md_bytes = parser.parse_string(modified_markdown)

            processor = DoclingProcessor(logger=self.logger,config=self.config_service)
            filename_without_ext = Path(recordName).stem

            # Phase 1: Parse document with Docling (no LLM calls)
            conv_res = await processor.parse_document(f"{filename_without_ext}.md", md_bytes)

            # Signal parsing complete after Docling parsing
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(conv_res)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                # Must yield indexing_complete to release indexing semaphore properly
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)

            blocks = block_containers.blocks
            for block in blocks:
                if block.type == BlockType.IMAGE.value and block.image_metadata:
                    caption = block.image_metadata.captions
                    if caption:
                        caption = caption[0]
                        if caption in caption_map and caption_map[caption]:
                            if block.data is None:
                                block.data = {}
                            if isinstance(block.data, dict):
                                block.data["uri"] = caption_map[caption]
                            else:
                                # If data is not a dict, create a new dict with the uri
                                block.data = {"uri": caption_map[caption]}
                        else:
                            self.logger.warning(f"⚠️ Skipping image with caption '{caption}' - no valid base64 data available")

            block_containers.blocks = blocks

            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ MD processing completed successfully using docling")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing Markdown document: {str(e)}")
            raise

    async def process_txt_document(
        self, recordName, recordId, version, source, orgId, txt_binary, virtual_record_id, recordType, connectorName, origin, event_type: Optional[str] = None
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
                event_type=event_type
            ):
                yield event
            self.logger.info("✅ TXT processing completed successfully")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing TXT document: {str(e)}")
            raise

    async def process_pptx_document(
        self, recordName, recordId, version, source, orgId, pptx_binary, virtual_record_id, event_type: Optional[str] = None
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

            processor = DoclingProcessor(logger=self.logger, config=self.config_service)

            # Phase 1: Parse document with Docling (no LLM calls)
            conv_res = await processor.parse_document(recordName, pptx_binary)

            # Signal parsing complete after Docling parsing
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}

            # Phase 2: Create blocks (involves LLM calls for tables)
            block_containers = await processor.create_blocks(conv_res)

            record = await self.graph_provider.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}

            self.logger.info("✅ PPTX processing completed successfully using docling")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing PPTX document: {str(e)}")
            raise

    async def process_ppt_document(
        self, recordName, recordId, version, source, orgId, ppt_binary, virtual_record_id, event_type: Optional[str] = None
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
            recordName, recordId, version, source, orgId, ppt_result, virtual_record_id, event_type
        ):
            yield event

    async def process_sql_structured_data(
        self, recordName: str, recordId: str, json_content: bytes, virtual_record_id: str,
        record_type: str = "SQL_TABLE", event_type: str = None
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
            # Signal parsing complete
            yield {"event": "parsing_complete", "data": {"record_id": recordId}}
            
            # Get the appropriate parser based on record type
            if record_type == "SQL_TABLE":
                parser = self.parsers.get(ExtensionTypes.SQL_TABLE.value)
            elif record_type == "SQL_VIEW":
                parser = self.parsers.get(ExtensionTypes.SQL_VIEW.value)
            else:
                self.logger.error(f"❌ Unknown record type: {record_type}")
                await self._mark_record(recordId, ProgressStatus.FAILED)
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            
            if not parser:
                self.logger.error(f"❌ No parser found for {record_type}")
                await self._mark_record(recordId, ProgressStatus.FAILED)
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
                return
            
            # Create a file-like stream from the JSON content
            if isinstance(json_content, bytes):
                file_stream = io.BytesIO(json_content)
            else:
                file_stream = io.BytesIO(json_content.encode("utf-8"))
            
            # Parse using the dedicated SQL parser (handles DDL, rows, etc.)
            block_containers = parser.parse_stream(file_stream)
            
            if not block_containers.block_groups and not block_containers.blocks:
                self.logger.info(f"No content to index for {record_type}: {recordName}")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                yield {"event": "indexing_complete", "data": {"record_id": recordId}}
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
            
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id

            ctx = self._create_transform_context(record, event_type)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            
            # Signal indexing complete
            yield {"event": "indexing_complete", "data": {"record_id": recordId}}
            
            self.logger.info(f"✅ {record_type} processing completed successfully for: {recordName} ({len(block_containers.block_groups)} block group(s), {len(block_containers.blocks)} block(s))")
            
        except Exception as e:
            self.logger.error(f"❌ Error processing {record_type} document: {str(e)}")
            raise

