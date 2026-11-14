"""Log and configuration files read as plain text, kept to a size the index can hold.

A log grows without bound and its newest lines matter most, so an oversized one keeps its first
and last halves of the cap, each cut at a line break, with a line saying which bytes were left
out. Config and log files come from any system, so the charset is found per file (UTF-16 without
a byte-order mark is common from Windows services) and the result is UTF-8. A file holding NUL
bytes that are not UTF-16 is binary and is not read at all.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass

from app.modules.parsers.text_decoding import decode_text
from app.utils.env_config import env_int

TEXT_LOG_MAX_BYTES = env_int("TEXT_LOG_MAX_BYTES", 5 * 1024 * 1024)
_SNIFF_BYTES = 8 * 1024
_CHARSET_SAMPLE_BYTES = 64 * 1024
_NEWLINE_SEARCH_BYTES = 64 * 1024
_UTF16_NUL_SHARE = 0.9
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
    skipped: tuple[int, int] | None  # the [start, end) byte range left out, when over the cap


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
    """'utf-16-le' or 'utf-16-be' when the sample reads as UTF-16 text without a byte-order mark.

    Nearly every byte of one parity is NUL (ASCII-range characters) and nearly none of the other
    is, and the whole sample decodes. A NUL-padded binary has NULs at both parities.
    """
    pairs = len(sample) // 2
    if pairs == 0:
        return None
    odd_nul = sample[1:pairs * 2:2].count(0)
    even_nul = sample[0:pairs * 2:2].count(0)
    if odd_nul >= _UTF16_NUL_SHARE * pairs and even_nul <= (1 - _UTF16_NUL_SHARE) * pairs:
        codec = "utf-16-le"
    elif even_nul >= _UTF16_NUL_SHARE * pairs and odd_nul <= (1 - _UTF16_NUL_SHARE) * pairs:
        codec = "utf-16-be"
    else:
        return None
    return codec if _decodes(sample[:pairs * 2], codec) else None


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


def _aligned(position: int, codec: str | None, bom_length: int) -> int:
    unit = 4 if codec in ("utf-32-le", "utf-32-be") else 2 if codec in ("utf-16-le", "utf-16-be") else 1
    return position - (position - bom_length) % unit


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


def prepare_capped_text(content: bytes, *, max_bytes: int = TEXT_LOG_MAX_BYTES) -> CappedText | BinaryFile:
    """*content* decoded, whole when it fits in *max_bytes*, else its head and tail with a marker."""
    if is_binary(content):
        return BinaryFile()
    codec, bom_length = _codec(content)
    total = len(content)
    body = content[bom_length:]
    if total <= max_bytes:
        return CappedText(_decode(body, codec), total, None)

    newline = _line_break(codec)
    half = max(1, max_bytes // 2)
    head_end = _aligned(_head_end(content, half, newline), codec, bom_length)
    tail_start = _aligned(_tail_start(content, total - half, newline), codec, bom_length)
    head = _decode(content[bom_length:head_end], codec)
    tail = _decode(content[tail_start:], codec)
    marker = (
        f"{'' if head.endswith(chr(10)) else chr(10)}[... {tail_start - head_end:,} bytes left out "
        f"(bytes {head_end:,} to {tail_start:,} of {total:,}); the start and the end of the file "
        "are kept ...]\n"
    )
    return CappedText(f"{head}{marker}{tail}", total, (head_end, tail_start))
