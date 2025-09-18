
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter


class HTMLParser:
    def __init__(self) -> None:
        self.converter = DocumentConverter()

    def parse_string(self, html_content: str) -> bytes:
        """
        Parse HTML content from a string.

        Args:
            html_content (str): HTML content as a string

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Convert string to bytes
        html_bytes = html_content.encode("utf-8")
        return html_bytes


    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse HTML content from a file.

        Args:
            file_path (str): Path to the HTML file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse HTML: {result.status}")

        return result.document


# Example usage:
if __name__ == "__main__":
    # Initialize parser
    parser = HTMLParser()

    # Example with string
    html_content = """
    <html>
        <body>
            <h1>Hello World</h1>
            <p>This is a test paragraph.</p>
        </body>
    </html>
    """

    try:
        # Parse HTML string
        doc = parser.parse_string(html_content)
        print("String parsing result:", doc)

    except ValueError as e:
        print(f"Error: {e}")
