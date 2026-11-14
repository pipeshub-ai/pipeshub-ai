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


class _Group:
    __slots__ = ("html_tag", "skip", "uc")

    def __init__(self, *, html_tag: bool, skip: bool, uc: int) -> None:
        self.html_tag = html_tag
        self.skip = skip
        self.uc = uc


def deencapsulate_html(rtf: bytes) -> str:
    """The HTML encapsulated in *rtf*. Call only when ``is_encapsulated_html`` is true."""
    match = re.search(rb"\\ansicpg(\d{1,5})", rtf[:4096])
    codepage = f"cp{match.group(1).decode()}" if match else "cp1252"
    try:
        codecs.lookup(codepage)
    except LookupError:
        codepage = "cp1252"

    out: list[str] = []
    pending = bytearray()  # \'hh bytes, decoded together so multibyte codepages read right

    def flush() -> None:
        if pending:
            out.append(pending.decode(codepage, errors="replace"))
            pending.clear()

    stack = [_Group(html_tag=False, skip=False, uc=1)]
    suppressed = False  # inside \htmlrtf ... \htmlrtf0
    star = False  # the last token was \*, so the next control word names a destination
    skip_chars = 0  # characters still to skip after a \uN

    for token in _TOKEN.finditer(rtf):
        word, param, hex_byte, symbol, brace, text = token.groups()
        group = stack[-1]
        if brace == b"{":
            flush()
            stack.append(_Group(html_tag=group.html_tag, skip=group.skip, uc=group.uc))
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
        if word is not None:
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
                out.append("\ufffd" if 0xD800 <= code_point <= 0xDFFF else chr(code_point))
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
            out.append(text.decode(codepage, errors="replace"))
    flush()
    return "".join(out)
