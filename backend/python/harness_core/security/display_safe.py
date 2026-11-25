"""Untrusted text as a decision surface shows it (SEC-B-04, ADR-TEXT-01).

A person approving a call decides on what the card says, and every string on it may be the injected model's: a
recipient, a channel, a URL, a tool's display name, a message. Three things make such text read as something it is
not, and each is undone here so the person sees what will happen:

- characters a reader cannot see or that reorder what follows (bidi controls, zero-width characters, the tag block)
  become a visible marker, ``⟨U+202E⟩``;
- a host that mixes scripts, is a whole-script lookalike, or holds a letter that passes for an ASCII one is shown in
  punycode (the web client shows the Unicode form beside it), and any other host in Unicode;
- in an identifier, a word that mixes scripts has its non-ASCII letters marked.

The tables are ``contracts/harness/vectors/display-safe.json``'s, which Node and the web client carry too; the tests
fail when they differ. Normal text, an accented name or a single-script word in any language, passes unchanged, and
the result is stable when applied again (the worker, Node and the client each apply it).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Literal

Mode = Literal["identifier", "prose"]

MARKER_OPEN: Final = "⟨U+"
MARKER_CLOSE: Final = "⟩"

INVISIBLE_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x2028, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF9, 0xFFFB), (0xE0000, 0xE007F), (0xE0100, 0xE01EF),
)
PROSE_CONTROLS: Final = frozenset({0x09, 0x0A, 0x0D})
PROSE_JOINERS: Final = frozenset({0x200C, 0x200D})
PROSE_VARIATION_SELECTORS: Final = frozenset({0xFE0E, 0xFE0F})

SCRIPTS: Final[tuple[tuple[str, tuple[tuple[int, int], ...]], ...]] = (
    ("Latin", ((0x41, 0x5A), (0x61, 0x7A), (0xAA, 0xAA), (0xBA, 0xBA), (0xC0, 0xD6), (0xD8, 0xF6), (0xF8, 0x2AF),
               (0x1D00, 0x1D7F), (0x1E00, 0x1EFF), (0x2C60, 0x2C7F), (0xA720, 0xA7FF), (0xAB30, 0xAB6F))),
    ("Fullwidth", ((0xFF21, 0xFF3A), (0xFF41, 0xFF5A))),
    ("Greek", ((0x370, 0x3FF), (0x1F00, 0x1FFF))),
    ("Cyrillic", ((0x400, 0x52F), (0x1C80, 0x1C8F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F))),
    ("Armenian", ((0x530, 0x58F),)),
    ("Hebrew", ((0x590, 0x5FF),)),
    ("Arabic", ((0x600, 0x6FF), (0x750, 0x77F), (0x8A0, 0x8FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFE))),
    ("Devanagari", ((0x900, 0x97F),)),
    ("Bengali", ((0x980, 0x9FF),)),
    ("Gurmukhi", ((0xA00, 0xA7F),)),
    ("Gujarati", ((0xA80, 0xAFF),)),
    ("Tamil", ((0xB80, 0xBFF),)),
    ("Telugu", ((0xC00, 0xC7F),)),
    ("Kannada", ((0xC80, 0xCFF),)),
    ("Malayalam", ((0xD00, 0xD7F),)),
    ("Thai", ((0xE00, 0xE7F),)),
    ("Lao", ((0xE80, 0xEFF),)),
    ("Georgian", ((0x10A0, 0x10FF),)),
    ("Cherokee", ((0x13A0, 0x13FF), (0xAB70, 0xABBF))),
    ("Hangul", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))),
    ("Hiragana", ((0x3040, 0x309F),)),
    ("Katakana", ((0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9F))),
    ("Bopomofo", ((0x3100, 0x312F), (0x31A0, 0x31BF))),
    ("Han", ((0x2E80, 0x2FDF), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B),
             (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F))),
)
ALLOWED_MIXES: Final[tuple[frozenset[str], ...]] = (
    frozenset({"Latin", "Han", "Hiragana", "Katakana"}),
    frozenset({"Latin", "Han", "Bopomofo"}),
    frozenset({"Latin", "Han", "Hangul"}),
)
LOOKALIKES: Final[dict[str, str]] = {
    "Cyrillic": "аысԁеԍһіюјӏорԛѕԝхуъьҽпгѵѡ",
    "Greek": "αικνορτυχω",
}
LATIN_PASSES_FOR_ASCII: Final = "ıɑɡɩɪʟʀȷᴀᴄᴅᴇᴊᴋᴍᴏᴘᴛᴜᴠᴡᴢ"
HIDDEN_MARKS: Final = frozenset({0x323, 0x324, 0x325, 0x326, 0x327, 0x328, 0x331, 0x332, 0x335, 0x336, 0x337, 0x338})

# ASCII punctuation and whitespace end a word; '⟨' and '⟩' do not, so a marked word stays one Latin word.
_WORD_SEPARATORS: Final = frozenset(" \t\r\n!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
# What ends an address or a host inside identifier text.
_TOKEN = re.compile(r"[^\s,;<>()\"']+")
_URL = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]{0,31}://)(?P<userinfo>[^\s/?#@]{0,256}@)?(?P<host>[^\s/?#:@\[\]]{1,253})")


def _in(cp: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= cp <= end for start, end in ranges)


def is_invisible(ch: str) -> bool:
    cp = ord(ch)
    return cp < 0x20 or 0x7F <= cp <= 0x9F or _in(cp, INVISIBLE_RANGES) or unicodedata.category(ch) == "Cf"


def marker(ch: str) -> str:
    return f"{MARKER_OPEN}{ord(ch):04X}{MARKER_CLOSE}"


def _visible_neighbour(ch: str | None) -> bool:
    return ch is not None and not ch.isascii() and not ch.isspace()


def _kept_in_prose(text: str, i: int) -> bool:
    cp = ord(text[i])
    before = text[i - 1] if i > 0 else None
    if cp in PROSE_CONTROLS:
        return True
    if cp in PROSE_JOINERS:
        after = text[i + 1] if i + 1 < len(text) else None
        return _visible_neighbour(before) and _visible_neighbour(after)
    if cp in PROSE_VARIATION_SELECTORS:
        return _visible_neighbour(before)
    return False


def mark_invisible(text: str, *, prose: bool = False) -> str:
    """``text`` with every character a reader cannot see as a marker (in prose, but a joiner inside a word)."""
    if text.isascii() and text.isprintable():
        return text
    out = [
        marker(ch) if is_invisible(ch) and not (prose and _kept_in_prose(text, i)) else ch
        for i, ch in enumerate(text)
    ]
    return "".join(out)


def script_of(ch: str) -> str | None:
    """The script a letter counts for; ``None`` for anything not a letter."""
    if not unicodedata.category(ch).startswith("L"):
        return None
    cp = ord(ch)
    for name, ranges in SCRIPTS:
        if _in(cp, ranges):
            return name
    return "Other"


def _scripts(text: str) -> set[str]:
    return {s for s in map(script_of, text) if s is not None}


def mixes_scripts(text: str) -> bool:
    found = _scripts(text)
    return len(found) > 1 and not any(found <= mix for mix in ALLOWED_MIXES)


def _punycode_decode(label: str) -> str | None:
    try:
        return label[4:].encode("ascii").decode("punycode")
    except (UnicodeError, ValueError, IndexError):
        return None


def _latin_passes_for_ascii(ch: str) -> bool:
    if ch in LATIN_PASSES_FOR_ASCII:
        return True
    return any(ord(c) in HIDDEN_MARKS for c in unicodedata.normalize("NFD", ch))


def _label_safe(label: str, tld_scripts: set[str]) -> bool:
    for ch in label:
        if ch.isascii():
            if not (ch.isalnum() or ch == "-"):
                return False
            continue
        if unicodedata.category(ch)[0] not in "LM" or is_invisible(ch):
            return False
        if script_of(ch) == "Latin" and _latin_passes_for_ascii(ch):
            return False
    found = _scripts(label)
    if mixes_scripts(label):
        return False
    if len(found) == 1:
        (script,) = found
        lookalikes = LOOKALIKES.get(script)
        letters = [ch for ch in label if script_of(ch) is not None]
        if lookalikes is not None and all(ch in lookalikes for ch in letters) and script not in tld_scripts:
            return False
    return True


def safe_host(host: str) -> str:
    """``host`` as a person should read it: as written when ASCII, in Unicode when nothing in it can pass for
    another site, else each non-ASCII label in punycode."""
    if not host:
        return host
    labels = host.split(".")
    decoded: list[str] = []
    for label in labels:
        if label[:4].lower() == "xn--":
            unicode_label = _punycode_decode(label)
            if unicode_label is None:
                return host
            decoded.append(unicode_label)
        else:
            decoded.append(label)
    if all(label.isascii() for label in decoded):
        return host
    folded = [unicodedata.normalize("NFKC", label).lower() for label in decoded]
    tld_scripts = _scripts(folded[-1])
    if all(_label_safe(label, tld_scripts) for label in folded):
        return ".".join(folded)
    return ".".join(label if label.isascii() else "xn--" + label.encode("punycode").decode("ascii") for label in folded)


def _host_like(text: str) -> bool:
    return "." in text.strip(".") and all(ch.isalnum() or ch in "-." or unicodedata.category(ch)[0] == "M" for ch in text)


def _safe_url_hosts(text: str) -> str:
    def host(match: re.Match[str]) -> str:
        return f"{match['scheme']}{match['userinfo'] or ''}{safe_host(match['host'])}"

    return _URL.sub(host, text)


def _safe_token(token: str) -> str:
    if "://" in token:
        return _safe_url_hosts(token)
    local, at, domain = token.rpartition("@")
    if at and _host_like(domain):
        return f"{local}@{safe_host(domain)}"
    return safe_host(token) if _host_like(token) else token


def _mark_mixed_words(text: str) -> str:
    out: list[str] = []
    word: list[str] = []

    def flush() -> None:
        if word and mixes_scripts("".join(word)):
            out.extend(marker(ch) if not ch.isascii() and script_of(ch) is not None else ch for ch in word)
        else:
            out.extend(word)
        word.clear()

    for ch in text:
        if ch in _WORD_SEPARATORS:
            flush()
            out.append(ch)
        else:
            word.append(ch)
    flush()
    return "".join(out)


def display_safe(text: str, mode: Mode = "identifier") -> str:
    """``text`` as a decision surface shows it (see the module's docstring)."""
    if not text or (text.isascii() and text.isprintable() and "xn--" not in text.lower()):
        return text
    if mode == "prose":
        return _safe_url_hosts(mark_invisible(text, prose=True))
    marked = mark_invisible(text)
    hosted = _TOKEN.sub(lambda m: _safe_token(m.group(0)), marked)
    return _mark_mixed_words(hosted)


def clip_display(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters with an ellipsis, never inside a marker: a cut one would read as text."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    opened = head.rfind(MARKER_OPEN[0])
    if opened != -1 and head.find(MARKER_CLOSE, opened) == -1:
        head = head[:opened]
    return head + "…"


# Unicode's White_Space, spelled out: ``str.split`` and ``\s`` each differ from JavaScript's at the edges.
_SPACE_RUN: Final = re.compile(r"[\t\n\x0b\x0c\r \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")


def _collapse_space(text: str) -> str:
    return _SPACE_RUN.sub(" ", text).strip(" ")


# What a client splits a display name at ("Server · tool"): a tool's own title holding one could name a server.
NAME_SEPARATOR: Final = " · "


def tool_display_name(owner: str, title: str) -> str:
    """``Owner · title`` for a tool a third party names (an MCP server's ``title``, an OpenAPI ``summary``): both
    display-safe, and the title without the separator, so the owner a client reads is always the configured one."""
    shown = display_safe(_collapse_space(title).replace("·", "-"))
    return f"{display_safe(_collapse_space(owner))}{NAME_SEPARATOR}{shown}"


def display_safe_changed(text: str, mode: Mode = "identifier") -> bool:
    return display_safe(text, mode) != text


__all__ = [
    "ALLOWED_MIXES",
    "HIDDEN_MARKS",
    "INVISIBLE_RANGES",
    "LATIN_PASSES_FOR_ASCII",
    "LOOKALIKES",
    "MARKER_CLOSE",
    "MARKER_OPEN",
    "PROSE_CONTROLS",
    "PROSE_JOINERS",
    "PROSE_VARIATION_SELECTORS",
    "SCRIPTS",
    "NAME_SEPARATOR",
    "Mode",
    "clip_display",
    "display_safe",
    "display_safe_changed",
    "is_invisible",
    "mark_invisible",
    "mixes_scripts",
    "safe_host",
    "script_of",
    "tool_display_name",
]
