import asyncio
from io import BytesIO

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    MarkdownFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling_core.types.doc.document import DoclingDocument

from app.models.blocks import BlocksContainer
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter

SUCCESS_STATUS = "success"

class DoclingProcessor():
    def __init__(self, logger, config) -> None:
        self.logger = logger
        self.config = config
        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_picture_images = True
        pipeline_options.do_ocr = False

        self.converter = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend),
            InputFormat.DOCX: WordFormatOption(),
            InputFormat.MD: MarkdownFormatOption(),
        })

    async def parse_document(self, doc_name: str, content: bytes) -> DoclingDocument:
        """Parse document and return raw Docling result (no block conversion).

        This is the first phase of document processing - pure parsing without LLM calls.
        """
        stream = BytesIO(content)
        source = DocumentStream(name=doc_name, stream=stream)
        conv_res: ConversionResult = await asyncio.to_thread(self.converter.convert, source)
        if conv_res.status.value != SUCCESS_STATUS:
            raise ValueError(f"Failed to parse document: {conv_res.status}")

        doc = conv_res.document
        return doc

    async def create_blocks(self, doc: DoclingDocument, page_number: int = None) -> BlocksContainer:
        """Convert parsed Docling result to BlocksContainer.

        This is the second phase - involves LLM calls for table processing.
        """
        doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=self.logger, config=self.config)
        block_containers = await doc_to_blocks_converter.convert(doc, page_number=page_number)
        return block_containers

    async def load_document(self, doc_name: str, content: bytes, page_number: int = None) -> BlocksContainer|bool:
        """Parse document and create blocks in one call (legacy method).

        For new code, prefer using parse_document() followed by create_blocks()
        to allow yielding progress events between phases.
        """
        conv_res = await self.parse_document(doc_name, content)
        return await self.create_blocks(conv_res, page_number=page_number)

    def process_document(self) -> None:
        pass



