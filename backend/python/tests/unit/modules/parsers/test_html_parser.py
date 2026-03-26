"""Unit tests for app.modules.parsers.html_parser.html_parser.HTMLParser."""

from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from app.modules.parsers.html_parser.html_parser import HTMLParser


@pytest.fixture
def parser():
    """Create HTMLParser with mocked DocumentConverter to avoid heavy import."""
    with patch("app.modules.parsers.html_parser.html_parser.DocumentConverter"):
        return HTMLParser()


# ---------------------------------------------------------------------------
# parse_string
# ---------------------------------------------------------------------------
class TestParseString:
    def test_returns_bytes(self, parser):
        result = parser.parse_string("<html><body>Hello</body></html>")
        assert isinstance(result, bytes)

    def test_utf8_encoding(self, parser):
        result = parser.parse_string("<p>Hello</p>")
        assert result == b"<p>Hello</p>"

    def test_unicode_content(self, parser):
        result = parser.parse_string("<p>Caf\u00e9 \u2603</p>")
        assert result == "<p>Caf\u00e9 \u2603</p>".encode("utf-8")

    def test_empty_string(self, parser):
        result = parser.parse_string("")
        assert result == b""

    def test_special_characters(self, parser):
        html = "<p>&amp; &lt; &gt;</p>"
        result = parser.parse_string(html)
        assert result == html.encode("utf-8")


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------
class TestParseFile:
    def test_success(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "success"
        mock_result.document = MagicMock()
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.html_parser.html_parser.DocumentConverter", return_value=mock_converter):
            parser = HTMLParser()
            doc = parser.parse_file("/path/to/file.html")
            assert doc is mock_result.document
            mock_converter.convert.assert_called_once_with("/path/to/file.html")

    def test_failure_raises_value_error(self):
        mock_converter = MagicMock()
        mock_result = MagicMock()
        mock_result.status.value = "failure"
        mock_result.status.__str__ = lambda self: "failure"
        mock_converter.convert.return_value = mock_result

        with patch("app.modules.parsers.html_parser.html_parser.DocumentConverter", return_value=mock_converter):
            parser = HTMLParser()
            with pytest.raises(ValueError, match="Failed to parse HTML"):
                parser.parse_file("/path/to/file.html")


# ---------------------------------------------------------------------------
# get_base_url_from_html
# ---------------------------------------------------------------------------
class TestGetBaseUrlFromHtml:
    def test_base_tag(self, parser):
        html = '<html><head><base href="https://example.com/"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://example.com/"

    def test_canonical_link(self, parser):
        html = '<html><head><link rel="canonical" href="https://example.com/page/123"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://example.com"

    def test_canonical_link_strips_path(self, parser):
        html = '<html><head><link rel="canonical" href="https://cdn.example.com/assets/style.css"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://cdn.example.com"

    def test_absolute_url_in_link_tag(self, parser):
        html = '<html><head><link href="https://static.example.com/style.css"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://static.example.com"

    def test_absolute_url_in_script_tag(self, parser):
        html = '<html><head></head><body><script src="https://cdn.example.com/app.js"></script></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://cdn.example.com"

    def test_absolute_url_in_img_tag(self, parser):
        html = '<html><body><img src="https://images.example.com/logo.png"></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://images.example.com"

    def test_absolute_url_in_a_tag(self, parser):
        html = '<html><body><a href="https://www.example.com/page">Link</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://www.example.com"

    def test_no_url_found(self, parser):
        html = "<html><body><p>Just text</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result is None

    def test_only_relative_urls(self, parser):
        html = '<html><body><img src="/images/logo.png"><a href="/page">Link</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result is None

    def test_base_tag_takes_precedence_over_canonical(self, parser):
        html = (
            '<html><head>'
            '<base href="https://base.example.com/">'
            '<link rel="canonical" href="https://canonical.example.com/page">'
            '</head><body></body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://base.example.com/"

    def test_canonical_takes_precedence_over_other_tags(self, parser):
        html = (
            '<html><head>'
            '<link rel="canonical" href="https://canonical.example.com/page">'
            '</head><body>'
            '<img src="https://other.example.com/logo.png">'
            '</body></html>'
        )
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result == "https://canonical.example.com"

    def test_empty_html(self, parser):
        soup = BeautifulSoup("", "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result is None

    def test_tag_with_empty_href(self, parser):
        html = '<html><body><a href="">Empty</a><link href=""></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        result = parser.get_base_url_from_html(soup)
        assert result is None


# ---------------------------------------------------------------------------
# replace_relative_image_urls
# ---------------------------------------------------------------------------
class TestReplaceRelativeImageUrls:
    def test_relative_urls_converted(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><img src="/images/logo.png"></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        assert "https://example.com/images/logo.png" in result

    def test_absolute_urls_unchanged(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><img src="https://cdn.example.com/logo.png"></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        assert "https://cdn.example.com/logo.png" in result

    def test_no_base_url_returns_original(self, parser):
        html = '<html><body><img src="/images/logo.png"></body></html>'
        result = parser.replace_relative_image_urls(html)
        assert result == html

    def test_multiple_images(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body>'
            '<img src="/img/a.png">'
            '<img src="/img/b.png">'
            '<img src="https://other.com/c.png">'
            '</body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        assert "https://example.com/img/a.png" in result
        assert "https://example.com/img/b.png" in result
        assert "https://other.com/c.png" in result

    def test_no_images(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><p>No images here</p></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        # Should still be valid HTML, no crash
        assert "No images here" in result

    def test_img_without_src(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><img alt="no src"></body></html>'
        )
        # Should not raise
        result = parser.replace_relative_image_urls(html)
        assert "no src" in result

    def test_relative_path_without_leading_slash(self, parser):
        html = (
            '<html><head><base href="https://example.com/docs/"></head>'
            '<body><img src="images/diagram.png"></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        assert "https://example.com/docs/images/diagram.png" in result

    def test_protocol_relative_url_unchanged(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body><img src="//cdn.example.com/logo.png"></body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        # Protocol-relative URLs have no scheme but have netloc, so urlparse gives scheme=""
        # Since parsed.scheme is falsy, urljoin will be used
        assert "cdn.example.com/logo.png" in result

    def test_non_img_tags_not_modified(self, parser):
        html = (
            '<html><head><base href="https://example.com/"></head>'
            '<body>'
            '<a href="/page">Link</a>'
            '<script src="/js/app.js"></script>'
            '</body></html>'
        )
        result = parser.replace_relative_image_urls(html)
        # <a> and <script> tags should keep relative URLs
        assert 'href="/page"' in result
        assert 'src="/js/app.js"' in result
