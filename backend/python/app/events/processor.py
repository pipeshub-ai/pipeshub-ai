import io
import json
from datetime import datetime

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
        domain_extractor,
    ) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing Processor")
        self.indexing_pipeline = indexing_pipeline
        self.arango_service = arango_service
        self.parsers = parsers
        self.config_service = config_service
        self.document_extraction = document_extractor
        self.sink_orchestrator = sink_orchestrator
        self.domain_extractor = domain_extractor

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

    async def process_google_slides(self, record_id, record_version, orgId, content, virtual_record_id) -> None:
        """Process Google Slides presentation and extract structured content

        Args:
            record_id (str): ID of the Google Slides presentation
            record_version (str): Version of the presentation
            orgId (str): Organization ID
        """
        self.logger.info(
            f"🚀 Starting Google Slides processing for record: {record_id}"
        )

        try:
            # Initialize Google Slides parser
            self.logger.debug("📊 Processing Google Slides content")
            # parser = self.parsers['google_slides']
            # presentation_data = await parser.process_presentation(record_id)
            presentation_data = content
            if not presentation_data:
                raise Exception("Failed to process presentation")

            # Extract text content from all slides
            self.logger.info("📝 Extracting text content")
            text_content = []
            numbered_items = []

            for slide in presentation_data["slides"]:
                slide_text = []

                # Process each element in the slide
                for element in slide["elements"]:
                    if element["type"] == "shape":
                        text = element["text"]["content"].strip()
                        if text:
                            slide_text.append(text)
                    elif element["type"] == "table":
                        for cell in element["cells"]:
                            cell_text = cell["text"]["content"].strip()
                            if cell_text:
                                slide_text.append(cell_text)

                # Join all text from the slide
                full_slide_text = " ".join(slide_text)
                if full_slide_text:
                    text_content.append(full_slide_text)

                # Create numbered item for the slide
                numbered_items.append(
                    {
                        "number": slide["slideNumber"],
                        "type": "slide",
                        "content": full_slide_text,
                        "elements": slide["elements"],
                        "layout": slide["layout"],
                        "masterObjectId": slide["masterObjectId"],
                        "hasNotesPage": slide.get("hasNotesPage", False),
                    }
                )

            # Join all text content with newlines
            full_text_content = "\n".join(text for text in text_content if text)

            # Extract metadata using domain extractor
            self.logger.info("🎯 Extracting metadata from content")
            domain_metadata = None
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    full_text_content, orgId
                )
                record = await self.domain_extractor.save_metadata_to_db(
                    orgId, record_id, metadata, virtual_record_id
                )
                file = await self.arango_service.get_document(
                    record_id, CollectionNames.FILES.value
                )
                domain_metadata = {**record, **file}
            except Exception as e:
                self.logger.error(f"❌ Error extracting metadata: {str(e)}")

            # Format content for output
            formatted_content = ""
            for slide in presentation_data["slides"]:
                formatted_content += f"[Slide {slide['slideNumber']}]\n"
                for element in slide["elements"]:
                    if element["type"] == "shape":
                        text = element["text"]["content"].strip()
                        if text:
                            formatted_content += f"{text}\n"
                    elif element["type"] == "table":
                        formatted_content += (
                            f"[Table with {len(element['cells'])} cells]\n"
                        )
                    elif element["type"] == "image":
                        formatted_content += "[Image]\n"
                    elif element["type"] == "video":
                        formatted_content += "[Video]\n"
                formatted_content += "\n"

            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "domain_metadata": domain_metadata,
                "recordId": record_id,
                "version": record_version,
                "presentation_metadata": presentation_data["metadata"],
                "total_slides": presentation_data["summary"]["totalSlides"],
                "has_notes": presentation_data["summary"]["hasNotes"],
            }

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for slide in presentation_data["slides"]:
                slide_number = slide["slideNumber"]

                # Process text elements
                for element in slide["elements"]:
                    if element["type"] == "shape":
                        text = element["text"]["content"].strip()
                        if text:
                            # Split into sentences
                            sentences = [
                                s.strip() + "." for s in text.split(".") if s.strip()
                            ]
                            for sentence in sentences:
                                sentence_data.append(
                                    {
                                        "text": sentence,
                                        "metadata": {
                                            **(domain_metadata or {}),
                                            "recordId": record_id,
                                            "blockType": "slide_text",
                                            "pageNum": slide_number,
                                            "totalSlides": slide["totalSlides"],
                                            "elementId": element["id"],
                                            "elementType": "shape",
                                            "virtualRecordId": virtual_record_id,
                                        },
                                    }
                                )

                    elif element["type"] == "table":
                        # Process table cells
                        for cell in element["cells"]:
                            cell_text = cell["text"]["content"].strip()
                            if cell_text:
                                sentence_data.append(
                                    {
                                        "text": cell_text,
                                        "metadata": {
                                            **(domain_metadata or {}),
                                            "recordId": record_id,
                                            "blockType": "slide_table_cell",
                                            "pageNum": slide_number,
                                            "totalSlides": slide["totalSlides"],
                                            "elementId": element["id"],
                                            "rowIndex": cell["rowIndex"],
                                            "columnIndex": cell["columnIndex"],
                                            "virtualRecordId": virtual_record_id,
                                        },
                                    }
                                )

            self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
            pipeline = self.indexing_pipeline
            await pipeline.index_documents(sentence_data,record_id)

            self.logger.info("✅ Google Slides processing completed successfully")
            return {
                "presentation_data": presentation_data,
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(
                f"❌ Error processing Google Slides presentation: {str(e)}"
            )
            raise

    async def process_google_docs(self, record_id, record_version, orgId, content, virtual_record_id) -> None:
        """Process Google Docs document and extract structured content

        Args:
            record_id (str): ID of the Google Doc
            record_version (str): Version of the document
        """
        self.logger.info(f"🚀 Starting Google Docs processing for record: {record_id}")

        try:
            # Initialize Google Docs parser
            self.logger.debug("📄 Processing Google Docs content")
            # Extract content from the structured response
            all_content = content.get("all_content", [])
            headers = content.get("headers", [])
            footers = content.get("footers", [])

            # Extract text content from all ordered content
            self.logger.info("📝 Extracting text content")
            text_content = []
            for item in all_content:
                if item["type"] == "paragraph":
                    text_content.append(item["content"]["text"].strip())
                elif item["type"] == "table":
                    # Extract text from table cells
                    for cell in item["content"]["cells"]:
                        cell_text = " ".join(cell["content"]).strip()
                        if cell_text:
                            text_content.append(cell_text)

            # Join all text content with newlines
            full_text_content = "\n".join(text for text in text_content if text)

            # Extract metadata using domain extractor
            self.logger.info("🎯 Extracting metadata from content")
            domain_metadata = None
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    full_text_content, orgId
                )
                record = await self.domain_extractor.save_metadata_to_db(
                    orgId, record_id, metadata, virtual_record_id
                )
                file = await self.arango_service.get_document(
                    record_id, CollectionNames.FILES.value
                )
                domain_metadata = {**record, **file}
            except Exception as e:
                self.logger.error(f"❌ Error extracting metadata: {str(e)}")

            # Format content for output
            formatted_content = ""
            numbered_items = []

            # Process all content for numbering and formatting
            self.logger.debug("📝 Processing content elements")
            for idx, item in enumerate(all_content, 1):
                if item["type"] == "paragraph":
                    element = item["content"]
                    element_entry = {
                        "number": idx,
                        "content": element["text"].strip(),
                        "type": "paragraph",
                        "style": element.get("style", {}),
                        "links": element.get("links", []),
                        "start_index": item["start_index"],
                        "end_index": item["end_index"],
                    }
                    numbered_items.append(element_entry)
                    formatted_content += f"[{idx}] {element['text'].strip()}\n\n"

                elif item["type"] == "table":
                    table = item["content"]
                    table_entry = {
                        "number": f"T{idx}",
                        "content": table,
                        "type": "table",
                        "rows": table["rows"],
                        "columns": table["columns"],
                        "start_index": item["start_index"],
                        "end_index": item["end_index"],
                    }
                    numbered_items.append(table_entry)
                    formatted_content += (
                        f"[T{idx}] Table ({table['rows']}x{table['columns']})\n\n"
                    )

                elif item["type"] == "image":
                    image = item["content"]
                    image_entry = {
                        "number": f"I{idx}",
                        "type": "image",
                        "source_uri": image["source_uri"],
                        "size": image.get("size"),
                        "start_index": item["start_index"],
                        "end_index": item["end_index"],
                    }
                    numbered_items.append(image_entry)
                    formatted_content += f"[I{idx}] Image\n\n"

            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "domain_metadata": domain_metadata,
                "recordId": record_id,
                "version": record_version,
                "has_header": bool(headers),
                "has_footer": bool(footers),
                "image_count": len(
                    [item for item in all_content if item["type"] == "image"]
                ),
                "table_count": len(
                    [item for item in all_content if item["type"] == "table"]
                ),
                "paragraph_count": len(
                    [item for item in all_content if item["type"] == "paragraph"]
                ),
            }

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3

            for idx, item in enumerate(all_content, 1):
                if item["type"] == "paragraph":
                    text = item["content"]["text"].strip()
                    if text:
                        # Create context from previous items
                        previous_context = " ".join(
                            [
                                prev["content"]["text"].strip()
                                for prev in context_window
                                if prev["type"] == "paragraph"
                            ]
                        )

                        # Current item's context
                        full_context = {"previous": previous_context, "current": text}

                        # Split into sentences (simple splitting, can be improved with NLP)
                        sentences = [
                            s.strip() + "." for s in text.split(".") if s.strip()
                        ]
                        for sentence in sentences:
                            sentence_data.append(
                                {
                                    "text": sentence,
                                    "metadata": {
                                        **(domain_metadata or {}),
                                        "recordId": record_id,
                                        "blockType": "text",
                                        "blockNum": [idx],
                                        "blockText": json.dumps(full_context),
                                        "start_index": item["start_index"],
                                        "end_index": item["end_index"],
                                        "virtualRecordId": virtual_record_id,
                                    },
                                }
                            )

                        # Update context window
                        context_window.append(item)
                        if len(context_window) > context_window_size:
                            context_window.pop(0)

                elif item["type"] == "table":
                    # Process table cells as sentences
                    for cell in item["content"]["cells"]:
                        cell_text = " ".join(cell["content"]).strip()
                        if cell_text:
                            sentence_data.append(
                                {
                                    "text": cell_text,
                                    "metadata": {
                                        **(domain_metadata or {}),
                                        "recordId": record_id,
                                        "blockType": "table_cell",
                                        "blockNum": [idx],
                                        "row": cell["row"],
                                        "column": cell["column"],
                                        "start_index": cell["start_index"],
                                        "end_index": cell["end_index"],
                                        "virtualRecordId": virtual_record_id,
                                    },
                                }
                            )

            self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
            pipeline = self.indexing_pipeline
            await pipeline.index_documents(sentence_data,record_id)

            self.logger.info("✅ Google Docs processing completed successfully")
            return {
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing Google Docs document: {str(e)}")
            raise

    async def process_google_sheets(self, record_id, record_version, orgId, content, virtual_record_id) -> None:
        self.logger.info("🚀 Processing Google Sheets")
        try:
            # Initialize Google Docs parser
            self.logger.debug("📄 Processing Google Sheets content")

            all_sheets_result = content["all_sheet_results"]
            content["parsed_result"]

            combined_texts = []
            row_counter = 1
            domain_metadata = None
            sentence_data = []

            for sheet_result in all_sheets_result:
                for table in sheet_result["tables"]:
                    for row in table["rows"]:
                        combined_texts.append(
                            f"{row_counter}. {row['natural_language_text']}"
                        )
                        row_counter += 1

            combined_text = "\n".join(combined_texts)
            if combined_text:
                try:
                    self.logger.info("🎯 Extracting metadata from Excel content")
                    metadata = await self.domain_extractor.extract_metadata(
                        combined_text, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, record_id, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        record_id, CollectionNames.FILES.value
                    )

                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            for sheet_idx, sheet_result in enumerate(all_sheets_result, 1):
                self.logger.info(f"sheet_name: {sheet_result['sheet_name']}")
                for table in sheet_result["tables"]:
                    for row in table["rows"]:
                        row_data = {
                            k: (v.isoformat() if isinstance(v, datetime) else v)
                            for k, v in row["raw_data"].items()
                        }
                        block_num = [int(row["row_num"])] if row["row_num"] else [0]
                        sentence_data.append(
                            {
                                "text": row["natural_language_text"],
                                "metadata": {
                                    **domain_metadata,
                                    "recordId": record_id,
                                    "sheetName": sheet_result["sheet_name"],
                                    "sheetNum": sheet_idx,
                                    "blockNum": block_num,
                                    "blockType": "table_row",
                                    "blockText": json.dumps(row_data),
                                    "virtualRecordId": virtual_record_id,
                                },
                            }
                        )

            self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
            pipeline = self.indexing_pipeline
            await pipeline.index_documents(sentence_data, record_id)

            self.logger.info("✅ Google sheets processing completed successfully")
            return {
                "formatted_content": combined_text,
                "numbered_items": [],
                "metadata": metadata,
            }
        except Exception as e:
            self.logger.error(f"❌ Error processing Google Sheets document: {str(e)}")
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

    async def process_pdf_document(
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

                if provider == OCRProvider.AZURE_DI.value:
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
                self.logger.debug("📚 Setting up PyMuPDF OCR handler")
                handler = OCRHandler(self.logger, OCRProvider.OCRMYPDF.value, config=self.config_service)
                provider = OCRProvider.OCRMYPDF.value

            # Process document
            self.logger.info("🔄 Processing document with OCR handler")
            try:
                ocr_result = await handler.process_document(pdf_binary)
            except Exception:
                if provider == OCRProvider.AZURE_DI.value:
                    self.logger.info("🔄 Switching to PyMuPDF OCR handler as Azure OCR failed")
                    handler = OCRHandler(self.logger, OCRProvider.OCRMYPDF.value, config=self.config_service)
                    ocr_result = await handler.process_document(pdf_binary)
                else:
                    raise

            self.logger.debug("✅ OCR processing completed")

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

                block_containers = await parser.get_blocks_from_csv_result(csv_result, recordId, orgId, recordName, version, origin, llm)
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
                version=version,
                source=source,
                orgId=orgId,
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
            recordName, recordId, version, source, orgId, md_content, virtual_record_id
        )

        return {"status": "success", "message": "MDX processed successfully"}

    async def process_md_document(
        self, recordName, recordId, version, source, orgId, md_binary, virtual_record_id
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
            block_containers = await processor.load_document(f"{recordName}.md", md_bytes)
            if block_containers is False:
                raise Exception("Failed to process MD document. It might contain scanned pages.")

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
                version=version,
                source=source,
                orgId=orgId,
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

