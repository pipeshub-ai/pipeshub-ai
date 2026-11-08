"""Image references in a push: ``data:`` URIs of PNG, JPEG, GIF and WebP, and ``storage://<id>`` references.

Nothing here makes a network request. A remote reference is refused outright
(the push has no fetch code), and indexing's image parser then finds only
``data:`` URIs in what a push stored. A ``storage://<24 hex>`` reference names a
document in the platform's storage; it is collected here (with its caps), swapped for a
sentinel in the stored bytes, and replaced by the image's own ``data:`` URI after the
materialiser has fetched it (``materialiser.py``). Markdown and HTML groups that
indexing processes are scanned with the extraction indexing itself uses, and every
image in them must be a ``data:`` URI.
"""

import base64
import binascii
import math
import re
import secrets
from dataclasses import dataclass, field

from app.modules.parsers.html_parser.image_sources import iter_img_sources
from app.modules.parsers.markdown.image_references import (
    TooManyImages,
    extract_and_replace_images,
)

from .contract import Blocks, ImageBlock, PushRefusal, SectionGroup

MAX_IMAGE_BYTES = 20 << 20
MAX_DISTINCT_REFS = 64
MAX_OCCURRENCES_OF_ONE_REF = 16
MAX_OCCURRENCES = 256
MAX_IMAGES_PER_RECORD = 1000
#: HTML markup a record may hold in processed groups, not counting the bytes of inline ``data:`` images. An
#: HTML tokenizer builds a tree, so its time grows with the markup and not with the images; the limit keeps the scan,
#: and the indexing of the same text, bounded.
MAX_HTML_GROUP_BYTES = 2 << 20
STORAGE_SCHEME = "storage://"
_STORAGE_REF = re.compile(r"storage://([0-9a-f]{24})")
_SENTINEL_PREFIX = "\\u0000pb-img:"  # the six characters of the JSON escape, as they appear in the stored file
# The prefix is bounded so a text made of "data:" repeated cannot make the search quadratic; base64 has no
# character that opens a tag, so excluding it from the count excludes nothing a parser has to work on.
_BASE64_PAYLOAD = re.compile(r"data:[a-z]{1,20}/[a-z0-9.+-]{1,20};base64,([A-Za-z0-9+/=]*)", re.IGNORECASE)
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
    if uri[: match.end()] != uri[: match.end()].lower():
        # Indexing reads an inline image only under the lower-case prefix; anything else would
        # be accepted here and then silently missing from the record.
        raise _fail("IMAGE_REF_INVALID", location, "Write the data:image/...;base64, prefix in lower case.")
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
    _check_html_budget(blocks.block_groups or [])
    for i, group in enumerate(blocks.block_groups or []):
        if group.requires_processing:
            _check_group_images(group, i, refs)
    return refs


def _check_html_budget(groups: list) -> None:
    """Refuse a record whose processed HTML groups hold too much text, before any of it is scanned."""
    total = sum(
        len(g.data) - sum(len(m.group(1)) for m in _BASE64_PAYLOAD.finditer(g.data))
        for g in groups
        if g.requires_processing and g.format == "html" and isinstance(g.data, str)
    )
    if total > MAX_HTML_GROUP_BYTES:
        raise PushRefusal(
            422, "LIMIT_EXCEEDED",
            f"The HTML of groups with requires_processing may hold at most {MAX_HTML_GROUP_BYTES >> 20} MiB of markup in total, not counting inline images.",
            "blocks.block_groups",
            extra={"code": "BLOCKS_INVALID", "limit": "htmlGroupBytes", "limitBytes": MAX_HTML_GROUP_BYTES},
        )


def group_image_sources(data: str, fmt: str | None, limit: int) -> list[str]:
    """The source of every image of a group's ``data``, found the way indexing finds them.

    Indexing reads a group as HTML when its format says so and as markdown otherwise. Raises
    ``TooManyImages`` when there are more than ``limit``.
    """
    if fmt == "html":
        lowered = data.lower()
        # Every tag starts with "<img", so this counts at least the tags. It decides before a parser
        # builds a tree, and a document without the text has no images to parse for.
        count = lowered.count("<img")
        if count > limit:
            raise TooManyImages(limit)
        return list(iter_img_sources(data)) if count else []
    return [image["url"] for image in extract_and_replace_images(data, max_images=limit)[1]]


def _check_group_images(group: object, i: int, refs: ImageRefs) -> None:
    """Every image of a group that indexing turns into blocks must be an inline ``data:`` image.

    Indexing reads such a group as HTML when its format says so and as markdown otherwise, and takes
    the images from the same extraction used here, so what is checked is what it would look at.
    """
    where = f"blocks.block_groups[{i}]"
    data = getattr(group, "data", None)
    if not isinstance(group, SectionGroup) or not isinstance(data, str) or not data:
        raise _fail(
            "BLOCKS_INVALID", f"{where}.requires_processing",
            "requires_processing needs a text_section, list or ordered_list group whose data is a non-empty string.",
        )
    location = f"{where}.data"
    try:
        sources = group_image_sources(data, group.format, MAX_IMAGES_PER_RECORD - refs.images)
    except TooManyImages:
        raise _limit(location, f"A record may hold at most {MAX_IMAGES_PER_RECORD} images.") from None
    for source in sources:
        refs.images += 1
        if source.strip()[:5].lower() != "data:":
            raise _fail(
                "IMAGE_REF_REFUSED", location,
                "An image in a markdown or HTML group must be a data:image/(png|jpeg|gif|webp);base64 URI.",
            )
        if source != source.strip():
            # The parser keeps these spaces (an angle-bracket destination), and indexing reads an
            # inline image only when its URI starts with data:, so the image would be dropped.
            raise _fail("IMAGE_REF_INVALID", location, "Remove the spaces around the image's data: URI.")
        check_data_uri(source, location)


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
