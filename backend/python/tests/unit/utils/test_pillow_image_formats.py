"""Image-format regression tests for the Pillow dependency.

Pillow majors have removed constants and changed codecs. These tests run the
real Pillow code paths the app relies on (no mocks) for every raster format we
accept, so a Pillow upgrade that breaks one of them fails here:

- ``app.utils.image_utils.to_llm_image_format`` (BMP/TIFF -> PNG)
- ``app.utils.image_utils.downscale_to_limits`` (any raster -> JPEG)
- ``app.modules.transformers.document_extraction._downscale_base64_image``
- ``WebConnector._convert_avif_bytes_to_png_base64`` (AVIF -> PNG)

HEIC/HEIF are not decoded through Pillow in this codebase (no HEIF plugin is
installed), so they are intentionally not covered here.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

# AVIF encode/decode is registered by importing the plugin the web connector imports.
import app.connectors.sources.web.connector as web_connector  # noqa: F401  (registers AVIF)
from app.connectors.sources.web.connector import WebConnector
from app.modules.transformers.document_extraction import _downscale_base64_image
from app.utils.image_utils import downscale_to_limits, to_llm_image_format

_SIZE = (64, 48)
_FORMATS = ["JPEG", "PNG", "GIF", "WEBP", "TIFF", "BMP", "AVIF"]
_MIME = {
    "JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp",
    "TIFF": "image/tiff", "BMP": "image/bmp", "AVIF": "image/avif",
}


def _fixture_bytes(fmt: str, size=_SIZE) -> bytes:
    """A gradient image (not a flat colour) so lossy codecs have something to encode."""
    img = Image.new("RGB", size)
    img.putdata([(x * 255 // size[0], y * 255 // size[1], 128) for y in range(size[1]) for x in range(size[0])])
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _data_uri(fmt: str, size=_SIZE) -> str:
    return f"data:{_MIME[fmt]};base64,{base64.b64encode(_fixture_bytes(fmt, size)).decode()}"


def _decode(data_uri: str) -> Image.Image:
    img = Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1])))
    img.load()
    return img


@pytest.mark.parametrize("fmt", _FORMATS)
def test_fixture_round_trips_through_pillow(fmt) -> None:
    img = Image.open(io.BytesIO(_fixture_bytes(fmt)))
    img.load()
    assert img.format == fmt
    assert img.size == _SIZE
    assert img.convert("RGB").size == _SIZE


@pytest.mark.parametrize("fmt", ["BMP", "TIFF"])
def test_to_llm_image_format_converts_unsupported_raster_to_png(fmt) -> None:
    out = to_llm_image_format(_data_uri(fmt), max_long_edge_px=1024)
    assert out is not None and out.startswith("data:image/png;base64,")
    assert _decode(out).format == "PNG"
    assert _decode(out).size == _SIZE


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_to_llm_image_format_keeps_supported_raster(fmt) -> None:
    uri = _data_uri(fmt)
    assert to_llm_image_format(uri, max_long_edge_px=1024) == uri


# read_image_dimensions only parses these headers; other formats are never "too large".
@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP", "BMP"])
def test_downscale_to_limits_reencodes_oversized_image_as_jpeg(fmt) -> None:
    out = downscale_to_limits(_data_uri(fmt, size=(200, 100)), max_long_edge_px=50, max_bytes=10_000_000)
    assert out.startswith("data:image/jpeg;base64,")
    img = _decode(out)
    assert img.format == "JPEG"
    assert max(img.size) == 50


def test_downscale_to_limits_flattens_transparency_on_white() -> None:
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    out = downscale_to_limits(uri, max_long_edge_px=50, max_bytes=10_000_000)
    assert _decode(out).convert("RGB").getpixel((1, 1)) == (255, 255, 255)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP"])
def test_document_extraction_downscale_resizes(fmt) -> None:
    out = _downscale_base64_image(_data_uri(fmt, size=(200, 100)), max_dim=50)
    assert out is not None
    img = _decode(out)
    assert img.format == fmt
    assert max(img.size) == 50


def test_web_connector_converts_avif_to_png() -> None:
    connector = WebConnector.__new__(WebConnector)
    connector.logger = MagicMock()
    b64 = connector._convert_avif_bytes_to_png_base64(_fixture_bytes("AVIF"), "https://example.com/a.avif")
    assert b64
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert img.format == "PNG"
    assert img.size == _SIZE


def test_web_connector_avif_garbage_returns_none() -> None:
    connector = WebConnector.__new__(WebConnector)
    connector.logger = MagicMock()
    assert connector._convert_avif_bytes_to_png_base64(b"not an avif", "u") is None
