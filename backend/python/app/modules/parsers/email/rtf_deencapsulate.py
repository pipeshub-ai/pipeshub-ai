"""The HTML an Outlook message keeps inside its RTF body (MS-OXRTFEX, ``\\fromhtml1``).

Outlook stores many HTML mails only as compressed RTF in which the original HTML is encapsulated:
markup sits in ``{\\*\\htmltagN ...}`` groups, text between them is the HTML's text, and RTF
written only for RTF readers is fenced by ``\\htmlrtf`` ... ``\\htmlrtf0``. This reads that
encapsulation back out. It is a single linear pass with no recursion, so a hostile file costs
time and memory in proportion to its size.
"""
from __future__ import annotations

import codecs
import re

_TOKEN = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?"  # control word, optional numeric parameter
    rb"|\\'([0-9a-fA-F]{2})"  # hex-escaped byte
    rb"|\\([^a-zA-Z'])"  # control symbol
    rb"|([{}])"
    rb"|([^\\{}\r\n]+)"
    rb"|[\r\n]+",
)
# Destinations whose content is never text of the HTML.
_SKIPPED = frozenset({
    b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict", b"object", b"listtable",
    b"listoverridetable", b"rsidtbl", b"generator", b"themedata", b"datastore", b"latentstyles",
})


def is_encapsulated_html(rtf: bytes) -> bool:
    return b"\\fromhtml1" in rtf[:2048]


# \fcharsetN -> code page (RTF 1.9.1, "Font table"); 0 and 1 are the document's \ansicpg.
_CHARSET_CODEPAGES = {
    2: "cp1252", 77: "mac_roman", 128: "cp932", 129: "cp949", 130: "cp1361", 134: "cp936",
    136: "cp950", 161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255", 178: "cp1256",
    186: "cp1257", 204: "cp1251", 222: "cp874", 238: "cp1250", 255: "cp437",
}


def _codec(name: str, fallback: str) -> str:
    try:
        return codecs.lookup(name).name
    except LookupError:
        return fallback


def fix_surrogates(text: str) -> str:
    """*text* with UTF-16 surrogate pairs joined into their character and lone halves replaced."""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


class _Group:
    __slots__ = ("font", "fonttbl", "html_tag", "skip", "uc")

    def __init__(self, *, html_tag: bool, skip: bool, uc: int, font: int | None, fonttbl: bool) -> None:
        self.html_tag = html_tag
        self.skip = skip
        self.uc = uc
        self.font = font
        self.fonttbl = fonttbl


def deencapsulate_html(rtf: bytes) -> str:
    """The HTML encapsulated in *rtf*. Call only when ``is_encapsulated_html`` is true."""
    match = re.search(rb"\\ansicpg(\d{1,5})", rtf[:4096])
    codepage = _codec(f"cp{match.group(1).decode()}", "cp1252") if match else "cp1252"
    # Visible text is in its font's code page; markup in \htmltag groups is in the document's.
    fonts: dict[int, str] = {}
    defining: list[int | None] = [None]  # the font the font table is declaring

    out: list[str] = []
    pending = bytearray()  # \'hh bytes, decoded together so multibyte codepages read right
    pending_codepage = codepage

    def current_codepage(group: _Group) -> str:
        if group.html_tag or group.font is None:
            return codepage
        return fonts.get(group.font, codepage)

    def flush() -> None:
        if pending:
            out.append(pending.decode(pending_codepage, errors="replace"))
            pending.clear()

    stack = [_Group(html_tag=False, skip=False, uc=1, font=None, fonttbl=False)]
    suppressed = False  # inside \htmlrtf ... \htmlrtf0
    star = False  # the last token was \*, so the next control word names a destination
    skip_chars = 0  # characters still to skip after a \uN

    for token in _TOKEN.finditer(rtf):
        word, param, hex_byte, symbol, brace, text = token.groups()
        group = stack[-1]
        if brace == b"{":
            flush()
            stack.append(_Group(
                html_tag=group.html_tag, skip=group.skip, uc=group.uc, font=group.font, fonttbl=group.fonttbl,
            ))
            star = False
            continue
        if brace == b"}":
            flush()
            if len(stack) > 1:
                stack.pop()
            star = False
            continue
        if symbol == b"*":
            star = True
            continue
        if word is not None and group.fonttbl:
            if word == b"f" and param is not None:
                defining[0] = int(param)
            elif word in (b"fcharset", b"cpg") and param is not None and defining[0] is not None:
                number = int(param)
                if word == b"cpg":
                    fonts[defining[0]] = _codec(f"cp{number}", codepage)
                elif number in _CHARSET_CODEPAGES:
                    fonts.setdefault(defining[0], _CHARSET_CODEPAGES[number])
            continue
        if word is not None:
            if word == b"fonttbl":
                group.fonttbl = group.skip = True
                star = False
                continue
            if word == b"f" and param is not None and not group.fonttbl:
                flush()
                group.font = int(param)
                continue
            if star or word in _SKIPPED:
                star = False
                if word.startswith(b"htmltag") or word == b"mhtmltag":
                    group.html_tag = True
                    group.skip = False
                elif word in _SKIPPED or group is not stack[0]:
                    group.skip = True
                continue
            if word == b"htmlrtf":
                suppressed = param != b"0"
                continue
            emitting = not group.skip and (group.html_tag or not suppressed)
            if word == b"uc" and param is not None:
                group.uc = max(0, int(param))
            elif word == b"u" and param is not None and emitting:
                flush()
                code_point = int(param) % 0x10000 if int(param) < 0 else min(int(param), 0x10FFFF)
                out.append(chr(code_point))  # a surrogate half is joined or replaced at the end
                skip_chars = group.uc
            elif emitting and word in (b"par", b"line"):
                flush()
                out.append("\r\n")
            elif emitting and word == b"tab":
                flush()
                out.append("\t")
            continue
        star = False
        emitting = not group.skip and (group.html_tag or not suppressed)
        if hex_byte is not None:
            if skip_chars:
                skip_chars -= 1
            elif emitting:
                if pending and pending_codepage != current_codepage(group):
                    flush()
                pending_codepage = current_codepage(group)
                pending.append(int(hex_byte, 16))
            continue
        if symbol is not None:
            if emitting and symbol in (b"\\", b"{", b"}"):
                flush()
                out.append(symbol.decode())
            elif emitting and symbol == b"~":
                flush()
                out.append("\u00a0")
            continue
        if text is not None and emitting:
            if skip_chars:
                dropped = min(skip_chars, len(text))
                text = text[dropped:]
                skip_chars -= dropped
            flush()
            out.append(text.decode(current_codepage(group), errors="replace"))
    flush()
    return fix_surrogates("".join(out))
