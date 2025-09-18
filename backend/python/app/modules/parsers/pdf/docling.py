import asyncio
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    MarkdownFormatOption,
    PdfFormatOption,
    WordFormatOption,
)

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
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
             InputFormat.DOCX: WordFormatOption(),
                 InputFormat.MD: MarkdownFormatOption(),
        })
        self.doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=logger,config=config)

    async def load_document(self, doc_name: str, content: bytes) -> BlocksContainer|bool:
        stream = BytesIO(content)
        source = DocumentStream(name=doc_name, stream=stream)
        conv_res: ConversionResult = await asyncio.to_thread(self.converter.convert, source)
        if conv_res.status.value != SUCCESS_STATUS:
            raise ValueError(f"Failed to parse PDF: {conv_res.status}")

        doc = conv_res.document
        block_containers = await self.doc_to_blocks_converter.convert(doc)
        if block_containers is False:
            return False

        return block_containers

    def process_document(self) -> None:
        pass



