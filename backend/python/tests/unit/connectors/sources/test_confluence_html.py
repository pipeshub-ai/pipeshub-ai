"""Unit tests for Confluence HTML URL helpers."""

import base64
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.confluence_datacenter

from app.connectors.sources.atlassian.core.confluence_html import (
    bytes_to_data_uri,
    extract_content_base_url,
    inline_authenticated_images_in_html,
    is_data_uri,
    is_image_content_type,
    is_same_origin,
    prepend_title_to_html,
    prepare_streaming_html,
    resolve_relative_urls_in_html,
)


class TestExtractContentBaseUrl:
    def test_from_links_base(self) -> None:
        data = {"_links": {"base": "http://localhost:8090"}}
        assert extract_content_base_url(data) == "http://localhost:8090"

    def test_from_self_link(self) -> None:
        data = {
            "_links": {
                "self": "http://localhost:8090/rest/api/content/2162690",
            }
        }
        assert extract_content_base_url(data) == "http://localhost:8090"

    def test_missing(self) -> None:
        assert extract_content_base_url({}) is None


class TestResolveRelativeUrlsInHtml:
    def test_resolves_href_and_src(self) -> None:
        html = (
            '<a href="/display/~admin">Admin</a>'
            '<img src="/s/icons/default.svg" />'
        )
        result = resolve_relative_urls_in_html(html, "http://localhost:8090")
        assert 'href="http://localhost:8090/display/~admin"' in result
        assert 'src="http://localhost:8090/s/icons/default.svg"' in result

    def test_leaves_absolute_urls_unchanged(self) -> None:
        html = '<a href="https://example.com">x</a><img src="data:image/png;base64,abc" />'
        result = resolve_relative_urls_in_html(html, "http://localhost:8090")
        assert 'href="https://example.com"' in result
        assert 'src="data:image/png;base64,abc"' in result

    def test_leaves_empty_and_fragment_hrefs(self) -> None:
        html = '<a href="">empty</a><a href="#section">section</a>'
        result = resolve_relative_urls_in_html(html, "http://localhost:8090")
        assert 'href=""' in result
        assert 'href="#section"' in result

    def test_resolves_srcset(self) -> None:
        html = '<img srcset="/a.png 1x, /b.png 2x" />'
        result = resolve_relative_urls_in_html(html, "http://localhost:8090")
        assert "http://localhost:8090/a.png 1x" in result
        assert "http://localhost:8090/b.png 2x" in result

    def test_noop_without_base(self) -> None:
        html = '<a href="/path">x</a>'
        assert resolve_relative_urls_in_html(html, "") == html


class TestIsDataUri:
    def test_data_uri_detected(self) -> None:
        assert is_data_uri("data:image/png;base64,abc123")
        assert is_data_uri("DATA:image/jpeg;base64,xyz")
        assert is_data_uri("  data:text/html,<h1>test</h1>")

    def test_not_data_uri(self) -> None:
        assert not is_data_uri("http://example.com/image.png")
        assert not is_data_uri("/download/attachments/123/file.png")
        assert not is_data_uri("")


class TestIsSameOrigin:
    def test_same_origin_absolute(self) -> None:
        base = "http://localhost:8090"
        assert is_same_origin("http://localhost:8090/display/~admin", base)
        assert is_same_origin("http://localhost:8090/download/attachments/123/x.png", base)

    def test_same_origin_relative(self) -> None:
        base = "http://localhost:8090"
        assert is_same_origin("/display/~admin", base)
        assert is_same_origin("/download/attachments/123/x.png", base)

    def test_different_origin(self) -> None:
        base = "http://localhost:8090"
        assert not is_same_origin("https://evil.com/steal.png", base)
        assert not is_same_origin("http://example.com/image.png", base)

    def test_different_scheme(self) -> None:
        base = "http://localhost:8090"
        assert not is_same_origin("https://localhost:8090/image.png", base)

    def test_different_port(self) -> None:
        base = "http://localhost:8090"
        assert not is_same_origin("http://localhost:8080/image.png", base)

    def test_empty_urls(self) -> None:
        assert not is_same_origin("", "http://localhost:8090")
        assert not is_same_origin("http://localhost:8090/image.png", "")


class TestIsImageContentType:
    def test_image_types(self) -> None:
        assert is_image_content_type("image/png")
        assert is_image_content_type("image/jpeg")
        assert is_image_content_type("image/svg+xml")
        assert is_image_content_type("IMAGE/PNG")

    def test_not_image(self) -> None:
        assert not is_image_content_type("text/html")
        assert not is_image_content_type("application/pdf")
        assert not is_image_content_type("")


class TestBytesToDataUri:
    def test_png_conversion(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n"
        result = bytes_to_data_uri(png_bytes, "image/png")
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        # Verify base64 decoding works
        data_part = result.split(",", 1)[1]
        assert base64.b64decode(data_part) == png_bytes

    def test_jpeg_conversion(self) -> None:
        jpeg_bytes = b"\xff\xd8\xff\xe0"
        result = bytes_to_data_uri(jpeg_bytes, "image/jpeg")
        assert result is not None
        assert result.startswith("data:image/jpeg;base64,")

    def test_svg_pass_through(self) -> None:
        """Test that SVG images are passed through as SVG data URIs."""
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        result = bytes_to_data_uri(svg_bytes, "image/svg+xml")
        assert result is not None
        assert result.startswith("data:image/svg+xml;base64,")
        # Verify base64 decoding works
        data_part = result.split(",", 1)[1]
        assert base64.b64decode(data_part) == svg_bytes

    def test_non_image_returns_none(self) -> None:
        html_bytes = b"<html></html>"
        result = bytes_to_data_uri(html_bytes, "text/html")
        assert result is None

    def test_empty_content_type(self) -> None:
        result = bytes_to_data_uri(b"data", "")
        assert result is None


@pytest.mark.asyncio
class TestInlineAuthenticatedImagesInHtml:
    async def test_inline_success(self) -> None:
        """Test successful image inlining with mock download."""
        html = '<img src="/download/attachments/123/test.png" />'
        base_url = "http://localhost:8090"
        
        # Mock successful download
        png_bytes = b"\x89PNG\r\n\x1a\n"
        async def mock_download(url: str):
            return (png_bytes, "image/png")
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        assert 'data:image/png;base64,' in result
        assert '/download/attachments/123/test.png' not in result

    async def test_skips_data_uris(self) -> None:
        """Test that existing data URIs are not re-downloaded."""
        html = '<img src="data:image/png;base64,iVBORw0KG" />'
        base_url = "http://localhost:8090"
        
        download_called = False
        async def mock_download(url: str):
            nonlocal download_called
            download_called = True
            return (b"data", "image/png")
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        assert not download_called
        assert 'data:image/png;base64,iVBORw0KG' in result

    async def test_skips_external_origin(self) -> None:
        """Test that external origin images are not downloaded (SSRF protection)."""
        html = '<img src="https://evil.com/steal.png" />'
        base_url = "http://localhost:8090"
        
        download_called = False
        async def mock_download(url: str):
            nonlocal download_called
            download_called = True
            return (b"data", "image/png")
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        assert not download_called
        assert 'https://evil.com/steal.png' in result

    async def test_download_failure_keeps_url(self) -> None:
        """Test that failed downloads leave the original URL unchanged."""
        html = '<img src="/download/attachments/123/test.png" />'
        base_url = "http://localhost:8090"
        
        async def mock_download(url: str):
            return None  # Simulate download failure
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        # Original relative URL should be preserved (absolute resolution happens in prepare_streaming_html)
        assert 'src="/download/attachments/123/test.png"' in result
        assert 'data:image' not in result

    async def test_invalid_content_type_keeps_url(self) -> None:
        """Test that non-image content types leave the URL unchanged."""
        html = '<img src="/download/attachments/123/file.pdf" />'
        base_url = "http://localhost:8090"
        
        async def mock_download(url: str):
            return (b"pdf data", "application/pdf")
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        # Original relative URL should be preserved
        assert 'src="/download/attachments/123/file.pdf"' in result
        assert 'data:image' not in result

    async def test_multiple_images_mixed(self) -> None:
        """Test processing multiple images with different outcomes."""
        html = '''
        <img src="/download/attachments/123/success.png" />
        <img src="data:image/gif;base64,R0lGOD" />
        <img src="https://external.com/skip.png" />
        <img src="/download/attachments/456/fail.png" />
        '''
        base_url = "http://localhost:8090"
        
        call_count = 0
        async def mock_download(url: str):
            nonlocal call_count
            call_count += 1
            if "success.png" in url:
                return (b"\x89PNG", "image/png")
            return None  # fail.png fails
        
        result = await inline_authenticated_images_in_html(
            html, base_url, mock_download
        )
        
        # Should call download only for same-origin, non-data URIs
        assert call_count == 2
        
        # success.png should be inlined
        assert 'data:image/png;base64,' in result
        
        # data URI should be unchanged
        assert 'data:image/gif;base64,R0lGOD' in result
        
        # External should be unchanged
        assert 'https://external.com/skip.png' in result
        
        # fail.png should keep original relative URL
        assert 'src="/download/attachments/456/fail.png"' in result


class TestPrependTitleToHtml:
    def test_prepends_h1(self) -> None:
        result = prepend_title_to_html("<p>Body</p>", "My Page")
        assert result == "<h1>My Page</h1>\n<p>Body</p>"

    def test_escapes_html_in_title(self) -> None:
        result = prepend_title_to_html("<p>Body</p>", "Foo <bar>")
        assert result == "<h1>Foo &lt;bar&gt;</h1>\n<p>Body</p>"

    def test_empty_title_unchanged(self) -> None:
        html = "<p>Body</p>"
        assert prepend_title_to_html(html, "") == html
        assert prepend_title_to_html(html, "   ") == html
        assert prepend_title_to_html(html, None) == html


@pytest.mark.asyncio
class TestPrepareStreamingHtml:
    async def test_full_pipeline(self) -> None:
        """Test the complete pipeline: URL resolution + image inlining + title."""
        html = '''
        <a href="/display/~admin">Link</a>
        <img src="/download/attachments/123/image.png" />
        '''
        response_data = {
            "_links": {"base": "http://localhost:8090"},
            "title": "Test Page",
        }
        
        png_bytes = b"\x89PNG\r\n\x1a\n"
        async def mock_download(url: str):
            return (png_bytes, "image/png")
        
        result = await prepare_streaming_html(
            html, response_data, mock_download
        )
        
        assert result.startswith("<h1>Test Page</h1>")
        assert 'href="http://localhost:8090/display/~admin"' in result
        assert 'data:image/png;base64,' in result
        assert '/download/attachments/123/image.png' not in result

    async def test_no_base_url_still_prepends_title(self) -> None:
        """Title is prepended even when base URL is missing."""
        html = '<p>Body</p>'
        response_data = {"title": "Comment Title"}

        async def mock_download(url: str):
            raise AssertionError("Should not be called")

        result = await prepare_streaming_html(
            html, response_data, mock_download
        )

        assert result == "<h1>Comment Title</h1>\n<p>Body</p>"

    async def test_title_override_used_when_api_title_empty(self) -> None:
        html = "<p>Body</p>"
        response_data = {"title": ""}

        async def mock_download(url: str):
            return None

        result = await prepare_streaming_html(
            html,
            response_data,
            mock_download,
            title="From Record",
        )

        assert result == "<h1>From Record</h1>\n<p>Body</p>"

    async def test_no_title_returns_body_unchanged(self) -> None:
        """Test that HTML is returned unchanged when no title is available."""
        html = '<img src="/test.png" />'
        response_data = {}

        async def mock_download(url: str):
            raise AssertionError("Should not be called")

        result = await prepare_streaming_html(
            html, response_data, mock_download
        )

        assert result == html
