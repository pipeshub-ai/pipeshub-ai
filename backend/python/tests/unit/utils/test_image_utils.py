"""Tests for app.utils.image_utils."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest

from app.utils.image_utils import (
    EXT_TO_MIME,
    _fetch_image_as_base64,
    get_extension_from_mimetype,
    get_image_info_from_url,
    get_mime_type_from_base64,
    mime_to_extension,
    supported_mime_types,
)


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
