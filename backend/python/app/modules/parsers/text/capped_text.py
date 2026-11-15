"""Log and configuration files read as text, kept to a size the index can hold.

A log grows without bound and its newest lines matter most, so an oversized one keeps its first
and last lines, at most ``TEXT_LOG_MAX_BYTES`` of decoded UTF-8 text in all, each part cut at a
line break, with a line saying how much was left out. Config and log files come from any system, so the charset is found per file (UTF-16 without
a byte-order mark is common from Windows services) and the result is UTF-8. A file holding NUL
bytes that are not UTF-16 is binary and is not read at all.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass

from app.config.file_types import TEXT_LOG_MAX_BYTES
from app.modules.parsers.text_decoding import decode_text

# Room in the cap for the line that says what was left out.
_SNIFF_BYTES = 8 * 1024
# Each end keeps at least this many bytes of text beside the marker.
_MIN_PART_BYTES = 16
_CHARSET_SAMPLE_BYTES = 64 * 1024
_NEWLINE_SEARCH_BYTES = 64 * 1024
_UTF16_NUL_SHARE = 0.9
_UTF16_PRINTABLE_SHARE = 0.95
_UTF16_SPACE_SHARE = 0.05
_UTF16_MAX_LINE = 400
_CHARDET_MIN_CONFIDENCE = 0.8
_CHARDET_FALLBACK_CONFIDENCE = 0.5
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


@dataclass(frozen=True, slots=True)
class CappedText:
    text: str
    total_bytes: int
    omitted_bytes: int  # of the file's bytes, how many the text leaves out (0 when it is whole)

    @property
    def truncated(self) -> bool:
        return self.omitted_bytes > 0


@dataclass(frozen=True, slots=True)
class BinaryFile:
    """Content that is not text."""


def _bom(content: bytes) -> tuple[str, int] | None:
    for bom, codec in _BOMS:
        if content.startswith(bom):
            return codec, len(bom)
    return None


def _decodes(sample: bytes, codec: str) -> bool:
    try:
        codecs.getincrementaldecoder(codec)("strict").decode(sample, final=False)
    except UnicodeDecodeError:
        return False
    return True


def _utf16_without_bom(sample: bytes) -> str | None:
    """'utf-16-le' or 'utf-16-be' when a sample holding NUL bytes reads as UTF-16 text.

    Text mostly in ASCII (any line length) has NULs at nearly every high byte and nearly none at
    the low ones, and decodes. Text in other scripts (Cyrillic, CJK, emoji) leaves fewer NULs: it
    is UTF-16 when it decodes strictly to printable text that has line breaks or spaces, which a
    binary that only decodes by chance does not.
    """
    pairs = len(sample) // 2
    if pairs == 0 or b"\x00" not in sample:
        return None
    sample = sample[:pairs * 2]
    odd_nul = sample[1::2].count(0)
    even_nul = sample[0::2].count(0)
    if odd_nul >= _UTF16_NUL_SHARE * pairs and even_nul <= (1 - _UTF16_NUL_SHARE) * pairs:
        return "utf-16-le" if _decodes(sample, "utf-16-le") else None
    if even_nul >= _UTF16_NUL_SHARE * pairs and odd_nul <= (1 - _UTF16_NUL_SHARE) * pairs:
        return "utf-16-be" if _decodes(sample, "utf-16-be") else None
    order = ("utf-16-le", "utf-16-be") if odd_nul >= even_nul else ("utf-16-be", "utf-16-le")
    return next((codec for codec in order if _reads_as_text(sample, codec)), None)


def _reads_as_text(sample: bytes, codec: str) -> bool:
    try:
        text = codecs.getincrementaldecoder(codec)("strict").decode(sample, final=False)
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    if printable < _UTF16_PRINTABLE_SHARE * len(text):
        return False
    # ASCII-heavy binary read as UTF-16 also looks printable (as CJK runs); text has line
    # breaks or spaces between its words.
    breaks = sum(1 for c in text if c in "\n\r")
    spaces = text.count(" ")
    return breaks >= max(1, len(text) // _UTF16_MAX_LINE) or spaces >= _UTF16_SPACE_SHARE * len(text)


def _codec(content: bytes) -> tuple[str | None, int]:
    """The codec to read *content* with and the length of its byte-order mark.

    None means no codec stood out, and each part is decoded on its own by ``decode_text``.
    """
    bom = _bom(content)
    if bom is not None:
        return bom
    utf16 = _utf16_without_bom(content[:_SNIFF_BYTES])
    if utf16 is not None:
        return utf16, 0
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        decoder.decode(content[:_CHARSET_SAMPLE_BYTES], final=len(content) <= _CHARSET_SAMPLE_BYTES)
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8", 0
    import chardet

    sample = content[:_CHARSET_SAMPLE_BYTES]
    guess = chardet.detect(sample)
    encoding, confidence = guess.get("encoding"), guess.get("confidence") or 0
    if not encoding or confidence < _CHARDET_FALLBACK_CONFIDENCE:
        return None, 0
    try:
        codec = codecs.lookup(encoding).name
    except LookupError:
        return None, 0
    if confidence >= _CHARDET_MIN_CONFIDENCE:
        return codec, 0
    # chardet is modest about short CJK samples (Shift-JIS at ~0.75); a guess the sample
    # decodes under without a single error is taken all the same.
    try:
        codecs.getincrementaldecoder(codec)("strict").decode(sample, final=len(content) <= len(sample))
    except UnicodeDecodeError:
        return None, 0
    return codec, 0


def _line_break(codec: str | None) -> bytes:
    if codec == "utf-16-le":
        return b"\n\x00"
    if codec == "utf-16-be":
        return b"\x00\n"
    if codec in ("utf-32-le", "utf-32-be"):
        return "\n".encode(codec)
    return b"\n"


def _aligned(content: bytes, position: int, codec: str | None, bom_length: int, *, forward: bool) -> int:
    """*position* moved to a character boundary: back for the end of the head, *forward* for the
    start of the tail, so neither part begins or ends inside a character (a UTF-16 surrogate pair,
    a UTF-8 sequence)."""
    unit = 4 if codec in ("utf-32-le", "utf-32-be") else 2 if codec in ("utf-16-le", "utf-16-be") else 1
    position -= (position - bom_length) % unit
    if position <= bom_length or position >= len(content):
        return position
    if unit == 2:
        high = content[position] if codec == "utf-16-be" else content[position + 1]
        if 0xDC <= high <= 0xDF:  # a low surrogate: the pair starts one unit earlier
            position += 2 if forward else -2
    elif unit == 1 and codec in (None, "utf-8"):
        step = 1 if forward else -1
        for _ in range(3):
            if not 0x80 <= content[position] <= 0xBF or not bom_length < position < len(content):
                break
            position += step
    return position


def _head_end(content: bytes, limit: int, newline: bytes) -> int:
    """Just after the last line break before *limit*, if one is near enough; else *limit*."""
    found = content.rfind(newline, max(0, limit - _NEWLINE_SEARCH_BYTES), limit)
    return found + len(newline) if found >= 0 else limit


def _tail_start(content: bytes, start: int, newline: bytes) -> int:
    """Just after the first line break at or after *start*, if one is near enough; else *start*."""
    found = content.find(newline, start, start + _NEWLINE_SEARCH_BYTES)
    return found + len(newline) if found >= 0 else start


def _decode(part: bytes, codec: str | None) -> str:
    if codec is None:
        return decode_text(part)
    return part.decode(codec, errors="replace")


def is_binary(content: bytes) -> bool:
    sample = content[:_SNIFF_BYTES]
    if b"\x00" not in sample:
        return False
    bom = _bom(sample)
    if bom is not None:
        # A byte-order mark makes it text only if what follows decodes under it.
        return not _decodes(sample[bom[1]:], bom[0])
    return _utf16_without_bom(sample) is None


def _trim_head(text: str, budget: int) -> str:
    """*text*'s first lines that fit in *budget* UTF-8 bytes (a whole line where one is near)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    cut = encoded[:budget]
    newline = cut.rfind(b"\n", max(0, budget - _NEWLINE_SEARCH_BYTES))
    return cut[: newline + 1 if newline >= 0 else budget].decode("utf-8", errors="ignore")


def _trim_tail(text: str, budget: int) -> str:
    """*text*'s last lines that fit in *budget* UTF-8 bytes (a whole line where one is near)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    cut = encoded[-budget:]
    newline = cut.find(b"\n", 0, _NEWLINE_SEARCH_BYTES)
    return cut[newline + 1 if newline >= 0 else 0:].decode("utf-8", errors="ignore")


def _marker(head: str, omitted: int, total: int) -> str:
    return (
        f"{'' if not head or head.endswith(chr(10)) else chr(10)}[... {omitted:,} bytes omitted from "
        f"this {total:,}-byte file; its first and last lines are kept ...]\n"
    )


def _longest_marker(total: int) -> int:
    return len(_marker("x", total, total).encode("utf-8"))


def minimum_cap() -> int:
    """The smallest *max_bytes* that keeps a line at each end of any file whose size fits in 63 bits."""
    return _longest_marker(2**63 - 1) + 2 * _MIN_PART_BYTES


def _raw_length(text: str, codec: str | None) -> int:
    try:
        return len(text.encode(codec or "utf-8"))
    except (UnicodeEncodeError, LookupError):
        return len(text.encode("utf-8"))


def prepare_capped_text(
    content: bytes, *, max_bytes: int = TEXT_LOG_MAX_BYTES, refuse_binary: bool = True,
) -> CappedText | BinaryFile:
    """*content* decoded: whole when it fits in *max_bytes* of UTF-8, else its first and last lines
    with a marker, all within *max_bytes*. *refuse_binary* False reads a binary as text anyway (a
    reader that always read it so)."""
    if refuse_binary and is_binary(content):
        return BinaryFile()
    codec, bom_length = _codec(content)
    total = len(content)
    # The marker's longest form for this size, so head + marker + tail stay within max_bytes.
    budget = (max_bytes - _longest_marker(total)) // 2
    if max_bytes < minimum_cap() or budget < _MIN_PART_BYTES:
        raise ValueError(f"max_bytes {max_bytes} is too small for the omission marker (minimum {minimum_cap()})")
    if total <= max_bytes:
        text = _decode(content[bom_length:], codec)
        if len(text.encode("utf-8")) <= max_bytes:
            return CappedText(text, total, 0)
        head_text = tail_text = text  # longer than both budgets together, so they cannot overlap
    else:
        # Only the ends are decoded: the middle of a large file is never read into a string.
        newline = _line_break(codec)
        half = max(1, max_bytes // 2)
        head_end = _aligned(content, _head_end(content, half, newline), codec, bom_length, forward=False)
        tail_start = _aligned(content, _tail_start(content, total - half, newline), codec, bom_length, forward=True)
        head_text = _decode(content[bom_length:head_end], codec)
        tail_text = _decode(content[tail_start:], codec)
    head = _trim_head(head_text, budget)
    tail = _trim_tail(tail_text, budget)
    omitted = max(0, total - bom_length - _raw_length(head, codec) - _raw_length(tail, codec))
    marker = _marker(head, omitted, total)
    return CappedText(f"{head}{marker}{tail}", total, omitted)
