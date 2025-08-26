import asyncio
from io import BytesIO
from app.models.blocks import BlocksContainer
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.document import ConversionResult
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter

SUCCESS_STATUS = "success"

class DoclingPDFProcessor():
    def __init__(self, logger, config) -> None:
        self.logger = logger
        self.config = config
        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_picture_images = True

        self.converter = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        })
        self.doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=logger,config=config)
       
    async def load_document(self, doc_name: str, content: bytes) -> BlocksContainer:
        stream = BytesIO(content)
        source = DocumentStream(name=doc_name, stream=stream)
        conv_res: ConversionResult = await asyncio.to_thread(self.converter.convert, source)
        if conv_res.status.value != SUCCESS_STATUS:
            raise ValueError(f"Failed to parse PDF: {conv_res.status}")

        doc = conv_res.document
        block_containers = await self.doc_to_blocks_converter.convert(doc)
        return block_containers

    def process_document(self) -> None:
        pass



