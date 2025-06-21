from io import BytesIO

import markdown
from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter


class MarkdownParser:
    def __init__(self):
        self.converter = DocumentConverter()

    def parse_string(self, md_content: str):
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

        # Create a BytesIO object from the bytes
        stream = BytesIO(md_bytes)

        # Create a DocumentStream
        source = DocumentStream(name="content.md", stream=stream)

        # Convert the document
        result = self.converter.convert(source)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse Markdown: {result.status}")

        return result.document

    def parse_file(self, file_path: str):
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
