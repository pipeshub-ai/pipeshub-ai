import asyncio
import logging
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.document import ConversionResult
from docling.document_converter import DocumentConverter

from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter

# from app.models.blocks import Blocks

SUCCESS_STATUS = "success"

class DoclingPDFProcessor():
    def __init__(self, logger):
        self.logger = logger
        self.converter = DocumentConverter(allowed_formats=[
            InputFormat.PDF,
        ])
        self.doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=logger)

    async def load_document(self, doc_name: str, content: bytes) -> None:
        stream = BytesIO(content)
        source = DocumentStream(name=doc_name, stream=stream)
        conv_res: ConversionResult = await asyncio.to_thread(self.converter.convert, source)
        if conv_res.status.value != SUCCESS_STATUS:
            raise ValueError(f"Failed to parse PDF: {conv_res.status}")

        doc_dict = conv_res.document.export_to_dict()

        self.doc_to_blocks_converter.convert(doc_dict)
        # print(json.dumps(blocks, indent=4))

        # return blocks


    def process_document(self) -> None:
        pass


if __name__ == "__main__":
    processor = DoclingPDFProcessor(logger=logging.getLogger(__name__))
    with open("your_document.pdf", "rb") as f:
        content = f.read()
    asyncio.run(processor.load_document("your_document.pdf", content))
