"""Reduce markdown links and URLs to the text a reader sees.

Parsers keep links in stored block text because citations, rendering and
navigation need them. What gets embedded should not carry them: a link target
is opaque to a dense model and, in a sparse index, repeats the anchor's words
(``[Acme](https://x/wiki/Acme)``) so every linked name counts twice.

Code spans and fenced code keep their text untouched: a URL there is content.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]{0,3}\1[ \t]*$|\Z)", re.MULTILINE | re.DOTALL)
_CODE_SPAN_RE = re.compile(r"(?<!\\)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[([^\]\n]+)\]:[ \t]*\S+(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^)\n]*\)))?[ \t]*$\n?",
    re.MULTILINE,
)
_AUTOLINK_RE = re.compile(r"<((?:[a-zA-Z][a-zA-Z0-9+.-]{1,31}):[^\s<>]+)>")
_HTML_ANCHOR_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?<![\w/@.])(?:https?|ftp)://[^\s<>\"'`\[\]]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?'\""
_INLINE_SPACES_RE = re.compile(r"[ \t]{2,}")
_PLACEHOLDER = "\x00{}\x00"
_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")


def anchor_text_only(text: str) -> str:
    """``text`` with every link reduced to its anchor text.

    ``[label](url)`` and ``[label][ref]`` become ``label``; ``![alt](src)``
    becomes ``alt``; reference definitions are dropped; an autolink or bare
    URL becomes its host, since it has no anchor and the host is the part of
    it a person would search for. Unchanged when there is nothing to strip.
    """
    if not text or not _may_contain_link(text):
        return text

    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return _PLACEHOLDER.format(len(protected) - 1)

    working = _FENCE_RE.sub(protect, text)
    working = _CODE_SPAN_RE.sub(protect, working)

    labels = {m.group(1).strip().lower() for m in _REFERENCE_DEFINITION_RE.finditer(working)}
    working = _REFERENCE_DEFINITION_RE.sub("", working)
    working = _strip_bracket_links(working, labels)
    working = _AUTOLINK_RE.sub(lambda m: _url_label(m.group(1)), working)
    working = _HTML_ANCHOR_TAG_RE.sub("", working)
    working = _BARE_URL_RE.sub(lambda m: _bare_url_label(m.group(0)), working)
    working = _INLINE_SPACES_RE.sub(" ", working)

    def restore(match: re.Match[str]) -> str:
        return protected[int(match.group(1))]

    # A placeholder can land inside another protected span only through a
    # fence containing a backtick run, so one extra pass restores nesting.
    working = _PLACEHOLDER_RE.sub(restore, working)
    working = _PLACEHOLDER_RE.sub(restore, working)
    return working.strip() if working != text else text


def _may_contain_link(text: str) -> bool:
    return "](" in text or "][" in text or "]:" in text or "://" in text or "<" in text or (
        "[" in text and "]" in text
    )


def _strip_bracket_links(text: str, reference_labels: set[str]) -> str:
    out: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char == "\\" and i + 1 < length:
            out.append(text[i : i + 2])
            i += 2
            continue
        is_image = char == "!" and i + 1 < length and text[i + 1] == "["
        if char != "[" and not is_image:
            out.append(char)
            i += 1
            continue
        open_at = i + 1 if is_image else i
        close_at = _matching(text, open_at, "[", "]")
        if close_at is None:
            out.append(char)
            i += 1
            continue
        label = text[open_at + 1 : close_at]
        after = close_at + 1
        if after < length and text[after] == "(":
            end = _link_destination_end(text, after)
            if end is not None:
                out.append(_strip_bracket_links(label, reference_labels).strip())
                i = end + 1
                continue
        # A block rarely holds the definitions its references point at, so an
        # undefined `[text][ref]` still counts, unless it trails a word: that
        # is subscripting (`grid[0][1]`), not a link.
        follows_word = open_at > 0 and (text[open_at - 1].isalnum() or text[open_at - 1] == "_")
        if after < length and text[after] == "[" and not follows_word:
            ref_end = _matching(text, after, "[", "]")
            if ref_end is not None:
                ref = text[after + 1 : ref_end].strip().lower() or label.strip().lower()
                if ref in reference_labels or not reference_labels:
                    out.append(_strip_bracket_links(label, reference_labels).strip())
                    i = ref_end + 1
                    continue
        if label.strip().lower() in reference_labels:
            out.append(_strip_bracket_links(label, reference_labels).strip())
            i = after
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _matching(text: str, start: int, opener: str, closer: str) -> int | None:
    """Index of the ``closer`` balancing ``text[start]``, skipping escapes."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "\n" and i + 1 < len(text) and text[i + 1] == "\n":
            return None
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _link_destination_end(text: str, start: int) -> int | None:
    """Index of the ``)`` closing an inline link destination opened at ``start``.

    Handles ``<...>`` destinations, balanced parentheses inside a URL, and a
    quoted title. Returns ``None`` when this is not a link destination.
    """
    i = start + 1
    length = len(text)
    while i < length and text[i] in " \t":
        i += 1
    if i < length and text[i] == "<":
        close = text.find(">", i)
        if close == -1 or "\n" in text[i:close]:
            return None
        i = close + 1
    else:
        depth = 0
        while i < length:
            char = text[i]
            if char == "\\":
                i += 2
                continue
            if char in " \t\n":
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    return i
                depth -= 1
            i += 1
    while i < length and text[i] in " \t\n":
        i += 1
    if i < length and text[i] in "\"'(":
        closing = ")" if text[i] == "(" else text[i]
        title_end = text.find(closing, i + 1)
        if title_end == -1:
            return None
        i = title_end + 1
        while i < length and text[i] in " \t\n":
            i += 1
    if i < length and text[i] == ")":
        return i
    return None


def _url_label(url: str) -> str:
    if url.lower().startswith("mailto:"):
        return url[len("mailto:"):]
    return _host(url) or ""


def _bare_url_label(url: str) -> str:
    trailing = ""
    while url and url[-1] in _TRAILING_URL_PUNCTUATION:
        trailing = url[-1] + trailing
        url = url[:-1]
    while url.endswith(")") and url.count(")") > url.count("("):
        trailing = ")" + trailing
        url = url[:-1]
    return (_host(url) or "") + trailing


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host
