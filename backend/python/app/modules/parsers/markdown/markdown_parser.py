
import markdown
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter


class MarkdownParser:
    def __init__(self) -> None:
        self.converter = DocumentConverter()

    def parse_string(self, md_content: str) -> bytes:
        """
        Parse Markdown content from a string.

        Args:
            md_content (str): Markdown content as a string

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Convert string to bytes

        html = markdown.markdown(md_content, extensions=["md_in_html"])
        md_bytes = html.encode("utf-8")
        return md_bytes

    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse Markdown content from a file.

        Args:
            file_path (str): Path to the Markdown file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse Markdown: {result.status}")

        return result.document
