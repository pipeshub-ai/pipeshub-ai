"""Tests for app.utils.image_utils."""

from __future__ import annotations

import asyncio
import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.utils.image_utils import (
    EXT_TO_MIME,
    _fetch_image_as_base64,
    downscale_to_limits,
    get_extension_from_mimetype,
    get_image_info_from_url,
    get_mime_type_from_base64,
    mime_to_extension,
    normalize_image_to_base64,
    supported_mime_types,
    is_below_content_size,
    is_extreme_aspect_ratio,
    is_decorative_image,
    to_llm_image_format, 
)

_RASTERIZE = "app.modules.parsers.image_parser.image_parser.ImageParser.svg_base64_to_png_base64"


def _svg_uri(svg: bytes) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


class TestDownscaleToLimits:
    def test_a_transparent_image_is_laid_on_white_not_black(self) -> None:
        source = Image.new("RGBA", (3000, 20), (0, 0, 0, 0))
        source.paste((200, 30, 30, 255), (0, 0, 40, 20))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        uri = downscale_to_limits(
            "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
            max_long_edge_px=2048, max_bytes=10 * 1024 * 1024,
        )

        assert uri.startswith("data:image/jpeg;base64,")
        out = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")
        assert min(out.getpixel((out.width - 5, out.height // 2))) > 240
        assert out.getpixel((5, out.height // 2))[0] > 150


class TestToLlmImageFormat:
    def test_svg_is_rasterized_at_the_models_long_edge(self) -> None:
        uri = _svg_uri(b'<svg xmlns="http://www.w3.org/2000/svg" width="3000" height="1500"></svg>')
        with patch(_RASTERIZE, return_value="UE5H") as rasterize:
            assert to_llm_image_format(uri, max_long_edge_px=1500) == "data:image/png;base64,UE5H"
        kwargs = rasterize.call_args.kwargs
        assert (kwargs["output_width"], kwargs["output_height"]) == (1500, 750)

    def test_line_wrapped_svg_is_still_rasterized(self) -> None:
        uri = _svg_uri(b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="15"></svg>')
        header, payload = uri.split(",", 1)
        wrapped = header + "," + "\n".join(payload[i:i + 8] for i in range(0, len(payload), 8))
        with patch(_RASTERIZE, return_value="UE5H"):
            assert to_llm_image_format(wrapped, max_long_edge_px=1568) == "data:image/png;base64,UE5H"

    def test_svg_that_cannot_be_rasterized_is_unsendable(self) -> None:
        uri = _svg_uri(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>')
        with patch(_RASTERIZE, side_effect=RuntimeError("bad svg")):
            assert to_llm_image_format(uri, max_long_edge_px=1568) is None

    def test_bmp_is_converted_to_png(self) -> None:
        pillow = pytest.importorskip("PIL.Image")
        import io

        buffer = io.BytesIO()
        pillow.new("RGB", (4, 3)).save(buffer, format="BMP")
        uri = "data:image/bmp;base64," + base64.b64encode(buffer.getvalue()).decode()
        converted = to_llm_image_format(uri, max_long_edge_px=1568)
        assert converted.startswith("data:image/png;base64,iVBORw0KGgo")

    def test_oversized_bmp_is_bounded_to_the_long_edge(self) -> None:
        pillow = pytest.importorskip("PIL.Image")
        import io

        buffer = io.BytesIO()
        pillow.new("RGB", (400, 200)).save(buffer, format="BMP")
        uri = "data:image/bmp;base64," + base64.b64encode(buffer.getvalue()).decode()
        converted = to_llm_image_format(uri, max_long_edge_px=100)
        with pillow.open(io.BytesIO(base64.b64decode(converted.split(",", 1)[1]))) as out:
            assert out.size == (100, 50)

    def test_unrecognizable_bytes_declared_as_svg_are_unsendable(self) -> None:
        uri = "data:image/svg+xml;base64," + base64.b64encode(b"\x1f\x8b\x08" + b"\x00" * 40).decode()
        assert to_llm_image_format(uri, max_long_edge_px=1568) is None

    def test_mislabeled_raster_is_relabeled(self) -> None:
        jpeg_b64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 20).decode()
        assert (
            to_llm_image_format(f"data:image/png;base64,{jpeg_b64}", max_long_edge_px=1568)
            == f"data:image/jpeg;base64,{jpeg_b64}"
        )

    @pytest.mark.parametrize("uri", [
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC",
        "https://example.com/photo.svg",
    ])
    def test_supported_images_and_urls_pass_through(self, uri: str) -> None:
        assert to_llm_image_format(uri, max_long_edge_px=1568) == uri


class TestNormalizeImageToBase64:
    def test_strips_data_uri_prefix(self) -> None:
        assert normalize_image_to_base64("data:image/png;base64,iVBORw0KGgo=") == "iVBORw0KGgo="

    def test_data_uri_without_comma_returns_none(self) -> None:
        assert normalize_image_to_base64("data:image/png;base64") is None


# ---------------------------------------------------------------------------
# get_mime_type_from_base64
# ---------------------------------------------------------------------------


def _make_b64(raw_bytes: bytes) -> str:
    return base64.b64encode(raw_bytes + b"\x00" * 20).decode()


class TestGetMimeTypeFromBase64:
    def test_png_signature(self) -> None:
        b64 = _make_b64(b"\x89PNG")
        assert get_mime_type_from_base64(b64) == "image/png"

    def test_jpeg_signature(self) -> None:
        b64 = _make_b64(b"\xff\xd8\xff")
        assert get_mime_type_from_base64(b64) == "image/jpeg"

    def test_gif87a_signature(self) -> None:
        b64 = _make_b64(b"GIF87a")
        assert get_mime_type_from_base64(b64) == "image/gif"

    def test_gif89a_signature(self) -> None:
        b64 = _make_b64(b"GIF89a")
        assert get_mime_type_from_base64(b64) == "image/gif"

    def test_webp_signature(self) -> None:
        # RIFF....WEBP
        raw = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8
        b64 = _make_b64(raw)
        assert get_mime_type_from_base64(b64) == "image/webp"

    def test_bmp_signature(self) -> None:
        b64 = _make_b64(b"BM")
        assert get_mime_type_from_base64(b64) == "image/bmp"

    def test_tiff_little_endian(self) -> None:
        b64 = _make_b64(b"II*\x00")
        assert get_mime_type_from_base64(b64) == "image/tiff"

    def test_tiff_big_endian(self) -> None:
        b64 = _make_b64(b"MM\x00*")
        assert get_mime_type_from_base64(b64) == "image/tiff"

    def test_ico_signature(self) -> None:
        b64 = _make_b64(b"\x00\x00\x01\x00")
        assert get_mime_type_from_base64(b64) == "image/x-icon"

    def test_unknown_returns_none(self) -> None:
        b64 = _make_b64(b"\x00\x01\x02\x03\x04\x05")
        assert get_mime_type_from_base64(b64) is None


# ---------------------------------------------------------------------------
# get_extension_from_mimetype
# ---------------------------------------------------------------------------


class TestGetExtensionFromMimetype:
    def test_png_mime(self) -> None:
        assert get_extension_from_mimetype("image/png") == "png"

    def test_jpeg_mime(self) -> None:
        assert get_extension_from_mimetype("image/jpeg") == "jpeg"

    def test_webp_mime(self) -> None:
        assert get_extension_from_mimetype("image/webp") == "webp"

    def test_unknown_mime_returns_none(self) -> None:
        assert get_extension_from_mimetype("image/unknown-format") is None

    def test_svg_mime(self) -> None:
        assert get_extension_from_mimetype("image/svg+xml") == "svg"

    def test_pdf_mime(self) -> None:
        assert get_extension_from_mimetype("application/pdf") == "pdf"


# ---------------------------------------------------------------------------
# get_image_info_from_url
# ---------------------------------------------------------------------------


class TestGetImageInfoFromUrl:
    def test_empty_url_returns_none_none(self) -> None:
        ext, mime = get_image_info_from_url("")
        assert ext is None
        assert mime is None

    def test_png_url(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/photo.png")
        assert ext == ".png"
        assert mime == "image/png"

    def test_jpeg_url(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/photo.jpg")
        assert ext == ".jpg"
        assert mime == "image/jpeg"

    def test_webp_url(self) -> None:
        ext, mime = get_image_info_from_url("https://cdn.com/img.webp")
        assert ext == ".webp"
        assert mime == "image/webp"

    def test_url_with_query_params(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/photo.png?v=123&size=large")
        assert ext == ".png"
        assert mime == "image/png"

    def test_url_without_extension(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/photo")
        assert ext is None
        assert mime is None

    def test_url_with_unknown_extension(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/file.xyz")
        assert ext == ".xyz"
        assert mime is None

    def test_url_encoded_path(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/my%20photo.jpeg")
        assert ext == ".jpeg"
        assert mime == "image/jpeg"

    def test_extension_case_insensitive(self) -> None:
        ext, mime = get_image_info_from_url("https://example.com/PHOTO.PNG")
        assert ext == ".png"
        assert mime == "image/png"


# ---------------------------------------------------------------------------
# _fetch_image_as_base64 (async)
# ---------------------------------------------------------------------------


class TestFetchImageAsBase64:
    def test_unsupported_mime_skips_fetch(self) -> None:
        with patch(
            "app.utils.image_utils.get_image_info_from_url",
            return_value=(".gif", "image/gif"),
        ):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/anim.gif")
            )
        assert result is None

    def test_non_200_status_returns_none(self) -> None:
        mock_result = MagicMock()
        mock_result.status_code = 404
        mock_result.content = b""

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img.png")
            )
        assert result is None

    def test_empty_content_returns_none(self) -> None:
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.content = b""

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img.png")
            )
        assert result is None

    def test_success_with_png_content(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        import base64 as b64mod
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.content = png_bytes

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(".png", "image/png")), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img.png")
            )
        assert result is not None
        b64_str, mime = result
        assert mime == "image/png"
        assert isinstance(b64_str, str)

    def test_unknown_mime_from_bytes_returns_none(self) -> None:
        # Content that doesn't match any known signature
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.content = b"\x00\x01\x02\x03" * 10

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img")
            )
        assert result is None

    def test_fetch_error_returns_none(self) -> None:
        from app.utils.url_fetcher import FetchError

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", side_effect=FetchError("connection refused")):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img.png")
            )
        assert result is None

    def test_generic_exception_returns_none(self) -> None:
        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", side_effect=RuntimeError("unexpected")):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/img.png")
            )
        assert result is None

    def test_mime_not_in_supported_list_returns_none(self) -> None:
        # TIFF is detected but not in supported_mime_types
        tiff_bytes = b"II*\x00" + b"\x00" * 20
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.content = tiff_bytes

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result):
            result = asyncio.run(
                _fetch_image_as_base64("https://example.com/photo.tiff")
            )
        assert result is None

    def test_fetches_with_private_host_blocking_on(self) -> None:
        mock_result = MagicMock()
        mock_result.status_code = 404
        mock_result.content = b""

        with patch("app.utils.image_utils.get_image_info_from_url", return_value=(None, None)), \
             patch("app.utils.image_utils.fetch_url", return_value=mock_result) as mock_fetch:
            asyncio.run(_fetch_image_as_base64("https://example.com/img.png"))

        assert mock_fetch.call_args.kwargs["block_private_hosts"] is True


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_supported_mime_types_contains_png(self) -> None:
        assert "image/png" in supported_mime_types

    def test_supported_mime_types_contains_jpeg(self) -> None:
        assert "image/jpeg" in supported_mime_types

    def test_supported_mime_types_contains_webp(self) -> None:
        assert "image/webp" in supported_mime_types

    def test_ext_to_mime_has_jpg(self) -> None:
        assert ".jpg" in EXT_TO_MIME
        assert EXT_TO_MIME[".jpg"] == "image/jpeg"

    def test_mime_to_extension_has_png(self) -> None:
        assert "image/png" in mime_to_extension
        assert mime_to_extension["image/png"] == "png"


class TestNormalizeImageToBase64DataUri:
    """Regression: the `data:` branch skipped the charset check the bare-base64
    branch performs, so a non-base64 data URI came back as if it were image
    bytes and was shipped to the embedding provider."""

    def test_non_base64_data_uri_is_rejected(self) -> None:
        assert normalize_image_to_base64("data:image/svg+xml,<svg></svg>") is None

    def test_plain_text_data_uri_is_rejected(self) -> None:
        assert normalize_image_to_base64("data:text/plain,hello") is None

    def test_base64_data_uri_is_accepted_and_padded(self) -> None:
        assert normalize_image_to_base64("data:image/png;base64,aW1hZ2U") == "aW1hZ2U="

    def test_charset_is_enforced_inside_a_base64_data_uri(self) -> None:
        assert normalize_image_to_base64("data:image/png;base64,<not base64>") is None

class TestImageBelowContentSize:
    """Test Image for identifying images that fall below minimum content dimensions."""

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            pytest.param(None, None, id="missing-both-dimensions"),
            pytest.param(None, 100, id="missing-width"),
            pytest.param(0, 100, id="zero-width"),
            pytest.param(0, 0, id="zero-dimensions"),
        ],
    )
    def test_missing_or_zero_dimensions_are_not_too_small(
        self, width: int | None, height: int | None
    ) -> None:
        assert is_below_content_size(width, height) is False

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            pytest.param(63, 200, True, id="below-minimum-short-edge"),
            pytest.param(200, 64, False, id="at-minimum-short-edge"),
        ],
    )
    def test_short_edge_threshold(
        self, width: int, height: int, expected: bool
    ) -> None:
        assert is_below_content_size(width, height) is expected

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            pytest.param(80, 100, True, id="below-minimum-area"),
            pytest.param(100, 100, False, id="at-minimum-area"),
            pytest.param(120, 90, False, id="above-minimum-area"),
        ],
    )
    def test_content_area_threshold(
        self, width: int, height: int, expected: bool
    ) -> None:
        assert is_below_content_size(width, height) is expected

class TestImageExtremeAspectRatio:
    """Tests for extreme image aspect ratios and boundary conditions."""

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            pytest.param(None, None, id="missing-both-dimensions"),
            pytest.param(None, 10, id="missing-width"),
            pytest.param(0, 90, id="zero-width"),
            pytest.param(100, 0, id="zero-height"),
        ],
    )
    def test_missing_or_zero_dimensions_are_not_extreme(
        self, width: int | None, height: int | None
    ) -> None:
        assert is_extreme_aspect_ratio(width, height) is False

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            pytest.param(200, 20, False, id="ratio-at-maximum"),
            pytest.param(201, 20, True, id="ratio-above-maximum"),
            pytest.param(10, 140, True, id="portrait-ratio-above-maximum"),
            pytest.param(1000, 50, True, id="landscape-ratio-above-maximum"),
        ],
    )
    def test_aspect_ratio_threshold(
        self, width: int, height: int, expected: bool
    ) -> None:
        assert is_extreme_aspect_ratio(width, height) is expected

class TestDecorativeImage:
    """Tests for decorative image classification based on size and aspect ratio."""

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            pytest.param(100, 100, False, id="normal-content-image"),
            pytest.param(10, 10, True, id="image-below-minimum-size"),
            pytest.param(1000, 50, True, id="decorative-landscape-image"),
            pytest.param(1100, 100, True, id="extreme-landscape-ratio"),
            pytest.param(100, 1100, True, id="extreme-portrait-ratio"),
        ],
    )
    def test_decorative_image_classification(
        self, width: int, height: int, expected: bool
    ) -> None:
        assert is_decorative_image(width, height) is expected
