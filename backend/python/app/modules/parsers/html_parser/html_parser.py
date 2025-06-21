from io import BytesIO

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter


class HTMLParser:
    def __init__(self):
        self.converter = DocumentConverter()

    def parse_string(self, html_content: str):
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

        # Create a BytesIO object from the bytes
        stream = BytesIO(html_bytes)

        # Create a DocumentStream
        source = DocumentStream(name="content.html", stream=stream)

        # Convert the document
        result = self.converter.convert(source)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse HTML: {result.status}")

        return result.document

    def parse_file(self, file_path: str):
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
