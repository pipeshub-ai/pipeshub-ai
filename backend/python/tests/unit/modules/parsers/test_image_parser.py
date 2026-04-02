"""Unit tests for app.modules.parsers.image_parser.image_parser.ImageParser."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.parsers.image_parser.image_parser import ImageParser


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def parser(logger):
    return ImageParser(logger)


# ---------------------------------------------------------------------------
# parse_image -- normal (non-SVG)
# ---------------------------------------------------------------------------
class TestParseImage:
    def test_creates_blocks_container_with_image_block(self, parser):
        content = b"fake-png-bytes"
        result = parser.parse_image(content, "png")
        assert len(result.blocks) == 1
        assert result.block_groups == []
        block = result.blocks[0]
        assert block.type == "image"
        assert block.format == "base64"
        expected_b64 = base64.b64encode(content).decode("utf-8")
        assert block.data["uri"] == f"data:image/png;base64,{expected_b64}"

    def test_base64_encoding_correct(self, parser):
        content = b"\x89PNG\r\n\x1a\n"
        result = parser.parse_image(content, "jpeg")
        block = result.blocks[0]
        assert block.data["uri"].startswith("data:image/jpeg;base64,")
        # Verify base64 round-trip
        b64_part = block.data["uri"].split(",", 1)[1]
        assert base64.b64decode(b64_part) == content

    def test_block_index_is_zero(self, parser):
        result = parser.parse_image(b"data", "png")
        assert result.blocks[0].index == 0


# ---------------------------------------------------------------------------
# parse_image -- SVG conversion
# ---------------------------------------------------------------------------
class TestParseImageSVG:
    def test_svg_calls_conversion(self, parser):
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        fake_png_b64 = base64.b64encode(b"fakepng").decode("utf-8")

        with patch.object(parser, "svg_base64_to_png_base64", return_value=fake_png_b64) as mock_convert:
            result = parser.parse_image(svg_content, "svg")
            mock_convert.assert_called_once()
            block = result.blocks[0]
            assert block.data["uri"] == f"data:image/png;base64,{fake_png_b64}"

    def test_svg_conversion_failure_raises(self, parser):
        with patch.object(parser, "svg_base64_to_png_base64", side_effect=Exception("conversion failed")):
            with pytest.raises(ValueError, match="Failed to convert SVG to PNG"):
                parser.parse_image(b"<svg></svg>", "svg")

    def test_svg_case_insensitive(self, parser):
        fake_png_b64 = base64.b64encode(b"fakepng").decode("utf-8")
        with patch.object(parser, "svg_base64_to_png_base64", return_value=fake_png_b64):
            result = parser.parse_image(b"<svg></svg>", "SVG")
            assert result.blocks[0].data["uri"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# _is_valid_image_url
# ---------------------------------------------------------------------------
class TestIsValidImageUrl:
    def test_http_with_image_extension(self, parser):
        assert parser._is_valid_image_url("http://example.com/image.png") is True

    def test_https_with_image_extension(self, parser):
        assert parser._is_valid_image_url("https://example.com/photo.jpg") is True

    def test_https_jpeg_extension(self, parser):
        assert parser._is_valid_image_url("https://example.com/photo.jpeg") is True

    def test_https_webp_extension(self, parser):
        assert parser._is_valid_image_url("https://example.com/photo.webp") is True

    def test_https_svg_extension(self, parser):
        assert parser._is_valid_image_url("https://example.com/icon.svg") is True

    def test_non_http_scheme_returns_false(self, parser):
        assert parser._is_valid_image_url("ftp://example.com/image.png") is False

    def test_none_returns_false(self, parser):
        assert parser._is_valid_image_url(None) is False

    def test_empty_string_returns_false(self, parser):
        assert parser._is_valid_image_url("") is False

    def test_non_string_returns_false(self, parser):
        assert parser._is_valid_image_url(12345) is False

    def test_http_url_without_image_extension_still_valid(self, parser):
        # The implementation returns True for any valid http/https URL
        # because it says "allow it but will check content-type later"
        assert parser._is_valid_image_url("https://example.com/api/image?id=123") is True

    def test_relative_path_returns_false(self, parser):
        assert parser._is_valid_image_url("/images/photo.png") is False


# ---------------------------------------------------------------------------
# _is_valid_image_content_type
# ---------------------------------------------------------------------------
class TestIsValidImageContentType:
    def test_image_png(self, parser):
        assert parser._is_valid_image_content_type("image/png") is True

    def test_image_jpeg(self, parser):
        assert parser._is_valid_image_content_type("image/jpeg") is True

    def test_image_webp(self, parser):
        assert parser._is_valid_image_content_type("image/webp") is True

    def test_image_svg(self, parser):
        assert parser._is_valid_image_content_type("image/svg+xml") is True

    def test_text_html_returns_false(self, parser):
        assert parser._is_valid_image_content_type("text/html") is False

    def test_application_json_returns_false(self, parser):
        assert parser._is_valid_image_content_type("application/json") is False

    def test_none_returns_false(self, parser):
        assert parser._is_valid_image_content_type(None) is False

    def test_empty_string_returns_false(self, parser):
        assert parser._is_valid_image_content_type("") is False

    def test_image_with_charset_suffix(self, parser):
        assert parser._is_valid_image_content_type("image/png; charset=utf-8") is True

    def test_case_insensitive(self, parser):
        assert parser._is_valid_image_content_type("Image/PNG") is True


# ---------------------------------------------------------------------------
# _fetch_single_url
# ---------------------------------------------------------------------------
class TestFetchSingleUrl:
    @pytest.mark.asyncio
    async def test_data_image_url_returned_as_is(self, parser):
        session = MagicMock()
        data_url = "data:image/png;base64,iVBORw0KGgo="
        result = await parser._fetch_single_url(session, data_url)
        assert result == data_url

    @pytest.mark.asyncio
    async def test_data_image_non_svg_returned_as_is(self, parser):
        session = MagicMock()
        data_url = "data:image/jpeg;base64,/9j/4AAQ"
        result = await parser._fetch_single_url(session, data_url)
        assert result == data_url

    @pytest.mark.asyncio
    async def test_svg_data_url_converted(self, parser):
        session = MagicMock()
        fake_png_b64 = base64.b64encode(b"fake-png").decode("utf-8")
        svg_data_url = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="

        with patch.object(parser, "svg_base64_to_png_base64", return_value=fake_png_b64) as mock_convert:
            result = await parser._fetch_single_url(session, svg_data_url)
            mock_convert.assert_called_once_with(svg_data_url)
            assert result == f"data:image/png;base64,{fake_png_b64}"

    @pytest.mark.asyncio
    async def test_invalid_url_returns_none(self, parser):
        session = MagicMock()
        result = await parser._fetch_single_url(session, "ftp://not-http.com/img.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_url_success(self, parser):
        """Successfully fetch and convert an HTTP URL to base64."""
        import aiohttp

        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.read = AsyncMock(return_value=b"fake-image-data")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch("app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype", return_value="png"):
            result = await parser._fetch_single_url(session, "https://example.com/image.png")

        assert result is not None
        assert result.startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_http_url_invalid_content_type(self, parser):
        """URL returning non-image content type returns None."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(session, "https://example.com/not-image")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_url_empty_content(self, parser):
        """URL returning empty content returns None."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.read = AsyncMock(return_value=b"")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch("app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype", return_value="png"):
            result = await parser._fetch_single_url(session, "https://example.com/empty.png")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_url_no_extension(self, parser):
        """URL with no determinable extension returns None."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch("app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype", return_value=None):
            result = await parser._fetch_single_url(session, "https://example.com/img")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_url_svg_extension_converts(self, parser):
        """SVG content fetched from URL is converted to PNG."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/svg+xml"}
        mock_response.read = AsyncMock(return_value=b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        fake_png_b64 = base64.b64encode(b"fake-png").decode("utf-8")

        with patch("app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype", return_value="svg"), \
             patch.object(parser, "svg_base64_to_png_base64", return_value=fake_png_b64):
            result = await parser._fetch_single_url(session, "https://example.com/icon.svg")

        assert result == f"data:image/png;base64,{fake_png_b64}"

    @pytest.mark.asyncio
    async def test_http_403_returns_none(self, parser):
        """403 Forbidden returns None."""
        import aiohttp

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=403, message="Forbidden"
            )
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(session, "https://example.com/protected.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_404_returns_none(self, parser):
        """404 Not Found returns None."""
        import aiohttp

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=404, message="Not Found"
            )
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(session, "https://example.com/missing.png")
        assert result is None

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self, parser):
        """Generic exception returns None."""
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("unexpected"))

        result = await parser._fetch_single_url(session, "https://example.com/img.png")
        assert result is None


# ---------------------------------------------------------------------------
# urls_to_base64
# ---------------------------------------------------------------------------
class TestUrlsToBase64:
    @pytest.mark.asyncio
    async def test_multiple_urls(self, parser):
        """Multiple URLs processed concurrently."""
        fake_b64 = "data:image/png;base64,abc"
        with patch.object(parser, "_fetch_single_url", new_callable=AsyncMock, return_value=fake_b64):
            result = await parser.urls_to_base64(["https://a.com/1.png", "https://b.com/2.png"])
        assert len(result) == 2
        assert all(r == fake_b64 for r in result)

    @pytest.mark.asyncio
    async def test_mixed_success_failure(self, parser):
        """Mix of successful and failed URL fetches."""
        call_count = [0]

        async def mock_fetch(session, url):
            call_count[0] += 1
            if "fail" in url:
                return None
            return "data:image/png;base64,abc"

        with patch.object(parser, "_fetch_single_url", side_effect=mock_fetch):
            result = await parser.urls_to_base64(["https://ok.com/1.png", "https://fail.com/2.png", "https://ok.com/3.png"])
        assert len(result) == 3
        assert result[0] == "data:image/png;base64,abc"
        assert result[1] is None
        assert result[2] == "data:image/png;base64,abc"

    @pytest.mark.asyncio
    async def test_empty_urls(self, parser):
        """Empty URL list returns empty list."""
        result = await parser.urls_to_base64([])
        assert result == []

    @pytest.mark.asyncio
    async def test_already_base64_url(self, parser):
        """Already-base64 URLs pass through."""
        data_url = "data:image/png;base64,iVBORw0KGgo="
        with patch.object(parser, "_fetch_single_url", new_callable=AsyncMock, return_value=data_url):
            result = await parser.urls_to_base64([data_url])
        assert result[0] == data_url


# ---------------------------------------------------------------------------
# svg_base64_to_png_base64
# ---------------------------------------------------------------------------
class TestSvgBase64ToPngBase64:
    def test_valid_svg(self, parser):
        """Valid SVG base64 converts to PNG base64."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch("app.modules.parsers.image_parser.image_parser.svg2png", return_value=b"fake-png"):
            result = ImageParser.svg_base64_to_png_base64(svg_b64)
        expected = base64.b64encode(b"fake-png").decode()
        assert result == expected

    def test_svg_with_data_uri_prefix(self, parser):
        """SVG with data:image/svg+xml;base64, prefix works."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()
        data_uri = f"data:image/svg+xml;base64,{svg_b64}"

        with patch("app.modules.parsers.image_parser.image_parser.svg2png", return_value=b"fake-png"):
            result = ImageParser.svg_base64_to_png_base64(data_uri)
        assert result == base64.b64encode(b"fake-png").decode()

    def test_invalid_base64_raises(self, parser):
        """Invalid base64 raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base64"):
            ImageParser.svg_base64_to_png_base64("not-valid-base64!!!")

    def test_non_svg_content_raises(self, parser):
        """Non-SVG content raises Exception."""
        non_svg = base64.b64encode(b"this is not SVG content").decode()
        with pytest.raises(Exception, match="not appear to be valid SVG"):
            ImageParser.svg_base64_to_png_base64(non_svg)

    def test_svg2png_none_raises(self, parser):
        """When cairosvg is not available, raises Exception."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch("app.modules.parsers.image_parser.image_parser.svg2png", None):
            with pytest.raises(Exception, match="cairosvg"):
                ImageParser.svg_base64_to_png_base64(svg_b64)

    def test_custom_dimensions(self, parser):
        """Custom output_width and output_height are passed to svg2png."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch("app.modules.parsers.image_parser.image_parser.svg2png", return_value=b"png") as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64, output_width=200, output_height=300)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 200
            assert call_kwargs["output_height"] == 300

    def test_with_background_color(self, parser):
        """background_color is passed to svg2png."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch("app.modules.parsers.image_parser.image_parser.svg2png", return_value=b"png") as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64, background_color="white")
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["background_color"] == "white"


# ---------------------------------------------------------------------------
# _extract_svg_dimensions
# ---------------------------------------------------------------------------
class TestExtractSvgDimensions:
    def test_width_height_attributes(self):
        svg = '<svg width="200" height="300"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 300

    def test_width_height_with_units(self):
        svg = '<svg width="200px" height="300pt"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 300

    def test_viewbox_fallback(self):
        svg = '<svg viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 400
        assert h == 600

    def test_no_dimensions(self):
        svg = '<svg><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w is None
        assert h is None

    def test_no_svg_tag(self):
        w, h = ImageParser._extract_svg_dimensions("<div>not svg</div>")
        assert w is None
        assert h is None

    def test_width_only_viewbox_for_height(self):
        svg = '<svg width="200" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 600

    def test_height_only_viewbox_for_width(self):
        svg = '<svg height="300" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 400
        assert h == 300

    def test_invalid_width_value(self):
        svg = '<svg width="abc" height="300"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        # "abc" has no digits, so width is None => viewBox fallback
        assert h == 300

    def test_float_dimensions(self):
        svg = '<svg width="200.5" height="300.7"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 300


# ---------------------------------------------------------------------------
# _sanitize_svg_content
# ---------------------------------------------------------------------------
class TestSanitizeSvgContent:
    def test_adds_xmlns_when_missing(self):
        svg = '<svg><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert b'xmlns="http://www.w3.org/2000/svg"' in result

    def test_preserves_existing_xmlns(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        # Should not duplicate
        assert result.count(b'xmlns="http://www.w3.org/2000/svg"') == 1

    def test_adds_xlink_namespace(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="#id"/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert b'xmlns:xlink="http://www.w3.org/1999/xlink"' in result

    def test_no_svg_tag_returns_as_is(self):
        content = "<div>not svg</div>"
        result = ImageParser._sanitize_svg_content(content)
        assert result == content.encode("utf-8")

    def test_returns_bytes(self):
        svg = '<svg><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert isinstance(result, bytes)

    def test_xmlns_prefix_only(self):
        """SVG with xmlns: prefix but no base xmlns."""
        svg = '<svg xmlns:custom="http://custom.com"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert b'xmlns="http://www.w3.org/2000/svg"' in result
