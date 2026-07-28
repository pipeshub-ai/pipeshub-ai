import asyncio
import gc
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import (
    TableFormerMode,
    TableStructureOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import (
    DocumentConverter,
    MarkdownFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling_core.types.doc.document import DoclingDocument

if TYPE_CHECKING:
    from docling.datamodel.document import ConversionResult

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter

SUCCESS_STATUS = "success"

DEFAULT_PAGE_BATCH_SIZE = 10


def _get_page_batch_size() -> int:
    raw = os.getenv("DOCLING_PAGE_BATCH_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_PAGE_BATCH_SIZE


PAGE_BATCH_SIZE = _get_page_batch_size()


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
    # Stage batch sizes and queue depth are held far below docling's defaults (4 / 100)
    # to cap how many pages the threaded pipeline keeps in flight, trading throughput
    # for a lower CPU and RAM ceiling on low-resource deployments.
    pipeline_options = ThreadedPdfPipelineOptions(
        do_ocr=False,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.FAST,
        ),
        generate_picture_images=True,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=10,
    )
    return DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend),
        InputFormat.DOCX: WordFormatOption(),
        InputFormat.MD: MarkdownFormatOption(),
    })


def _parse_document_in_worker(
    doc_name: str, content: bytes, page_range: tuple[int, int] | None = None
) -> str:
    source = DocumentStream(name=doc_name, stream=BytesIO(content))
    kwargs: dict = {}
    if page_range is not None:
        kwargs["page_range"] = page_range
    conv_res: ConversionResult = _get_converter().convert(source, **kwargs)
    conv_res.input._backend.unload()
    if conv_res.status.value != SUCCESS_STATUS:
        raise DocumentProcessingError(
            f"Failed to parse document: {conv_res.status}",
            details={"status": str(conv_res.status)},
        )

    return conv_res.document.model_dump_json()


def get_pdf_page_count(content: bytes) -> int:
    """Return the number of pages in a PDF binary using pypdfium2."""
    pdf = pdfium.PdfDocument(content)
    try:
        return len(pdf)
    finally:
        pdf.close()


class DoclingProcessor():
    def __init__(self, logger: logging.Logger, config: object) -> None:
        self.logger = logger
        self.config = config
        self.converter = _get_converter()

    async def parse_document(
        self,
        doc_name: str,
        content: bytes | BytesIO,
        page_range: tuple[int, int] | None = None,
    ) -> DoclingDocument:
        """Parse document and return raw Docling result (no block conversion).

        Args:
            page_range: Optional 1-based inclusive (start, end) page range.
        """
        raw_content = content.getvalue() if isinstance(content, BytesIO) else content

        if LOCAL_DOCLING_PARSE_WORKERS > 1:
            loop = asyncio.get_running_loop()
            serialized_doc = await loop.run_in_executor(
                _get_process_pool(),
                _parse_document_in_worker,
                doc_name,
                raw_content,
                page_range,
            )
            return DoclingDocument.model_validate_json(serialized_doc)

        source = DocumentStream(name=doc_name, stream=BytesIO(raw_content))
        kwargs: dict = {}
        if page_range is not None:
            kwargs["page_range"] = page_range
        conv_res: ConversionResult = await asyncio.to_thread(
            self.converter.convert, source, **kwargs
        )
        conv_res.input._backend.unload()
        if conv_res.status.value != SUCCESS_STATUS:
            raise DocumentProcessingError(
                f"Failed to parse document: {conv_res.status}",
                details={"status": str(conv_res.status)},
            )

        return conv_res.document

    async def create_blocks(self, doc: DoclingDocument, page_number: int | None = None) -> BlocksContainer:
        """Convert parsed Docling result to BlocksContainer.

        This is the second phase - involves LLM calls for table processing.
        """
        doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=self.logger, config=self.config)
        return await doc_to_blocks_converter.convert(doc, page_number=page_number)

    async def process_in_batches(
        self,
        doc_name: str,
        content: bytes,
        batch_size: int = PAGE_BATCH_SIZE,
    ) -> BlocksContainer:
        """Parse the PDF in page-range batches to cap peak memory, then convert it as one document."""
        page_count = await asyncio.to_thread(get_pdf_page_count, content)

        if page_count <= batch_size:
            doc = await self.parse_document(doc_name, content)
            return await self.create_blocks(doc)

        self.logger.info(
            f"Parsing '{doc_name}' ({page_count} pages) in batches of {batch_size} pages"
        )
        docs: list[DoclingDocument] = []
        for start in range(1, page_count + 1, batch_size):
            end = min(start + batch_size - 1, page_count)
            docs.append(
                await self.parse_document(doc_name, content, page_range=(start, end))
            )
            self.logger.info(f"Parsed pages {start}-{end} of {page_count} for '{doc_name}'")
            gc.collect()

        merged = await asyncio.to_thread(DoclingDocument.concatenate, docs)
        # concatenate() names the result by joining every input name with " + ".
        merged.name = doc_name
        docs.clear()
        gc.collect()

        return await self.create_blocks(merged)

    async def load_document(self, doc_name: str, content: bytes, page_number: int | None = None) -> BlocksContainer|bool:
        """Parse document and create blocks in one call (legacy method).

        For new code, prefer using parse_document() followed by create_blocks()
        to allow yielding progress events between phases.
        """
        conv_res = await self.parse_document(doc_name, content)
        return await self.create_blocks(conv_res, page_number=page_number)

    def process_document(self) -> None:
        pass



