"""Image references in a push: only ``data:`` URIs of PNG, JPEG, GIF and WebP are accepted.

Nothing here makes a network request. A remote reference is refused outright
(the push has no fetch code), and indexing's image parser then finds only
``data:`` URIs in what a push stored. ``storage://`` references and the scan of
markdown/HTML groups arrive with the next slice; until then both are refused, so
no reference that could cause a fetch is stored.
"""

import base64
import binascii
import re

from .contract import Blocks, ImageBlock, PushRefusal

MAX_IMAGE_BYTES = 20 << 20
_REMOTE = re.compile(r"\s*(https?:|//)", re.IGNORECASE)
_DATA_URI = re.compile(r"data:image/(png|jpeg|gif|webp);base64,", re.IGNORECASE)
_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),
}


def _fail(reason: str, location: str, message: str) -> PushRefusal:
    return PushRefusal(422, reason, message, location, extra={"code": "BLOCKS_INVALID"})


def check_data_uri(uri: str, location: str) -> None:
    """Raise ``PushRefusal`` unless ``uri`` is a well-formed ``data:`` image of an allowed type."""
    if _REMOTE.match(uri):
        raise _fail("IMAGE_REF_REFUSED", location, "Remote image URLs are not accepted; send the image as a data: URI.")
    match = _DATA_URI.match(uri)
    if not match:
        raise _fail("IMAGE_REF_INVALID", location, "An image reference must be a data:image/(png|jpeg|gif|webp);base64 URI.")
    subtype = match.group(1).lower()
    payload = uri[match.end():]
    if len(payload) > (MAX_IMAGE_BYTES * 4) // 3 + 8:
        raise _fail("IMAGE_REF_INVALID", location, "The image is larger than 20 MiB.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise _fail("IMAGE_REF_INVALID", location, "The image is not valid base64.") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise _fail("IMAGE_REF_INVALID", location, "The image is larger than 20 MiB.")
    if not raw.startswith(_MAGIC[subtype]) or (subtype == "webp" and raw[8:12] != b"WEBP"):
        raise _fail("IMAGE_REF_INVALID", location, f"The bytes are not a {subtype} image.")


def check_blocks_images(blocks: Blocks) -> None:
    for i, block in enumerate(blocks.blocks or []):
        if isinstance(block, ImageBlock):
            check_data_uri(block.data["uri"], f"blocks.blocks[{i}].data.uri")
    for i, group in enumerate(blocks.block_groups or []):
        if group.requires_processing:
            raise _fail(
                "BLOCKS_INVALID", f"blocks.block_groups[{i}].requires_processing",
                "Groups that indexing processes as markdown or HTML are not accepted yet.",
            )
