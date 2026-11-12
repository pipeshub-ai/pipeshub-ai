"""Serve the parser fixtures' remote images from files in this repository.

The snapshots embed each image's bytes, so a fixture that fetched its image from
the live host failed whenever that host was slow (a ReadTimeout drops the image
and reshapes the blocks around it).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from app.utils.public_http import PublicFetchLimits, PublicFetchResponse

_IMAGES_DIR = Path(__file__).resolve().parent / "fixtures" / "images"

_IMAGES = {
    "https://i.ibb.co/Wp7ZyN2s/Screenshot-2026-06-15-171126.png": (
        _IMAGES_DIR / "connectors_screenshot.png",
        "image/png",
    ),
}


class _FixtureImageFetcher:
    async def get(self, url: str, limits: PublicFetchLimits) -> PublicFetchResponse:
        from app.utils.public_http import PublicFetchError, PublicFetchResponse

        if url not in _IMAGES:
            raise PublicFetchError(f"GET {url} failed: no fixture image; add one to {_IMAGES_DIR.name}/")
        path, content_type = _IMAGES[url]
        return PublicFetchResponse(
            url=url,
            status_code=200,
            headers={"content-type": content_type},
            content=path.read_bytes(),
        )


@contextmanager
def fixture_images() -> Iterator[None]:
    """Answer the image parser's remote fetches from ``fixtures/images``."""
    with patch(
        "app.modules.parsers.image_parser.image_parser.PublicUrlFetcher",
        _FixtureImageFetcher,
    ):
        yield
