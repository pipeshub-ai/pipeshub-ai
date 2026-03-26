import asyncio
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    MarkdownFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling_core.types.doc.document import DoclingDocument

if TYPE_CHECKING:
    from docling.datamodel.document import ConversionResult

from app.models.blocks import BlocksContainer
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter

SUCCESS_STATUS = "success"


def _get_local_parse_worker_count() -> int:
    raw_value = os.getenv("LOCAL_DOCLING_PARSE_WORKERS")
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            return 1

    return 1


LOCAL_DOCLING_PARSE_WORKERS = _get_local_parse_worker_count()


@lru_cache(maxsize=1)
def _get_process_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=LOCAL_DOCLING_PARSE_WORKERS,
        mp_context=multiprocessing.get_context("spawn"),
    )


@lru_cache(maxsize=1)
def _get_converter() -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = False

    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend),
        InputFormat.DOCX: WordFormatOption(),
        InputFormat.MD: MarkdownFormatOption(),
    })


def _parse_document_in_worker(doc_name: str, content: bytes) -> str:
    source = DocumentStream(name=doc_name, stream=BytesIO(content))
    conv_res: ConversionResult = _get_converter().convert(source)
    if conv_res.status.value != SUCCESS_STATUS:
        raise ValueError(f"Failed to parse document: {conv_res.status}")

    return conv_res.document.model_dump_json()

class DoclingProcessor():
    def __init__(self, logger: logging.Logger, config: object) -> None:
        self.logger = logger
        self.config = config
        self.converter = _get_converter()

    async def parse_document(self, doc_name: str, content: bytes | BytesIO) -> DoclingDocument:
        """Parse document and return raw Docling result (no block conversion).

        This is the first phase of document processing - pure parsing without LLM calls.
        """
        raw_content = content.getvalue() if isinstance(content, BytesIO) else content

        if LOCAL_DOCLING_PARSE_WORKERS > 1:
            loop = asyncio.get_running_loop()
            serialized_doc = await loop.run_in_executor(
                _get_process_pool(),
                _parse_document_in_worker,
                doc_name,
                raw_content,
            )
            return DoclingDocument.model_validate_json(serialized_doc)

        source = DocumentStream(name=doc_name, stream=BytesIO(raw_content))
        conv_res: ConversionResult = await asyncio.to_thread(self.converter.convert, source)
        if conv_res.status.value != SUCCESS_STATUS:
            raise ValueError(f"Failed to parse document: {conv_res.status}")

        return conv_res.document

    async def create_blocks(self, doc: DoclingDocument, page_number: int | None = None) -> BlocksContainer:
        """Convert parsed Docling result to BlocksContainer.

        This is the second phase - involves LLM calls for table processing.
        """
        doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=self.logger, config=self.config)
        return await doc_to_blocks_converter.convert(doc, page_number=page_number)

    async def load_document(self, doc_name: str, content: bytes, page_number: int | None = None) -> BlocksContainer|bool:
        """Parse document and create blocks in one call (legacy method).

        For new code, prefer using parse_document() followed by create_blocks()
        to allow yielding progress events between phases.
        """
        conv_res = await self.parse_document(doc_name, content)
        return await self.create_blocks(conv_res, page_number=page_number)

    def process_document(self) -> None:
        pass



