
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
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

    def get_base_url_from_html(self, soup: BeautifulSoup) -> str | None:
        """
        Extract base URL from HTML document using multiple fallback strategies.

        Args:
            soup: BeautifulSoup object

        Returns:
            Base URL as string, or None if not found
        """
        # Strategy 1: Look for <base> tag
        base_tag = soup.find('base', href=True)
        if base_tag:
            return base_tag['href']

        # Strategy 2: Look for canonical link
        canonical = soup.find('link', rel='canonical', href=True)
        if canonical:
            canonical_url = canonical['href']
            parsed = urlparse(canonical_url)
            return f"{parsed.scheme}://{parsed.netloc}"

        # Strategy 3: Look for any absolute URL in the document
        for tag_name, attr in [('link', 'href'), ('script', 'src'), ('img', 'src'), ('a', 'href')]:
            for tag in soup.find_all(tag_name):
                if tag.get(attr):
                    url = tag[attr]
                    parsed = urlparse(url)
                    if parsed.scheme and parsed.netloc:
                        return f"{parsed.scheme}://{parsed.netloc}"

        return None


    def replace_relative_image_urls(self, html_string) -> str:
        """
        Replace all relative image URLs with absolute URLs.
        Absolute URLs are left unchanged.
        Base URL is automatically extracted from the HTML document.

        Args:
            html_string: HTML content as string

        Returns:
            Modified HTML string with absolute image URLs
        """
        # Parse HTML
        soup = BeautifulSoup(html_string, 'html.parser')
        # Get base URL from HTML
        base_url = self.get_base_url_from_html(soup)

        if not base_url:
            return html_string



        # Find all img tags
        images = soup.find_all('img')

        # Replace relative URLs with absolute URLs
        for img in images:
            if img.get('src'):
                original_url = img['src']
                parsed = urlparse(original_url)

                # Only replace if it's a relative URL (no scheme)
                if not parsed.scheme:
                    absolute_url = urljoin(base_url, original_url)
                    img['src'] = absolute_url

        return str(soup)

