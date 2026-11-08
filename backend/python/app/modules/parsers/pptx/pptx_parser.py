from io import BytesIO
from typing import TYPE_CHECKING

from docling_core.types.doc.document import DoclingDocument

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.utils.lazy_heavy import require_off_loop

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter


class PPTXParser:
    """Parses PPTX with Docling. The library is imported when the converter is first
    needed, not when the parser is built (see ``app.utils.lazy_heavy``): call the parse
    methods from a worker thread, or after ``await ensure_docling()``."""

    def __init__(self) -> None:
        self._converter: "DocumentConverter | None" = None

    @property
    def converter(self) -> "DocumentConverter":
        if self._converter is None:
            require_off_loop()
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter(allowed_formats=[InputFormat.PPTX])
        return self._converter

    @converter.setter
    def converter(self, value: "DocumentConverter") -> None:
        self._converter = value

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

        from docling.datamodel.base_models import DocumentStream

        # Create a DocumentStream
        source = DocumentStream(name="presentation.pptx", stream=stream)

        # Convert the document using PPTX format
        result = self.converter.convert(source)

        if result.status.value != "success":
            raise DocumentProcessingError(
                f"Failed to parse PPTX: {result.status}",
                details={"status": str(result.status)},
            )

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
            raise DocumentProcessingError(
                f"Failed to parse PPTX: {result.status}",
                details={"status": str(result.status)},
            )

        return result.document
