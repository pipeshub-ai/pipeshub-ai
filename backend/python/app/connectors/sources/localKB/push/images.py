"""Image references in a push: ``data:`` URIs of PNG, JPEG, GIF and WebP, and ``storage://<id>`` references.

Nothing here makes a network request. A remote reference is refused outright
(the push has no fetch code), and indexing's image parser then finds only
``data:`` URIs in what a push stored. A ``storage://<24 hex>`` reference names a
document in the platform's storage; it is collected here (with its caps), swapped for a
sentinel in the stored bytes, and replaced by the image's own ``data:`` URI after the
materialiser has fetched it (``materialiser.py``). The scan of markdown/HTML groups
arrives with a later slice; until then every such group is refused, so no
reference that could cause a fetch is stored.
"""

import base64
import binascii
import math
import re
import secrets
from dataclasses import dataclass, field

from .contract import Blocks, ImageBlock, PushRefusal

MAX_IMAGE_BYTES = 20 << 20
MAX_DISTINCT_REFS = 64
MAX_OCCURRENCES_OF_ONE_REF = 16
MAX_OCCURRENCES = 256
MAX_IMAGES_PER_RECORD = 1000
STORAGE_SCHEME = "storage://"
_STORAGE_REF = re.compile(r"storage://([0-9a-f]{24})")
_SENTINEL_PREFIX = "\\u0000pb-img:"  # the six characters of the JSON escape, as they appear in the stored file
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


@dataclass
class ImageRefs:
    """The ``storage://`` images of a body: ids in first-occurrence order, with their occurrence counts."""

    order: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    images: int = 0  # every image block, ``data:`` and ``storage://``
    #: The blocks holding a reference, with its id, so the swaps below do not parse a uri twice.
    slots: list[tuple[ImageBlock, str]] = field(default_factory=list, repr=False)

    @property
    def occurrences(self) -> int:
        return sum(self.counts.values())

    def add(self, ref_id: str, location: str) -> None:
        if ref_id not in self.counts:
            if len(self.order) >= MAX_DISTINCT_REFS:
                raise _limit(location, f"A record may reference at most {MAX_DISTINCT_REFS} distinct stored images.")
            self.order.append(ref_id)
            self.counts[ref_id] = 0
        self.counts[ref_id] += 1
        if self.counts[ref_id] > MAX_OCCURRENCES_OF_ONE_REF:
            raise _limit(location, f"One stored image may appear at most {MAX_OCCURRENCES_OF_ONE_REF} times in a record.")
        if self.occurrences > MAX_OCCURRENCES:
            raise _limit(location, f"A record may use stored images at most {MAX_OCCURRENCES} times in total.")


def _limit(location: str, message: str) -> PushRefusal:
    return PushRefusal(422, "LIMIT_EXCEEDED", message, location, extra={"code": "BLOCKS_INVALID"})


def check_blocks_images(blocks: Blocks) -> ImageRefs:
    """Check every image of the body; returns the ``storage://`` references (empty when there are none)."""
    refs = ImageRefs()
    for i, block in enumerate(blocks.blocks or []):
        if isinstance(block, ImageBlock):
            location = f"blocks.blocks[{i}].data.uri"
            refs.images += 1
            if refs.images > MAX_IMAGES_PER_RECORD:
                raise _limit(location, f"A record may hold at most {MAX_IMAGES_PER_RECORD} images.")
            uri = block.data["uri"]
            if uri.startswith(STORAGE_SCHEME):
                match = _STORAGE_REF.fullmatch(uri)
                if not match:
                    raise _fail("IMAGE_REF_INVALID", location, "A storage image reference must be storage:// followed by 24 lowercase hex characters.")
                refs.add(match.group(1), location)
                refs.slots.append((block, match.group(1)))
            else:
                check_data_uri(uri, location)
    for i, group in enumerate(blocks.block_groups or []):
        if group.requires_processing:
            raise _fail(
                "BLOCKS_INVALID", f"blocks.block_groups[{i}].requires_processing",
                "Groups that indexing processes as markdown or HTML are not accepted yet.",
            )
    return refs


# ---- sentinels and the splice -------------------------------------------------------------------

#: A 1x1 PNG the validator sees in place of a ``storage://`` image (it only checks structure, not pixels).
PLACEHOLDER_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def new_nonce() -> str:
    """128 random bits, drawn after the body is parsed so no body text can contain it."""
    return secrets.token_hex(16)


def sentinel(nonce: str, index: int) -> str:
    """The value that stands in for the ``index``-th distinct reference in the stored file (a NUL-led string)."""
    return f"\x00pb-img:{nonce}:{index}"


def sentinel_token(nonce: str, index: int) -> bytes:
    """The quoted token as it appears in the stored JSON (the NUL is written as the escape ``\\u0000``)."""
    return f'"{_SENTINEL_PREFIX}{nonce}:{index}"'.encode()


def swap_uris(refs: ImageRefs, mapping: dict[str, str]) -> None:
    """Replace the ``uri`` of every ``storage://`` image block by ``mapping[ref_id]``."""
    for block, ref_id in refs.slots:
        block.data["uri"] = mapping[ref_id]


def data_uri_prefix(subtype: str) -> str:
    return f"data:image/{subtype};base64,"


def inlined_size_lower_bound(stored_size: int, nonce: str, refs: ImageRefs, sizes: dict[str, int]) -> int:
    """The size the stored file will have once every reference is inlined, at least.

    Per occurrence the quoted sentinel becomes ``"data:image/<t>;base64,<base64>"``; the subtype is not
    known before the download, so the shortest one (``png``) is counted: the true size is never smaller,
    hence a refusal on this number is never wrong. ``sizes`` holds each image's raw byte length.
    """
    total = stored_size
    for index, ref_id in enumerate(refs.order):
        token = len(sentinel_token(nonce, index))
        inlined = len(data_uri_prefix("png")) + 2 + 4 * math.ceil(sizes[ref_id] / 3)
        total += refs.counts[ref_id] * (inlined - token)
    return total
