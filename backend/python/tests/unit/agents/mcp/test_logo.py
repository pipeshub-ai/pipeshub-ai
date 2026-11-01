"""Custom MCP server logos (`app.agents.mcp.logo`): what is kept of an upload, and how it is stored."""
from __future__ import annotations

import base64
import io
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.agents.mcp import logo as mcp_logo
from app.agents.mcp.logo import LOGO_SIDE_PX, LogoError, normalize_logo
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

PNG_PREFIX = "data:image/png;base64,"


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _image_bytes(fmt: str, size: tuple[int, int] = (300, 150), mode: str = "RGBA", **save: Any) -> bytes:  # noqa: ANN401
    color: Any = (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30)
    image = Image.new(mode, size, color)
    out = io.BytesIO()
    image.save(out, format=fmt, **save)
    return out.getvalue()


def _decoded(data_url: str) -> Image.Image:
    assert data_url.startswith(PNG_PREFIX)
    image = Image.open(io.BytesIO(base64.b64decode(data_url[len(PNG_PREFIX):])))
    assert image.format == "PNG"
    return image


class TestNormalizeLogo:
    @pytest.mark.parametrize(
        ("fmt", "mode", "mime"),
        [("PNG", "RGBA", "image/png"), ("JPEG", "RGB", "image/jpeg"), ("WEBP", "RGBA", "image/webp"), ("GIF", "RGB", "image/gif")],
    )
    def test_each_raster_format_becomes_a_small_png_keeping_its_shape(self, fmt: str, mode: str, mime: str) -> None:
        logo = _decoded(normalize_logo(_data_url(_image_bytes(fmt, mode=mode), mime)))

        assert logo.size == (LOGO_SIDE_PX, LOGO_SIDE_PX // 2)

    def test_a_small_image_is_not_enlarged(self) -> None:
        assert _decoded(normalize_logo(_data_url(_image_bytes("PNG", size=(20, 20))))).size == (20, 20)

    def test_transparency_is_kept(self) -> None:
        image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        out = io.BytesIO()
        image.save(out, format="PNG")

        logo = _decoded(normalize_logo(_data_url(out.getvalue()))).convert("RGBA")

        assert logo.getpixel((0, 0))[3] == 0

    def test_an_animated_gif_keeps_its_first_frame(self) -> None:
        frames = [Image.new("RGB", (32, 32), color) for color in ((255, 0, 0), (0, 0, 255))]
        out = io.BytesIO()
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:])

        logo = _decoded(normalize_logo(_data_url(out.getvalue(), "image/gif"))).convert("RGB")

        assert logo.getpixel((16, 16)) == (255, 0, 0)

    def test_the_declared_type_is_ignored_and_the_bytes_decide(self) -> None:
        logo = normalize_logo(_data_url(_image_bytes("JPEG", mode="RGB"), "image/svg+xml"))

        assert _decoded(logo).size == (LOGO_SIDE_PX, LOGO_SIDE_PX // 2)

    def test_bytes_after_the_image_are_not_kept(self) -> None:
        upload = _image_bytes("PNG", size=(16, 16)) + b"<script>alert(1)</script>"

        logo = normalize_logo(_data_url(upload))

        assert b"<script>" not in base64.b64decode(logo[len(PNG_PREFIX):])

    @pytest.mark.parametrize(
        "upload",
        [
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b"not an image at all",
            _image_bytes("BMP", mode="RGB"),
            _image_bytes("PNG")[:40],
        ],
        ids=["svg", "text", "bmp", "truncated"],
    )
    def test_anything_but_a_whole_png_jpeg_webp_or_gif_is_refused(self, upload: bytes) -> None:
        with pytest.raises(LogoError, match="PNG, JPEG, WebP or GIF"):
            normalize_logo(_data_url(upload))

    @pytest.mark.parametrize(
        ("data_url", "reason"),
        [
            ("https://example.com/logo.png", "data URL"),
            ("data:image/png,rawbytes", "data URL"),
            ("data:image/png;base64,@@@@", "base64"),
            ("data:image/png;base64,", "empty"),
        ],
    )
    def test_a_malformed_upload_says_what_is_wrong(self, data_url: str, reason: str) -> None:
        with pytest.raises(LogoError, match=reason):
            normalize_logo(data_url)

    def test_a_file_over_the_size_limit_is_refused(self) -> None:
        with patch.object(mcp_logo, "MAX_LOGO_UPLOAD_BYTES", 100), pytest.raises(LogoError, match="1 MB"):
            normalize_logo(_data_url(_image_bytes("PNG", size=(64, 64), mode="RGB", compress_level=0)))

    def test_a_canvas_over_the_pixel_limit_is_refused_before_it_is_decoded(self) -> None:
        with patch.object(mcp_logo, "MAX_SOURCE_PIXELS", 100), pytest.raises(LogoError, match="too large"):
            normalize_logo(_data_url(_image_bytes("PNG", size=(11, 10))))


class TestStorage:
    async def test_a_saved_logo_is_read_back_and_kept_out_of_the_cache(self) -> None:
        store = FakeConfigService()
        logo = normalize_logo(_data_url(_image_bytes("PNG", size=(8, 8))))

        assert await mcp_logo.save_logo(store, "inst-1", logo)

        assert await mcp_logo.load_logo(store, "inst-1") == logo
        assert store.data["/services/mcp/logos/inst-1"] == {"dataUrl": logo}

    async def test_none_when_there_is_no_logo_or_it_is_not_one_we_wrote(self) -> None:
        store = FakeConfigService({
            "/services/mcp/logos/svg": {"dataUrl": "data:image/svg+xml;base64,PHN2Zz4="},
            "/services/mcp/logos/odd": "data:image/png;base64,AA==",
        })

        assert await mcp_logo.load_logo(store, "missing") is None
        assert await mcp_logo.load_logo(store, "svg") is None
        assert await mcp_logo.load_logo(store, "odd") is None

    async def test_a_failed_read_is_no_logo(self) -> None:
        store = FakeConfigService()

        async def _fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("store down")

        store.get_config = _fail  # type: ignore[method-assign]

        assert await mcp_logo.load_logo(store, "inst-1") is None

    async def test_removing_a_logo_that_is_not_there_succeeds(self) -> None:
        store = FakeConfigService()

        async def _nothing_deleted(_key: str) -> bool:
            return False

        store.delete_config = _nothing_deleted  # type: ignore[method-assign]

        assert await mcp_logo.delete_logo(store, "inst-1")

    async def test_removing_fails_when_the_logo_is_still_there(self) -> None:
        store = FakeConfigService({"/services/mcp/logos/inst-1": {"dataUrl": "data:image/png;base64,AA=="}})

        async def _nothing_deleted(_key: str) -> bool:
            return False

        store.delete_config = _nothing_deleted  # type: ignore[method-assign]

        assert not await mcp_logo.delete_logo(store, "inst-1")
