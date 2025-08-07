from io import BytesIO

from docling.datamodel.base_models import DocumentStream
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter


class PPTXParser:
    def __init__(self) -> None:
        self.converter = DocumentConverter()

    def parse_binary(self, pptx_binary: bytes) -> DoclingDocument:
        """
        Parse PPTX content from binary data.

        Args:
            pptx_binary (bytes): PPTX content as bytes

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Create a BytesIO object from the bytes
        stream = BytesIO(pptx_binary)

        # Create a DocumentStream
        source = DocumentStream(name="presentation.pptx", stream=stream)

        # Convert the document using PPTX format
        result = self.converter.convert(source)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse PPTX: {result.status}")

        return result.document

    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse PPTX content from a file.

        Args:
            file_path (str): Path to the PPTX file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse PPTX: {result.status}")

        return result.document
