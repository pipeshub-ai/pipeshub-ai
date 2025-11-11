import json
import os
import time
from io import BytesIO
from typing import Any, Dict, List

import fitz  # PyMuPDF for initial document check
import spacy
from azure.ai.formrecognizer.aio import (
    DocumentAnalysisClient as AsyncDocumentAnalysisClient,
)
from azure.core.credentials import AzureKeyCredential
from spacy import Language
from spacy.tokens import Doc

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockType,
    CitationMetadata,
    DataFormat,
    GroupType,
    Point,
    TableMetadata,
)
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.utils.indexing_helpers import (
    get_rows_text,
    get_table_summary_n_headers,
)

LENGTH_THRESHOLD = 2
WORD_THRESHOLD = 15
WORD_OVERLAP_THRESHOLD = 0.9

class AzureOCRStrategy(OCRStrategy):
    def __init__(
        self, logger, config, endpoint: str, key: str, model_id: str = "prebuilt-document"
    ) -> None:
        self.logger = logger
        self.endpoint = endpoint
        self.key = key
        self.model_id = model_id
        self.config = config
        self.document_analysis_result = None
        self.doc = None  # PyMuPDF document for initial check
        self._processed = False
        self.ocr_pdf_content = None  # Store the OCR-processed PDF content

        # Initialize spaCy with custom tokenizer
        self.logger.info("🧠 Loading spaCy model and creating custom tokenizer...")
        try:
            self.nlp = self._create_custom_tokenizer(spacy.load("en_core_web_sm"))
            self.logger.info("✅ spaCy model loaded successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to load spaCy model: {e}")
            self.nlp = None

    async def load_document(self, content: bytes) -> None:
        """Load and analyze document using Azure Document Intelligence"""
        self.logger.info("🚀 Starting Azure Document Intelligence document loading")
        self.logger.info(f"📊 Document size: {len(content):,} bytes")


        self.logger.debug("📄 Opening PDF with PyMuPDF for initial OCR need analysis")

        try:
            with fitz.open(stream=content, filetype="pdf") as temp_doc:
                # Check if any page needs OCR
                self.logger.info("🔍 Analyzing OCR requirements per page...")
                pages_needing_ocr = []

                for page_num, page in enumerate(temp_doc):
                    self.logger.debug(f"🔍 Checking page {page_num + 1}/{len(temp_doc)} for OCR need")

                    # Log page dimensions
                    self.logger.debug(f"   📐 Page dimensions: {page.rect.width:.1f} x {page.rect.height:.1f}")

                    page_needs_ocr = self.needs_ocr(page)
                    if page_needs_ocr:
                        pages_needing_ocr.append(page_num + 1)
                        self.logger.info(f"   ✅ Page {page_num + 1}: NEEDS OCR")
                    else:
                        self.logger.debug(f"   ❌ Page {page_num + 1}: OCR not needed")

                needs_ocr = len(pages_needing_ocr) > 0
                self._needs_ocr = needs_ocr

                self.logger.info(f"   🎯 Final decision: {'AZURE OCR' if needs_ocr else 'PYMUPDF DIRECT'}")

        except Exception as e:
            self.logger.error(f"❌ Error during OCR need analysis: {e}")
            self.logger.warning("⚠️ Defaulting to Azure OCR due to analysis failure")
            needs_ocr = True
            self._needs_ocr = True

        try:
            if needs_ocr:
                await self._process_with_azure(content)
                self.document_analysis_result = await self._preprocess_document()
            else:
                raise Exception("Azure OCR is not needed as ocr was not needed")


        except Exception as e:
            self.logger.error(f"❌ Error during document loading: {e}")
            raise e

    async def _process_with_azure(self, content: bytes) -> None:
        """Process document using Azure Document Intelligence"""
        self.logger.info("🤖 Starting Azure Document Intelligence processing...")

        try:
            self.logger.debug("🔗 Creating Azure Document Analysis Client")
            async with AsyncDocumentAnalysisClient(
                endpoint=self.endpoint, credential=AzureKeyCredential(self.key)
            ) as doc_client:

                self.logger.debug("📤 Preparing document for Azure submission")
                document = BytesIO(content)
                document.seek(0)

                self.logger.info(f"📤 Sending document to Azure DI (model: {self.model_id})")
                start_time = time.time()

                poller = await doc_client.begin_analyze_document(
                    model_id=self.model_id, document=document
                )

                self.logger.info("⏳ Waiting for Azure analysis to complete...")
                self.doc = await poller.result()

                processing_time = time.time() - start_time
                self.logger.info(f"✅ Azure processing completed in {processing_time:.2f} seconds")

                # Log Azure response structure
                self.logger.info("📊 Azure Response Analysis:")
                if hasattr(self.doc, 'pages'):
                    self.logger.info(f"   📄 Pages in response: {len(self.doc.pages)}")
                    for i, page in enumerate(self.doc.pages):
                        self.logger.debug(f"     Page {i+1}: {page.width}x{page.height} {page.unit}")
                        if hasattr(page, 'lines'):
                            self.logger.debug(f"       Lines: {len(page.lines)}")
                        if hasattr(page, 'words'):
                            self.logger.debug(f"       Words: {len(page.words) if hasattr(page, 'words') else 'N/A'}")

                if hasattr(self.doc, 'paragraphs'):
                    self.logger.info(f"   📚 Paragraphs: {len(self.doc.paragraphs)}")

                if hasattr(self.doc, 'tables'):
                    self.logger.info(f"   📊 Tables: {len(self.doc.tables)}")

                self.ocr_pdf_content = None  # Commented out searchable PDF creation
        except Exception as e:
            self.logger.error(f"❌ Azure Document Intelligence processing failed: {e}")
            self.logger.error(f"   🔍 Error type: {type(e).__name__}")
            self.logger.error(f"   📝 Error details: {str(e)}")
            self.logger.warning("⚠️ Falling back to PyMuPDF extraction")
            raise e


    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc) -> Doc:
        for token in doc[:-1]:  # Avoid out-of-bounds errors
            next_token = doc[token.i + 1]

            # Force sentence split BEFORE bullet points and list markers
            if (
                # Check if next token is a bullet point or list marker
                next_token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
                or
                # Numeric bullets (1., 2., etc)
                (
                    next_token.like_num
                    and len(next_token.text) <= LENGTH_THRESHOLD
                    and next_token.i + 1 < len(doc)
                    and doc[next_token.i + 1].text == "."
                )
                or
                # Letter bullets (a., b., etc)
                (
                    len(next_token.text) == 1
                    and next_token.text.isalpha()
                    and next_token.i + 1 < len(doc)
                    and doc[next_token.i + 1].text == "."
                )
            ):
                next_token.is_sent_start = True
                continue

            # Check if current token is a bullet point or list marker
            is_current_bullet = (
                token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
                or (token.like_num and next_token.text == "." and len(token.text) <= LENGTH_THRESHOLD)
                or (
                    len(token.text) == 1
                    and token.text.isalpha()
                    and next_token.text == "."
                )
            )

            # Check if next token starts a new bullet point
            is_next_bullet_start = token.i + 2 < len(doc) and (
                doc[token.i + 2].text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
                or (doc[token.i + 2].like_num and len(doc[token.i + 2].text) <= LENGTH_THRESHOLD)
                or (len(doc[token.i + 2].text) == 1 and doc[token.i + 2].text.isalpha())
            )

            # Split between bullet points
            if is_current_bullet and is_next_bullet_start:
                doc[token.i + 2].is_sent_start = True
                continue

            # Handle common abbreviations
            if (
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
                continue

            # Handle single uppercase letters (likely acronyms)
            if len(token.text) == 1 and token.text.isupper() and next_token.text == ".":
                next_token.is_sent_start = False
                continue

            # Handle ellipsis (...) - don't split
            if token.text == "." and next_token.text == ".":
                next_token.is_sent_start = False
                continue

        return doc

    def _create_custom_tokenizer(self, nlp) -> Language:
        """
        Creates a custom tokenizer that handles special cases for sentence boundaries.
        """
        # Add the custom rule to the pipeline
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")

        # Add custom sentence boundary detection
        if "custom_sentence_boundary" not in nlp.pipe_names:
            nlp.add_pipe("custom_sentence_boundary", after="sentencizer")

        # Configure the tokenizer to handle special cases
        special_cases = {
            # Common abbreviations
            "e.g.": [{"ORTH": "e.g."}],
            "i.e.": [{"ORTH": "i.e."}],
            "etc.": [{"ORTH": "etc."}],
            "...": [{"ORTH": "..."}],
            # Bullet points and list markers
            "·": [{"ORTH": "·"}],
            "•": [{"ORTH": "•"}],
            "-": [{"ORTH": "-"}],
            "–": [{"ORTH": "–"}],
            "—": [{"ORTH": "—"}],
        }

        for case, mapping in special_cases.items():
            nlp.tokenizer.add_special_case(case, mapping)

        return nlp

    async def process_page(self, page) -> Dict[str, Any]:
        """Process a single page - Implemented for consistency but not primary method"""
        if self._processed:
            raise NotImplementedError("Azure processes entire document at once")
        else:
            # Use PyMuPDF extraction for non-OCR pages
            page_width = page.rect.width
            page_height = page.rect.height

            words = []
            lines = []

            # Extract words
            for word in page.get_text("words"):
                x0, y0, x1, y1, text = word[:5]
                if text.strip():
                    words.append(
                        {
                            "content": text.strip(),
                            "confidence": None,
                            "bounding_box": self._normalize_bbox(
                                (x0, y0, x1, y1), page_width, page_height
                            ),
                        }
                    )

            # Extract lines
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    text = " ".join(
                        span.get("text", "") for span in line.get("spans", [])
                    )
                    if text.strip() and line.get("bbox"):
                        lines.append(
                            {
                                "content": text.strip(),
                                "bounding_box": self._normalize_bbox(
                                    line["bbox"], page_width, page_height
                                ),
                            }
                        )

            return {
                "words": words,
                "lines": lines,
                "page_width": page_width,
                "page_height": page_height,
            }

    def _normalize_bbox(
        self, bbox, page_width: float, page_height: float
    ) -> List[Dict[str, float]]:
        """Normalize bounding box coordinates to 0-1 range"""
        x0, y0, x1, y1 = bbox
        return [
            {"x": x0 / page_width, "y": y0 / page_height},
            {"x": x1 / page_width, "y": y0 / page_height},
            {"x": x1 / page_width, "y": y1 / page_height},
            {"x": x0 / page_width, "y": y1 / page_height},
        ]

    def _get_bounding_box(self, element) -> List[Dict[str, float]]:
        """Get bounding box from element"""
        if hasattr(element, "polygon"):
            return [{"x": point.x, "y": point.y} for point in element.polygon]
        elif hasattr(element, "bounding_regions"):
            region = element.bounding_regions[0]
            return [{"x": point.x, "y": point.y} for point in region.polygon]
        return []

    def _normalize_coordinates(
        self, coordinates: List[Dict[str, float]], page_width: float, page_height: float
    ) -> List[Dict[str, float]]:
        """Normalize coordinates to 0-1 range"""
        if not coordinates:
            return None

        normalized = []
        for point in coordinates:
            x = point.get("x", 0)
            y = point.get("y", 0)
            normalized.append({"x": x / page_width, "y": y / page_height})
        return normalized

    def _normalize_element_data(
        self, element_data: Dict[str, Any], page_width: float, page_height: float
    ) -> Dict[str, Any]:
        """Normalize coordinates to 0-1 range"""
        if "bounding_box" in element_data and element_data["bounding_box"]:
            element_data["bounding_box"] = self._normalize_coordinates(
                element_data["bounding_box"], page_width, page_height
            )
        return element_data

    def _process_block_text_pymupdf(
        self, block: Dict[str, Any], page_width: float, page_height: float
    ) -> Dict[str, Any]:
        """Process a text block to extract lines, sentences, and metadata

        Handles both single-span and multi-span lines:
        - Single-span: One span containing complete line text
        - Multi-span: Multiple spans containing individual words/characters

        Args:
            block: Dictionary containing text block data
            page_width: Width of the page for bbox normalization
            page_height: Height of the page for bbox normalization

        Returns:
            Dictionary containing processed text data including lines, spans, words and metadata
        """

        block_lines = []
        block_text = []
        block_spans = []
        block_words = []

        # Process lines and their spans
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue

            # Check if this is a multi-span line by looking at text content
            is_multi_span = len(spans) > 1

            # Combine span text appropriately
            if is_multi_span:
                # For multi-span lines, preserve spaces between spans
                line_text = ""
                for span in spans:
                    span_text = span.get("text", "")
                    # Add space only if it's not already a space span
                    if (
                        line_text
                        and not span_text.isspace()
                        and not line_text.endswith(" ")
                    ):
                        line_text += " "
                    line_text += span_text
            else:
                # For single-span lines, use the text directly
                line_text = spans[0].get("text", "")

            if line_text.strip():
                line_data = {
                    "content": line_text.strip(),
                    "bounding_box": self._normalize_bbox(
                        line["bbox"], page_width, page_height
                    ),
                }
                block_lines.append(line_data)

                # Process spans
                for span in spans:
                    span_text = span.get("text", "").strip()
                    if (
                        span_text or is_multi_span
                    ):  # Include empty spans for multi-span lines
                        block_text.append(span.get("text", ""))
                        span_data = {
                            "text": span.get("text", ""),
                            "bounding_box": self._normalize_bbox(
                                span["bbox"], page_width, page_height
                            ),
                            "font": span.get("font"),
                            "size": span.get("size"),
                            "flags": span.get("flags"),
                        }
                        block_spans.append(span_data)

                        # Process individual characters if available
                        for char in span.get("chars", []):
                            word_text = char.get("c", "").strip()
                            if word_text:
                                word = {
                                    "content": word_text,
                                    "bounding_box": self._normalize_bbox(
                                        char["bbox"], page_width, page_height
                                    ),
                                    "confidence": None,
                                }
                                block_words.append(word)

        # Get block metadata from first available span
        first_span = (
            block.get("lines", [])[0].get("spans", [])[0] if block.get("lines") else {}
        )
        block_metadata = {
            "font": first_span.get("font"),
            "size": first_span.get("size"),
            "color": first_span.get("color"),
            "span_type": (
                "multi_span"
                if len(block.get("lines", [])[0].get("spans", [])) > 1
                else "single_span"
            ),
        }

        # Process sentences using the lines
        sentences = self._merge_lines_to_sentences(block_lines)
        processed_sentences = []
        for sentence in sentences:
            sentence_data = {
                "content": sentence["sentence"],
                "bounding_box": sentence["bounding_box"],
                "block_number": block.get("number"),
                "block_type": block.get("type"),
                "metadata": block_metadata,
            }
            processed_sentences.append(sentence_data)

        # Create paragraph from block
        paragraph = {
            "content": " ".join(block_text).strip(),
            "bounding_box": self._normalize_bbox(
                block["bbox"], page_width, page_height
            ),
            "block_number": block.get("number"),
            "spans": block_spans,
            "words": block_words,
            "metadata": block_metadata,
        }

        return {
            "lines": block_lines,
            "sentences": processed_sentences,
            "paragraph": paragraph if block_text else None,
            "words": block_words,
        }

    def _process_block_text_azure(
        self, block, page_width: float, page_height: float
    ) -> Dict[str, Any]:
        """Process a text block to extract lines, sentences, and metadata

        Args:
            block: Azure paragraph object
            page_width: Width of the page for bbox normalization
            page_height: Height of the page for bbox normalization

        Returns:
            Dictionary containing processed text data including lines, spans, words and metadata
        """
        block_text = []
        block_words = []

        # Process words if available
        if hasattr(block, "words"):
            for word in block.words:
                word_data = {
                    "content": word.content,
                    "bounding_box": self._normalize_coordinates(
                        self._get_bounding_box(word), page_width, page_height
                    ),
                    "confidence": (
                        word.confidence if hasattr(word, "confidence") else None
                    ),
                }
                block_words.append(word_data)

        # Get block metadata
        block_metadata = {
            "role": block.role if hasattr(block, "role") else None,
            "confidence": block.confidence if hasattr(block, "confidence") else None,
        }

        # Create paragraph from block
        if hasattr(block, "content"):
            block_text.append(block.content.strip())

        paragraph = {
            "content": " ".join(block_text).strip(),
            "bounding_box": self._normalize_coordinates(
                self._get_bounding_box(block), page_width, page_height
            ),
            "words": block_words,
            "metadata": block_metadata,
        }

        return paragraph if block_text else None

    def _should_merge_blocks(
        self, block1: Dict[str, Any], block2: Dict[str, Any], word_threshold: int = WORD_THRESHOLD
    ) -> bool:
        """
        Determine if blocks should be merged based on word count threshold.
        Merges if block1 has fewer words than the threshold.

        Args:
            block1: First text block
            block2: Second text block
            word_threshold: Minimum word count threshold (default 10 words)

        Returns:
            bool: True if blocks should be merged
        """
        if block1.get("type") != 0 or block2.get("type") != 0:
            return False

        # Get word count for first block
        text1 = " ".join(
            span.get("text", "")
            for line in block1.get("lines", [])
            for span in line.get("spans", [])
        )
        word_count = len(text1.split())

        self.logger.debug(f"Block word count: {word_count}")

        # Merge if word count is below threshold
        return word_count < word_threshold

    def _merge_block_content(
        self, block1: Dict[str, Any], block2: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge two text blocks into one.
        """
        merged_block = block1.copy()

        # Merge lines
        merged_block["lines"] = block1.get("lines", []) + block2.get("lines", [])

        # Update bbox to encompass both blocks
        b1 = block1.get("bbox", (0, 0, 0, 0))
        b2 = block2.get("bbox", (0, 0, 0, 0))
        merged_block["bbox"] = (
            min(b1[0], b2[0]),  # x0
            min(b1[1], b2[1]),  # y0
            max(b1[2], b2[2]),  # x1
            max(b1[3], b2[3]),  # y1
        )

        return merged_block

    async def _preprocess_document(self) -> Dict[str, Any]:
        """Pre-process document to match PyMuPDF's structure"""
        self.logger.info("🔄 Starting document preprocessing")

        # Initialize result structure
        result = {
            "pages": [],
            "lines": [],
            "paragraphs": [],
            "sentences": [],
            "tables": [],
            "key_value_pairs": [],
            "blocks": [],
        }

        self.logger.debug("📊 Initialized result structure with empty collections")

        # Get pages to process
        doc_pages = self.doc.pages

        # Process each page
        for page_idx, page in enumerate(doc_pages):
            page_number = page_idx + 1


            # Get page properties
            page_dict = self._extract_page_properties(page,True, page_number)

            await self._process_azure_page(page, page_dict, result, page_number)

            result["pages"].append(page_dict)

            # Log page processing summary
            self.logger.debug(f"   📝 Lines: {len(page_dict['lines'])}")
            self.logger.debug(f"   🔤 Words: {len(page_dict['words'])}")
            self.logger.debug(f"   📊 Tables: {len(page_dict['tables'])}")

        # Final summary
        self.logger.info("📊 Document preprocessing completed:")
        self.logger.info(f"   📄 Pages: {len(result['pages'])}")
        self.logger.info(f"   📝 Total lines: {len(result['lines'])}")
        self.logger.info(f"   📚 Total paragraphs: {len(result['paragraphs'])}")
        self.logger.info(f"   🔤 Total sentences: {len(result['sentences'])}")
        self.logger.info(f"   📊 Total tables: {len(result['tables'])}")

        return result

    def _extract_page_properties(self, page, needs_ocr: bool, page_number: int) -> Dict[str, Any]:
        """Extract and log page properties"""
        if needs_ocr and hasattr(page, "width"):
            # Azure DI page
            page_width = page.width
            page_height = page.height
            page_unit = page.unit
            actual_page_number = page.page_number

            self.logger.debug("📐 Azure page properties:")
            self.logger.debug(f"   Width: {page_width}")
            self.logger.debug(f"   Height: {page_height}")
            self.logger.debug(f"   Unit: {page_unit}")
            self.logger.debug(f"   Page number: {actual_page_number}")

        else:
            # PyMuPDF page
            page_width = page.rect.width
            page_height = page.rect.height
            page_unit = "point"
            actual_page_number = page_number + 1

            self.logger.debug("📐 PyMuPDF page properties:")
            self.logger.debug(f"   Width: {page_width}")
            self.logger.debug(f"   Height: {page_height}")
            self.logger.debug(f"   Unit: {page_unit}")
            self.logger.debug(f"   Page number: {actual_page_number}")

        return {
            "page_number": actual_page_number,
            "width": page_width,
            "height": page_height,
            "unit": page_unit,
            "lines": [],
            "words": [],
            "tables": [],
        }

    async def _process_azure_page(self, page, page_dict: Dict[str, Any], result: Dict[str, Any], page_number: int) -> None:
        """Process Azure DI page"""
        self.logger.debug(f"🤖 Processing Azure page {page_number}")

        # Process lines
        page_lines = []
        if hasattr(page, "lines"):
            self.logger.debug(f"📝 Processing {len(page.lines)} lines from Azure")
            for line_idx, line in enumerate(page.lines):
                line_data = self._process_line(line, page_dict["width"], page_dict["height"])
                if line_data:
                    line_data["page_number"] = page_number
                    line_data["line_index"] = line_idx
                    page_lines.append(line_data)
                    page_dict["lines"].append(line_data)
                    result["lines"].append(line_data)

                    self.logger.debug(f"   Line {line_idx}: '{line_data['content'][:50]}...'")

        # Process paragraphs
        if hasattr(self.doc, "paragraphs"):
            self.logger.debug(f"📚 Processing {len(self.doc.paragraphs)} paragraphs from Azure")
            for idx, paragraph in enumerate(self.doc.paragraphs):
                processed_paragraph = self._process_block_text_azure(
                    paragraph, page_dict["width"], page_dict["height"]
                )
                if processed_paragraph:
                    processed_paragraph["page_number"] = page_number
                    processed_paragraph["paragraph_number"] = idx

                    self.logger.debug(f"   Paragraph {idx}: '{processed_paragraph['content'][:50]}...'")

                    # Find lines for this paragraph
                    paragraph_lines = self._get_lines_for_paragraph(
                        page_lines,
                        processed_paragraph["content"],
                        processed_paragraph["bounding_box"],
                    )

                    self.logger.debug(f"     Found {len(paragraph_lines)} lines for paragraph {idx}")

                    # Process sentences
                    paragraph_sentences = self._merge_lines_to_sentences(paragraph_lines)
                    self.logger.debug(f"     Created {len(paragraph_sentences)} sentences from paragraph {idx}")

                    # Add sentences to result
                    for sent_idx, sentence in enumerate(paragraph_sentences):
                        sentence_data = {
                            "content": sentence["sentence"],
                            "bounding_box": sentence["bounding_box"],
                            "paragraph_numbers": [idx],
                            "page_number": page_number,
                            "sentence_index": sent_idx,
                        }
                        result["sentences"].append(sentence_data)

                    processed_paragraph["sentences"] = paragraph_sentences
                    result["paragraphs"].append(processed_paragraph)
                    result["blocks"].append(processed_paragraph)

        # Process tables
        if hasattr(page, "tables"):
            self.logger.debug(f"📊 Processing {len(page.tables)} tables from Azure page")
            for table_idx, table in enumerate(page.tables):
                table_data = self._process_table(table, page)
                cells = table_data.get("cells", [])
                if len(cells) == 0:
                    self.logger.warning(f"No cells found in table {table_idx}")
                    continue
                row_count = table_data.get("row_count", 0)
                col_count = table_data.get("column_count", 0)
                table_markdown,table_data_grid = self.cells_to_markdown(row_count, col_count, cells)
                response = await get_table_summary_n_headers(self.config, table_markdown)
                table_summary = response.summary
                column_headers = response.headers

                table_rows_text,table_rows = await get_rows_text(self.config, {"grid": table_data_grid}, table_summary, column_headers)
                bbox = table_data.get("bounding_boxes", [])
                if bbox != []:
                    bbox = [Point(x=p["x"], y=p["y"]) for p in bbox]

                block_group = BlockGroup(
                    index=len(result["tables"]),
                    type=GroupType.TABLE,
                    description=None,
                    table_metadata=TableMetadata(
                        num_of_rows=row_count,
                        num_of_cols=col_count,
                    ),
                    data={
                        "table_summary": table_summary,
                        "column_headers": column_headers,
                        "table_markdown": table_markdown,
                    },
                    format=DataFormat.JSON,
                    citation_metadata=CitationMetadata(
                        page_number=page_number,
                        bounding_boxes=bbox,
                    ),
                )
                for i,row in enumerate(table_rows):
                    block = Block(
                        type=BlockType.TABLE_ROW,
                        format=DataFormat.JSON,
                        comments=[],
                        parent_index=block_group.index,
                        data={
                            "row_natural_language_text": table_rows_text[i] if i<len(table_rows_text) else "",
                            "row_number": i+1,
                            "row":json.dumps(row)
                        },
                        citation_metadata=block_group.citation_metadata
                    )
                    # _enrich_metadata(block, row, doc_dict)
                    result["blocks"].append(block)


                result["tables"].append(block_group)
                table_data["table_index"] = table_idx
                page_dict["tables"].append(table_data)
                self.logger.debug(f"   Table {table_idx}: {table_data['row_count']}x{table_data['column_count']}")


    def cells_to_markdown(row_count: int, col_count: int, cells: list[dict]) -> tuple[str, list[list[str]]]:
        # Initialize empty grid
        grid = [['' for _ in range(col_count)] for _ in range(row_count)]

        # Track which cells are occupied by spans
        occupied = [[False for _ in range(col_count)] for _ in range(row_count)]

        # Fill the grid with cell content
        for cell in cells:
            row_idx = cell['row_index']
            col_idx = cell['column_index']
            content = cell['content'].strip() if cell['content'] else ''
            row_span = cell.get('row_span', 1)
            col_span = cell.get('column_span', 1)

            # Place content in the starting cell
            if row_idx < row_count and col_idx < col_count:
                grid[row_idx][col_idx] = content

                # Mark cells as occupied by this span
                for r in range(row_idx, min(row_idx + row_span, row_count)):
                    for c in range(col_idx, min(col_idx + col_span, col_count)):
                        occupied[r][c] = True

        # Build markdown table
        lines = []

        for row_idx, row in enumerate(grid):
            # Create row with pipe separators
            row_content = '| ' + ' | '.join(row) + ' |'
            lines.append(row_content)

            # Add header separator after first row
            if row_idx == 0:
                separator = '| ' + ' | '.join(['---'] * col_count) + ' |'
                lines.append(separator)

        markdown = '\n'.join(lines)

        # Return both markdown and grid (list of lists)
        return markdown, grid

    def _merge_small_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge small text blocks based on word count threshold"""
        self.logger.debug("🔗 Starting block merging process")

        merged_blocks = []
        i = 0
        merge_count = 0

        while i < len(blocks):
            current_block = blocks[i]
            next_index = i + 1

            # Keep merging blocks until we have enough words or run out of blocks
            while next_index < len(blocks) and self._should_merge_blocks(
                current_block, blocks[next_index]
            ):
                self.logger.debug(f"   Merging block {i} with block {next_index}")
                current_block = self._merge_block_content(current_block, blocks[next_index])
                next_index += 1
                merge_count += 1

            merged_blocks.append(current_block)

            if next_index > i + 1:
                self.logger.debug(f"   Created merged block from {i} to {next_index-1}")

            i = next_index if next_index > i + 1 else i + 1

        self.logger.debug(f"🔗 Block merging completed: {merge_count} merges performed")
        return merged_blocks

    def _merge_lines_to_sentences(self, lines_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge lines into sentences using spaCy"""
        self.logger.debug(f"🔤 Starting sentence processing for {len(lines_data)} lines")

        if not self.nlp:
            self.logger.error("❌ spaCy model not available for sentence processing")
            return []

        # Build full text and line mapping
        full_text = ""
        line_map = []
        char_index = 0

        self.logger.debug("📝 Building text and line mapping:")
        for line_idx, line_data in enumerate(lines_data):
            content = line_data["content"].strip()

            if not content:
                self.logger.debug(f"   Skipping empty line {line_idx}")
                continue

            self.logger.debug(f"   Line {line_idx}: '{content}' (chars {char_index}-{char_index + len(content)})")

            full_text += content + " "
            line_map.append((char_index, char_index + len(content), line_data["bounding_box"]))
            char_index += len(content) + 1

        # Process with spaCy
        doc = self.nlp(full_text)
        sentences = []

        self.logger.debug(f"🔤 spaCy identified {len(list(doc.sents))} sentences")

        # Process each sentence
        for sent_idx, sent in enumerate(doc.sents):
            sent_text = sent.text.strip()
            sent_start, sent_end = sent.start_char, sent.end_char

            # Find overlapping line bounding boxes
            sentence_bboxes = []
            overlapping_lines = []

            for line_idx, (start_idx, end_idx, bbox) in enumerate(line_map):
                if start_idx < sent_end and end_idx > sent_start:
                    sentence_bboxes.append(bbox)
                    overlapping_lines.append(line_idx)
                    self.logger.debug(f"     Including line {line_idx} bbox")

            merged_bbox = self._merge_bounding_boxes(sentence_bboxes) if sentence_bboxes else None

            sentences.append({
                "sentence": sent_text,
                "bounding_box": merged_bbox,
                "overlapping_lines": overlapping_lines,
                "char_span": (sent_start, sent_end)
            })

        self.logger.info(f"✅ Sentence processing completed: {len(sentences)} sentences created from {len(lines_data)} lines")
        return sentences

    def _process_table(self, table, page) -> Dict[str, Any]:
        """Process table data with normalized coordinates"""
        cells_data = []
        for cell in table.cells:
            cell_data = {
                "content": cell.content,
                "row_index": cell.row_index,
                "column_index": cell.column_index,
                "row_span": cell.row_span,
                "column_span": cell.column_span,
                "bounding_box": self._normalize_element_data(
                    {"bounding_box": self._get_bounding_box(cell)},
                    page.width,
                    page.height,
                )["bounding_box"],
                "confidence": cell.confidence if hasattr(cell, "confidence") else None,
            }
            cells_data.append(cell_data)

        return {
            "row_count": table.row_count,
            "column_count": table.column_count,
            "page_number": self._get_page_number(table),
            "cells": cells_data,
            "bounding_box": self._normalize_element_data(
                {"bounding_box": self._get_bounding_box(table)}, page.width, page.height
            )["bounding_box"],
        }

    def _merge_bounding_boxes(
        self, bboxes: List[List[Dict[str, float]]]
    ) -> List[Dict[str, float]]:
        """Merge multiple bounding boxes into one encompassing box

        Args:
            bboxes: List of bounding boxes, each containing 4 points with x,y coordinates

        Returns:
            Single bounding box containing 4 points that encompass all input boxes
        """
        self.logger.debug(f"🚀 Merging bounding boxes: {bboxes}")

        # Flatten all points from all boxes
        all_points = [point for box in bboxes for point in box]

        # Find the extremes
        min_x = min(point["x"] for point in all_points)
        min_y = min(point["y"] for point in all_points)
        max_x = max(point["x"] for point in all_points)
        max_y = max(point["y"] for point in all_points)

        return [
            {"x": min_x, "y": min_y},  # top-left
            {"x": max_x, "y": min_y},  # top-right
            {"x": max_x, "y": max_y},  # bottom-right
            {"x": min_x, "y": max_y},  # bottom-left
        ]

    def _process_line(
        self, line, page_width: float, page_height: float
    ) -> Dict[str, Any]:
        """Process a single line from Azure Document output

        Args:
            line: Azure DocumentLine object
            page_width: Width of the page for bbox normalization
            page_height: Height of the page for bbox normalization

        Returns:
            Dictionary containing processed line data
        """
        if not hasattr(line, "content") or not line.content.strip():
            return None

        return {
            "content": line.content.strip(),
            "bounding_box": self._normalize_coordinates(
                self._get_bounding_box(line), page_width, page_height
            ),
            "confidence": line.confidence if hasattr(line, "confidence") else None,
        }

    def _check_bbox_overlap(self, bbox1, bbox2, threshold=0.1) -> bool:
        """Check if two bounding boxes overlap significantly"""
        # Calculate intersection area
        x_left = max(min(p["x"] for p in bbox1), min(p["x"] for p in bbox2))
        x_right = min(max(p["x"] for p in bbox1), max(p["x"] for p in bbox2))
        y_top = max(min(p["y"] for p in bbox1), min(p["y"] for p in bbox2))
        y_bottom = min(max(p["y"] for p in bbox1), max(p["y"] for p in bbox2))

        if x_right < x_left or y_bottom < y_top:
            return False

        intersection = (x_right - x_left) * (y_bottom - y_top)

        # Calculate areas of both boxes
        def box_area(bbox: List[Dict[str, float]]) -> float:
            width = max(p["x"] for p in bbox) - min(p["x"] for p in bbox)
            height = max(p["y"] for p in bbox) - min(p["y"] for p in bbox)
            return width * height

        area1 = box_area(bbox1)
        area2 = box_area(bbox2)

        # Calculate overlap ratio
        overlap_ratio = intersection / min(area1, area2)

        return overlap_ratio > threshold

    async def _create_searchable_pdf(
        self, original_content: bytes, output_dir: str = "output/searchable/azure"
    ) -> bytes:
        """Create a searchable PDF by overlaying OCR text from Azure results"""
        self.logger.debug("🔄 Starting searchable PDF creation")

        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        self.logger.debug(f"📁 Using output directory: {output_dir}")

        # Generate unique filename using timestamp
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_filename = f"searchable_pdf_{timestamp}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        self.logger.debug(f"📄 Output file will be saved as: {output_path}")

        # Open the original PDF from bytes
        doc = fitz.open(stream=original_content, filetype="pdf")
        self.logger.debug(f"📄 Opened original PDF with {len(doc)} pages")

        # Process each page
        for page_num in range(len(doc)):
            page = doc[page_num]

            # Get Azure OCR results for this page
            azure_page = next(
                (p for p in self.doc.pages if p.page_number == page_num), None
            )
            if not azure_page:
                self.logger.debug(
                    f"⚠️ No Azure OCR results found for page {page_num + 1}"
                )
                continue

            self.logger.debug(f"🔄 Processing page {page_num + 1}")
            word_count = 0

            # Add text overlay for each word
            for word in azure_page.words:
                if not word.content.strip():
                    continue

                # Get the bounding box
                if not word.bounding_regions:
                    continue

                bbox = word.bounding_regions[0].polygon

                # Convert Azure coordinates (0-1) to page coordinates and ensure correct bounds
                x_coords = [p.x * page.rect.width for p in bbox]
                y_coords = [p.y * page.rect.height for p in bbox]
                rect = fitz.Rect(
                    min(x_coords), min(y_coords), max(x_coords), max(y_coords)
                )

                # Add searchable text overlay
                page.insert_textbox(
                    rect,  # rectangle to place text
                    word.content,  # the text to insert
                    fontname="helv",  # use a standard font
                    fontsize=10,  # reasonable font size
                    align=0,  # left alignment
                    color=(0, 0, 0, 0),  # transparent color
                    overlay=True,  # overlay on top of existing content
                )

                word_count += 1

        # Save the modified PDF to the output file
        self.logger.info(f"💾 Saving searchable PDF to: {output_path}")
        doc.save(output_path)
        doc.close()
        self.logger.debug("📄 Closed PDF document")

        # Read the saved file
        self.logger.debug(f"📖 Reading saved searchable PDF: {output_path}")
        with open(output_path, "rb") as f:
            ocr_pdf_content = f.read()

        self.logger.info("✅ Searchable PDF creation completed")
        return ocr_pdf_content

    def _get_lines_for_paragraph(
        self,
        page_lines: List[Dict[str, Any]],
        paragraph_text: str,
        paragraph_bbox: List[Dict[str, float]],
        overlap_threshold: float = WORD_OVERLAP_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        """Find lines that belong to a paragraph based on content and spatial overlap"""

        paragraph_lines = []
        paragraph_words = set(paragraph_text.lower().split())

        for line in page_lines:
            line_content = line["content"]
            line_bbox = line["bounding_box"]

            # Check for content overlap
            line_words = set(line_content.lower().split())
            word_overlap = len(line_words.intersection(paragraph_words)) / len(line_words) if len(line_words) > 0 else 0

            # Check for spatial overlap
            spatial_overlap = self._check_bbox_overlap(
                line_bbox, paragraph_bbox, threshold=overlap_threshold
            )

            if word_overlap > WORD_OVERLAP_THRESHOLD and spatial_overlap:
                paragraph_lines.append(line)
                self.logger.debug(f"Added line to paragraph: {line_content}")

        # Sort lines by vertical position
        paragraph_lines.sort(
            key=lambda x: sum(p["y"] for p in x["bounding_box"])
            / len(x["bounding_box"])
        )

        return paragraph_lines


