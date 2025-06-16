import json
import os
from datetime import datetime
from uuid import uuid4

import spacy
from spacy.language import Language

from app.config.configuration_service import config_node_constants
from app.config.utils.named_constants.ai_models_named_constants import (
    AzureDocIntelligenceModel,
    OCRProvider,
)
from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    ExtensionTypes,
)
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
    ):
        self.logger = logger
        self.logger.info("🚀 Initializing Processor")
        self.domain_extractor = domain_extractor
        self.indexing_pipeline = indexing_pipeline
        self.arango_service = arango_service
        self.parsers = parsers
        self.config_service = config_service
        self.nlp = None

    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc):
        for token in doc[:-1]:  # Avoid out-of-bounds errors
            next_token = doc[token.i + 1]

            # If token is a number and followed by a period, don't treat it as a sentence boundary
            if token.like_num and next_token.text == ".":
                next_token.is_sent_start = False

            # Handle common abbreviations
            elif (
                token.text.lower()
                in [
                    "mr",
                    "mrs",
                    "dr",
                    "ms",
                    "prof",
                    "sr",
                    "jr",
                    "inc",
                    "ltd",
                    "co",
                    "etc",
                    "vs",
                    "fig",
                    "et",
                    "al",
                    "e.g",
                    "i.e",
                    "vol",
                    "pg",
                    "pp",
                ]
                and next_token.text == "."
            ):
                next_token.is_sent_start = False

            # Handle bullet points and list markers
            elif (
                # Numeric bullets with period (1., 2., etc)
                (
                    token.like_num and next_token.text == "." and len(token.text) <= 2
                )  # Limit to 2 digits
                or
                # Letter bullets with period (a., b., etc)
                (
                    len(token.text) == 1
                    and token.text.isalpha()
                    and next_token.text == "."
                )
                or
                # Common bullet point markers
                token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
            ):
                next_token.is_sent_start = False

            # Check for potential headings (all caps or title case without period)
            elif (
                # All caps text likely a heading
                token.text.isupper()
                and len(token.text) > 1  # Avoid single letters
                and not any(c.isdigit() for c in token.text)  # Avoid serial numbers
            ):
                if next_token.i < len(doc) - 1:
                    next_token.is_sent_start = False

            # Handle ellipsis (...) - don't split
            elif token.text == "." and next_token.text == ".":
                next_token.is_sent_start = False

        return doc

    def _create_custom_tokenizer(self, nlp):
        """
        Creates a custom tokenizer that handles special cases for sentence boundaries.
        """
        # Add the custom rule to the pipeline
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")

        # Add custom sentence boundary detection
        nlp.add_pipe("custom_sentence_boundary", after="sentencizer")

        # Configure the tokenizer to handle special cases
        special_cases = {
            "e.g.": [{"ORTH": "e.g."}],
            "i.e.": [{"ORTH": "i.e."}],
            "etc.": [{"ORTH": "etc."}],
            "...": [{"ORTH": "..."}],
        }

        for case, mapping in special_cases.items():
            nlp.tokenizer.add_special_case(case, mapping)

        return nlp

    async def _process_document_blocks(self, block_sections, text_blocks, record_id, org_id, virtual_record_id):
        """Process document blocks and extract metadata"""
        processed_blocks = []
        block_documents = []
        block_id_map = {}  # Map section index to block ID
        BLOCK_TYPE_MAP = {
            0: "text",
            1: "image",
            2: "table",
            3: "list",
            4: "header",
        }

        self.logger.info("Text blocks: %s", text_blocks)
        # First loop - create and batch upsert blocks
        for idx, section in enumerate(block_sections):
            block_id = str(uuid4())
            block_id_map[idx] = block_id  # Store mapping of section index to block ID

            # Combine text from blocks in the section
            block_numbers = list(range(section["start_block"], section["end_block"] + 1))
            block_type = BLOCK_TYPE_MAP.get(section.get("block_type")) or section.get("block_type", "text")
            # Collect texts and bounding boxes
            section_texts = []
            bounding_boxes = []
            page_numbers = set()

            for block_num in block_numbers:
                if block_num in text_blocks:
                    block = text_blocks[block_num]
                    section_texts.append(block["text"])

                    # Collect bounding box if available
                    if "context" in block and "bounding_box" in block["context"]:
                        bounding_boxes.append(block["context"]["bounding_box"])

                    # Track page numbers
                    if "context" in block and "page_number" in block["context"]:
                        page_numbers.add(block["context"]["page_number"])

            self.logger.info(f"Section texts for record id {record_id}: {section_texts}")
            section_text = "\n".join(section_texts)
            self.logger.info(f"Section text for record id {record_id}: {section_text}")

            # Combine bounding boxes if available
            combined_bounding_box = None
            next_page_bounding_boxes = {}
            if bounding_boxes and page_numbers:
                self.logger.info(f"🚀 Combining bounding boxes for section {section['start_block']}-{section['end_block']}")

                # Group bounding boxes by page number
                boxes_by_page = {}
                for i, box in enumerate(bounding_boxes):
                    block_num = block_numbers[i]
                    self.logger.info(f"Block num: {block_num}")
                    self.logger.info(f"Text block: {idx}: {text_blocks[block_num]}")
                    page_num = text_blocks[block_num]["context"]["page_number"]
                    boxes_by_page.setdefault(page_num, []).append(box)

                # Sort page numbers to ensure consistent processing
                sorted_pages = sorted(boxes_by_page.keys())

                for page_num in sorted_pages:
                    page_boxes = boxes_by_page[page_num]
                    # Find the extremes of bounding boxes for this page
                    x_coords = []
                    y_coords = []

                    for box in page_boxes:
                        for point in box:
                            if isinstance(point, dict) and 'x' in point and 'y' in point:
                                x_coords.append(point['x'])
                                y_coords.append(point['y'])

                    if x_coords and y_coords:
                        merged_box = [
                            {'x': min(x_coords), 'y': min(y_coords)},  # Top-left
                            {'x': max(x_coords), 'y': min(y_coords)},  # Top-right
                            {'x': max(x_coords), 'y': max(y_coords)},  # Bottom-right
                            {'x': min(x_coords), 'y': max(y_coords)}   # Bottom-left
                        ]

                        # First page goes to combined_bounding_box
                        if page_num == sorted_pages[0]:
                            combined_bounding_box = merged_box
                        # Subsequent pages go to next_page_bounding_boxes
                        else:
                            next_page_bounding_boxes[str(page_num)] = merged_box

            # Create block document
            block_doc = {
                "_key": block_id,
                "blockNum": block_numbers,
                "orgId": org_id,
                "blockType": block_type,
                "virtualRecordId": virtual_record_id,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "boundingBox": combined_bounding_box,
                "nextPageBoundingBoxes": next_page_bounding_boxes if next_page_bounding_boxes else None,
                "pageNum": sorted(list(page_numbers)) if page_numbers else None
            }
            block_documents.append(block_doc)

        # Batch upsert all blocks
        await self.arango_service.batch_upsert_nodes(
            block_documents,
            CollectionNames.BLOCKS.value,
        )

        # Get record metadata once for all blocks
        record = await self.arango_service.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        doc = dict(record)

        # Second loop - process metadata using the mapped block IDs
        for idx, section in enumerate(block_sections):
            block_id = block_id_map[idx]  # Get the corresponding block ID
            block_doc = next(doc for doc in block_documents if doc["_key"] == block_id)

            # Combine text from blocks in the section
            block_numbers = list(range(section["start_block"], section["end_block"] + 1))
            section_texts = []
            for block_num in block_numbers:
                if block_num in text_blocks:
                    block = text_blocks[block_num]
                    section_texts.append(block["text"])

            section_text = "\n".join(section_texts)

            # Extract and save metadata for the block
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    section_text, org_id
                )
                entities = await self.domain_extractor.extract_entities(
                    section_text, org_id
                )

                block_metadata = await self.domain_extractor.save_metadata_to_db(
                    org_id,
                    block_id,
                    metadata,
                    virtual_record_id,
                    entities = entities,
                    collection_name=CollectionNames.BLOCKS.value
                )
                block_doc.update({
                    "text": section_text,
                    **block_metadata,
                    **doc
                })

                processed_blocks.append(block_doc)

            except Exception as e:
                self.logger.error(f"❌ Error processing block metadata: {str(e)}")
                processed_blocks.append(block_doc)

        return processed_blocks

    async def _process_blocks_for_sheets(self, blocks, record_id, org_id, virtual_record_id):
        """Process blocks for sheet-type documents and extract metadata

        Args:
            blocks (list): List of blocks with text and metadata
            record_id (str): ID of the record
            org_id (str): Organization ID
            virtual_record_id (str): Virtual record ID

        Returns:
            list: List of processed blocks with metadata and storage info
        """
        processed_blocks = []
        block_documents = []
        block_id_map = {}  # Map block index to block ID

        # First loop - create and batch upsert blocks
        for idx, block in enumerate(blocks):
            block_id = str(uuid4())
            block_id_map[idx] = block_id

            # Create block document
            block_doc = {
                "_key": block_id,
                "blockNum": block["metadata"]["blockNum"],
                "orgId": org_id,
                "blockType": block["metadata"]["blockType"],
                "virtualRecordId": virtual_record_id,
                "createdAtTimestamp": get_epoch_timestamp_in_ms()
            }

            # Add sheet-specific metadata if available
            if "sheetName" in block["metadata"]:
                block_doc["sheetName"] = block["metadata"]["sheetName"]
            if "sheetNum" in block["metadata"]:
                block_doc["sheetNum"] = block["metadata"]["sheetNum"]

            block_documents.append(block_doc)

        # Batch upsert all blocks
        await self.arango_service.batch_upsert_nodes(
            block_documents,
            CollectionNames.BLOCKS.value,
        )

        # Get record metadata once for all blocks
        record = await self.arango_service.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        doc = dict(record)

        # Second loop - process metadata using the mapped block IDs
        for idx, block in enumerate(blocks):
            block_id = block_id_map[idx]
            block_doc = next(doc for doc in block_documents if doc["_key"] == block_id)

            # Extract and save metadata for the block
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    block["text"], org_id
                )
                block_metadata = await self.domain_extractor.save_metadata_to_db(
                    org_id,
                    block_id,
                    metadata,
                    virtual_record_id,
                    collection_name=CollectionNames.BLOCKS.value
                )
                block_doc.update({
                    "text": block["text"],
                    **block_metadata,
                    **doc
                })

                processed_blocks.append(block_doc)

            except Exception as e:
                self.logger.error(f"❌ Error processing block metadata: {str(e)}")
                processed_blocks.append(block_doc)

        return processed_blocks


    async def process_google_slides(self, record_id, record_version, orgId, content, virtual_record_id):
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
            presentation_data = content
            if not presentation_data:
                raise Exception("Failed to process presentation")

            # Process content in reading order
            self.logger.debug("📑 Processing presentation structure in reading order")
            ordered_content = []

            for slide in presentation_data["slides"]:
                slide_number = slide["slideNumber"]

                # Process each element in the slide
                for element in slide["elements"]:
                    if element["type"] == "shape":
                        text = element["text"]["content"].strip()
                        if text:
                            ordered_content.append({
                                "text": text,
                                "context": {
                                    "type": "shape",
                                    "label": "slide_text",
                                    "slide_number": slide_number,
                                    "element_id": element["id"],
                                    "total_slides": slide["totalSlides"],
                                    "layout": slide["layout"],
                                    "master_object_id": slide["masterObjectId"]
                                }
                            })
                    elif element["type"] == "table":
                        # Handle table as a block first
                        table_text = []
                        for cell in element["cells"]:
                            cell_text = cell["text"]["content"].strip()
                            if cell_text:
                                table_text.append(cell_text)

                        if table_text:
                            ordered_content.append({
                                "text": "\n".join(table_text),
                                "context": {
                                    "type": "table",
                                    "label": "slide_table",
                                    "slide_number": slide_number,
                                    "element_id": element["id"],
                                    "total_slides": slide["totalSlides"]
                                }
                            })

                            # Add individual cells as separate entries
                            for cell in element["cells"]:
                                cell_text = cell["text"]["content"].strip()
                                if cell_text:
                                    ordered_content.append({
                                        "text": cell_text,
                                        "context": {
                                            "type": "table_cell",
                                            "label": "slide_table_cell",
                                            "slide_number": slide_number,
                                            "element_id": element["id"],
                                            "row_index": cell["rowIndex"],
                                            "column_index": cell["columnIndex"],
                                            "total_slides": slide["totalSlides"]
                                        }
                                    })

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": item["context"]
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing presentation structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                record_id,
                orgId,
                virtual_record_id
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
                        orgId, record_id, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        record_id, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings and mapping
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": record_id,
                        "blockId": block["_key"],
                        "blockType": "slide_section",
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data and maintain slides formatting
            sentence_data = []
            numbered_items = []
            formatted_content = ""
            current_slide = None

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    matching_block = block_number_mapping.get(idx)
                    context = item["context"]
                    slide_number = context["slide_number"]

                    # Add slide header if we're on a new slide
                    if current_slide != slide_number:
                        current_slide = slide_number
                        formatted_content += f"\n[Slide {slide_number}]\n"

                    if matching_block:
                        # Add to sentence data
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": record_id,
                                "blockId": matching_block["_key"],
                                "blockType": context["type"],
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False,
                                "slideNumber": slide_number,
                                "totalSlides": context["total_slides"],
                                "elementId": context["element_id"],
                                **({"rowIndex": context["row_index"], "columnIndex": context["column_index"]}
                                   if context["type"] == "table_cell" else {})
                            }
                        })

                    # Maintain slides specific formatting
                    numbered_items.append({
                        "number": f"S{slide_number}-{idx}",
                        "type": context["type"],
                        "content": item["text"].strip(),
                        "slide_number": slide_number,
                        **{k: v for k, v in context.items() if k not in ["type", "label", "slide_number"]}
                    })

                    formatted_content += f"{item['text'].strip()}\n"
                    if context["type"] == "table":
                        formatted_content += f"[Table in Slide {slide_number}]\n"

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "domain_metadata": domain_metadata,
                "recordId": record_id,
                "version": record_version,
                "presentation_metadata": presentation_data["metadata"],
                "structure_info": {
                    "total_slides": presentation_data["summary"]["totalSlides"],
                    "has_notes": presentation_data["summary"]["hasNotes"],
                    "text_count": len([i for i in ordered_content if i["context"]["type"] == "shape"]),
                    "table_count": len([i for i in ordered_content if i["context"]["type"] == "table"]),
                    "block_count": len(block_sections) if block_sections else 0
                }
            }

            self.logger.info("✅ Google Slides processing completed successfully")
            return {
                "presentation_data": presentation_data,
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing Google Slides presentation: {str(e)}")
            raise

    async def process_google_docs(self, record_id, record_version, orgId, content, virtual_record_id):
        """Process Google Docs document and extract structured content"""
        self.logger.info(f"🚀 Starting Google Docs processing for record: {record_id}")

        try:
            # Initialize Google Docs parser
            self.logger.debug("📄 Processing Google Docs content")
            all_content = content.get("all_content", [])
            headers = content.get("headers", [])
            footers = content.get("footers", [])

            # Process content in reading order
            self.logger.debug("📑 Processing document structure in reading order")
            ordered_content = []
            for idx, item in enumerate(all_content, 1):
                if item["type"] == "paragraph":
                    ordered_content.append({
                        "text": item["content"]["text"].strip(),
                        "context": {
                            "type": "paragraph",
                            "label": "text",
                            "style": item["content"].get("style", {}),
                            "links": item["content"].get("links", []),
                            "start_index": item["start_index"],
                            "end_index": item["end_index"]
                        }
                    })
                elif item["type"] == "table":
                    # Handle table as a block first
                    table = item["content"]
                    table_text = []
                    for cell in table["cells"]:
                        cell_text = " ".join(cell["content"]).strip()
                        if cell_text:
                            table_text.append(cell_text)

                    ordered_content.append({
                        "text": "\n".join(table_text),
                        "context": {
                            "type": "table",
                            "label": "table",
                            "rows": table["rows"],
                            "columns": table["columns"],
                            "start_index": item["start_index"],
                            "end_index": item["end_index"]
                        }
                    })

                    # Add individual cells as separate entries
                    for cell in table["cells"]:
                        cell_text = " ".join(cell["content"]).strip()
                        if cell_text:
                            ordered_content.append({
                                "text": cell_text,
                                "context": {
                                    "type": "table_cell",
                                    "label": "table_cell",
                                    "row": cell["row"],
                                    "column": cell["column"],
                                    "start_index": cell["start_index"],
                                    "end_index": cell["end_index"]
                                }
                            })

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": item["context"]
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                record_id,
                orgId,
                virtual_record_id
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
                        orgId, record_id, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        record_id, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings and mapping
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": record_id,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data for indexing
            sentence_data = []
            numbered_items = []
            formatted_content = ""

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    matching_block = block_number_mapping.get(idx)
                    context = item["context"]

                    if matching_block:
                        # Add to sentence data
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": record_id,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False,
                                "start_index": context.get("start_index"),
                                "end_index": context.get("end_index"),
                                "type": context.get("type"),
                                **({"row": context["row"], "column": context["column"]}
                                   if context["type"] == "table_cell" else {})
                            }
                        })

                    # Maintain Google Docs specific formatting
                    if context["type"] == "paragraph":
                        numbered_items.append({
                            "number": idx,
                            "content": item["text"].strip(),
                            "type": "paragraph",
                            "style": context.get("style", {}),
                            "links": context.get("links", []),
                            "start_index": context.get("start_index"),
                            "end_index": context.get("end_index")
                        })
                        formatted_content += f"[{idx}] {item['text'].strip()}\n\n"
                    elif context["type"] == "table":
                        numbered_items.append({
                            "number": f"T{idx}",
                            "content": item["text"],
                            "type": "table",
                            "rows": context["rows"],
                            "columns": context["columns"],
                            "start_index": context["start_index"],
                            "end_index": context["end_index"]
                        })
                        formatted_content += f"[T{idx}] Table ({context['rows']}x{context['columns']})\n\n"

            # Index both blocks and sentences (same as DOCX)
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

            # Prepare metadata (combining DOCX structure with Google Docs specifics)
            metadata = {
                "domain_metadata": domain_metadata,
                "recordId": record_id,
                "version": record_version,
                "has_header": bool(headers),
                "has_footer": bool(footers),
                "structure_info": {
                    "text_count": len([i for i in ordered_content if i["context"]["type"] == "paragraph"]),
                    "table_count": len([i for i in ordered_content if i["context"]["type"] == "table"]),
                    "block_count": len(block_sections) if block_sections else 0,
                    "image_count": len([i for i in all_content if i["type"] == "image"])
                }
            }

            self.logger.info("✅ Google Docs processing completed successfully")
            return {
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing Google Docs document: {str(e)}")
            raise

    async def process_google_sheets(self, record_id, record_version, orgId, content, virtual_record_id):
        self.logger.info("🚀 Processing Google Sheets")
        try:
            # Initialize Google Docs parser
            self.logger.debug("📄 Processing Google Sheets content")

            nlp = spacy.load("en_core_web_sm")
            self.nlp = self._create_custom_tokenizer(nlp)  # Apply custom tokenization rules

            all_sheets_result = content["all_sheet_results"]
            content["parsed_result"]

            combined_texts = []
            row_counter = 1
            domain_metadata = None
            block_data = []
            sentence_data = []

            # Build combined text for metadata extraction
            for sheet_result in all_sheets_result:
                for table in sheet_result["tables"]:
                    for row in table["rows"]:
                        combined_texts.append(
                            f"{row_counter}. {row['natural_language_text']}"
                        )
                        row_counter += 1

            # Extract metadata from combined text
            combined_text = "\n".join(combined_texts)

            # Process each sheet and create blocks and sentences
            for sheet_idx, sheet_result in enumerate(all_sheets_result, 1):
                self.logger.info(f"sheet_name: {sheet_result['sheet_name']}")
                for table in sheet_result["tables"]:
                    for row in table["rows"]:
                        row_data = {
                            k: (v.isoformat() if isinstance(v, datetime) else v)
                            for k, v in row["raw_data"].items()
                        }
                        block_num = [int(row["row_num"])] if row["row_num"] else [0]

                        # Common metadata for both block and sentences
                        shared_metadata = {
                            "recordId": record_id,
                            "sheetName": sheet_result["sheet_name"],
                            "sheetNum": sheet_idx,
                            "blockNum": block_num,
                            "blockType": "table_row",
                            "blockText": json.dumps(row_data),
                            "virtualRecordId": virtual_record_id,
                        }

                        # Add block data
                        block_data.append({
                            "text": row["natural_language_text"],
                            "metadata": {
                                **shared_metadata,
                                "isBlock": True,
                            }
                        })

                        # Split text into sentences using spaCy
                        doc = self.nlp(row["natural_language_text"])
                        for sent in doc.sents:
                            sentence_text = sent.text.strip()
                            if sentence_text:
                                sentence_data.append({
                                    "text": sentence_text,
                                    "metadata": {
                                        **shared_metadata,
                                        "isBlock": False,
                                    }
                                })

            # Process blocks first to create records and extract metadata
            if block_data:
                processed_blocks = await self._process_blocks_for_sheets(
                    block_data, record_id, orgId, virtual_record_id
                )

            # Extract domain metadata
            if combined_text:
                try:
                    self.logger.info("🎯 Extracting metadata from Google Sheets content")
                    metadata = await self.domain_extractor.extract_metadata(
                        combined_text, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, record_id, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        record_id, CollectionNames.FILES.value
                    )
                    domain_metadata = {
                        k: (v.isoformat() if isinstance(v, datetime) else v)
                        for k, v in {**record, **file}.items()
                    }

                    # Update block and sentence metadata with domain metadata
                    copied_block_data = block_data.copy()
                    for block in copied_block_data:
                        block["metadata"] = {**domain_metadata, **block["metadata"]}
                    block_data = copied_block_data

                    copied_sentence_data = sentence_data.copy()
                    for sentence in copied_sentence_data:
                        sentence["metadata"] = {**domain_metadata, **sentence["metadata"]}
                    sentence_data = copied_sentence_data

                    self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                    await self.indexing_pipeline.index_documents(block_data)

                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Index sentences if available
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

            self.logger.info("✅ Google sheets processing completed successfully")
            return {
                "formatted_content": combined_text,
                "numbered_items": [],
            }
        except Exception as e:
            self.logger.error(f"❌ Error processing Google Sheets document: {str(e)}")
            raise

    async def process_gmail_message(
        self, recordName, recordId, version, source, orgId, html_content, virtual_record_id
    ):
        """Process Gmail message and extract structured content"""
        self.logger.info("🚀 Processing Gmail Message")

        try:
            self.logger.info(f"🚀 Processing Gmail Message for record: {recordName}")
            self.logger.debug(f"ORIGINAL HTML content for Gmail Message: {html_content}")
            # Convert binary to string
            html_content = (
                html_content.decode("utf-8")
                if isinstance(html_content, bytes)
                else html_content
            )
            self.logger.debug(f"📄 Decoded Gmail content length: {len(html_content)}")
            self.logger.debug(f"📄 Decoded Gmail content: {html_content}")

            # Initialize HTML parser and parse content
            self.logger.debug("📄 Processing Gmail content")
            parser = self.parsers["html"]
            html_result = parser.parse_string(html_content)
            self.logger.debug(f"📄 Gmail structure processed: {html_result}")
            # Get the full document structure
            doc_dict = html_result.export_to_dict()
            self.logger.debug("📑 Gmail structure processed")

            # Process content in reading order
            self.logger.debug("📑 Processing Gmail structure in reading order")
            ordered_content = self._process_content_in_order(doc_dict)
            self.logger.debug(f"📄 Gmail ordered content: {ordered_content}")
            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": item["context"]
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing Gmail structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
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

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)

                    if matching_block:
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False
                            }
                        })

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)
            else:
                self.logger.info("NO SENTENCES TO INDEX")
                record = await self.arango_service.get_document(
                    recordId, CollectionNames.RECORDS.value
                )
                record.update({
                    "indexingStatus": "COMPLETED",
                    "extractionStatus": "COMPLETED",
                    "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                    "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                    "virtualRecordId": virtual_record_id,
                    "isDirty": False
                })
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

            self.logger.info("✅ Gmail message processing completed successfully")
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
            self.logger.error(f"❌ Error processing Gmail message: {str(e)}")
            raise

    async def process_pdf_document(
        self, recordName, recordId, version, source, orgId, pdf_binary, virtual_record_id
    ):
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

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            sentences = ocr_result.get("sentences", [])
            if sentences:
                for idx, s in enumerate(sentences, 1):
                    if s.get("content") and s["content"].strip():
                        text_blocks[idx] = {
                            "text": s["content"],
                            "context": {
                                "block_type": s.get("block_type", 0),
                                "page_number": s.get("page_number", 0),
                                "bounding_box": s["bounding_box"]
                            }
                        }
                        self.logger.info(f"Text block: {idx}: {text_blocks[idx]}")

            # Extract text content
            text_content = "\n".join(
                s["content"].strip()
                for s in sentences
                if s.get("content") and s["content"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.info("Number of block sections: %s", len(block_sections))

            self.logger.info("Block sections: %s", block_sections)

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )

            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None


            # Create block data for embeddings and mapping
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "bounding_box": block.get("boundingBox", []),
                        "pageNum": block.get("pageNum", []),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data for indexing
            sentence_data = []
            if sentences:
                self.logger.debug("📑 Creating semantic sentences")

                for idx, s in enumerate(sentences, 1):
                    if s.get("content"):
                        # Find the block this sentence belongs to
                        matching_block = block_number_mapping.get(idx)

                        if matching_block:
                            sentence_data.append({
                                "text": s["content"].strip(),
                                "metadata": {
                                    **(domain_metadata or {}),
                                    "recordId": recordId,
                                    "blockId": matching_block["_key"],
                                    "blockType": matching_block.get("blockType", "text"),
                                    "blockNum": matching_block["blockNum"],
                                    "blockText": matching_block.get("text", ""),
                                    "bounding_box": matching_block.get("boundingBox", []),
                                    "pageNum": matching_block.get("pageNum", []),
                                    "virtualRecordId": virtual_record_id,
                                    "isBlock": False
                                }
                            })

            # Index blocks first
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            # Then index sentences
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

            # Prepare metadata
            metadata = {
                "recordId": recordId,
                "recordName": recordName,
                "orgId": orgId,
                "version": version,
                "source": source,
                "domain_metadata": domain_metadata,
                "document_info": {
                    "ocr_provider": provider,
                },
                "structure_info": {
                    "sentence_count": len(sentences),
                    "block_count": len(block_sections) if block_sections else 0,
                    "text_block_count": len([s for s in sentences if s.get("block_type", 0) == 0]),
                    "table_block_count": len([s for s in sentences if s.get("block_type", 0) == 2]),
                    "header_block_count": len([s for s in sentences if s.get("block_type", 0) == 4]),
                },
            }

            self.logger.info("✅ PDF processing completed successfully")
            return {
                "ocr_result": ocr_result,
                "formatted_content": text_content,
                "numbered_items": sentences,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing PDF document: {str(e)}")
            raise

    async def process_doc_document(
        self, recordName, recordId, version, source, orgId, doc_binary, virtual_record_id
    ):
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
    ):
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

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": item["context"]
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata for the whole document
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    self.logger.info("🎯 Extracting metadata from DOCX content")
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block


            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)

                    if matching_block:
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False
                            }
                        })

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
    ):
        """Process Excel document and extract structured content"""
        self.logger.info(
            f"🚀 Starting Excel document processing for record: {recordName}"
        )

        try:
            self.logger.debug("📊 Processing Excel content")
            llm = await get_llm(self.logger, self.config_service)
            parser = self.parsers[ExtensionTypes.XLSX.value]
            excel_result = parser.parse(excel_binary)

            nlp = spacy.load("en_core_web_sm")
            self.nlp = self._create_custom_tokenizer(nlp)  # Apply custom tokenization rules

            # Format content for output
            formatted_content = ""
            numbered_items = []
            block_data = []
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

                        # Common metadata for both block and sentences
                        shared_metadata = {
                            "recordId": recordId,
                            "sheetName": sheet_name,
                            "sheetNum": sheet_idx,
                            "blockNum": block_num,
                            "blockType": "table_row",
                            "blockText": json.dumps(row_data),
                            "virtualRecordId": virtual_record_id,
                        }

                        # Add block data
                        block_data.append({
                            "text": row["natural_language_text"],
                            "metadata": {
                                **shared_metadata,
                                "isBlock": True,
                            }
                        })

                        # Split text into sentences using spaCy
                        doc = self.nlp(row["natural_language_text"])
                        for sent in doc.sents:
                            sentence_text = sent.text.strip()
                            if sentence_text:
                                sentence_data.append({
                                    "text": sentence_text,
                                    "metadata": {
                                        **shared_metadata,
                                        "isBlock": False,
                                    }
                                })

            # Index blocks and sentences
            if block_data:
                # Process blocks to create records and extract metadata
                processed_blocks = await self._process_blocks_for_sheets(
                    block_data, recordId, orgId, virtual_record_id
                )

            # Extract domain metadata from text content
            self.logger.info("🎯 Extracting domain metadata")
            if excel_result["text_content"]:
                try:
                    self.logger.info("🎯 Extracting metadata from Excel content")
                    metadata = await self.domain_extractor.extract_metadata(
                        excel_result["text_content"], orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
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

                copied_block_data = block_data.copy()
                for block in copied_block_data:
                    block["metadata"]= {**block["metadata"], **domain_metadata}
                block_data = copied_block_data

                copied_sentence_data = sentence_data.copy()
                for sentence in copied_sentence_data:
                    sentence["metadata"]= {**sentence["metadata"], **domain_metadata}
                sentence_data = copied_sentence_data

                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
    ):
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
    ):
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

            nlp = spacy.load("en_core_web_sm")
            self.nlp = self._create_custom_tokenizer(nlp)  # Apply custom tokenization rules

            llm = await get_llm(self.logger, self.config_service)

            # Save temporary file to process CSV
            temp_file_path = f"/tmp/{recordName}_temp.csv"
            try:
                with open(temp_file_path, "wb") as f:
                    f.write(csv_binary)

                # Try different encodings
                encodings = ["utf-8", "latin1", "cp1252", "iso-8859-1"]
                csv_result = None

                for encoding in encodings:
                    try:
                        self.logger.debug(
                            f"Attempting to read CSV with {encoding} encoding"
                        )
                        csv_result = parser.read_file(temp_file_path, encoding=encoding)
                        self.logger.debug(
                            f"Successfully read CSV with {encoding} encoding"
                        )
                        break
                    except UnicodeDecodeError:
                        continue

                if csv_result is None:
                    raise ValueError(
                        "Unable to decode CSV file with any supported encoding"
                    )

                self.logger.debug("📑 CSV result processed")

            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)

            # Format content for output
            formatted_content = ""
            numbered_rows = []
            block_data = []
            sentence_data = []

            # Process rows for formatting
            self.logger.debug("📝 Processing rows")
            batch_size = 10
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

                    # Common metadata for both block and sentences
                    shared_metadata = {
                        "recordId": recordId,
                        "blockType": "table_row",
                        "blockText": json.dumps(row),
                        "blockNum": [idx],
                        "virtualRecordId": virtual_record_id,
                    }

                    # Add block data
                    block_data.append({
                        "text": row_text,
                        "metadata": {
                            **shared_metadata,
                            "isBlock": True,
                        }
                    })

                    # Split text into sentences using spaCy
                    doc = self.nlp(row_text)
                    for sent in doc.sents:
                        sentence_text = sent.text.strip()
                        if sentence_text:
                            sentence_data.append({
                                "text": sentence_text,
                                "metadata": {
                                    **shared_metadata,
                                    "isBlock": False,
                                }
                            })

            # Index blocks and sentences
            if block_data:
                # Process blocks to create records and extract metadata
                processed_blocks = await self._process_blocks_for_sheets(
                    block_data, recordId, orgId, virtual_record_id
                )
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
                            orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                        )
                        file = await self.arango_service.get_document(
                            recordId, CollectionNames.FILES.value
                        )
                        domain_metadata = {**record, **file}
                    except Exception as e:
                        self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                        domain_metadata = None

                copied_block_data = block_data.copy()
                for block in copied_block_data:
                    block["metadata"]= {**block["metadata"], **domain_metadata}
                block_data = copied_block_data

                copied_sentence_data = sentence_data.copy()
                for sentence in copied_sentence_data:
                    sentence["metadata"]= {**sentence["metadata"], **domain_metadata}
                sentence_data = copied_sentence_data

                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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

    def _process_content_in_order(self, doc_dict):
        """
        Process document content in proper reading order by following references.

        Args:
            doc_dict (dict): The document dictionary from Docling

        Returns:
            list: Ordered list of text items with their context
        """
        ordered_items = []
        processed_refs = set()

        def process_item(ref, level=0, parent_context=None):
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
        self, recordName, recordId, version, source, orgId, html_content, virtual_record_id
    ):
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

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": item["context"]
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item["text"].strip() for item in ordered_content if item["text"].strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

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
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for idx, item in enumerate(ordered_content, 1):
                if item["text"].strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)

                    if matching_block:
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False
                            }
                        })

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
    ):
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
    ):
        """Process Markdown document and extract structured content"""
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
            doc_dict = md_result.export_to_dict()

            # Process content in reading order
            ordered_content = doc_dict.get("texts", [])

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_content, 1):
                if item.get("text", "").strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": {
                            "label": item.get("label", "text"),
                            "language": item.get("language") if item.get("label") == "code" else None
                        }
                    }

            # Extract text in reading order
            text_content = "\n".join(
                item.get("text", "").strip()
                for item in ordered_content
                if item.get("text", "").strip()
            )

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}
                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block


            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for idx, item in enumerate(ordered_content, 1):
                if item.get("text", "").strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)

                    if matching_block:
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False,
                                "codeLanguage": (
                                    item.get("language")
                                    if item.get("label") == "code"
                                    else None
                                ),
                            }
                        })

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups"),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": text_content,
                "numbered_items": ordered_content,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing Markdown document: {str(e)}")
            raise

    async def process_txt_document(
        self, recordName, recordId, version, source, orgId, txt_binary, virtual_record_id
    ):
        """Process TXT document and extract structured content"""
        self.logger.info(
            f"🚀 Starting TXT document processing for record: {recordName}"
        )

        try:
            # Try different encodings to decode the binary content
            encodings = ["utf-8", "utf-8-sig", "latin-1", "iso-8859-1"]
            text_content = None
            encoding = None

            for enc in encodings:
                try:
                    text_content = txt_binary.decode(enc)
                    encoding = enc
                    self.logger.debug(f"Successfully decoded text with {enc} encoding")
                    break
                except UnicodeDecodeError:
                    continue

            if text_content is None:
                raise ValueError("Unable to decode text file with any supported encoding")

            # Split content into blocks and prepare for LLM
            blocks = [block for block in text_content.split("\n\n") if block.strip()]

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, block in enumerate(blocks, 1):
                if block.strip():
                    text_blocks[idx] = {
                        "text": block,
                        "context": {
                            "label": "text",
                            "block_type": "paragraph"
                        }
                    }

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            try:
                metadata = await self.domain_extractor.extract_metadata(
                    text_content, orgId
                )
                record = await self.domain_extractor.save_metadata_to_db(
                    orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                )
                file = await self.arango_service.get_document(
                    recordId, CollectionNames.FILES.value
                )
                domain_metadata = {**record, **file}
            except Exception as e:
                self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                domain_metadata = None

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block


            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []
            numbered_items = []

            for idx, block in enumerate(blocks, 1):
                if block.strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)

                    if matching_block:
                        # Create numbered item
                        numbered_items.append({
                            "number": idx,
                            "content": block,
                            "type": "paragraph"
                        })

                        sentence_data.append({
                            "text": block.strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False
                            }
                        })

            # Format content
            formatted_content = "\n\n".join(
                f"[{item['number']}] {item['content']}"
                for item in numbered_items
            )

            # Index both blocks and sentences
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
                    "block_count": len(block_sections) if block_sections else 0,
                },
            }

            self.logger.info("✅ TXT processing completed successfully")
            return {
                "txt_result": {
                    "content": text_content,
                    "metadata": domain_metadata
                },
                "formatted_content": formatted_content,
                "numbered_items": numbered_items,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing TXT document: {str(e)}")
            raise

    async def process_pptx_document(
        self, recordName, recordId, version, source, orgId, pptx_binary, virtual_record_id
    ):
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

            def process_item(ref, level=0, parent_context=None):
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

            # Prepare text blocks for LLM analysis
            text_blocks = {}
            for idx, item in enumerate(ordered_items, 1):
                if item["text"].strip():
                    text_blocks[idx] = {
                        "text": item["text"],
                        "context": {
                            "label": item["context"].get("label", "text"),
                            "pageNum": item["context"].get("pageNum"),
                        }
                    }

            # Get block sections from LLM
            self.logger.debug("🤖 Analyzing document structure with LLM")
            block_sections = await self.domain_extractor.get_block_sections(text_blocks)
            self.logger.debug("🏗️ Number of block sections: %s", len(block_sections))

            # Process blocks and create metadata
            self.logger.debug("📑 Processing document blocks")
            processed_blocks = await self._process_document_blocks(
                block_sections,
                text_blocks,
                recordId,
                orgId,
                virtual_record_id
            )
            self.logger.debug("🏗️ Processed blocks: %s", processed_blocks)

            # Extract domain metadata
            self.logger.info("🎯 Extracting domain metadata")
            domain_metadata = None
            if text_content:
                try:
                    metadata = await self.domain_extractor.extract_metadata(
                        text_content, orgId
                    )
                    record = await self.domain_extractor.save_metadata_to_db(
                        orgId, recordId, metadata, virtual_record_id, block_content=processed_blocks
                    )
                    file = await self.arango_service.get_document(
                        recordId, CollectionNames.FILES.value
                    )
                    domain_metadata = {**record, **file}

                except Exception as e:
                    self.logger.error(f"❌ Error extracting metadata: {str(e)}")
                    domain_metadata = None

            # Create block data for embeddings
            block_data = []
            block_number_mapping = {}

            for block in processed_blocks:
                # Create block embedding data
                block_data.append({
                    "text": block.get("text", ""),
                    "metadata": {
                        **(domain_metadata or {}),
                        "recordId": recordId,
                        "blockId": block["_key"],
                        "blockType": block.get("blockType", "text"),
                        "blockNum": block["blockNum"],
                        "blockText": block.get("text", ""),
                        "pageNum": block.get("pageNum", []),
                        "virtualRecordId": virtual_record_id,
                        "isBlock": True,
                    }
                })

                # Create mapping for each block number to its block
                for block_num in block["blockNum"]:
                    block_number_mapping[block_num] = block

            # Create sentence data for indexing
            self.logger.debug("📑 Creating semantic sentences")
            sentence_data = []

            for idx, item in enumerate(ordered_items, 1):
                if item["text"].strip():
                    # Find the block this sentence belongs to
                    matching_block = block_number_mapping.get(idx)
                    context = item["context"]
                    pageNum = context.get("pageNum")
                    pageNum = int(pageNum) if pageNum else None

                    if matching_block:
                        sentence_data.append({
                            "text": item["text"].strip(),
                            "metadata": {
                                **(domain_metadata or {}),
                                "recordId": recordId,
                                "blockId": matching_block["_key"],
                                "blockType": matching_block.get("blockType", "text"),
                                "blockNum": matching_block["blockNum"],
                                "blockText": matching_block.get("text", ""),
                                "pageNum": [pageNum],
                                "virtualRecordId": virtual_record_id,
                                "isBlock": False
                            }
                        })

            # Index blocks first
            if block_data:
                self.logger.debug(f"📑 Indexing {len(block_data)} blocks")
                await self.indexing_pipeline.index_documents(block_data)

            # Then index sentences
            if sentence_data:
                self.logger.debug(f"📑 Indexing {len(sentence_data)} sentences")
                await self.indexing_pipeline.index_documents(sentence_data)

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
                    "items": ordered_items,
                    "document_structure": {
                        "body": doc_dict.get("body"),
                        "groups": doc_dict.get("groups"),
                    },
                    "metadata": domain_metadata,
                },
                "formatted_content": text_content,
                "numbered_items": ordered_items,
                "metadata": metadata,
            }

        except Exception as e:
            self.logger.error(f"❌ Error processing PPTX document: {str(e)}")
            raise

    async def process_ppt_document(
        self, recordName, recordId, version, source, orgId, ppt_binary, virtual_record_id
    ):
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
