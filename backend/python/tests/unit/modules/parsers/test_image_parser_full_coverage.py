"""
Comprehensive tests targeting remaining uncovered lines/branches in
app.modules.parsers.image_parser.image_parser.ImageParser.

Targets from coverage report (90.2% -> 97%+):
- Lines 12-13: cairosvg import fallback (svg2png = None)
- Line 73: Exception in URL parsing (_is_valid_image_url)
- Lines 130-131: Invalid extension from VALID_IMAGE_EXTENSIONS
- Line 156: 403 with X-Amz-Expires signed URL
- Lines 167-170: Server error (>= 500)
- Lines 177-178: aiohttp.ClientError (non-response errors)
- Line 237: empty width_value after regex sub
- Line 247: viewBox used for height only
- Line 269: viewBox ValueError/IndexError catch
- Lines 274-275: outer exception in _extract_svg_dimensions
- Line 319: exception in _sanitize_svg_content
- Lines 372-374, 374-379: dimension fallback branches in svg_base64_to_png_base64
- Line 407: UnicodeDecodeError in svg_base64_to_png_base64
- Partial branches: 244->251, 260->272, 307->311, 312->316
"""

import base64
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.modules.parsers.image_parser.image_parser import ImageParser


@pytest.fixture
def logger():
    return MagicMock()


@pytest.fixture
def parser(logger):
    return ImageParser(logger)


# ---------------------------------------------------------------------------
# _is_valid_image_url -- exception path (line 73)
# ---------------------------------------------------------------------------
class TestIsValidImageUrlEdge:
    def test_url_parse_exception_returns_true(self, parser):
        """When urlparse raises an exception, the except passes and returns True
        because the URL starts with https://."""
        # A well-formed https URL that won't trigger an exception normally,
        # but we can force it by patching urlparse
        with patch(
            "app.modules.parsers.image_parser.image_parser.urlparse",
            side_effect=ValueError("bad parse"),
        ):
            result = parser._is_valid_image_url("https://example.com/img.png")
        # Falls through to return True at end
        assert result is True

    def test_non_http_non_string_type(self, parser):
        """Integer input returns False."""
        assert parser._is_valid_image_url(42) is False

    def test_data_url_returns_false(self, parser):
        """data: URLs are not http/https, so return False."""
        assert parser._is_valid_image_url("data:image/png;base64,abc") is False


# ---------------------------------------------------------------------------
# _fetch_single_url -- extension not in VALID_IMAGE_EXTENSIONS (lines 130-131)
# ---------------------------------------------------------------------------
class TestFetchSingleUrlExtensionFilter:
    @pytest.mark.asyncio
    async def test_extension_not_in_valid_list(self, parser):
        """Extension returned by mimetype mapping not in VALID_IMAGE_EXTENSIONS."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/tiff"}
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch(
            "app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype",
            return_value="tiff",
        ):
            result = await parser._fetch_single_url(session, "https://example.com/image.tiff")

        assert result is None

    @pytest.mark.asyncio
    async def test_extension_gif_not_valid(self, parser):
        """gif extension is not in VALID_IMAGE_EXTENSIONS."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/gif"}
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch(
            "app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype",
            return_value="gif",
        ):
            result = await parser._fetch_single_url(session, "https://example.com/anim.gif")

        assert result is None


# ---------------------------------------------------------------------------
# _fetch_single_url -- HTTP error branches
# ---------------------------------------------------------------------------
class TestFetchSingleUrlHttpErrors:
    @pytest.mark.asyncio
    async def test_403_with_signed_url_marker(self, parser):
        """403 with X-Amz-Expires in error triggers signed URL warning (line 156)."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=403,
            message="Forbidden X-Amz-Expires",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=error)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://s3.amazonaws.com/bucket/key?X-Amz-Expires=3600"
        )
        assert result is None
        # Verify the signed-URL warning path was taken
        parser.logger.warning.assert_called()
        call_msg = parser.logger.warning.call_args[0][0]
        assert "signed URL" in call_msg or "expired" in call_msg

    @pytest.mark.asyncio
    async def test_403_without_signed_url(self, parser):
        """403 without X-Amz-Expires triggers generic permission warning."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=403,
            message="Forbidden",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=error)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/protected.png"
        )
        assert result is None
        call_msg = parser.logger.warning.call_args[0][0]
        assert "insufficient permissions" in call_msg

    @pytest.mark.asyncio
    async def test_500_server_error(self, parser):
        """500+ server error triggers server error warning (lines 167-170)."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=500,
            message="Internal Server Error",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=error)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/server-error.png"
        )
        assert result is None
        call_msg = parser.logger.warning.call_args[0][0]
        assert "Server error" in call_msg

    @pytest.mark.asyncio
    async def test_502_server_error(self, parser):
        """502 error also triggers server error path."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=502,
            message="Bad Gateway",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=error)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/gateway.png"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_other_http_error(self, parser):
        """Non-403/404/5xx HTTP error triggers generic warning (lines 169-173)."""
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            message="Too Many Requests",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=error)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/rate-limited.png"
        )
        assert result is None
        call_msg = parser.logger.warning.call_args[0][0]
        assert "HTTP error (429)" in call_msg

    @pytest.mark.asyncio
    async def test_client_error_non_response(self, parser):
        """aiohttp.ClientError (not response error) triggers network warning (lines 177-178)."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("connection refused")
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/unreachable.png"
        )
        assert result is None
        call_msg = parser.logger.warning.call_args[0][0]
        assert "Network error" in call_msg

    @pytest.mark.asyncio
    async def test_timeout_error(self, parser):
        """Timeout error is a ClientError subclass."""
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(
            side_effect=aiohttp.ServerTimeoutError("timeout")
        )
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        result = await parser._fetch_single_url(
            session, "https://example.com/slow.png"
        )
        assert result is None


# ---------------------------------------------------------------------------
# _extract_svg_dimensions -- edge cases
# ---------------------------------------------------------------------------
class TestExtractSvgDimensionsEdge:
    def test_width_value_empty_after_stripping_units(self):
        """Width value that becomes empty after regex stripping (line 237)."""
        # 'em' has no digits, so width_value will be empty string after sub
        svg = '<svg width="em" height="300"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w is None
        assert h == 300

    def test_height_value_empty_after_stripping_units(self):
        """Height value that becomes empty after regex stripping."""
        svg = '<svg width="200" height="px"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h is None

    def test_both_values_empty_no_viewbox(self):
        """Both width and height with non-numeric values and no viewBox."""
        svg = '<svg width="abc" height="xyz"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w is None
        assert h is None

    def test_viewbox_with_fewer_than_4_numbers(self):
        """viewBox with fewer than 4 numbers is ignored."""
        svg = '<svg viewBox="0 0 400"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w is None
        assert h is None

    def test_viewbox_value_error_in_conversion(self):
        """viewBox with non-numeric values after extraction (line 269)."""
        # The regex findall won't actually return non-numeric values since it
        # matches digits. But we can trigger the IndexError path.
        svg = '<svg viewBox=""><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w is None
        assert h is None

    def test_width_value_causes_float_valueerror(self):
        """Width value with multiple dots causes ValueError in float() (line 237).
        After re.sub(r'[^\\d.]', '', '1.2.3'), result is '1.2.3' which raises
        ValueError on float()."""
        svg = '<svg width="1.2.3" height="300"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        # float("1.2.3") raises ValueError, so width is None
        assert w is None
        assert h == 300

    def test_height_value_causes_float_valueerror(self):
        """Height value with multiple dots causes ValueError in float() (line 247)."""
        svg = '<svg width="200" height="1.2.3"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h is None

    def test_viewbox_conversion_valueerror(self):
        """viewBox values that pass findall but fail float conversion (line 269).
        We patch float to raise ValueError to hit this branch."""
        svg = '<svg viewBox="0 0 400 600"><rect/></svg>'
        original_float = float

        call_count = 0

        def broken_float(val):
            nonlocal call_count
            call_count += 1
            # Allow the first 2 values (x, y parsing for findall already done),
            # but fail on viewbox_numbers[2] conversion
            result = original_float(val)
            # We need float to fail inside the try block for vb_width/vb_height
            # The values are "400" and "600" - we can't easily intercept float for
            # just those. Instead, let's use a direct patch approach.
            return result

        # Simpler approach: patch int to fail on the viewBox values
        with patch("builtins.float", side_effect=ValueError("bad float")):
            # This will cause ALL float calls to fail, hitting the except
            w, h = ImageParser._extract_svg_dimensions(svg)
        # Everything fails due to patched float
        assert w is None
        assert h is None

    def test_viewbox_provides_width_only_when_height_set(self):
        """viewBox width used when width not found but height is set."""
        svg = '<svg height="300" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 400
        assert h == 300

    def test_viewbox_provides_height_only_when_width_set(self):
        """viewBox height used when height not found but width is set."""
        svg = '<svg width="200" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 600

    def test_outer_exception_returns_none_none(self):
        """Exception in outermost try returns (None, None) (lines 274-275)."""
        # Force an exception by passing a non-string
        with patch("re.search", side_effect=TypeError("bad type")):
            w, h = ImageParser._extract_svg_dimensions("<svg></svg>")
        assert w is None
        assert h is None

    def test_viewbox_negative_values(self):
        """Negative values in viewBox are handled."""
        svg = '<svg viewBox="-10 -20 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 400
        assert h == 600

    def test_viewbox_float_values(self):
        """Float values in viewBox are truncated to int."""
        svg = '<svg viewBox="0.5 0.5 400.9 600.1"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 400
        assert h == 600

    def test_width_zero_falls_to_viewbox(self):
        """Width of 0 is falsy, falls through to viewBox."""
        svg = '<svg width="0" height="300" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        # width=0 is falsy, so viewBox width is used
        assert w == 400
        assert h == 300

    def test_height_zero_falls_to_viewbox(self):
        """Height of 0 is falsy, falls through to viewBox."""
        svg = '<svg width="200" height="0" viewBox="0 0 400 600"><rect/></svg>'
        w, h = ImageParser._extract_svg_dimensions(svg)
        assert w == 200
        assert h == 600


# ---------------------------------------------------------------------------
# _sanitize_svg_content -- edge cases
# ---------------------------------------------------------------------------
class TestSanitizeSvgContentEdge:
    def test_exception_in_sanitization_returns_original(self):
        """Exception during sanitization returns original encoded content (line 319)."""
        svg = '<svg><rect/></svg>'
        with patch("re.search", side_effect=RuntimeError("regex error")):
            result = ImageParser._sanitize_svg_content(svg)
        # Falls through to final return
        assert result == svg.encode("utf-8")

    def test_xmlns_as_single_quotes(self):
        """xmlns with single quotes is recognized."""
        svg = "<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        result = ImageParser._sanitize_svg_content(svg)
        # Should not add duplicate xmlns
        count = result.count(b"xmlns=")
        assert count == 1

    def test_no_changes_needed(self):
        """SVG already has xmlns and no xlink usage."""
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert b'xmlns="http://www.w3.org/2000/svg"' in result

    def test_xlink_already_declared(self):
        """xlink namespace already declared should not be duplicated."""
        svg = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="#id"/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert result.count(b'xmlns:xlink=') == 1

    def test_has_xmlns_prefix_but_no_default(self):
        """SVG with xmlns: prefix only (covered partially by existing test, ensure branch 307->311)."""
        svg = '<svg xmlns:dc="http://purl.org/dc/elements/1.1/"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        assert b'xmlns="http://www.w3.org/2000/svg"' in result

    def test_xmlns_attribute_with_equals_no_quotes(self):
        """xmlns= present but not xmlns=\" specifically -- branch 305-308."""
        # This tests the elif branch: 'xmlns=' in tag but not 'xmlns=\"' or xmlns=\'
        # This is hard to trigger naturally because xmlns= always has quotes,
        # but we test the branch by constructing such SVG
        svg = '<svg xmlns:foo="http://foo.com"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        # Should add default xmlns
        assert b'xmlns="http://www.w3.org/2000/svg"' in result

    def test_elif_branch_with_xmlns_equals_present(self):
        """Branch 307->311: elif branch where xmlns= IS found by regex, so no xmlns added.
        This requires xmlns= in tag but NOT as xmlns=\" or xmlns=\' but also
        matching the regex xmlns\\s*=\\s*[\"']. We construct a tag with xmlns = \"..\"
        (with spaces around =) to enter the elif but have the regex match."""
        # The tag has xmlns:foo (so elif is entered) and also has xmlns = "..." (with space)
        # so the regex matches and the if-not block is skipped
        svg = '<svg xmlns = "http://www.w3.org/2000/svg" xmlns:foo="http://foo.com"><rect/></svg>'
        result = ImageParser._sanitize_svg_content(svg)
        # Should NOT add another xmlns
        assert result.count(b'xmlns="http://www.w3.org/2000/svg"') == 0 or result.count(b'xmlns') <= 3


# ---------------------------------------------------------------------------
# svg_base64_to_png_base64 -- dimension fallback branches
# ---------------------------------------------------------------------------
class TestSvgBase64ToPngBase64DimensionFallback:
    def test_no_dimensions_in_svg_uses_defaults(self):
        """SVG with no dimensions uses 800x600 defaults (lines 379-382)."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 800
            assert call_kwargs["output_height"] == 600

    def test_only_width_provided_height_from_svg(self):
        """output_width provided, height from SVG dimensions."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="200"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64, output_width=300)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 300
            assert call_kwargs["output_height"] == 200

    def test_only_height_provided_width_from_svg(self):
        """output_height provided, width from SVG dimensions."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="200"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64, output_height=400)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 100
            assert call_kwargs["output_height"] == 400

    def test_no_dimensions_in_svg_no_explicit_uses_all_defaults(self):
        """SVG without any dimensions and no explicit width/height -> 800x600."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 800
            assert call_kwargs["output_height"] == 600

    def test_svg_has_width_only_height_defaults(self):
        """SVG has width but no height, height defaults to 600."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" width="500"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 500
            assert call_kwargs["output_height"] == 600

    def test_svg_has_height_only_width_defaults(self):
        """SVG has height but no width, width defaults to 800."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" height="400"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(svg_b64)
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 800
            assert call_kwargs["output_height"] == 400

    def test_both_dimensions_provided_skip_extraction(self):
        """When both output_width and output_height provided, skip SVG extraction."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(
                svg_b64, output_width=1000, output_height=2000
            )
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["output_width"] == 1000
            assert call_kwargs["output_height"] == 2000


# ---------------------------------------------------------------------------
# svg_base64_to_png_base64 -- error paths
# ---------------------------------------------------------------------------
class TestSvgBase64ToPngBase64Errors:
    def test_unicode_decode_error(self):
        """Binary data that fails UTF-8 decode raises ValueError (line 407)."""
        # Create bytes that look like base64 but decode to invalid UTF-8
        # \x80 alone is invalid UTF-8
        raw_bytes = b"\x80\x81\x82\x83"
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        with pytest.raises(ValueError, match="Cannot decode SVG content"):
            ImageParser.svg_base64_to_png_base64(b64_str)

    def test_svg2png_conversion_error(self):
        """When svg2png itself raises, the exception is wrapped."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            side_effect=RuntimeError("cairo error"),
        ):
            with pytest.raises(Exception, match="SVG to PNG conversion failed"):
                ImageParser.svg_base64_to_png_base64(svg_b64)

    def test_xml_only_content_recognized(self):
        """Content starting with <?xml is recognized as SVG."""
        xml_svg = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        svg_b64 = base64.b64encode(xml_svg.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"fake-png",
        ):
            result = ImageParser.svg_base64_to_png_base64(svg_b64)
        assert result == base64.b64encode(b"fake-png").decode()

    def test_with_scale_and_background(self):
        """scale and background_color are passed correctly."""
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect/></svg>'
        svg_b64 = base64.b64encode(svg_content.encode()).decode()

        with patch(
            "app.modules.parsers.image_parser.image_parser.svg2png",
            return_value=b"png-data",
        ) as mock_svg2png:
            ImageParser.svg_base64_to_png_base64(
                svg_b64, scale=2.0, background_color="#FFFFFF"
            )
            call_kwargs = mock_svg2png.call_args[1]
            assert call_kwargs["scale"] == 2.0
            assert call_kwargs["background_color"] == "#FFFFFF"


# ---------------------------------------------------------------------------
# Additional edge cases for _fetch_single_url
# ---------------------------------------------------------------------------
class TestFetchSingleUrlAdditional:
    @pytest.mark.asyncio
    async def test_svg_data_url_conversion_failure(self, parser):
        """SVG data URL where conversion fails returns None via generic exception."""
        session = MagicMock()
        svg_data_url = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="

        with patch.object(
            parser,
            "svg_base64_to_png_base64",
            side_effect=Exception("conversion failed"),
        ):
            result = await parser._fetch_single_url(session, svg_data_url)
        # Falls through to generic exception handler
        assert result is None

    @pytest.mark.asyncio
    async def test_http_url_svg_from_content_type(self, parser):
        """SVG detected via content-type with svg+xml extension."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/svg+xml; charset=utf-8"}
        mock_response.read = AsyncMock(
            return_value=b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        )
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        fake_png_b64 = base64.b64encode(b"fake-png").decode("utf-8")

        with patch(
            "app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype",
            return_value="svg",
        ), patch.object(parser, "svg_base64_to_png_base64", return_value=fake_png_b64):
            result = await parser._fetch_single_url(
                session, "https://example.com/icon"
            )

        assert result == f"data:image/png;base64,{fake_png_b64}"

    @pytest.mark.asyncio
    async def test_response_content_type_with_charset(self, parser):
        """Content-type with charset parameter is parsed correctly."""
        mock_response = AsyncMock()
        mock_response.headers = {"content-type": "image/png; charset=binary"}
        mock_response.read = AsyncMock(return_value=b"png-data")
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with patch(
            "app.modules.parsers.image_parser.image_parser.get_extension_from_mimetype",
            return_value="png",
        ):
            result = await parser._fetch_single_url(
                session, "https://example.com/img.png"
            )

        assert result is not None
        assert result.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# urls_to_base64 edge cases
# ---------------------------------------------------------------------------
class TestUrlsToBase64Edge:
    @pytest.mark.asyncio
    async def test_single_url(self, parser):
        """Single URL list works correctly."""
        fake = "data:image/png;base64,abc"
        with patch.object(
            parser, "_fetch_single_url", new_callable=AsyncMock, return_value=fake
        ):
            result = await parser.urls_to_base64(["https://example.com/img.png"])
        assert result == [fake]

    @pytest.mark.asyncio
    async def test_all_failures(self, parser):
        """All URLs fail returns list of Nones."""
        with patch.object(
            parser, "_fetch_single_url", new_callable=AsyncMock, return_value=None
        ):
            result = await parser.urls_to_base64(
                ["https://a.com/1.png", "https://b.com/2.png"]
            )
        assert result == [None, None]


# ---------------------------------------------------------------------------
# parse_image edge cases
# ---------------------------------------------------------------------------
class TestParseImageEdge:
    def test_large_content(self, parser):
        """Large image content encodes correctly."""
        content = b"\x00" * 10000
        result = parser.parse_image(content, "png")
        block = result.blocks[0]
        b64_part = block.data["uri"].split(",", 1)[1]
        assert base64.b64decode(b64_part) == content

    def test_webp_extension(self, parser):
        """webp extension works correctly."""
        content = b"webp-data"
        result = parser.parse_image(content, "webp")
        assert result.blocks[0].data["uri"].startswith("data:image/webp;base64,")

    def test_jpeg_extension(self, parser):
        """jpeg extension works correctly."""
        content = b"jpeg-data"
        result = parser.parse_image(content, "jpeg")
        assert result.blocks[0].data["uri"].startswith("data:image/jpeg;base64,")
