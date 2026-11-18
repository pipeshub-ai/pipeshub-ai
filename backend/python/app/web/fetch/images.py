"""Download an image a fetched page shows, for a model that reads images.

Every download goes through ``fetch_public``: the address and each redirect must be
public, and the caller's ``allow_hop`` (the web access gate) must accept every redirect.
"""

import asyncio
import base64

from app.utils.image_utils import ImageCheck, check_image, supported_mime_types
from app.utils.url_redaction import redact_url
from app.web.deadline import Deadline
from app.web.errors import TimedOut, UnsupportedContent
from app.web.fetch.http import check_page_status, fetch_public
from app.web.ports import HopCheck

# Larger images cost the model as much as a page of text and are rarely the point of one.
MAX_IMAGE_MB = 5

# The bytes decide, not the header: a server that answers an image URL with an HTML error
# page often still labels it image/*.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def image_media_type(content: bytes) -> str | None:
    """The type of ``content`` among those models read (PNG, JPEG, WebP), from its bytes."""
    for signature, media_type in _SIGNATURES:
        if content.startswith(signature):
            return media_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


async def fetch_image_data_uri(url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> str:
    """``url`` as a ``data:`` URI. Raises a ``WebError``: ``UnsupportedContent`` when the
    body is not a PNG, JPEG or WebP image that decodes, ``TooLarge`` past ``MAX_IMAGE_MB``."""
    body = await fetch_public(url, deadline, allow_hop=allow_hop, max_size_mb=MAX_IMAGE_MB)
    check_page_status(body)
    media_type = image_media_type(body.content or b"")
    if media_type is None or media_type not in supported_mime_types:
        raise UnsupportedContent(f"{redact_url(url)} is not a PNG, JPEG or WebP image")
    try:
        check = await asyncio.wait_for(asyncio.to_thread(check_image, body.content), timeout=deadline.remaining())
    except TimeoutError as e:
        raise TimedOut(f"{redact_url(url)} could not be checked in time") from e
    if check is not ImageCheck.OK:
        raise UnsupportedContent(f"{redact_url(url)} is not sent: {check.value}")
    return f"data:{media_type};base64,{base64.b64encode(body.content).decode('ascii')}"
