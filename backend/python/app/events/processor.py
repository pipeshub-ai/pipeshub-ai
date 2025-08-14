import io
import json
from datetime import datetime

from app.config.constants.ai_models import (
    AzureDocIntelligenceModel,
    OCRProvider,
)
from app.config.constants.arangodb import (
    CollectionNames,
    ExtensionTypes,
)
from app.config.constants.service import config_node_constants
from app.config.utils.named_constants.arangodb_constants import MimeTypes
from app.models.entities import RecordType
from app.modules.parsers.pdf.ocr_handler import OCRHandler
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class Processor:
    def __init__(
        self,
        logger,
        config_service,
        domain_extractor,
        indexing_pipeline,
        arango_service,
        parsers,
    ) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing Processor")
        self.domain_extractor = domain_extractor
        self.indexing_pipeline = indexing_pipeline
        self.arango_service = arango_service
        self.parsers = parsers
        self.config_service = config_service

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

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

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

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

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

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data, merge_documents=False)

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
            # Convert binary to string
            html_content = (
                html_content.decode("utf-8")
                if isinstance(html_content, bytes)
                else html_content
            )
            self.logger.debug(f"📄 Decoded HTML content length: {len(html_content)}")

            # Initialize HTML parser and parse content
            self.logger.debug("📄 Processing HTML content")
            parser = self.parsers["html"]
            html_result = parser.parse_string(html_content)

            # Get the full document structure
            doc_dict = html_result.export_to_dict()
            self.logger.debug("📑 Document structure processed")

            # Process content in reading order
            self.logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    mail = await self.arango_service.get_document(
                        recordId, CollectionNames.MAILS.value
                    )
                    domain_metadata = {**record, **mail}
                    domain_metadata["extension"] = "html"
                    domain_metadata["mimeType"] = "text/html"
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    context = item["context"]

                    # Create context text from previous items
                    previous_context = " ".join(
                        [prev["text"].strip() for prev in context_window]
                    )

                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item["text"].strip(),
                    }

                    # Handle potentially None page numbers
                    context.get("pageNum")

                    sentence_data.append(
                        {
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockType": context.get("label", "text"),
                                "blockNum": [idx],
                                "blockText": json.dumps(full_context),
                                "virtualRecordId": virtual_record_id,
                            },
                        }
                    )

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)
            else:
                self.logger.info(" NO SENTENCES TO INDEX")
                record = await self.arango_service.get_document(
                    recordId, CollectionNames.RECORDS.value
                )
                record.update(
                    {
                        "indexingStatus": "COMPLETED",
                        "extractionStatus": "COMPLETED",
                        "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                        "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                        "virtualRecordId": virtual_record_id,
                        "isDirty": False
                    }
                )
                await self.arango_service.batch_upsert_nodes(
                    [record], CollectionNames.RECORDS.value
                )

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get("schema_name"),
                    "version": doc_dict.get("version"),
                    "name": doc_dict.get("name"),
                    "origin": doc_dict.get("origin"),
                },
                "structure_info": {
                    "text_count": len(doc_dict.get("texts", [])),
                    "group_count": len(doc_dict.get("groups", [])),
                    "list_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("list_info")
                        ]
                    ),
                    "heading_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("label") == "heading"
                        ]
                    ),
                },
            }

            self.logger.info("✅ HTML processing completed successfully")
            return {
                "html_result": {
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups", []),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing HTML document: {str(e)}")
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
                        self.logger, OCRProvider.OCRMYPDF.value
                    )
                    break

            if not handler:
                self.logger.debug("📚 Setting up PyMuPDF OCR handler")
                handler = OCRHandler(self.logger, OCRProvider.OCRMYPDF.value)
                provider = OCRProvider.OCRMYPDF.value

            # Process document
            self.logger.info("🔄 Processing document with OCR handler")
            ocr_result = await handler.process_document(pdf_binary)
            self.logger.debug("✅ OCR processing completed")

            # Extract domain metadata from paragraphs
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            paragraphs = ocr_result.get("paragraphs", [])
            sentences = ocr_result.get("sentences", [])
            if paragraphs:
                # Join all paragraph content with newlines
                paragraphs_text = "\n ".join(
                    p["content"].strip()
                    for p in paragraphs
                    if p.get("content") and p["content"].strip()
                )

                # Extract metadata using domain extractor
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        paragraphs_text, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = record
                    ocr_result["metadata"] = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None
                    ocr_result["metadata"] = None

            # Use the OCR-processed PDF for highlighting if available

            # Initialize containers
            self.logger.debug("🏗️ Initializing result containers")
            formatted_content = ""
            numbered_paragraphs = []

            # Process paragraphs for numbering and formatting
            self.logger.debug("📝 Processing paragraphs")
            paragraphs = ocr_result.get("paragraphs", [])
            for paragraph in paragraphs:
                paragraph["blockText"] = paragraph["content"]

            # Create sentence data for indexing
            sentence_data = []
            sentences = ocr_result.get("sentences", [])
            if sentences:
                self.logger.debug("📑 Creating semantic sentences")

                # Define block type mapping
                BLOCK_TYPE_MAP = {
                    0: "text",
                    1: "image",
                    2: "table",
                    3: "list",
                    4: "header",
                }

                # Prepare sentences for indexing with separated metadata
                sentence_data = [
                    {
                        "text": s["content"].strip(),
                        "metadata": {
                            **ocr_result.get("metadata"),
                            "recordId": recordId,
                            "blockText": s["block_text"],
                            "blockType": BLOCK_TYPE_MAP.get(s.get("block_type", 0)),
                            "blockNum": [int(s.get("block_number", 0))],
                            "pageNum": [int(s.get("page_number", 0))],
                            "bounding_box": s["bounding_box"],
                            "virtualRecordId": virtual_record_id,
                        },
                    }
                    for idx, s in enumerate(sentences)
                    if s.get("content")
                ]

            # Index sentences if available
            if sentence_data:
                pipeline = self.indexing_pipeline
                # Get chunks (these will be merged based on semantic similarity)
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "ocr_provider": provider,
                    "page_count": len(set(p.get("pageNum", 1) for p in paragraphs)),
                },
                "structure_info": {
                    "paragraph_count": len(paragraphs),
                    "sentence_count": len(sentences),
                    "average_confidence": (
                        sum(p.get("confidence", 1.0) for p in paragraphs)
                        / len(paragraphs)
                        if paragraphs
                        else 0
                    ),
                },
            }

            self.logger.info("✅ PDF processing completed successfully")
            return {
                "ocr_result": ocr_result,
                "formatted_content": formatted_content,
                "numbered_paragraphs": numbered_paragraphs,
                "metadata": metadata,
            }

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
            parser = self.parsers[ExtensionTypes.DOCX.value]
            docx_result = parser.parse(docx_binary)

            # Get the full document structure
            doc_dict = docx_result.export_to_dict()

            # Process content in reading order
            self.logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    self.logger.info("🎯 Extracting metadata from DOCX content")
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    context = item["context"]

                    # Create context text from previous items
                    previous_context = " ".join(
                        [prev["text"].strip() for prev in context_window]
                    )

                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item["text"].strip(),
                    }

                    sentence_data.append(
                        {
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockType": context.get("label", "text"),
                                "blockNum": [idx],
                                "blockText": json.dumps(full_context),
                                "virtualRecordId": virtual_record_id,
                            },
                        }
                    )

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get("schema_name"),
                    "version": doc_dict.get("version"),
                    "name": doc_dict.get("name"),
                    "origin": doc_dict.get("origin"),
                },
                "structure_info": {
                    "text_count": len(doc_dict.get("texts", [])),
                    "group_count": len(doc_dict.get("groups", [])),
                    "list_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("list_info")
                        ]
                    ),
                    "heading_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("label") == "heading"
                        ]
                    ),
                },
            }

            self.logger.info("✅ DOCX processing completed successfully")
            return {
                "docx_result": {
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups", []),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata,
            }

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
            llm = await get_llm(self.config_service)
            parser = self.parsers[ExtensionTypes.XLSX.value]
            excel_result = parser.parse(excel_binary)

            # Extract domain metadata from text content
            self.logger.info("🎯 Extracting domain metadata")
            if excel_result["text_content"]:
                try:
                    self.logger.info("🎯 Extracting metadata from Excel content")
                    metadata = await self.domain_extractor.extract_metadata(
                        excel_result["text_content"], orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    # Convert datetime objects to strings
                    domain_metadata = {
                        k: (v.isoformat() if isinstance(v, datetime) else v)
                        for k, v in {**record, **file}.items()
                    }
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Format content for output
            formatted_content = ""
            numbered_items = []
            sentence_data = []

            # Process each sheet
            self.logger.debug("📝 Processing sheets")
            for sheet_idx, sheet_name in enumerate(excel_result["sheet_names"], 1):
                sheet_data = await parser.process_sheet_with_summaries(llm, sheet_name)
                if sheet_data is None:
                    continue
                # Add sheet entry
                sheet_entry = {
                    "number": f"S{sheet_idx}",
                    "name": sheet_data["sheet_name"],
                    "type": "sheet",
                    "row_count": len(sheet_data["tables"]),
                    "column_count": max(
                        (len(table["headers"]) for table in sheet_data["tables"]),
                        default=0,
                    ),
                }
                numbered_items.append(sheet_entry)

                # Format content and sentence data
                formatted_content += f"\n[Sheet]: {sheet_data['sheet_name']}\n"

                for table in sheet_data["tables"]:
                    formatted_content += f"\nTable Summary: {table['summary']}\n"
                    for row in table["rows"]:
                        # Convert datetime objects in row_data to strings
                        row_data = {
                            k: (v.isoformat() if isinstance(v, datetime) else v)
                            for k, v in row["raw_data"].items()
                        }
                        formatted_content += f"Row Data: {row_data}\n"
                        formatted_content += (
                            f"Natural Text: {row['natural_language_text']}\n"
                        )

                        block_num = [int(row["row_num"])] if row["row_num"] else [0]

                        # Add processed rows to sentence data
                        sentence_data.append(
                            {
                                "text": row["natural_language_text"],
                                "metadata": {
                                    **(
                                        {
                                            k: (
                                                v.isoformat()
                                                if isinstance(v, datetime)
                                                else v
                                            )
                                            for k, v in domain_metadata.items()
                                        }
                                    ),
                                    "recordId": recordId,
                                    "sheetName": sheet_name,
                                    "sheetNum": sheet_idx,
                                    "blockNum": block_num,
                                    "blockType": "table_row",
                                    "blockText": json.dumps(row_data),
                                    "virtualRecordId": virtual_record_id,
                                },
                            }
                        )
            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data, merge_documents=False)
            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": {
                    k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in excel_result.get("metadata", {}).items()
                },
                "sheet_count": len(excel_result["sheets"]),
                "total_rows": excel_result["total_rows"],
                "total_cells": excel_result["total_cells"],
            }
            self.logger.info("✅ Excel processing completed successfully")
            return {
                "excel_result": excel_result,
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

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
        self, recordName, recordId, version, source, orgId, csv_binary, virtual_record_id
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

            llm = await get_llm(self.config_service)

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

                    self.logger.debug(
                        f"Successfully processed CSV with {encoding} encoding"
                    )
                    break
                except UnicodeDecodeError:
                    self.logger.debug(f"Failed to decode with {encoding} encoding")
                    continue
                except Exception as e:
                    self.logger.debug(f"Failed to process CSV with {encoding} encoding: {str(e)}")
                    continue

            if csv_result is None:
                raise ValueError(
                    "Unable to decode and process CSV file with any supported encoding"
                )

            self.logger.debug("📑 CSV result processed")

            # Extract domain metadata from CSV content
            self.logger.info("🎯 Extracting domain metadata")
            if csv_result:
                # Convert CSV data to text for metadata extraction
                csv_text = "\n".join(
                    [
                        " ".join(str(value) for value in row.values())
                        for row in csv_result
                    ]
                )

                try:
                    self.logger.info("🎯 Extracting metadata from CSV content")
                    metadata = await self.domain_extractor.extract_metadata(
                        csv_text, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Format content for output
            formatted_content = ""
            numbered_rows = []
            sentence_data = []

            # Process rows for formatting
            self.logger.debug("📝 Processing rows")
            batch_size = 20  # Define a suitable batch size
            for i in range(0, len(csv_result), batch_size):
                batch = csv_result[i : i + batch_size]
                row_texts = await parser.get_rows_text(llm, batch)

                for idx, (row, row_text) in enumerate(
                    zip(batch, row_texts), start=i + 1
                ):
                    row_entry = {"number": idx, "content": row, "type": "row"}
                    numbered_rows.append(row_entry)
                    formatted_content += f"[{idx}] {json.dumps(row)}\n"
                    formatted_content += f"Natural Text: {row_text}\n"

                    # Add sentence data for indexing
                    sentence_data.append(
                        {
                            "text": row_text,
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockType": "table_row",
                                "blockText": json.dumps(row),
                                "blockNum": [idx],
                                "virtualRecordId": virtual_record_id,
                            },
                        }
                    )

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data, merge_documents=False)

            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "row_count": len(csv_result),
                "column_count": len(csv_result[0]) if csv_result else 0,
                "columns": list(csv_result[0].keys()) if csv_result else [],
            }

            self.logger.info("✅ CSV processing completed successfully")
            return {
                "csv_result": csv_result,
                "formatted_content": formatted_content,
                "numbered_rows": numbered_rows,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing CSV document: {str(e)}")
            raise

    def _process_content_in_order(self, doc_dict) -> list:
        """
        Process document content in proper reading order by following references.

        Args:
            doc_dict (dict): The document dictionary from Docling

        Returns:
            list: Ordered list of text items with their context
        """
        ordered_items = []
        processed_refs = set()

        def process_item(ref, level=0, parent_context=None) -> None:
            """Recursively process items following references"""
            if isinstance(ref, dict):
                ref_path = ref.get("$ref", "")
            else:
                ref_path = ref

            if not ref_path or ref_path in processed_refs:
                return
            processed_refs.add(ref_path)

            if not ref_path.startswith("#/"):
                return

            path_parts = ref_path[2:].split("/")
            item_type = path_parts[0]  # 'texts', 'groups', etc.
            try:
                item_index = int(path_parts[1])
            except (IndexError, ValueError):
                return

            items = doc_dict.get(item_type, [])
            if item_index >= len(items):
                return
            item = items[item_index]

            # Get page number from the item's page reference
            page_no = None
            if "prov" in item:
                prov = item["prov"]
                if isinstance(prov, list) and len(prov) > 0:
                    # Take the first page number from the prov list
                    page_no = prov[0].get("page_no")
                elif isinstance(prov, dict) and "$ref" in prov:
                    # Handle legacy reference format if needed
                    page_path = prov["$ref"]
                    page_index = int(page_path.split("/")[-1])
                    pages = doc_dict.get("pages", [])
                    if page_index < len(pages):
                        page_no = pages[page_index].get("page_no")

            # Create context for current item
            current_context = {
                "ref": item.get("self_ref"),
                "label": item.get("label"),
                "level": item.get("level"),
                "parent_context": parent_context,
                "slide_number": item.get("slide_number"),
                "pageNum": page_no,  # Add page number to context
            }

            if item_type == "texts":
                ordered_items.append(
                    {"text": item.get("text", ""), "context": current_context}
                )

            # Process children with current_context as parent
            children = item.get("children", [])
            for child in children:
                process_item(child, level + 1, current_context)

        # Start processing from body
        body = doc_dict.get("body", {})
        for child in body.get("children", []):
            process_item(child)

        self.logger.debug(f"Processed {len(ordered_items)} items in order")
        return ordered_items

    async def process_html_document(
        self, recordName, recordId, version, source, orgId, html_content, virtual_record_id, origin, recordType
    ) -> None:
        """Process HTML document and extract structured content"""
        self.logger.info(
            f"🚀 Starting HTML document processing for record: {recordName}"
        )

        try:
            # Convert binary to string
            html_content = (
                html_content.decode("utf-8")
                if isinstance(html_content, bytes)
                else html_content
            )
            self.logger.debug(f"📄 Decoded HTML content length: {len(html_content)}")

            # Initialize HTML parser and parse content
            self.logger.debug("📄 Processing HTML content")
            parser = self.parsers[ExtensionTypes.HTML.value]
            html_result = parser.parse_string(html_content)

            # Get the full document structure
            doc_dict = html_result.export_to_dict()
            self.logger.debug("📑 Document structure processed")

            # Process content in reading order
            self.logger.debug("📑 Processing document structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    self.logger.info("🎯 Extracting metadata from HTML content")
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    if recordType == RecordType.FILE.value:
                        file = await self.arango_service.get_document(
                            recordId, CollectionNames.FILES.value
                        )
                        domain_metadata = {**record, **file}
                    else:
                        domain_metadata = record

                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    context = item["context"]

                    # Create context text from previous items
                    previous_context = " ".join(
                        [prev["text"].strip() for prev in context_window]
                    )

                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item["text"].strip(),
                    }

                    sentence_data.append(
                        {
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordName": recordName,
                                "recordId": recordId,
                                "blockType": context.get("label", "text"),
                                "blockNum": [idx],
                                "blockText": json.dumps(full_context),
                                "virtualRecordId": virtual_record_id,
                                "mimeType": MimeTypes.HTML.value,
                                "connectorName": source,
                                "origin": origin,
                                "recordType": recordType,
                            },
                        }
                    )

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get("schema_name"),
                    "version": doc_dict.get("version"),
                    "name": doc_dict.get("name"),
                    "origin": doc_dict.get("origin"),
                },
                "structure_info": {
                    "text_count": len(doc_dict.get("texts", [])),
                    "group_count": len(doc_dict.get("groups", [])),
                    "list_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("list_info")
                        ]
                    ),
                    "heading_count": len(
                        [
                            item
                            for item in ordered_content
                            if item.get("context", {}).get("label") == "heading"
                        ]
                    ),
                },
            }

            self.logger.info("✅ HTML processing completed successfully")
            return {
                "html_result": {
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups", []),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata,
            }

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
            md_content = md_binary.decode("utf-8")

            # Initialize Markdown parser
            self.logger.debug("📄 Processing Markdown content")
            parser = self.parsers[ExtensionTypes.MD.value]
            md_result = parser.parse_string(md_content)
            # Get the full document structure
            doc_dict = md_result.export_to_dict()

            # Extract text content from all text elements
            text_content = "\n".join(
                text_item.get("text", "").strip()
                for text_item in doc_dict.get("texts", [])
                if text_item.get("text", "").strip()
            )

            # Extract domain metadata from content
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Format content for output
            formatted_content = ""
            numbered_items = []

            # Process text items for numbering and formatting
            self.logger.debug("📝 Processing text items")
            for idx, item in enumerate(doc_dict.get("texts", []), 1):
                if item.get("text", "").strip():
                    # Create item entry with metadata
                    item_entry = {
                        "number": idx,
                        "content": item["text"].strip(),
                        "type": item.get("label", "text"),
                        "level": item.get("level"),
                        "parent_ref": item.get("parent", {}).get("$ref"),
                        "children_refs": [
                            child.get("$ref") for child in item.get("children", [])
                        ],
                        "code_language": (
                            item.get("language")
                            if item.get("label") == "code"
                            else None
                        ),
                    }
                    numbered_items.append(item_entry)
                    formatted_content += f"[{idx}] {item['text'].strip()}\n\n"

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(doc_dict.get("texts", []), 1):
                # Create context text from previous items
                previous_context = " ".join(
                    [prev.get("text", "").strip() for prev in context_window]
                )

                # Current item's context with previous items
                full_context = {
                    "previous": previous_context,
                    "current": item["text"].strip(),
                }

                sentence_data.append(
                    {
                        "text": item["text"].strip(),
                        "metadata": {
                            **(domain_metadata or {}),
                            "recordId": recordId,
                            "blockType": item.get("label", "text"),
                            "blockNum": [idx],
                            "blockText": json.dumps(full_context),
                            "virtualRecordId": virtual_record_id,
                            "codeLanguage": (
                                item.get("language")
                                if item.get("label") == "code"
                                else None
                            ),
                        },
                    }
                )

                # Update context window
                context_window.append(item)
                if len(context_window) > context_window_size:
                    context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            self.logger.debug("📋 Preparing metadata")
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get("schema_name"),
                    "version": doc_dict.get("version"),
                    "name": doc_dict.get("name"),
                    "origin": doc_dict.get("origin"),
                },
                "structure_info": {
                    "text_count": len(doc_dict.get("texts", [])),
                    "group_count": len(doc_dict.get("groups", [])),
                    "table_count": len(doc_dict.get("tables", [])),
                    "code_block_count": len(
                        [
                            item
                            for item in doc_dict.get("texts", [])
                            if item.get("label") == "code"
                        ]
                    ),
                    "heading_count": len(
                        [
                            item
                            for item in doc_dict.get("texts", [])
                            if item.get("label") == "heading"
                        ]
                    ),
                },
            }

            self.logger.info("✅ Markdown processing completed successfully")
            return {
                "md_result": {
                    "items": numbered_items,
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups"),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

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

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    text_content, orgId
                )
                record = await self.domain_extractor.save_metadata_to_db(
                    orgId, recordId, metadata, virtual_record_id
                )
                file = await self.arango_service.get_document(
                    recordId, CollectionNames.FILES.value
                )
                domain_metadata = {**record, **file}
            except Exception as e:
                self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                domain_metadata = None

            # Split content into blocks (paragraphs)
            blocks = [
                block.strip() for block in text_content.split("\n\n") if block.strip()
            ]

            # Format content and create numbered items
            formatted_content = ""
            numbered_items = []
            sentence_data = []

            # Keep track of previous blocks for context
            context_window = []
            context_window_size = 3

            for idx, block in enumerate(blocks, 1):
                # Create numbered item
                item_entry = {"number": idx, "content": block, "type": "paragraph"}
                numbered_items.append(item_entry)
                formatted_content += f"[{idx}] {block}\n\n"

                # Create context from previous blocks
                previous_context = " ".join([prev for prev in context_window])

                # Current block's context with previous blocks
                full_context = {"previous": previous_context, "current": block}

                # Add to sentence data for indexing
                sentence_data.append(
                    {
                        "text": block,
                        "metadata": {
                            **(domain_metadata or {}),
                            "recordName": recordName,
                            "orgId": orgId,
                            "recordId": recordId,
                            "blockType": "text",
                            "blockNum": [idx],
                            "blockText": json.dumps(full_context),
                            "virtualRecordId": virtual_record_id,
                            "recordType": recordType,
                            "connectorName": connectorName,
                            "origin": origin,
                            "mimeType": MimeTypes.PLAIN_TEXT.value,
                        },
                    }
                )

                # Update context window
                context_window.append(block)
                if len(context_window) > context_window_size:
                    context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "encoding": encoding,
                    "size": len(text_content),
                    "line_count": text_content.count("\n") + 1,
                },
                "structure_info": {
                    "paragraph_count": len(blocks),
                    "character_count": len(text_content),
                    "word_count": len(text_content.split()),
                },
            }

            self.logger.info("✅ TXT processing completed successfully")
            return {
                "txt_result": {"content": text_content, "metadata": domain_metadata},
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

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
            parser = self.parsers[ExtensionTypes.PPTX.value]
            pptx_result = parser.parse_binary(pptx_binary)

            # Get the full document structure
            doc_dict = pptx_result.export_to_dict()

            # Log structure counts
            self.logger.debug("📊 Document structure counts:")
            self.logger.debug(f"- Texts: {len(doc_dict.get('texts', []))}")
            self.logger.debug(f"- Groups: {len(doc_dict.get('groups', []))}")
            self.logger.debug(f"- Pictures: {len(doc_dict.get('pictures', []))}")

            # Process content in reading order
            ordered_items = []
            processed_refs = set()

            def process_item(ref, level=0, parent_context=None) -> None:
                if isinstance(ref, dict):
                    ref_path = ref.get("$ref", "")
                else:
                    ref_path = ref

                if not ref_path or ref_path in processed_refs:
                    return
                processed_refs.add(ref_path)

                if not ref_path.startswith("#/"):
                    return

                path_parts = ref_path[2:].split("/")
                item_type = path_parts[0]
                try:
                    item_index = int(path_parts[1])
                except (IndexError, ValueError):
                    return

                self.logger.debug(f"item_type: {item_type}")

                items = doc_dict.get(item_type, [])
                if item_index >= len(items):
                    return
                item = items[item_index]

                # Get page number from the item's page reference
                page_no = None
                if "prov" in item:
                    prov = item["prov"]
                    if isinstance(prov, list) and len(prov) > 0:
                        # Take the first page number from the prov list
                        page_no = prov[0].get("page_no")
                    elif isinstance(prov, dict) and "$ref" in prov:
                        # Handle legacy reference format if needed
                        page_path = prov["$ref"]
                        page_index = int(page_path.split("/")[-1])
                        pages = doc_dict.get("pages", [])
                        if page_index < len(pages):
                            page_no = pages[page_index].get("page_no")

                # Create context for current item
                current_context = {
                    "ref": item.get("self_ref"),
                    "label": item.get("label"),
                    "level": item.get("level"),
                    "parent_context": parent_context,
                    "pageNum": page_no,  # Add page number to context
                }

                if item_type == "texts":
                    ordered_items.append(
                        {"text": item.get("text", ""), "context": current_context}
                    )

                children = item.get("children", [])
                for child in children:
                    process_item(child, level + 1, current_context)

            # Start processing from body
            body = doc_dict.get("body", {})
            for child in body.get("children", []):
                process_item(child)

            # Extract text content from ordered items
            text_content = "\n".join(
                item["text"].strip() for item in ordered_items if item["text"].strip()
            )

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}

                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create numbered items with slide information
            numbered_items = []
            formatted_content = ""

            for idx, item in enumerate(ordered_items, 1):
                if item["text"].strip():
                    context = item["context"]
                    item_entry = {
                        "number": idx,
                        "content": item["text"].strip(),
                        "type": context.get("label", "text"),
                        "level": context.get("level"),
                        "ref": context.get("ref"),
                        "parent_ref": context.get("parent_context", {}).get("ref"),
                        "pageNum": context.get("pageNum"),
                    }
                    numbered_items.append(item_entry)

                    # Format with slide numbers
                    slide_info = (
                        f"[Slide {context.get('slide_number', '?')}] "
                        if context.get("slide_number")
                        else ""
                    )
                    formatted_content += f"{slide_info}[{idx}] {item['text'].strip()}\n"

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            # Keep track of previous items for context
            context_window = []
            context_window_size = 3  # Number of previous items to include for context

            for idx, item in enumerate(ordered_items, 1):
                if item["text"].strip():
                    context = item["context"]

                    # Create context text from previous items
                    previous_context = " ".join(
                        [prev["text"].strip() for prev in context_window]
                    )

                    # Current item's context with previous items
                    full_context = {
                        "previous": previous_context,
                        "current": item["text"].strip(),
                    }
                    pageNum = context.get("pageNum")
                    pageNum = int(pageNum) if pageNum else None

                    sentence_data.append(
                        {
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockType": context.get("label", "text"),
                                "blockNum": [idx],
                                "blockText": json.dumps(full_context),
                                "pageNum": [pageNum],
                                "virtualRecordId": virtual_record_id,
                            },
                        }
                    )

                    # Update context window
                    context_window.append(item)
                    if len(context_window) > context_window_size:
                        context_window.pop(0)

            # Index sentences if available
            if sentence_data:
                self.logger.debug("📑 Indexing %s sentences", len(sentence_data))
                pipeline = self.indexing_pipeline
                await pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "schema_name": doc_dict.get("schema_name"),
                    "version": doc_dict.get("version"),
                    "name": doc_dict.get("name"),
                    "origin": doc_dict.get("origin"),
                },
                "structure_info": {
                    "text_count": len(doc_dict.get("texts", [])),
                    "group_count": len(doc_dict.get("groups", [])),
                    "picture_count": len(doc_dict.get("pictures", [])),
                    "slide_count": len(
                        set(
                            item["context"].get("slide_number")
                            for item in ordered_items
                            if item["context"].get("slide_number")
                        )
                    ),
                },
            }

            self.logger.info("✅ PPTX processing completed successfully")
            return {
                "pptx_result": {
                    "items": numbered_items,
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups"),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

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
