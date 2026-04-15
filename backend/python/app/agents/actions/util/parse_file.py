"""
Parse supported file bytes into a ``BlocksContainer``, mirroring ``Processor`` flows.

- PDF: ``PyMuPDFOpenCVProcessor.load_document`` (PyMuPDF + OpenCV), not Docling.
- DOCX / PPTX / MD / (HTML→MD) / TXT: ``DoclingProcessor`` parse + ``create_blocks``.
- DOC / XLS / PPT: OLE2 → OOXML via existing converters, then same as DOCX / XLSX / PPTX.
- CSV / TSV: decode → ``read_raw_rows`` → ``find_tables_in_csv`` →
  ``get_blocks_from_csv_with_multiple_tables`` (requires LLM).
- XLSX: ``ExcelParser.load_workbook_from_binary`` → ``create_blocks`` (requires LLM).
- MDX: ``MDXParser.convert_mdx_to_md`` then MD pipeline.

Only the extensions in ``SUPPORTED_BLOCK_FILE_EXTENSIONS`` are accepted.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from tarfile import data_filter
from typing import Final, Optional
from typing import Any
import json
from bs4 import BeautifulSoup
from html_to_markdown import convert

from app.api.routes.chatbot import get_model_config
from app.config.configuration_service import ConfigurationService
from app.models.entities import FileRecord
from app.models.blocks import BlockType, BlocksContainer
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.xls_parser import XLSParser
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.markdown.mdx_parser import MDXParser
from app.modules.parsers.pdf.docling import DoclingProcessor
from app.modules.parsers.pdf.pymupdf_opencv_processor import PyMuPDFOpenCVProcessor
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.utils.chat_helpers import count_tokens_text
from app.utils.llm import get_llm

# ---------------------------------------------------------------------------
# Supported extensions (OOXML, OLE2, and text/markup as specified)
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        "pdf",
        "md",
        "html",
        "doc",
        "ppt",
        "xls",
        "xlsx",
        "pptx",
        "docx",
        "csv",
        "tsv",
        "txt",
        "mdx",
    }
)

_MAGIC_PDF: Final[bytes] = b"%PDF"
_MAGIC_ZIP: Final[bytes] = b"PK\x03\x04"
_MAGIC_OLE2: Final[bytes] = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"


def _validate_magic(data: bytes, ext: str) -> None:
    """Raise ``ValueError`` if magic bytes contradict the declared extension."""
    if ext == "pdf":
        if len(data) < 4 or data[:4] != _MAGIC_PDF:
            raise ValueError("File does not look like a PDF (unexpected magic bytes)")
    elif ext in {"docx", "xlsx", "pptx"}:
        if len(data) < 4 or data[:4] != _MAGIC_ZIP:
            raise ValueError(f".{ext} file does not look like a ZIP/OOXML container")
    elif ext in {"doc", "xls", "ppt"}:
        if len(data) < 8 or data[:8] != _MAGIC_OLE2:
            raise ValueError(f".{ext} file does not look like an OLE2 container")


def _docling_document_name(file_name: str, ext: str) -> str:
    stem = Path(file_name).stem if file_name else "document"
    return f"{stem}.{ext}"


class FileContentParser:
    """Build ``BlocksContainer`` from raw bytes for supported office/document types."""

    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(
        self,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> None:
        self._logger = logger
        self._config = config_service

        self._md_parser = MarkdownParser()
        self._mdx_parser = MDXParser()
        self._html_parser = HTMLParser()
        self._doc_parser = DocParser()
        self._xls_parser = XLSParser()
        self._ppt_parser = PPTParser()
        # Same as Processor: PNG parser used for fetching remote images in Markdown
        self._image_parser = ImageParser(logger)

        self._csv_parser = CSVParser()
        self._tsv_parser = CSVParser(delimiter="\t")

    async def parse_raw_to_blocks(
        self,
        raw: bytes,
        ext: str,
        *,
        file_name: str = "document",
    ) -> BlocksContainer:
        """
        Parse ``raw`` using ``ext`` and return blocks.

        ``ext`` must be normalized by the caller (lowercase, no leading dot), e.g. ``\"docx\"``.

        Raises:
            ValueError: unknown extension or magic-byte mismatch for binary formats.
        """
        ext_n = ext
        if not ext_n:
            raise ValueError("File extension is required (normalized: lowercase, no leading dot)")
        if ext_n not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type: .{ext_n}. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_EXTENSIONS))}"
            )

        if ext_n in {
            "pdf",
            "docx",
            "xlsx",
            "pptx",
            "doc",
            "xls",
            "ppt",
        }:
            _validate_magic(raw, ext_n)

        dispatch = {
            "pdf": self.handle_pdf,
            "md": self.handle_md,
            "html": self.handle_html,
            "mdx": self.handle_mdx,
            "doc": self.handle_doc,
            "docx": self.handle_docx,
            "ppt": self.handle_ppt,
            "pptx": self.handle_pptx,
            "xls": self.handle_xls,
            "xlsx": self.handle_xlsx,
            "csv": self.handle_csv,
            "tsv": self.handle_tsv,
            "txt": self.handle_txt,
        }
        return await dispatch[ext_n](raw, file_name)
    
    def is_supported_extension(self, extension: str) -> bool:
        return extension in self.SUPPORTED_EXTENSIONS

    async def parse_to_block_container(
        self,
        file_record: FileRecord,
        raw: bytes,
    ) -> BlocksContainer:
        if not file_record:
            raise ValueError("File record is required")
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise ValueError("File content is required")

        ext_n = (file_record.extension or "").strip().lower().lstrip(".")
        file_name = file_record.record_name or "document"
        return await self.parse_raw_to_blocks(bytes(raw), ext_n, file_name=file_name)

    async def check_token_limit(
        self,
        model_name: Optional[str],
        model_key: Optional[str],
        configuration_service: ConfigurationService,
        data: list[dict[str, Any]],
    ) -> bool:
        if not data:
            return True

        model_config, _ = await get_model_config(configuration_service, model_key, model_name)
        if isinstance(model_config, list):
            model_config = model_config[0] if model_config else {}
        context_length = (model_config or {}).get("contextLength") or 128_000
        max_allowed_tokens = int(context_length * 0.8)

        token_count = 0
        for message in data:
            if not isinstance(message, dict):
                continue
            text_content = message.get("text", "") if message.get("type") == "text" else ""
            if text_content:
                token_count += count_tokens_text(text_content, None)
        print(f"\033[95m[check_token_limit] token_count: {token_count} | max_allowed_tokens: {max_allowed_tokens}\033[0m")
        return token_count < max_allowed_tokens

    async def parse(
        self,
        file_record: FileRecord,
        raw: bytes,
        model_name: Optional[str],
        model_key: Optional[str],
        configuration_service: ConfigurationService,
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Return ``(True, llm_context)`` on success, or ``(False, error_message)``."""
        try:
            ext_n = (file_record.extension or "").strip().lower().lstrip(".")
            if not ext_n or not self.is_supported_extension(ext_n):
                return (False, [{"error": "File type not supported"}])

            try:
                blocks = await self.parse_to_block_container(file_record, raw)
            except Exception as exc:
                self._logger.exception("parse_to_block_container failed")
                return (False, [{"error": f"Parse failed: {exc}"}])

            file_record.block_containers = blocks

            try:
                llm_context = file_record.to_llm_full_context()
            except Exception as exc:
                self._logger.exception("to_llm_full_context failed")
                return (False, [{"error": f"Context build failed: {exc}"}])

            try:
                is_within_limit = await self.check_token_limit(
                    model_name=model_name,
                    model_key=model_key,
                    configuration_service=configuration_service,
                    data=llm_context,
                )
            except Exception as exc:
                self._logger.exception("Token check failed")
                return (False, [{"error": f"Token check failed: {exc}"}])

            print(f"\033[32m[content_parser] llm_context: {json.dumps(llm_context, indent=4)}\033[0m")

            if is_within_limit:
                return (True, llm_context)
            return (False, [{"error": "Token limit exceeded"}])
        except Exception as exc:
            self._logger.exception("Failed to parse file record")
            return (False, [{"error": f"Failed to parse file: {exc}"}])

    # --- File type parsers -----------------------

    async def handle_pdf(self, raw: bytes, file_name: str) -> BlocksContainer:
        name = file_name if file_name.lower().endswith(".pdf") else f"{file_name}.pdf"
        processor = PyMuPDFOpenCVProcessor(logger=self._logger, config=self._config)
        return await processor.load_document(name, raw)

    async def handle_docx(self, raw: bytes, file_name: str) -> BlocksContainer:
        processor = DoclingProcessor(logger=self._logger, config=self._config)
        doc_name = _docling_document_name(file_name, "docx")
        conv_res = await processor.parse_document(doc_name, raw)
        return await processor.create_blocks(conv_res)

    async def handle_pptx(self, raw: bytes, file_name: str) -> BlocksContainer:
        processor = DoclingProcessor(logger=self._logger, config=self._config)
        doc_name = _docling_document_name(file_name, "pptx")
        conv_res = await processor.parse_document(doc_name, raw)
        return await processor.create_blocks(conv_res)

    async def handle_doc(self, raw: bytes, file_name: str) -> BlocksContainer:
        docx_stream = self._doc_parser.convert_doc_to_docx(raw)
        docx_bytes = docx_stream.read()
        return await self.handle_docx(docx_bytes, file_name)

    async def handle_ppt(self, raw: bytes, file_name: str) -> BlocksContainer:
        pptx_bytes = self._ppt_parser.convert_ppt_to_pptx(raw)
        return await self.handle_pptx(pptx_bytes, file_name)

    async def handle_xls(self, raw: bytes, file_name: str) -> BlocksContainer:
        xlsx_bytes = self._xls_parser.convert_xls_to_xlsx(raw)
        return await self.handle_xlsx(xlsx_bytes, file_name)

    async def handle_xlsx(self, raw: bytes, file_name: str) -> BlocksContainer:
        llm, _ = await get_llm(self._config)
        excel = ExcelParser(self._logger)
        excel.load_workbook_from_binary(raw)
        return await excel.create_blocks(llm)

    async def handle_csv(self, raw: bytes, file_name: str) -> BlocksContainer:
        return await self._handle_delimited(raw, file_name, self._csv_parser)

    async def handle_tsv(self, raw: bytes, file_name: str) -> BlocksContainer:
        return await self._handle_delimited(raw, file_name, self._tsv_parser)

    async def _handle_delimited(
        self,
        raw: bytes,
        file_name: str,
        parser: CSVParser,
    ) -> BlocksContainer:
        encodings = ["utf-8", "latin1", "cp1252", "iso-8859-1"]
        all_rows = None
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                stream = io.StringIO(text)
                all_rows = parser.read_raw_rows(stream)
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                continue

        if not all_rows:
            self._logger.info(
                "Delimited file empty or undecodable for %s — returning empty blocks",
                file_name,
            )
            return BlocksContainer(blocks=[], block_groups=[])

        tables = parser.find_tables_in_csv(all_rows)
        llm, _ = await get_llm(self._config)
        return await parser.get_blocks_from_csv_with_multiple_tables(tables, llm)

    async def handle_md(self, raw: bytes, file_name: str) -> BlocksContainer:
        md_content = None
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "iso-8859-1"):
            try:
                md_content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if md_content is None:
            raise ValueError("Unable to decode Markdown with any supported encoding")
        return await self._markdown_string_to_blocks(md_content, file_name)

    async def handle_txt(self, raw: bytes, file_name: str) -> BlocksContainer:
        text_content = None
        for encoding in ("utf-8", "utf-8-sig", "latin-1", "iso-8859-1"):
            try:
                text_content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text_content is None:
            raise ValueError("Unable to decode text file with any supported encoding")
        return await self._markdown_string_to_blocks(text_content, file_name)

    async def handle_mdx(self, raw: bytes, file_name: str) -> BlocksContainer:
        md_bytes = self._mdx_parser.convert_mdx_to_md(raw)
        md_content = md_bytes.decode("utf-8")
        return await self._markdown_string_to_blocks(md_content, file_name)

    async def handle_html(self, raw: bytes, file_name: str) -> BlocksContainer:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            for element in soup(
                ["script", "style", "noscript", "iframe", "nav", "footer", "header"]
            ):
                element.decompose()
            html_content = str(soup)
        except Exception as e:
            self._logger.warning("Failed to clean HTML: %s", e)
            html_content = raw.decode("utf-8", errors="replace")

        html_content = self._html_parser.replace_relative_image_urls(html_content)
        markdown = convert(html_content)
        return await self._markdown_string_to_blocks(markdown, file_name)

    async def _markdown_string_to_blocks(
        self,
        markdown: str,
        file_name: str,
    ) -> BlocksContainer:
        markdown = (markdown or "").strip()
        if not markdown:
            return BlocksContainer(blocks=[], block_groups=[])

        modified_markdown, images = self._md_parser.extract_and_replace_images(markdown)
        caption_map: dict[str, str] = {}
        urls_to_convert = [image["url"] for image in images]
        if urls_to_convert:
            base64_urls = await self._image_parser.urls_to_base64(urls_to_convert)
            for i, image in enumerate(images):
                if base64_urls[i]:
                    caption_map[image["new_alt_text"]] = base64_urls[i]

        md_bytes = self._md_parser.parse_string(modified_markdown)
        processor = DoclingProcessor(logger=self._logger, config=self._config)
        stem = Path(file_name).stem
        conv_res = await processor.parse_document(f"{stem}.md", md_bytes)
        block_containers = await processor.create_blocks(conv_res)

        for block in block_containers.blocks:
            if block.type == BlockType.IMAGE and block.image_metadata:
                caps = block.image_metadata.captions
                if caps:
                    caption = caps[0]
                    uri = caption_map.get(caption)
                    if uri:
                        if block.data is None or not isinstance(block.data, dict):
                            block.data = {"uri": uri}
                        else:
                            block.data["uri"] = uri
                    else:
                        self._logger.warning(
                            "Skipping image with caption %r — no base64 data", caption
                        )

        return block_containers
