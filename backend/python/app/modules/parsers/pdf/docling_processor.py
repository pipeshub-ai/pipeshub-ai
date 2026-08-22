import asyncio
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING

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

    from app.services.resource_governor import ResourceGovernor

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter
from app.utils.pdf_utils import PAGE_BATCH_SIZE, get_pdf_page_count  # noqa: F401 - re-exported

SUCCESS_STATUS = "success"

# Wired by each service's lifespan (see parsing_main.py/indexing_main.py/
# docling_main.py) once a ResourceGovernor is constructed for that process —
# mirrors pdf_rasterizer.py's identical singleton and for the same reason:
# this module is a leaf shared across services with no DI path to whichever
# governor its caller's process owns.
_resource_governor: "ResourceGovernor | None" = None


def set_resource_governor(governor: "ResourceGovernor") -> None:
    """Wire an initialized ResourceGovernor so a worker OOM-kill can trigger
    its fast incident path instead of waiting for the next periodic sample."""
    globals()["_resource_governor"] = governor

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
            try:
                serialized_doc = await loop.run_in_executor(
                    _get_process_pool(),
                    _parse_document_in_worker,
                    doc_name,
                    raw_content,
                    page_range,
                )
            except BrokenProcessPool:
                self.logger.warning(
                    "Docling process pool broke while parsing '%s' (worker "
                    "likely OOM-killed); recreating pool",
                    doc_name,
                )
                _get_process_pool.cache_clear()
                if _resource_governor is not None:
                    # Same rationale as pdf_rasterizer.py: a worker OOM-kill
                    # is proof of memory exhaustion the periodic sampler may
                    # not see for several seconds — react now instead of
                    # letting admission keep granting heavy-parse slots at
                    # the limit that just caused this kill.
                    _resource_governor.report_memory_incident(
                        "docling worker OOM-killed (BrokenProcessPool)"
                    )
                raise
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

    async def create_blocks(
        self,
        doc: DoclingDocument,
        page_number: int | None = None,
        skip_table_enrichment: bool = False,
    ) -> BlocksContainer:
        """Convert parsed Docling result to BlocksContainer.

        This is the second phase - involves LLM calls for table processing,
        unless skip_table_enrichment is set.
        """
        doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=self.logger, config=self.config)
        return await doc_to_blocks_converter.convert(
            doc, page_number=page_number, skip_table_enrichment=skip_table_enrichment
        )

    def process_document(self) -> None:
        pass



