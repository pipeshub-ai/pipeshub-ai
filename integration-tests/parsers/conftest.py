"""The parser snapshots read no network.

Two fixtures link a screenshot on a public image host, and the parser downloads
every linked image. When that host was slow, the download timed out, the image
blocks were left out, and both snapshots failed although the parser had not
changed. The image is served here from a copy in ``fixtures/remote_images``; any
other address fails the way an unreachable one does, so a new remote image in a
fixture has to be added here too.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

_REMOTE_IMAGES = Path(__file__).resolve().parent / "fixtures" / "remote_images"

# URL a fixture links -> (copy under fixtures/remote_images, content type the host sends)
_SERVED = {
    "https://i.ibb.co/Wp7ZyN2s/Screenshot-2026-06-15-171126.png": ("screenshot.png", "image/png"),
}


@pytest.fixture(autouse=True)
def _remote_images_served_locally() -> Iterator[None]:
    # Imported here: the backend is on sys.path only once the root conftest has run.
    from app.utils.public_http import PublicFetchError, PublicFetchResponse, PublicUrlFetcher

    async def get(_self: PublicUrlFetcher, url: str, _limits: object) -> PublicFetchResponse:
        if url not in _SERVED:
            raise PublicFetchError(f"{url} is not served to the parser snapshots (see parsers/conftest.py)")
        name, content_type = _SERVED[url]
        return PublicFetchResponse(
            url=url,
            status_code=200,
            headers={"content-type": content_type},
            content=(_REMOTE_IMAGES / name).read_bytes(),
        )

    with patch.object(PublicUrlFetcher, "get", get):
        yield
