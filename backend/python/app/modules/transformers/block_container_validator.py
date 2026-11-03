from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, List, Optional

from app.exceptions.indexing_exceptions import BlockContainerValidationError
from app.models.blocks import BlockType, BlocksContainer, DataFormat, GroupType, IndexRange
import base64

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

 
# ── image magic signatures ──────────────────────────────────────────
# Each entry: (prefix_bytes, secondary_bytes_at_offset_8_or_None)
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", None),   # PNG
    (b"\xff\xd8\xff",       None),   # JPEG / JFIF / EXIF
    (b"GIF87a",             None),   # GIF 87a
    (b"GIF89a",             None),   # GIF 89a
    (b"RIFF",               b"WEBP"),# WEBP  (RIFF....WEBP)
    (b"II\x2a\x00",        None),   # TIFF little-endian
    (b"MM\x00\x2a",        None),   # TIFF big-endian
    (b"\x00\x00\x01\x00",  None),   # ICO
    (b"\x00\x00\x02\x00",  None),   # CUR
)
 
 
def _is_image_data(data: bytes) -> bool:
    """Return True if *data* starts with a known image magic signature."""
    if len(data) < 4:
        return False
 
    for prefix, secondary in _IMAGE_SIGNATURES:
        if data[: len(prefix)] == prefix:
            if secondary is not None:
                # RIFF container: verify the format marker at byte 8
                return len(data) >= 12 and data[8:12] == secondary
            return True
 
    # AVIF / HEIC / HEIF — ISO BMFF ftyp box
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis", b"heic", b"heix", b"mif1"):
            return True
 
    return False
 
 
# ── regex helpers ────────────────────────────────────────────────────
_B64_CHARS = r"A-Za-z0-9+/\-_"
 
# data:image/<subtype>;base64,<payload>
_DATA_URI_RE = re.compile(
    rf"data:image/[a-zA-Z0-9.+_-]+;base64,([{_B64_CHARS}=\s]+)",
    re.ASCII,
)
 
# Raw base64 runs — at least 16 contiguous base64 chars (decodes to ≥12
# bytes, enough for every magic signature) optionally followed by more
# base64, whitespace, or padding.
_RAW_B64_RE = re.compile(
    rf"[{_B64_CHARS}]{{16,}}[{_B64_CHARS}=\s]*",
    re.ASCII,
)
 
 
# Docs, tickets and chats quote truncated examples
# ("data:image/png;base64,iVBORw0KGgo...") whose prefix decodes to a valid PNG
# signature; rejecting them failed whole records over a few harmless characters.
# 64 bytes is under the smallest real PNG (67) and over the header of every
# supported format; a smaller image left in a text block costs only a few dozen
# characters, which is all this check exists to prevent.
_MIN_IMAGE_PAYLOAD_BYTES = 64
_MIN_IMAGE_PAYLOAD_CHARS = (_MIN_IMAGE_PAYLOAD_BYTES * 4 + 2) // 3
# Wrapped base64 breaks lines at 64 (PEM) or 76 (MIME) characters. A run of
# full lines may be led by one short line (the rest of a "data:image/...;base64,"
# line) and closed by one padded short line. An unpadded short line after a full
# one ends the run uncounted, so a prose word after a quoted fragment cannot push
# it over the minimum.
_MIN_WRAPPED_LINE_CHARS = 60
_SEGMENT_RE = re.compile(r"\S+")


def _image_payload_prefixes(raw: str) -> Iterator[str]:
    """Yield the start of each base64 run in *raw* long enough to be an image.

    A run led by a short segment yields twice: from the short segment and from
    the first full line, because the short one may be a prose word.
    """
    lead = ""
    run: list[str] = []
    length = 0
    for m in _SEGMENT_RE.finditer(raw):
        segment = m.group()
        chars = len(segment.rstrip("="))
        full = chars >= _MIN_WRAPPED_LINE_CHARS
        # Padding only ever ends an encoding, so a padded short line is the
        # last line of a wrap rather than prose.
        tail = not full and segment.endswith("=") and bool(run or lead)
        if full or tail:
            if not run:
                run = [lead + segment] if lead else []
                if full:
                    run.append(segment)
                length = len(lead.rstrip("="))
            before, length = length, length + chars
            if before < _MIN_IMAGE_PAYLOAD_CHARS <= length:
                yield from run
            if full:
                lead = ""
                continue
        run, length = [], 0
        lead = "" if segment.endswith("=") else segment


def _decode_b64_prefix(raw: str, decoded_bytes_needed: int = 32) -> Optional[bytes]:
    """Decode just enough of a base64 string to check magic bytes.
 
    Strips whitespace, normalises base64url → standard base64, fixes
    padding, and returns the first *decoded_bytes_needed* decoded bytes
    (or fewer if the input is short).  Returns ``None`` on failure.
    """
    cleaned = re.sub(r"\s+", "", raw)
    if len(cleaned) < 8:          # too short to be any image
        return None
 
    # base64url → standard
    cleaned = cleaned.replace("-", "+").replace("_", "/")
 
    # Only decode enough chars for the prefix we need.
    # 4 base64 chars → 3 bytes
    chars_needed = ((decoded_bytes_needed + 2) // 3) * 4
    snippet = cleaned[:chars_needed]
 
    # Fix padding
    pad = (4 - len(snippet) % 4) % 4
    snippet += "=" * pad
 
    try:
        return base64.b64decode(snippet, validate=True)
    except Exception:
        # Retry without strict validation (ignores stray characters)
        try:
            return base64.b64decode(snippet, validate=False)
        except Exception:
            return None
 
 
# ── public API ───────────────────────────────────────────────────────
_MAX_CANDIDATES = 64  # safety cap to avoid pathological inputs
 
 
def contains_base64_image(text: str) -> bool:
    """Return ``True`` if *text* contains a base64-encoded image substring.
 
    Detection covers two forms:
 
    1. **Data-URI** — ``data:image/<type>;base64,<payload>``
       The payload is decoded and its leading bytes are checked against
       known image magic signatures.
 
    2. **Raw base64** — a bare base64 (or base64url) string whose decoded
       bytes begin with a recognised image header.
 
    Supported formats: PNG, JPEG, GIF, BMP, WEBP, TIFF, ICO, CUR,
    AVIF, HEIC.
 
    The function is designed to be robust against:
      • missing or excess ``=`` padding
      • base64url encoding (``-_`` instead of ``+/``)
      • embedded whitespace / line-breaks (MIME-style)
      • very large input strings (only a prefix is decoded per candidate)
    """
    tried = 0
 
    # ── pass 1: data URIs ────────────────────────────────────────
    for m in _DATA_URI_RE.finditer(text):
        if tried >= _MAX_CANDIDATES:
            break
        tried += 1
        for prefix in _image_payload_prefixes(m.group(1)):
            decoded = _decode_b64_prefix(prefix)
            if decoded and _is_image_data(decoded):
                return True
 
    # ── pass 2: raw base64 ───────────────────────────────────────
    for m in _RAW_B64_RE.finditer(text):
        if tried >= _MAX_CANDIDATES:
            break
        tried += 1
        for prefix in _image_payload_prefixes(m.group()):
            decoded = _decode_b64_prefix(prefix)
            if decoded and _is_image_data(decoded):
                return True

    return False


# What a TEXT block keeps where an image payload was.
BASE64_IMAGE_PLACEHOLDER = "[image data omitted]"


# A line too short to hold an image by itself is a line of a wrap (MIME wraps at
# 76, other encoders at 80 or 84); one at least that long is a whole payload.
_MAX_WRAPPED_LINE_CHARS = _MIN_IMAGE_PAYLOAD_CHARS - 1


def _png_length(data: bytes | bytearray) -> int | None:
    """Length of the PNG at the start of *data* once its IEND chunk is in, by
    walking the chunks (length, type, data, CRC)."""
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        pos += 12 + length
        if kind == b"IEND":
            return pos if pos <= len(data) else None
    return None


def _jpeg_length(data: bytes | bytearray) -> int | None:
    """Length of the JPEG at the start of *data* once its end-of-image marker is
    in. Segments are skipped by their length, so an EXIF thumbnail's own EOI
    inside APP1 is passed over; entropy-coded data after SOS runs to the next
    marker that isn't a stuffed byte or a restart marker."""
    pos = 2
    while pos + 2 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        pos += 2
        if marker == 0xD9:
            return pos
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if pos + 2 > len(data):
            return None
        pos += int.from_bytes(data[pos:pos + 2], "big")
        if marker == 0xDA:
            while True:
                pos = data.find(b"\xff", pos)
                if pos < 0 or pos + 1 >= len(data):
                    return None
                following = data[pos + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    pos += 2
                    continue
                break
    return None


def _gif_length(data: bytes | bytearray) -> int | None:
    """Length of the GIF at the start of *data* once its trailer is in, by
    walking the colour tables, extensions and image data sub-blocks."""

    def sub_blocks(pos: int) -> int | None:
        while pos < len(data):
            size = data[pos]
            pos += 1 + size
            if size == 0:
                return pos
        return None

    if len(data) < 13:
        return None
    flags = data[10]
    pos = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    while pos < len(data):
        block = data[pos]
        if block == 0x3B:
            return pos + 1
        if block == 0x21:
            pos = sub_blocks(pos + 2)
        elif block == 0x2C:
            if pos + 10 > len(data):
                return None
            local = data[pos + 9]
            pos += 10 + (3 * 2 ** ((local & 7) + 1) if local & 0x80 else 0) + 1
            pos = sub_blocks(pos)
        else:
            return None
        if pos is None:
            return None
    return None


def _webp_length(data: bytes | bytearray) -> int | None:
    return int.from_bytes(data[4:8], "little") + 8 if len(data) >= 8 else None


def _ico_length(data: bytes | bytearray) -> int | None:
    """End of the last image an ICO or CUR directory points at."""
    if len(data) < 6:
        return None
    count = int.from_bytes(data[4:6], "little")
    if not count or len(data) < 6 + 16 * count:
        return None
    return max(
        int.from_bytes(data[at + 8:at + 12], "little") + int.from_bytes(data[at + 12:at + 16], "little")
        for at in range(6, 6 + 16 * count, 16)
    )


# How to tell the image's length from its first bytes, per format, and the last
# bytes that can end it (None: its length is in its header). TIFF, AVIF and
# HEIC have no end that can be read in order, so their runs go on through every
# line of the wrap.
_IMAGE_ENDS: tuple[tuple[bytes, Callable[[bytearray], int | None], bytes | None], ...] = (
    (b"\x89PNG\r\n\x1a\n", _png_length, b"IEND\xaeB`\x82"),
    (b"\xff\xd8\xff", _jpeg_length, b"\xff\xd9"),
    (b"GIF8", _gif_length, b"\x3b"),
    (b"RIFF", _webp_length, None),
    (b"\x00\x00\x01\x00", _ico_length, None),
    (b"\x00\x00\x02\x00", _ico_length, None),
)


class _ImageEnd:
    """Decodes a payload line by line and says when the image is whole.

    Only whole quads are decoded; the rest waits for the next line. A format
    whose length is in its header is measured once; one that ends with a marker
    is walked only when the bytes so far end with that marker.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._data = bytearray()
        self._measure: Callable[[bytearray], int | None] | None = None
        self._ending: bytes | None = None
        self._known = False
        self._length: int | None = None

    def feed(self, line: str) -> None:
        self._pending += line.rstrip("=").replace("-", "+").replace("_", "/")
        whole = len(self._pending) // 4 * 4
        try:
            self._data += base64.b64decode(self._pending[:whole], validate=True)
        except Exception:
            self._measure = None  # not base64 after all: no end can be read
            self._known = True
        self._pending = self._pending[whole:]
        if not self._known:
            for magic, measure, ending in _IMAGE_ENDS:
                if self._data[: len(magic)] == magic:
                    self._measure, self._ending = measure, ending
                    break
            self._known = len(self._data) >= 8

    def finished_by(self, line: str) -> bool:
        """Whether *line*, a short last line, completes the image. Asked once,
        at the end of a run, so the copy it makes is made once."""
        probe = _ImageEnd()
        probe.__dict__.update(self.__dict__)
        probe._data = bytearray(self._data)
        probe.feed(line)
        return probe.whole

    @property
    def whole(self) -> bool:
        """Whether the bytes so far hold the whole image."""
        if self._measure is None or self._pending:
            return False
        data = self._data
        if self._ending is None:
            if self._length is None:
                self._length = self._measure(data)
            return self._length is not None and self._length <= len(data)
        if not data.endswith(self._ending):
            return False
        length = self._measure(data)
        return length is not None and length <= len(data)


def _one_line_break(gap: str) -> bool:
    """Whether *gap* between two segments is a single line break: the next
    line of a wrap. A blank line, or a space on the same line, ends it."""
    return gap.count("\n") == 1 and not gap.strip()


def _image_payload_spans(raw: str) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` in *raw* of each base64 run that holds an image.

    A run starts as ``_image_payload_prefixes`` reads it: a full line, maybe led
    by one short segment, which belongs to the span only when the image is found
    by reading from it (otherwise it is a prose word before the payload). Unlike
    that check, which only has to find an image, the span has to stop where the
    payload does, or it takes the next line of text with it. So a run goes on
    only through the lines of a wrap:

    - A line long enough to be an image by itself, or one that ends in padding,
      is the whole of what is left: the run ends with it.
    - Each next line is one line break further on. The second line sets the
      wrap's width; the first can be shorter (the rest of a ``base64,`` line).
      Every later full line has that width.
    - The run ends once the lines so far decode to a whole image of the format
      its first bytes name (``_ImageEnd``): a PNG at IEND, a JPEG at the EOI
      after its scans, a GIF at its trailer, WEBP, ICO and CUR at the size in
      their headers. So a same-width line after an image that ends on a full
      line is not taken. TIFF, AVIF and HEIC can't be measured that way, so
      their runs go on through every line of the wrap.
    - A shorter last line belongs to the run when it is padded and brings the
      run to a whole number of base64 quads, or when it completes the image. An
      unpadded short line that doesn't (or follows a format whose end can't be
      read) is left as text: it can't be told apart from a word, and it is too
      short to be an image.
    """
    segments = list(_SEGMENT_RE.finditer(raw))
    i = 0
    while i < len(segments):
        m = segments[i]
        segment = m.group()
        if len(segment.rstrip("=")) < _MIN_WRAPPED_LINE_CHARS:
            i += 1
            continue
        previous = segments[i - 1] if i else None
        lead = (
            previous
            if previous is not None
            and len(previous.group().rstrip("=")) < _MIN_WRAPPED_LINE_CHARS
            and not previous.group().endswith("=")
            else None
        )
        start = None
        image = _ImageEnd()
        if lead is not None:
            decoded = _decode_b64_prefix(lead.group() + segment)
            if decoded and _is_image_data(decoded):
                start = lead.start()
                image.feed(lead.group())
        if start is None:
            decoded = _decode_b64_prefix(segment)
            if decoded and _is_image_data(decoded):
                start = m.start()
        image.feed(segment)

        end = m.end()
        total = len(re.sub(r"\s", "", raw[start if start is not None else m.start():end]))
        last = i
        if len(segment) <= _MAX_WRAPPED_LINE_CHARS and not segment.endswith("="):
            width = None
            for j in range(i + 1, len(segments)):
                if image.whole:
                    break
                line = segments[j]
                text = line.group()
                if not _one_line_break(raw[segments[j - 1].end():line.start()]):
                    break
                limit = width or _MAX_WRAPPED_LINE_CHARS
                if text.endswith("=") and len(text) <= limit:
                    if (total + len(text)) % 4 == 0:
                        end, last = line.end(), j
                    break
                if len(text) < (width or len(segment)):
                    # A short last line, unpadded: part of the image only if it
                    # completes it, so a word after the payload stays.
                    if image.finished_by(text):
                        end, last = line.end(), j
                    break
                if not _MIN_WRAPPED_LINE_CHARS <= len(text) <= limit:
                    break
                if width is None:
                    if len(text) < len(segment):
                        break
                    width = len(text)
                elif len(text) != width:
                    break
                total += len(text)
                image.feed(text)
                end, last = line.end(), j

        chars = len(re.sub(r"[\s=]", "", raw[start:end])) if start is not None else 0
        if start is not None and chars >= _MIN_IMAGE_PAYLOAD_CHARS:
            yield start, end
        i = last + 1


def _strip_image_runs(raw: str) -> str:
    spans = list(_image_payload_spans(raw))
    for start, end in reversed(spans):
        raw = raw[:start] + BASE64_IMAGE_PLACEHOLDER + raw[end:]
    return raw


def strip_base64_images(text: str) -> str:
    """*text* with every base64 image payload replaced by a short placeholder.

    For text that cannot become an IMAGE block: an image inside code, a table
    cell, or a code fence the author never closed. Without this the block fails
    TEXT_DATA_CONTAINS_BASE64_IMAGE and takes the whole record with it. A data
    URI keeps its ``data:image/<type>;base64,`` header, and markdown image syntax
    keeps its alt text, so the text still says an image was there.

    Unlike ``contains_base64_image`` this reads the whole text, so a payload past
    that check's candidate cap is removed too. Text without an image comes back
    unchanged.
    """
    if not text:
        return text

    def strip_data_uri(m: re.Match[str]) -> str:
        return m.group()[: m.start(1) - m.start()] + _strip_image_runs(m.group(1))

    text = _DATA_URI_RE.sub(strip_data_uri, text)
    return _RAW_B64_RE.sub(lambda m: _strip_image_runs(m.group()), text)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    location: str


class BlockContainerValidator:
    """
    Runs structural and content validations on a BlocksContainer before embedding.

    All checks always run; errors are raised together as a BlockContainerValidationError.
    Warnings are logged and returned so callers can surface them without aborting.

    Adding a new validation: implement a ``_check_*`` method and call it inside
    ``validate()`` — no other changes required.
    """

    _TEXT_FORMATS = {
        DataFormat.TXT.value,
        DataFormat.MARKDOWN.value,
        DataFormat.CODE.value,
        DataFormat.HTML.value,
        DataFormat.JSON.value,
        DataFormat.YAML.value,
    }

    def __init__(
        self,
        logger=None,
        record_id: Optional[str] = None,
        virtual_record_id: Optional[str] = None,
        record_name: Optional[str] = None,
    ) -> None:
        self._logger = logger
        self._record_id = record_id
        self._virtual_record_id = virtual_record_id
        self._record_name = record_name

    # ──────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────

    def validate(self, container: BlocksContainer) -> List[ValidationIssue]:
        """Run all validations.

        Raises:
            BlockContainerValidationError: if any ERROR-severity issue is found.
        Returns:
            List of WARNING-severity issues (already logged).
        """
        issues: List[ValidationIssue] = []

        blocks = container.blocks or []
        block_groups = container.block_groups or []

        self._check_index_contiguity(blocks, block_groups, issues)
        self._check_parent_child_linkage(blocks, block_groups, issues)
        self._check_text_blocks(blocks, issues)
        self._check_image_blocks(blocks, issues)
        self._check_table_row_blocks(blocks, block_groups, issues)
        self._check_table_groups(block_groups, blocks, issues)

        warnings = [i for i in issues if i.severity == Severity.WARNING]
        errors = [i for i in issues if i.severity == Severity.ERROR]

        record_context = self._format_record_context()

        for w in warnings:
            if self._logger:
                self._logger.warning(
                    "[block-validation]%s [%s] %s: %s",
                    record_context, w.location, w.code, w.message
                )

        if errors:
            raise BlockContainerValidationError(
                errors=errors,
                record_id=self._record_id,
                virtual_record_id=self._virtual_record_id,
                record_name=self._record_name,
            )

        return warnings

    # ──────────────────────────────────────────────────────────────────────
    # 1. Index contiguity & uniqueness
    # ──────────────────────────────────────────────────────────────────────

    def _check_index_contiguity(self, blocks, block_groups, issues) -> None:
        self._check_list_indices("block", blocks, issues)
        self._check_list_indices("block_group", block_groups, issues)

    @staticmethod
    def _check_list_indices(label: str, items, issues: list) -> None:
        seen: dict[int, int] = {}
        for pos, item in enumerate(items):
            idx = item.index
            loc = f"{label}[{pos}]"

            if idx is None:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_NULL",
                    message=f"{label}.index is null",
                    location=loc,
                ))
                continue

            if idx != pos:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_MISMATCH",
                    message=f"{label}.index={idx} does not match array position {pos}",
                    location=loc,
                ))

            if idx in seen:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="INDEX_DUPLICATE",
                    message=f"{label}.index={idx} already used at position {seen[idx]}",
                    location=loc,
                ))
            else:
                seen[idx] = pos

    # ──────────────────────────────────────────────────────────────────────
    # 2. Parent / child linkage
    # ──────────────────────────────────────────────────────────────────────

    def _check_parent_child_linkage(self, blocks, block_groups, issues) -> None:
        n_blocks = len(blocks)
        n_groups = len(block_groups)

        # block.parent_index must point at a valid group
        for i, block in enumerate(blocks):
            p = block.parent_index
            if p is not None and not (0 <= p < n_groups):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_OUT_OF_RANGE",
                    message=f"block.parent_index={p} out of range [0, {n_groups})",
                    location=f"block[{i}]",
                ))

        # block_group.parent_index must point at a valid group with no cycles
        for i, group in enumerate(block_groups):
            p = group.parent_index
            if p is None:
                continue
            if not (0 <= p < n_groups):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_OUT_OF_RANGE",
                    message=f"block_group.parent_index={p} out of range [0, {n_groups})",
                    location=f"block_group[{i}]",
                ))
                continue

            visited = {i}
            curr = p
            cycle_detected = False
            while curr is not None:
                if curr in visited:
                    cycle_detected = True
                    break
                if not (0 <= curr < n_groups):
                    break
                visited.add(curr)
                curr = block_groups[curr].parent_index

            if cycle_detected:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="PARENT_INDEX_CYCLE",
                    message=f"block_group[{i}].parent_index={p} introduces a cyclic reference",
                    location=f"block_group[{i}]",
                ))

        # Forward children: ranges must be in-bounds and cross-reference parent_index correctly
        children_block_membership: set[tuple[int, int]] = set()
        children_group_membership: set[tuple[int, int]] = set()

        for gi, group in enumerate(block_groups):
            if group.children is None:
                continue
            loc = f"block_group[{gi}]"

            for r in (group.children.block_ranges or []):
                if not self._append_if_block_range_invalid(r, n_blocks, loc, issues):
                    continue
                for bi in range(r.start, r.end + 1):
                    children_block_membership.add((gi, bi))
                    child = blocks[bi]
                    if child.parent_index is not None and child.parent_index != gi:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            code="CHILDREN_PARENT_INDEX_MISMATCH",
                            message=(
                                f"block[{bi}].parent_index={child.parent_index} "
                                f"but block is listed as child of block_group[{gi}]"
                            ),
                            location=loc,
                        ))

            for r in (group.children.block_group_ranges or []):
                if not self._append_if_group_range_invalid(r, n_groups, loc, issues):
                    continue
                for cgi in range(r.start, r.end + 1):
                    children_group_membership.add((gi, cgi))
                    child_group = block_groups[cgi]
                    if child_group.parent_index is not None and child_group.parent_index != gi:
                        issues.append(ValidationIssue(
                            severity=Severity.WARNING,
                            code="CHILDREN_PARENT_INDEX_MISMATCH",
                            message=(
                                f"block_group[{cgi}].parent_index={child_group.parent_index} "
                                f"but group is listed as child of block_group[{gi}]"
                            ),
                            location=loc,
                        ))

        # Reverse linkage: a block/group declaring a parent should appear in that parent's children
        for i, block in enumerate(blocks):
            p = block.parent_index
            if p is not None and 0 <= p < n_groups and (p, i) not in children_block_membership:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="REVERSE_LINKAGE_MISSING",
                    message=(
                        f"block[{i}].parent_index={p} but block[{i}] is absent "
                        f"from block_group[{p}].children.block_ranges"
                    ),
                    location=f"block[{i}]",
                ))

        for i, group in enumerate(block_groups):
            p = group.parent_index
            if p is not None and 0 <= p < n_groups and (p, i) not in children_group_membership:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="REVERSE_LINKAGE_MISSING",
                    message=(
                        f"block_group[{i}].parent_index={p} but block_group[{i}] is absent "
                        f"from block_group[{p}].children.block_group_ranges"
                    ),
                    location=f"block_group[{i}]",
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 3. TEXT blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_text_blocks(self, blocks, issues) -> None:
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.TEXT.value:
                continue
            loc = f"block[{i}](text)"

            # data must be a string — spaCy at vectorstore.py:1147 will crash on non-string
            if not isinstance(block.data, str):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TEXT_DATA_NOT_STRING",
                    message=(
                        f"data must be a string (empty string allowed), "
                        f"got {type(block.data).__name__}"
                    ),
                    location=loc,
                ))
            elif contains_base64_image(block.data):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TEXT_DATA_CONTAINS_BASE64_IMAGE",
                    message=(
                        "data contains an embedded base64 image; "
                        "images must be stored in IMAGE blocks, not TEXT blocks"
                    ),
                    location=loc,
                ))

            fmt = self._format(block.format)
            if fmt is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TEXT_FORMAT_MISSING",
                    message="format is missing; expected one of 'txt', 'markdown', 'code', 'html'",
                    location=loc,
                ))
            elif fmt not in self._TEXT_FORMATS:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TEXT_FORMAT_UNEXPECTED",
                    message=f"format='{fmt}' is unusual for a text block; expected one of 'txt', 'markdown', 'code', 'html'",
                    location=loc,
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 4. IMAGE blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_image_blocks(self, blocks, issues) -> None:
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.IMAGE.value:
                continue
            loc = f"block[{i}](image)"

            # format must be "base64"; document_extraction.py:198 does block.format.value
            # which crashes if format is None
            fmt = self._format(block.format)
            if fmt != DataFormat.BASE64.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_FORMAT_NOT_BASE64",
                    message=f"image block format must be 'base64', got {fmt!r}",
                    location=loc,
                ))

            data = block.data
            if data is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="IMAGE_DATA_MISSING",
                    message="image block data is missing; block will not be embedded",
                    location=loc,
                ))
                continue

            if isinstance(data, str):
                # Legacy raw base64 string — silently skipped by vectorstore (no .get("uri"))
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_DATA_NOT_NORMALIZED",
                    message='image data is a raw string; normalize to {"uri": "..."} so the block is embedded',
                    location=loc,
                ))
            elif isinstance(data, dict):
                uri = data.get("uri")
                if not uri:
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="IMAGE_URI_EMPTY",
                        message="image data.uri is empty or missing; block will not be embedded",
                        location=loc,
                    ))
                elif not isinstance(uri, str):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="IMAGE_URI_INVALID_TYPE",
                        message=f"image data.uri must be a string, got {type(uri).__name__}",
                        location=loc,
                    ))
                elif not self._is_valid_image_uri(uri):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="IMAGE_URI_INVALID",
                        message="data.uri must be a data:image/...;base64,... URI",
                        location=loc,
                    ))
            else:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="IMAGE_DATA_INVALID_TYPE",
                    message=f"image data must be a dict or string, got {type(data).__name__}",
                    location=loc,
                ))

    @staticmethod
    def _is_valid_image_uri(uri: str) -> bool:
        uri = uri.strip()
        if uri.startswith("data:image/") and ";base64," in uri:
            return True
        return False

    # ──────────────────────────────────────────────────────────────────────
    # 5. TABLE_ROW blocks
    # ──────────────────────────────────────────────────────────────────────

    def _check_table_row_blocks(self, blocks, block_groups, issues) -> None:
        n_groups = len(block_groups)
        fragment_parent_indices = self._block_indices_with_fragment_children(blocks)
        for i, block in enumerate(blocks):
            if self._block_type(block) != BlockType.TABLE_ROW.value:
                continue
            loc = f"block[{i}](table_row)"

            fmt = self._format(block.format)
            if fmt != DataFormat.JSON.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_FORMAT_NOT_JSON",
                    message=f"table_row block format must be 'json', got {fmt!r}",
                    location=loc,
                ))

            data = block.data
            if data is None:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_DATA_MISSING",
                    message="table_row block data is missing; block cannot be embedded",
                    location=loc,
                ))
                self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)
                continue

            if not isinstance(data, dict):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_DATA_NOT_DICT",
                    message=f"table_row block data must be a dict, got {type(data).__name__}",
                    location=loc,
                ))
                # Skip content checks — data shape is wrong
                self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)
                continue

            self._check_table_row_parent(block, block_groups, n_groups, i, loc, issues)

            # Embedding content: cells OR row_natural_language_text must be present
            cells = data.get("cells")
            row_text = data.get("row_natural_language_text")
            has_cells = isinstance(cells, list) and len(cells) > 0

            if row_text is not None and not isinstance(row_text, str):
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_TEXT_NOT_STRING",
                    message=(
                        f"row_natural_language_text must be a string, "
                        f"got {type(row_text).__name__}"
                    ),
                    location=loc,
                ))
            elif not has_cells:
                row_container_index = block.index if block.index is not None else i
                has_fragment_children = row_container_index in fragment_parent_indices
                if not has_fragment_children and (
                    not isinstance(row_text, str) or not row_text.strip()
                ):
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="TABLE_ROW_NO_EMBEDDABLE_CONTENT",
                        message=(
                            "table_row has no non-empty cells list and "
                            "row_natural_language_text is absent or empty; block cannot be embedded"
                        ),
                        location=loc,
                    ))

            # row_number optional but must be int >= 1
            row_number = data.get("row_number")
            if row_number is not None and (not isinstance(row_number, int) or row_number < 1):
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TABLE_ROW_NUMBER_INVALID",
                    message=f"row_number should be int >= 1, got {row_number!r}",
                    location=loc,
                ))

            # cells must be list of strings
            if cells is not None:
                if not isinstance(cells, list):
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="TABLE_ROW_CELLS_NOT_LIST",
                        message=f"cells must be a list, got {type(cells).__name__}",
                        location=loc,
                    ))
                else:
                    for j, cell in enumerate(cells):
                        if not isinstance(cell, str):
                            issues.append(ValidationIssue(
                                severity=Severity.ERROR,
                                code="TABLE_ROW_CELL_NOT_STRING",
                                message=f"cells[{j}] must be a string, got {type(cell).__name__}",
                                location=loc,
                            ))

    @staticmethod
    def _check_table_row_parent(block, block_groups, n_groups, i, loc, issues) -> None:
        """Validate that table_row.parent_index points at a table-type group."""
        if block.parent_index is None:
            issues.append(ValidationIssue(
                severity=Severity.ERROR,
                code="TABLE_ROW_PARENT_INDEX_MISSING",
                message="table_row block must have parent_index pointing to a table group",
                location=loc,
            ))
        elif 0 <= block.parent_index < n_groups:
            parent = block_groups[block.parent_index]
            parent_type = (
                parent.type.value if hasattr(parent.type, "value") else str(parent.type)
            )
            if parent_type != GroupType.TABLE.value:
                issues.append(ValidationIssue(
                    severity=Severity.ERROR,
                    code="TABLE_ROW_PARENT_NOT_TABLE",
                    message=(
                        f"table_row parent block_group[{block.parent_index}] "
                        f"has type '{parent_type}', expected 'table'"
                    ),
                    location=loc,
                ))

    # ──────────────────────────────────────────────────────────────────────
    # 6. TABLE block groups
    # ──────────────────────────────────────────────────────────────────────

    def _check_table_groups(self, block_groups, blocks, issues) -> None:
        n_blocks = len(blocks)
        for gi, group in enumerate(block_groups):
            if self._group_type(group) != GroupType.TABLE.value:
                continue
            loc = f"block_group[{gi}](table)"

            # Every child block must be table_row
            if group.children:
                for r in (group.children.block_ranges or []):
                    if not self._is_index_range_in_bounds(r, n_blocks):
                        continue
                    for bi in range(r.start, r.end + 1):
                        child_type = self._block_type(blocks[bi])
                        if child_type != BlockType.TABLE_ROW.value:
                            issues.append(ValidationIssue(
                                severity=Severity.ERROR,
                                code="TABLE_CHILD_NOT_TABLE_ROW",
                                message=(
                                    f"table group child block[{bi}] has type '{child_type}', "
                                    "expected 'table_row'"
                                ),
                                location=loc,
                            ))

            # table_metadata presence and num_of_cells
            tm = group.table_metadata
            if tm is None:
                issues.append(ValidationIssue(
                    severity=Severity.WARNING,
                    code="TABLE_METADATA_MISSING",
                    message="table group has no table_metadata",
                    location=loc,
                ))
            else:
                num_cells: Optional[int] = (
                    tm.get("num_of_cells") if isinstance(tm, dict)
                    else getattr(tm, "num_of_cells", None)
                )
                if num_cells is None:
                    issues.append(ValidationIssue(
                        severity=Severity.WARNING,
                        code="TABLE_METADATA_NUM_CELLS_MISSING",
                        message="table_metadata.num_of_cells is missing; table will be treated as 'large' during indexing",
                        location=loc,
                    ))
                elif not isinstance(num_cells, int) or num_cells < 0:
                    issues.append(ValidationIssue(
                        severity=Severity.ERROR,
                        code="TABLE_METADATA_NUM_CELLS_INVALID",
                        message=f"table_metadata.num_of_cells must be int >= 0, got {num_cells!r}",
                        location=loc,
                    ))

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _block_indices_with_fragment_children(blocks) -> set[int]:
        """Block indices referenced by image-split fragment children."""
        parents: set[int] = set()
        for block in blocks:
            if block.parent_block_index is not None:
                parents.add(block.parent_block_index)
        return parents

    @staticmethod
    def _is_index_range_in_bounds(r: IndexRange, upper_bound: int) -> bool:
        """True when [start, end] is non-inverted and fully within [0, upper_bound)."""
        if r.start < 0 or r.start > r.end or r.end >= upper_bound:
            return False
        return True

    @staticmethod
    def _append_if_block_range_invalid(
        r: IndexRange,
        n_blocks: int,
        location: str,
        issues: List[ValidationIssue],
    ) -> bool:
        """Return True when the range is safe to iterate; append one error otherwise."""
        if BlockContainerValidator._is_index_range_in_bounds(r, n_blocks):
            return True
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="CHILDREN_BLOCK_INDEX_OUT_OF_RANGE",
            message=(
                f"children.block_ranges [{r.start}, {r.end}] is out of range "
                f"for n_blocks={n_blocks}"
            ),
            location=location,
        ))
        return False

    @staticmethod
    def _append_if_group_range_invalid(
        r: IndexRange,
        n_groups: int,
        location: str,
        issues: List[ValidationIssue],
    ) -> bool:
        """Return True when the range is safe to iterate; append one error otherwise."""
        if BlockContainerValidator._is_index_range_in_bounds(r, n_groups):
            return True
        issues.append(ValidationIssue(
            severity=Severity.ERROR,
            code="CHILDREN_GROUP_INDEX_OUT_OF_RANGE",
            message=(
                f"children.block_group_ranges [{r.start}, {r.end}] is out of range "
                f"for n_groups={n_groups}"
            ),
            location=location,
        ))
        return False

    def _format_record_context(self) -> str:
        """Build a log-friendly record context string."""
        parts = []
        if self._record_id:
            parts.append(f"record_id={self._record_id}")
        if self._virtual_record_id:
            parts.append(f"vrid={self._virtual_record_id}")
        if self._record_name:
            parts.append(f"name={self._record_name!r}")
        return f" [{', '.join(parts)}]" if parts else ""

    @staticmethod
    def _block_type(block) -> Optional[str]:
        t = getattr(block, "type", None)
        if t is None:
            return None
        return t.value if hasattr(t, "value") else str(t)

    @staticmethod
    def _group_type(group) -> Optional[str]:
        t = getattr(group, "type", None)
        if t is None:
            return None
        return t.value if hasattr(t, "value") else str(t)

    @staticmethod
    def _format(fmt) -> Optional[str]:
        if fmt is None:
            return None
        return fmt.value if hasattr(fmt, "value") else str(fmt)

