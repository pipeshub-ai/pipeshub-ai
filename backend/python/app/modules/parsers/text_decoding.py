"""Decode uploaded text files (plain text, Markdown, MDX, HTML, JSON) into str.

Files arrive in whatever encoding the tool that saved them used: UTF-8, UTF-8
or UTF-16 with a byte-order mark (Windows Notepad, Excel "Unicode Text",
PowerShell), or Windows-1252. A strict UTF-8 decode turns every one of those
into either an exception or a stray BOM glued to the first word.
"""

from __future__ import annotations

import codecs

from bs4.dammit import EncodingDetector

# UTF-32 LE must be checked before UTF-16 LE: its BOM starts with the same two bytes.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _labels(codec: str, *labels: str) -> dict[str, str]:
    return dict.fromkeys(labels, codec)


# Charset labels the WHATWG Encoding Standard (https://encoding.spec.whatwg.org/#names-and-labels)
# reads differently from Python's codec of the same name. Browsers decode an
# "iso-8859-1" page as windows-1252, so Word and older editors write curly
# quotes, dashes and the euro sign in 0x80-0x9F under that label; Python's
# Latin-1 would turn them into invisible control characters.
_WHATWG_LABELS: dict[str, str] = {
    **_labels(
        "cp1252",
        "ansi_x3.4-1968", "ascii", "cp1252", "cp819", "csisolatin1", "ibm819",
        "iso-8859-1", "iso-ir-100", "iso8859-1", "iso88591", "iso_8859-1",
        "iso_8859-1:1987", "l1", "latin1", "latin-1", "us-ascii", "windows-1252", "x-cp1252",
    ),
    **_labels(
        "cp1254",
        "cp1254", "csisolatin5", "iso-8859-9", "iso-ir-148", "iso8859-9", "iso88599",
        "iso_8859-9", "iso_8859-9:1989", "l5", "latin5", "windows-1254", "x-cp1254",
    ),
    **_labels(
        "cp874",
        "dos-874", "iso-8859-11", "iso8859-11", "iso885911", "tis-620", "windows-874",
    ),
    **_labels(
        "gb18030",
        "chinese", "csgb2312", "csiso58gb231280", "gb2312", "gb_2312", "gb_2312-80", "gbk",
        "iso-ir-58", "x-gbk", "gb18030",
    ),
    **_labels(
        "cp949",
        "cseuckr", "csksc56011987", "euc-kr", "iso-ir-149", "korean", "ks_c_5601-1987",
        "ks_c_5601-1989", "ksc5601", "ksc_5601", "windows-949",
    ),
    **_labels(
        "cp932",
        "csshiftjis", "ms932", "ms_kanji", "shift-jis", "shift_jis", "sjis", "windows-31j", "x-sjis",
    ),
    **_labels("big5hkscs", "big5", "big5-hkscs", "cn-big5", "csbig5", "x-x-big5"),
}

# Encodings whose text can be pure ASCII bytes, so a successful UTF-8 decode
# proves nothing about them; a declaration of one of these is trusted first.
_STATEFUL_CODECS = frozenset({
    "iso2022_jp", "iso2022_jp_1", "iso2022_jp_2", "iso2022_jp_2004", "iso2022_jp_3",
    "iso2022_jp_ext", "iso2022_kr", "hz", "utf-7",
})


def _declared_codec(content: bytes) -> str | None:
    label = EncodingDetector.find_declared_encoding(content, is_html=True)
    if not label:
        return None
    label = label.strip().lower()
    codec = _WHATWG_LABELS.get(label, label)
    try:
        return codecs.lookup(codec).name
    except LookupError:
        return None


def decode_text(content: bytes | str, *, html: bool = False) -> str:
    """Return *content* as text, never raising on an unexpected encoding.

    Order: byte-order mark; for HTML, a declared stateful charset such as
    ISO-2022-JP; strict UTF-8; for HTML, any other declared charset, read the
    way browsers read its label; then Windows-1252 with undefined bytes replaced.
    """
    if isinstance(content, str):
        return content.removeprefix("\ufeff")

    for bom, encoding in _BOMS:
        if content.startswith(bom):
            return content[len(bom):].decode(encoding, errors="replace")

    declared = _declared_codec(content) if html else None

    if declared in _STATEFUL_CODECS:
        try:
            return content.decode(declared)
        except UnicodeDecodeError:
            pass

    # UTF-8 goes before any other declared charset: pages re-saved as UTF-8
    # often keep a stale <meta charset="iso-8859-1">, and single-byte codecs
    # accept any bytes, so trusting the declaration first would garble accents.
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass

    if declared:
        try:
            return content.decode(declared)
        except UnicodeDecodeError:
            pass

    # A UTF-8 file with a few damaged bytes still has far more valid multi-byte
    # characters than broken ones; reading it as Windows-1252 would garble them all.
    lenient = content.decode("utf-8", errors="replace")
    broken = lenient.count("\ufffd")
    valid_non_ascii = sum(1 for ch in lenient if ord(ch) > 0x7F) - broken
    if valid_non_ascii > broken:
        return lenient

    return content.decode("cp1252", errors="replace")
