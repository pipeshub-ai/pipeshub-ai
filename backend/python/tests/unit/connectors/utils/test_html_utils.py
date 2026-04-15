"""Unit tests for app.connectors.utils.html_utils — embed_html_images_as_base64."""

import base64
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.utils.html_utils import embed_html_images_as_base64


def _logger():
    return logging.getLogger("test.html_utils")


def _response(
    status_code: int = 200,
    content: bytes = b"x",
    content_type: str = "image/png",
):
    r = MagicMock()
    r.status_code = status_code
    r.content = content
    r.headers = MagicMock()

    def _get(key, default=None):
        if key is not None and str(key).lower() == "content-type":
            return content_type
        return default

    r.headers.get = MagicMock(side_effect=_get)
    return r


@pytest.mark.asyncio
class TestEmbedHtmlImagesAsBase64:
    async def test_empty_string_returns_unchanged(self):
        client = MagicMock()
        out = await embed_html_images_as_base64("", client, _logger())
        assert out == ""
        client.get.assert_not_called()

    async def test_no_img_tags_returns_original_html(self):
        html = "<p>hello</p>"
        client = MagicMock()
        out = await embed_html_images_as_base64(html, client, _logger())
        assert out == html
        client.get.assert_not_called()

    async def test_img_without_src_skipped(self):
        html = '<img alt="x"><img src="">'
        client = MagicMock()
        out = await embed_html_images_as_base64(html, client, _logger())
        client.get.assert_not_called()
        assert "<img" in out

    async def test_url_filter_false_skips_download(self):
        html = '<img src="https://example.com/a.png">'
        client = MagicMock()
        out = await embed_html_images_as_base64(
            html,
            client,
            _logger(),
            url_filter=lambda u: False,
        )
        assert "https://example.com/a.png" in out
        client.get.assert_not_called()

    async def test_url_filter_true_processes(self):
        html = '<img src="https://example.com/a.png">'
        client = MagicMock()
        client.get = AsyncMock(return_value=_response(content=b"abc"))
        out = await embed_html_images_as_base64(
            html,
            client,
            _logger(),
            url_filter=lambda u: u.endswith(".png"),
        )
        assert out.startswith("<")
        assert "data:image/png;base64," in out
        b64 = base64.b64encode(b"abc").decode("ascii")
        assert b64 in out
        client.get.assert_awaited_once()

    async def test_no_url_filter_processes_all(self):
        html = '<img src="https://example.com/a.png">'
        client = MagicMock()
        client.get = AsyncMock(return_value=_response(content=b"x"))
        await embed_html_images_as_base64(html, client, _logger(), url_filter=None)
        client.get.assert_awaited_once()

    async def test_non_200_logs_error_and_leaves_src(self):
        html = '<img src="https://example.com/x.png">'
        client = MagicMock()
        client.get = AsyncMock(return_value=_response(status_code=404, content=b""))
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "https://example.com/x.png" in out

    async def test_success_replaces_src_with_data_uri_default_png(self):
        raw = b"\x89PNG\r\n\x1a\nxyz"
        html = '<img src="https://cdn.example.com/p.png">'
        client = MagicMock()
        client.get = AsyncMock(return_value=_response(content=raw, content_type="image/png"))
        out = await embed_html_images_as_base64(html, client, _logger())
        expected_b64 = base64.b64encode(raw).decode("utf-8")
        assert f"data:image/png;base64,{expected_b64}" in out

    async def test_octet_stream_detects_jpeg_magic(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        html = '<img src="https://x/j">'
        client = MagicMock()
        client.get = AsyncMock(
            return_value=_response(content=jpeg, content_type="application/octet-stream")
        )
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "data:image/jpeg;base64," in out

    async def test_octet_stream_detects_png_magic(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        html = '<img src="https://x/p">'
        client = MagicMock()
        client.get = AsyncMock(
            return_value=_response(content=png, content_type="binary/octet-stream")
        )
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "data:image/png;base64," in out

    async def test_octet_stream_detects_gif_magic(self):
        gif = b"GIF89a" + b"\x00" * 4
        html = '<img src="https://x/g">'
        client = MagicMock()
        client.get = AsyncMock(
            return_value=_response(content=gif, content_type="application/octet-stream")
        )
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "data:image/gif;base64," in out

    async def test_octet_stream_no_magic_match_keeps_header_mime(self):
        html = '<img src="https://x/u">'
        unknown = b"not-an-image-prefix"
        client = MagicMock()
        client.get = AsyncMock(
            return_value=_response(
                content=unknown,
                content_type="application/octet-stream",
            )
        )
        out = await embed_html_images_as_base64(html, client, _logger())
        b64 = base64.b64encode(unknown).decode("utf-8")
        assert "application/octet-stream" in out
        assert b64 in out

    async def test_get_exception_logs_and_preserves_src(self):
        html = '<img src="https://bad.example/img">'
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("connection reset"))
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "https://bad.example/img" in out

    async def test_missing_content_type_uses_get_default_image_png(self):
        """Headers.get('Content-Type', 'image/png') returns default when key absent."""
        html = '<img src="https://x/z">'
        r = MagicMock()
        r.status_code = 200
        r.content = b"zz"
        r.headers = MagicMock()
        r.headers.get = MagicMock(side_effect=lambda k, d=None: d if k == "Content-Type" else None)
        client = MagicMock()
        client.get = AsyncMock(return_value=r)
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "data:image/png;base64," in out

    async def test_multiple_images_mixed_outcomes(self):
        html = (
            '<img src="https://ok/1.png">'
            '<img src="https://fail/2.png">'
            '<img>'
        )

        async def get_side_effect(url):
            u = str(url)
            if "ok" in u:
                return _response(content=b"OK", content_type="image/png")
            return _response(status_code=500, content=b"")

        client = MagicMock()
        client.get = AsyncMock(side_effect=get_side_effect)
        out = await embed_html_images_as_base64(html, client, _logger())
        assert "data:image/png;base64," in out
        assert "https://fail/2.png" in out
