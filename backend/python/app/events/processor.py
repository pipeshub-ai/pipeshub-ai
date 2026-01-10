import io
import json
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from html_to_markdown import convert

from app.config.constants.ai_models import (
    AzureDocIntelligenceModel,
    OCRProvider,
)
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
    BlocksContainer,
    BlockType,
    CitationMetadata,
    DataFormat,
    Point,
)
from app.models.entities import Record, RecordType
from app.modules.parsers.pdf.docling import DoclingProcessor
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext
from app.services.docling.client import DoclingClient
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
        id=record_dict.get("_key"),
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
        arango_service,
        parsers,
        document_extractor,
        sink_orchestrator,
    ) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing Processor")
        self.indexing_pipeline = indexing_pipeline
        self.arango_service = arango_service
        self.parsers = parsers
        self.config_service = config_service
        self.document_extraction = document_extractor
        self.sink_orchestrator = sink_orchestrator

        # Initialize Docling client for external service
        self.docling_client = DoclingClient()

    async def process_image(self, record_id, content, virtual_record_id) -> None:
        try:
            # Initialize image parser
            self.logger.debug("📸 Processing image content")
            if not content:
                raise Exception("No image data provided")

            record = await self.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
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
                    success = await self.arango_service.batch_upsert_nodes(
                        docs, CollectionNames.RECORDS.value
                    )
                    if not success:
                        raise DocumentProcessingError(
                            "Failed to update indexing status", doc_id=record_id
                        )

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

            block_containers = parser.parse_image(content,extension)
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info("✅ Image processing completed successfully")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing image: {str(e)}")
            raise



    async def process_gmail_message(
        self, recordName, recordId, version, source, orgId, html_content, virtual_record_id
    ) -> None:

        self.logger.info("🚀 Processing Gmail Message")

        try:

            await self.process_html_document(
                recordName=recordName,
                recordId=recordId,
                version=version,
                source=source,
                orgId=orgId,
                html_binary=html_content,
                virtual_record_id=virtual_record_id
            )

            self.logger.info("✅ Gmail Message processing completed successfully using markdown conversion.")

        except Exception as e:
            self.logger.error(f"❌ Error processing Gmail Message document: {str(e)}")
            raise

    async def process_pdf_with_docling(self, recordName, recordId, pdf_binary, virtual_record_id) -> None|bool:
        self.logger.info(f"🚀 Starting PDF document processing for record: {recordName}")
        try:
            self.logger.debug("📄 Processing PDF binary content using external Docling service")

            # Use external Docling service
            record_name = recordName if recordName.endswith(".pdf") else f"{recordName}.pdf"

            block_containers = await self.docling_client.process_pdf(record_name, pdf_binary)
            if block_containers is None:
                self.logger.error(f"❌ External Docling service failed to process {recordName}")
                return False

            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                return

            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info(f"✅ PDF processing completed for record: {recordName}, using external Docling service")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document with external Docling service: {str(e)}")
            raise

    async def process_pdf_document_with_ocr(
        self, recordName, recordId, version, source, orgId, pdf_binary, virtual_record_id
    ) -> None:
        """Process PDF document with automatic OCR selection based on environment settings"""
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

                all_blocks = []
                all_block_groups = []
                block_index_offset = 0
                block_group_index_offset = 0
                processor = DoclingProcessor(logger=self.logger, config=self.config_service)

                for page in pages:
                    page_number = page.get("page_number")
                    page_markdown = page.get("markdown", "")

                    if not page_markdown.strip():
                        self.logger.debug(f"⏭️ Skipping empty page {page_number}")
                        continue

                    # Process each page through DoclingProcessor with page number
                    page_filename = f"{Path(recordName).stem}_page_{page_number}.md"
                    md_bytes = page_markdown.encode('utf-8')

                    try:
                        page_block_containers = await processor.load_document(
                            page_filename,
                            md_bytes,
                            page_number=page_number
                        )
                    except Exception as e:
                        self.logger.error(f"❌ Failed to process page {page_number}: {str(e)}")
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
                                for child in block_group.children:
                                    if child.block_index is not None:
                                        child.block_index = child.block_index + block_index_offset
                                    if child.block_group_index is not None:
                                        child.block_group_index = child.block_group_index + block_group_index_offset
                            all_block_groups.append(block_group)

                        block_index_offset = len(all_blocks)
                        block_group_index_offset = len(all_block_groups)

                # Create combined BlocksContainer
                combined_block_containers = BlocksContainer(blocks=all_blocks, block_groups=all_block_groups)
                self.logger.info(f"📦 Combined {len(all_blocks)} blocks and {len(all_block_groups)} block groups from all pages")

                # Get record and run indexing pipeline
                record = await self.arango_service.get_document(recordId, CollectionNames.RECORDS.value)
                if record is None:
                    self.logger.error(f"❌ Record {recordId} not found in database")
                    return
                record = convert_record_dict_to_record(record)
                record.block_containers = combined_block_containers
                record.virtual_record_id = virtual_record_id
                record.is_vlm_ocr_processed = True

                ctx = TransformContext(record=record)
                pipeline = IndexingPipeline(
                    document_extraction=self.document_extraction,
                    sink_orchestrator=self.sink_orchestrator
                )
                await pipeline.apply(ctx)

                self.logger.info("✅ PDF processing completed successfully using VLM OCR")
                return
            # Extract domain metadata from paragraphs
            self.logger.info("🎯 Extracting domain metadata")
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
                block_group.children = table_rows.get(block_group.index, [])
            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                return
            record = convert_record_dict_to_record(record)
            record.block_containers = BlocksContainer(blocks=blocks, block_groups=block_groups)
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)

            self.logger.info("✅ PDF processing completed successfully")
            return

        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document: {str(e)}")
            raise

    async def process_doc_document(
        self, recordName, recordId, version, source, orgId, doc_binary, virtual_record_id
    ) -> None:
        self.logger.info(
            f"🚀 Starting DOC document processing for record: {recordName}"
        )
        # Implement DOC processing logic here
        parser = self.parsers[ExtensionTypes.DOC.value]
        doc_result = parser.convert_doc_to_docx(doc_binary)
        await self.process_docx_document(
            recordName, recordId, version, source, orgId, doc_result, virtual_record_id
        )

        return {"status": "success", "message": "DOC processed successfully"}

    async def process_docx_document(
        self, recordName, recordId, version, source, orgId, docx_binary, virtual_record_id
    ) -> None:
        """Process DOCX document and extract structured content

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

            processor = DoclingProcessor(logger=self.logger,config=self.config_service)
            block_containers = await processor.load_document(recordName, docx_binary)

            if block_containers is False:
                raise Exception("Failed to process DOCX document. It might contain scanned pages.")

            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )

            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise Exception(f"Record {recordId} not found in graph db")
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info("✅ Docx/Doc processing completed successfully using docling")

        except Exception as e:
            self.logger.error(f"❌ Error processing DOCX document: {str(e)}")
            raise

    async def process_excel_document(
        self, recordName, recordId, version, source, orgId, excel_binary, virtual_record_id
    ) -> None:
        """Process Excel document and extract structured content"""
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
                return
            blocks_containers = await parser.parse(excel_binary, llm)
            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise Exception(f"Record {recordId} not found in graph db")
            record = convert_record_dict_to_record(record)
            record.block_containers = blocks_containers
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info("✅ Excel processing completed successfully.")
        except Exception as e:
            self.logger.error(f"❌ Error processing Excel document: {str(e)}")
            raise

    async def process_xls_document(
        self, recordName, recordId, version, source, orgId, xls_binary, virtual_record_id
    ) -> None:
        """Process XLS document and extract structured content"""
        self.logger.info(
            f"🚀 Starting XLS document processing for record: {recordName}"
        )

        try:
            # Convert XLS to XLSX binary
            xls_parser = self.parsers[ExtensionTypes.XLS.value]
            xlsx_binary = xls_parser.convert_xls_to_xlsx(xls_binary)

            # Process the converted XLSX using the Excel parser
            result = await self.process_excel_document(
                recordName, recordId, version, source, orgId, xlsx_binary, virtual_record_id
            )
            self.logger.debug("📑 XLS document processed successfully")
            return result

        except Exception as e:
            self.logger.error(f"❌ Error processing XLS document: {str(e)}")
            raise

    async def process_csv_document(
        self, recordName, recordId, version, source, orgId, csv_binary, virtual_record_id, origin
    ) -> None:
        """Process CSV document and extract structured content

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the document
            orgId (str): Organization ID
            csv_binary (bytes): Binary content of the CSV file
        """
        self.logger.info(
            f"🚀 Starting CSV document processing for record: {recordName}"
        )

        try:
            # Initialize CSV parser
            self.logger.debug("📊 Processing CSV content")
            parser = self.parsers[ExtensionTypes.CSV.value]

            llm, _ = await get_llm(self.config_service)

            # Try different encodings to decode binary data
            encodings = ["utf-8", "latin1", "cp1252", "iso-8859-1"]
            csv_result = None
            for encoding in encodings:
                try:
                    self.logger.debug(
                        f"Attempting to decode CSV with {encoding} encoding"
                    )
                    # Decode binary data to string
                    csv_text = csv_binary.decode(encoding)

                    # Create string stream from decoded text
                    csv_stream = io.StringIO(csv_text)

                    # Use the parser's read_stream method directly
                    csv_result = parser.read_stream(csv_stream)

                    self.logger.info(
                        f"✅ Successfully parsed CSV with {encoding} encoding. Rows: {len(csv_result):,}"
                    )
                    break
                except UnicodeDecodeError:
                    self.logger.debug(f"Failed to decode with {encoding} encoding")
                    continue
                except Exception as e:
                    self.logger.debug(f"Failed to process CSV with {encoding} encoding: {str(e)}")
                    continue


            if csv_result is None:
                self.logger.info(f"Unable to decode CSV file with any supported encoding for record: {recordName}. Setting indexing status to EMPTY.")
                await self._mark_record(recordId, ProgressStatus.EMPTY)
                return

            self.logger.debug("📑 CSV result processed")

            # Extract domain metadata from CSV content
            self.logger.info("🎯 Extracting domain metadata")
            if csv_result:

                record = await self.arango_service.get_document(
                    recordId, CollectionNames.RECORDS.value
                    )
                if record is None:
                    self.logger.error(f"❌ Record {recordId} not found in database")
                    return
                record = convert_record_dict_to_record(record)
                record.virtual_record_id = virtual_record_id

                block_containers = await parser.get_blocks_from_csv_result(csv_result, llm)
                record.block_containers = block_containers

                ctx = TransformContext(record=record)
                pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
                await pipeline.apply(ctx)


            self.logger.info("✅ CSV processing completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error processing CSV document: {str(e)}")
            raise



    async def _mark_record(self, record_id, indexing_status: ProgressStatus) -> None:
        record = await self.arango_service.get_document(
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

        success = await self.arango_service.batch_upsert_nodes(
            docs, CollectionNames.RECORDS.value
        )
        if not success:
            raise DocumentProcessingError(
                "Failed to update indexing status", doc_id=record_id
            )
        return

    async def process_html_document(
        self, recordName, recordId, version, source, orgId, html_binary, virtual_record_id
    ) -> None:
        """Process HTML document by converting to markdown and using markdown processing"""
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
            await self.process_md_document(
                recordName=recordName,
                recordId=recordId,
                md_binary=md_binary,
                virtual_record_id=virtual_record_id
            )

            self.logger.info("✅ HTML processing completed successfully using markdown conversion.")

        except Exception as e:
            self.logger.error(f"❌ Error processing HTML document: {str(e)}")
            raise

    async def process_mdx_document(
        self, recordName: str, recordId: str, version: str, source: str, orgId: str, mdx_content: str, virtual_record_id
    ) -> None:
        """Process MDX document by converting it to MD and then processing it as markdown

        Args:
            recordName (str): Name of the record
            recordId (str): ID of the record
            version (str): Version of the record
            source (str): Source of the record
            orgId (str): Organization ID
            mdx_content (str): Content of the MDX file

        Returns:
            dict: Processing status and message
        """
        self.logger.info(
            f"🚀 Starting MDX document processing for record: {recordName}"
        )

        # Convert MDX to MD using our parser
        parser = self.parsers[ExtensionTypes.MDX.value]
        md_content = parser.convert_mdx_to_md(mdx_content)

        # Process the converted markdown content
        await self.process_md_document(
            recordName, recordId, md_content, virtual_record_id
        )

        return {"status": "success", "message": "MDX processed successfully"}

    async def process_md_document(
        self, recordName, recordId, md_binary, virtual_record_id
    ) -> None:
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

            block_containers = await processor.load_document(f"{filename_without_ext}.md", md_bytes)

            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise Exception(f"Record {recordId} not found in graph db")
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
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info("✅ MD processing completed successfully using docling")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing Markdown document: {str(e)}")
            raise

    async def process_txt_document(
        self, recordName, recordId, version, source, orgId, txt_binary, virtual_record_id, recordType, connectorName, origin
    ) -> None:
        """Process TXT document and extract structured content"""
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

            await self.process_md_document(
                recordName=recordName,
                recordId=recordId,
                md_binary=text_content,
                virtual_record_id=virtual_record_id
            )
            self.logger.info("✅ TXT processing completed successfully")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing TXT document: {str(e)}")
            raise

    async def process_pptx_document(
        self, recordName, recordId, version, source, orgId, pptx_binary, virtual_record_id
    ) -> None:
        """Process PPTX document and extract structured content

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
            block_containers = await processor.load_document(recordName, pptx_binary)
            if block_containers is False:
                raise Exception(("Failed to process PPTX document. It might contain scanned pages."))
            record = await self.arango_service.get_document(
                recordId, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {recordId} not found in database")
                raise Exception(f"Record {recordId} not found in graph db")
            record = convert_record_dict_to_record(record)
            record.block_containers = block_containers
            record.virtual_record_id = virtual_record_id
            ctx = TransformContext(record=record)
            pipeline = IndexingPipeline(document_extraction=self.document_extraction, sink_orchestrator=self.sink_orchestrator)
            await pipeline.apply(ctx)
            self.logger.info("✅ PPTX processing completed successfully using docling")
            return
        except Exception as e:
            self.logger.error(f"❌ Error processing PPTX document: {str(e)}")
            raise

    async def process_ppt_document(
        self, recordName, recordId, version, source, orgId, ppt_binary, virtual_record_id
    ) -> None:
        """Process PPT document and extract structured content

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
        await self.process_pptx_document(
            recordName, recordId, version, source, orgId, ppt_result, virtual_record_id
        )

        return {"status": "success", "message": "PPT processed successfully"}

