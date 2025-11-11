
import os
import tempfile
from typing import Any, Dict, List

import fitz
import ocrmypdf
import spacy
from spacy.language import Language
from spacy.tokens import Doc

from app.models.blocks import Block, BlockType, CitationMetadata, DataFormat, Point
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.utils.indexing_helpers import (
    _normalize_bbox,
    image_bytes_to_base64,
    process_table_pymupdf,
)

LENGTH_THRESHOLD = 2

class PyMuPDFOCRStrategy(OCRStrategy):
    def __init__(self, logger, config, language: str = "eng") -> None:
        self.logger = logger
        self.language = language
        self.config = config
        self.doc = None
        self._processed_pages = {}
        self._needs_ocr = False
        self.document_analysis_result = None
        self.nlp = self._create_custom_tokenizer(spacy.load("en_core_web_sm"))
        self.ocr_pdf_content = None

    async def load_document(self, content: bytes) -> None:
        """Load and analyze document"""
        self.logger.info("🔄 Starting document load...")

        # Load with PyMuPDF first
        self.logger.debug("📄 Initial PyMuPDF load")
        self.logger.debug(f"Content type: {type(content)} {len(content)}")
        temp_doc = fitz.open(stream=content, filetype="pdf")

        # Check if any page needs OCR
        self.logger.debug("🔍 Checking if document needs OCR")
        needs_ocr = any(self.needs_ocr(page) for page in temp_doc)
        self._needs_ocr = needs_ocr
        self.logger.debug(f"📊 OCR need determination: {needs_ocr}")

        if needs_ocr:
            self.logger.info("🤖 Document needs OCR, processing with OCRmyPDF")
            try:
                self.logger.debug("📝 Creating temporary files for OCR processing")
                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as temp_in, tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as temp_out:

                    self.logger.debug("📤 Writing content to temporary input file")
                    temp_in.write(content)
                    temp_in.flush()

                    self.logger.debug("🔄 Running OCRmyPDF")
                    ocrmypdf.ocr(
                        temp_in.name,
                        temp_out.name,
                        language=self.language,
                        output_type="pdf",
                        force_ocr=True,
                        optimize=0,
                        progress_bar=False,
                        deskew=True,
                        clean=True,
                        quiet=True,
                    )

                    self.logger.debug("📥 Loading OCR-processed PDF")
                    with open(temp_out.name, "rb") as f:
                        ocr_content = f.read()
                        processed_doc = fitz.open("pdf", ocr_content)
                        # Store the OCR-processed PDF content
                        self.ocr_pdf_content = ocr_content

                        # # Create output directory if it doesn't exist
                        # output_dir = "output/searchable/pymupdf"
                        # os.makedirs(output_dir, exist_ok=True)
                        # self.logger.debug(f"📁 Using output directory: {output_dir}")

                        # # Generate unique filename using timestamp
                        # timestamp = time.strftime("%Y%m%d_%H%M%S")
                        # output_filename = f"ocr_processed_{timestamp}.pdf"
                        # output_path = os.path.join(output_dir, output_filename)

                        # # Save the PDF to file
                        # self.logger.info(
                        #     f"💾 Saving OCR-processed PDF to: {output_path}")
                        # with open(output_path, "wb") as ocr_file:
                        #     ocr_file.write(ocr_content)

                self.logger.info("✅ OCR processing completed successfully")
                self.doc = processed_doc

            except Exception as e:
                self.logger.error(f"❌ OCR processing failed: {str(e)}")
                self.logger.info("⚠️ Falling back to direct PyMuPDF extraction")
                self.doc = temp_doc
                self._needs_ocr = False
                self.ocr_pdf_content = None

            finally:
                self.logger.debug("🧹 Cleaning up temporary files")
                for path in [temp_in.name, temp_out.name]:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception as e:
                            self.logger.error(
                                "❌ Error cleaning up temp file, %s: %s", path, str(e)
                            )
        else:
            self.logger.info(
                "📝 Document doesn't need OCR, using direct PyMuPDF extraction"
            )
            self.doc = temp_doc
            self.ocr_pdf_content = None

        self.logger.debug("🔄 Pre-processing document to match Azure's structure")
        self.document_analysis_result = await self._preprocess_document()
        self.logger.info(f"✅ Document loaded with {len(self.doc)} pages")


    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc) -> Doc:
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
                     "pvt",
                    "llc",
                    "llp",
                    "lp",
                    "ll",
                    "ltd",
                    "inc",
                    "corp",
                ]
                and next_token.text == "."
            ):
                next_token.is_sent_start = False

            # Handle bullet points and list markers
            elif (
                # Numeric bullets with period (1., 2., etc)
                (
                    token.like_num and next_token.text == "." and len(token.text) <= LENGTH_THRESHOLD
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

    def _create_custom_tokenizer(self, nlp) -> Language:
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

    def _merge_bounding_boxes(
        self, bboxes: List[List[Dict[str, float]]]
    ) -> List[Dict[str, float]]:
        """Merge multiple bounding boxes into one encompassing box"""
        all_points = [point for box in bboxes for point in box]
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

    def _merge_lines_to_sentences(
        self, lines_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge lines into sentences using spaCy"""
        self.logger.debug("🚀 Merging lines to sentences")

        full_text = ""
        line_map = []
        char_index = 0

        # Log each line being processed
        for line_data in lines_data:
            content = line_data["content"].strip()
            if not content:
                continue

            full_text += content + "\n "
            line_map.append(
                (char_index, char_index + len(content), line_data["bounding_box"])
            )
            char_index += len(content) + 1

        doc = self.nlp(full_text)
        sentences = []

        # Log each sentence being formed
        for sent in doc.sents:
            sent_text = sent.text.strip()
            sent_start, sent_end = sent.start_char, sent.end_char

            sentence_bboxes = []
            for start_idx, end_idx, bbox in line_map:
                if start_idx < sent_end and end_idx > sent_start:
                    sentence_bboxes.append(bbox)

            merged_bbox = (
                self._merge_bounding_boxes(sentence_bboxes) if sentence_bboxes else None
            )

            sentences.append({"sentence": sent_text, "bounding_box": merged_bbox})

        self.logger.debug(f"✅ Merged into {len(sentences)} sentences")
        return sentences

    def _process_block_text(
        self, block: Dict[str, Any], page_width: float, page_height: float, block_number: int
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
        block_text = ""
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
                    "bounding_box": _normalize_bbox(
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
                        span_data = {
                            "text": span.get("text", ""),
                            "bounding_box": _normalize_bbox(
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
                                    "bounding_box": _normalize_bbox(
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
        block_text = " ".join(sentence["sentence"] for sentence in sentences)
        for sentence in sentences:
            sentence_data = {
                "content": sentence["sentence"],
                "bounding_box": sentence["bounding_box"],
                "block_number": block_number,
                "block_type": block.get("type"),
                "block_text": block_text,
                "metadata": block_metadata,
            }
            processed_sentences.append(sentence_data)

        # Create paragraph from block
        paragraph = {
            "content": block_text.strip(),
            "bounding_box": _normalize_bbox(
                block["bbox"], page_width, page_height
            ),
            "block_number": block_number,
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

    def _should_merge_blocks(
        self, block1: Dict[str, Any], block2: Dict[str, Any], word_threshold: int = 15
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
        """Pre-process document to match Azure's structure"""
        self.logger.debug("🔄 Starting document pre-processing")
        result = {
            "pages": [],
            "lines": [],
            "paragraphs": [],
            "sentences": [],
            "tables": [],
            "blocks": [],
            "key_value_pairs": [],
        }

        block_number = 0
        for page_idx in range(len(self.doc)):
            self.logger.debug(f"📄 Processing page {page_idx + 1}")
            page = self.doc[page_idx]
            page_width = page.rect.width
            page_height = page.rect.height

            self.logger.debug("📊 Processing page content")
            page_dict = {
                "page_number": page_idx + 1,
                "width": page_width,
                "height": page_height,
                "unit": "pt",
                "lines": [],
                "words": [],
                "tables": [],
            }

            self.logger.debug("📝 Extracting text blocks and paragraphs")
            text_dict = page.get_text("dict")
            blocks = text_dict.get("blocks", [])

            # Process and merge blocks
            merged_blocks = []
            i = 0
            while i < len(blocks):
                current_block = blocks[i]
                next_index = i + 1

                # Keep merging blocks until we have enough words or run out of blocks
                while next_index < len(blocks) and self._should_merge_blocks(
                    current_block, blocks[next_index]
                ):
                    self.logger.debug(f"Merging blocks {i} and {next_index}")
                    current_block = self._merge_block_content(
                        current_block, blocks[next_index]
                    )
                    next_index += 1

                merged_blocks.append(current_block)
                i = next_index if next_index > i + 1 else i + 1

            # Process merged blocks
            for block in merged_blocks:
                if block.get("type") == 0:  # Text block
                    processed_block = self._process_block_text(
                        block, page_width, page_height, block_number
                    )
                    # Add to page-level collections
                    page_dict["lines"].extend(processed_block["lines"])
                    page_dict["words"].extend(processed_block["words"])

                    # Add to document-level collections
                    if processed_block["paragraph"]:
                        processed_block["paragraph"]["page_number"] = page_idx + 1
                        result["paragraphs"].append(processed_block["paragraph"])
                        self.logger.debug(
                            "📚 Added paragraph to document collection (Page %s, Block %s)",
                            page_idx + 1,
                            processed_block["paragraph"]["block_number"],
                        )

                    for sentence in processed_block["sentences"]:
                        sentence["page_number"] = page_idx + 1
                        result["sentences"].append(sentence)
                        self.logger.debug(
                            "📑 Added sentence to document collection (Page %s, Block %s)",
                            page_idx + 1,
                            sentence["block_number"],
                        )
                    result["blocks"].append(processed_block["paragraph"])
                    block_number += 1
                elif block.get("type") == 1:  # Image block
                    ext = block.get("ext")
                    if ext not in ["png", "jpg", "jpeg", "webp"]:
                        continue
                    image_bytes = block.get("image", None)
                    if image_bytes:
                        image_base64 = image_bytes_to_base64(image_bytes, ext)
                        bbox = _normalize_bbox(block["bbox"], page_width, page_height) if block.get("bbox") else None
                        bbox = [Point(x=p["x"], y=p["y"]) for p in bbox]
                        block = Block(
                            type=BlockType.IMAGE,
                            format=DataFormat.BASE64,
                            data={"uri": image_base64},
                            comments=[],
                            citation_metadata=CitationMetadata(
                                page_number=page_idx + 1,
                                bounding_boxes=bbox,
                            ),
                        )
                        result["blocks"].append(block)

            await process_table_pymupdf(page, result, self.config,page_idx+1)

            self.logger.debug(f"✅ Completed processing page {page_idx + 1}")
            self.logger.debug("📊 Page statistics:")
            self.logger.debug(f"- Lines: {len(page_dict['lines'])}")
            self.logger.debug(f"- Words: {len(page_dict['words'])}")
            result["pages"].append(page_dict)

        self.logger.debug("📊 Final document analysis result:")
        self.logger.debug(f"- Total pages: {len(result['pages'])}")
        self.logger.debug(f"- Total paragraphs: {len(result['paragraphs'])}")
        self.logger.debug(f"- Total sentences: {len(result['sentences'])}")

        return result





    async def process_page(self, page) -> Dict[str, Any]:
        """Process a single page"""
        self.logger.debug("📊 Processing page content")
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
                        "bounding_box": _normalize_bbox(
                            (x0, y0, x1, y1), page_width, page_height
                        ),
                    }
                )

        # Extract lines
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                text = " ".join(span.get("text", "") for span in line.get("spans", []))
                if text.strip() and line.get("bbox"):
                    lines.append(
                        {
                            "content": text.strip(),
                            "bounding_box": _normalize_bbox(
                                line["bbox"], page_width, page_height
                            ),
                        }
                    )

        self.logger.debug("✅ Completed processing page")

        return {
            "words": words,
            "lines": lines,
            "page_width": page_width,
            "page_height": page_height,
        }

    def print_merge_statistics(self) -> None:
        """Print statistics about the merged sentences and paragraphs"""
        if not self.document_analysis_result:
            self.logger.error("❌ No document analysis result available")
            return

        self.logger.info("📊 Document Merge Statistics:")

        # Page statistics
        for page_idx, page in enumerate(self.document_analysis_result["pages"]):
            self.logger.info(f"\n📄 Page {page_idx + 1}:")
            self.logger.info(f"- Lines: {len(page['lines'])}")
            self.logger.info(f"- Words: {len(page['words'])}")

            # Count sentences and paragraphs on this page
            page_sentences = [
                s
                for s in self.document_analysis_result["sentences"]
                if s["page_number"] == page_idx + 1
            ]
            page_paragraphs = [
                p
                for p in self.document_analysis_result["paragraphs"]
                if p["page_number"] == page_idx + 1
            ]

            self.logger.info(f"- Sentences: {len(page_sentences)}")
            self.logger.info(f"- Paragraphs: {len(page_paragraphs)}")

            # Log sample content
            if page_sentences:
                self.logger.info("\n📝 Sample sentences:")
                # Show first 3 sentences
                for i, sent in enumerate(page_sentences[:3]):
                    self.logger.info(
                        "   Block: %s, BBox: %s",
                        sent["block_number"],
                        sent["bounding_box"],
                    )

            if page_paragraphs:
                self.logger.info("\n📚 Sample paragraphs:")
                # Show first 2 paragraphs
                for i, para in enumerate(page_paragraphs[:2]):
                    self.logger.info(
                        "   Block: %s, BBox: %s",
                        para["block_number"],
                        para["bounding_box"],
                    )

    def create_debug_pdf(self, output_path: str) -> None:
        """Create a debug PDF showing sentence and paragraph boundaries"""
        self.logger.info("🎨 Creating debug visualization PDF")

        # Copy original document
        debug_doc = fitz.open()
        for page in self.doc:
            debug_doc.new_page(width=page.rect.width, height=page.rect.height)

        # Define colors
        paragraph_color = (1, 0, 0)  # Red for paragraphs
        sentence_color = (0, 0, 1)  # Blue for sentences

        for page_idx, page in enumerate(debug_doc):
            page_width = page.rect.width
            page_height = page.rect.height

            # Draw paragraphs
            self.logger.debug(f"📝 Drawing paragraphs for page {page_idx + 1}")
            for para in self.document_analysis_result["paragraphs"]:
                if para["page_number"] - 1 == page_idx:
                    bbox = para["bounding_box"]
                    rect = fitz.Rect(
                        bbox[0]["x"] * page_width,
                        bbox[0]["y"] * page_height,
                        bbox[2]["x"] * page_width,
                        bbox[2]["y"] * page_height,
                    )
                    # Draw rectangle with red color
                    page.draw_rect(rect, color=paragraph_color, width=1)

            # Draw sentences
            self.logger.debug(f"📝 Drawing sentences for page {page_idx + 1}")
            for sent in self.document_analysis_result["sentences"]:
                if sent["page_number"] - 1 == page_idx:
                    bbox = sent["bounding_box"]
                    rect = fitz.Rect(
                        bbox[0]["x"] * page_width,
                        bbox[0]["y"] * page_height,
                        bbox[2]["x"] * page_width,
                        bbox[2]["y"] * page_height,
                    )
                    # Draw rectangle with blue color
                    page.draw_rect(rect, color=sentence_color, width=0.5)

        # Save debug PDF
        debug_doc.save(output_path)
        debug_doc.close()
        self.logger.info(f"✅ Debug PDF saved to {output_path}")

