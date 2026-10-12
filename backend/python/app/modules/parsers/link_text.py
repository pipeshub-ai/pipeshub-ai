"""Reduce markdown links and URLs to the text a reader sees.

Parsers keep links in stored block text because citations, rendering and
navigation need them. What gets embedded should not carry them: a link target
is opaque to a dense model and, in a sparse index, repeats the anchor's words
(``[Acme](https://x/wiki/Acme)``) so every linked name counts twice.

Code spans and fenced code keep their text untouched: a URL there is content.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

# A closing fence may be longer than the opener (CommonMark 4.5).
_FENCE_RE = re.compile(
    r"^[ \t]{0,3}(?P<fence>(?P<char>[`~])(?P=char){2,})[^\n]*\n"
    r".*?(?:^[ \t]{0,3}(?P=fence)(?P=char)*[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CODE_SPAN_RE = re.compile(r"(?<!\\)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_REFERENCE_DEFINITION_RE = re.compile(
    r"^[ \t]{0,3}\[([^\]\n]+)\]:[ \t]*\n?[ \t]*(\S+)"
    r"(?:(?:[ \t]+|[ \t]*\n[ \t]*)(?:\"[^\"\n]*\"|'[^'\n]*'|\([^)\n]*\)))?[ \t]*$\n?",
    re.MULTILINE,
)
_AUTOLINK_RE = re.compile(r"<((?:[a-zA-Z][a-zA-Z0-9+.-]{1,31}):[^\s<>]+)>")
_HTML_ANCHOR_TAG_RE = re.compile(r"</?a\b[^>]*>", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?<![\w/@.])(?:https?|ftp)://[^\s<>\"'`\[\]]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?'\""
_INLINE_SPACES_RE = re.compile(r"[ \t]{2,}")
_PLACEHOLDER = "\x00{}\x00"
# Characters a bare CommonMark link destination cannot hold raw.
# Python's ``\s`` adds Unicode spaces (``&nbsp;``, em space), which split a
# destination for a reader and for most markdown renderers alike.
_UNSAFE_DESTINATION_CHAR_RE = re.compile(r"[\x00-\x20()<>\x7f]|\s")
_ESCAPED_CHAR_RE = re.compile(r"\\(.)")
_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")


def markdown_link_destination(url: str) -> str:
    """``url`` percent-encoded so it reads as one bare markdown link destination.

    HTML allows spaces and parentheses in an ``href``; markdownify copies them
    through, and ``[a](https://x/New York (state))`` is not a markdown link.
    Only those characters are encoded, so existing ``%XX`` escapes and
    non-ASCII text are untouched and the URL names the same resource.
    """
    return _UNSAFE_DESTINATION_CHAR_RE.sub(lambda m: quote(m.group(0), safe=""), url.strip())


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

    references = {
        m.group(1).strip().lower(): m.group(2).strip("<>")
        for m in _REFERENCE_DEFINITION_RE.finditer(working)
    }
    working = _REFERENCE_DEFINITION_RE.sub("", working)
    working = _strip_bracket_links(working, references)
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


def _strip_bracket_links(text: str, reference_labels: dict[str, str]) -> str:
    """Reduce bracket links to their text; ``reference_labels`` maps label → URL."""
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
            end = link_destination_end(text, after)
            if end is not None:
                destination = None if is_image else _inline_destination(text[after + 1 : end])
                out.append(_visible_label(label, reference_labels, destination))
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
                    destination = None if is_image else reference_labels.get(ref)
                    out.append(_visible_label(label, reference_labels, destination))
                    i = ref_end + 1
                    continue
        if label.strip().lower() in reference_labels:
            destination = None if is_image else reference_labels[label.strip().lower()]
            out.append(_visible_label(label, reference_labels, destination))
            i = after
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _visible_label(
    label: str, reference_labels: dict[str, str], destination: str | None
) -> str:
    """A link's text, or its host when the text is the link's own URL.

    The bare-URL pass stops at whitespace, so ``[https://x/New York](...)``
    would keep "York". Text that only starts with a URL ("https://x/guide
    for details") is prose and is left to that pass.
    """
    text = _strip_bracket_links(label, reference_labels).strip()
    if destination and _ESCAPED_CHAR_RE.sub(r"\1", text) in {destination, unquote(destination)}:
        return _url_label(text)
    return text


def _inline_destination(inside: str) -> str:
    """The URL of an inline link, from the text between its parentheses."""
    inside = inside.strip()
    if inside.startswith("<"):
        return inside[1 : inside.find(">")]
    return re.split(r"[ \t\n]", inside, maxsplit=1)[0]


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


def link_destination_end(text: str, start: int) -> int | None:
    """Index of the ``)`` closing an inline link whose ``(`` is at ``start``.

    Follows CommonMark: the destination is ``<...>`` or a run without spaces
    whose parentheses balance, optionally followed by whitespace and a title
    in ``"..."``, ``'...'`` or ``(...)``. Returns ``None`` otherwise, so prose
    such as ``[this](a quick note)`` is not taken for a link. HTML hrefs are
    made to fit by ``markdown_link_destination`` before they become markdown.
    """
    length = len(text)
    i = _skip_link_whitespace(text, start + 1)
    if i < length and text[i] == "<":
        close = i + 1
        while close < length and text[close] not in ">\n<":
            close += 2 if text[close] == "\\" else 1
        if close >= length or text[close] != ">":
            return None
        i = close + 1
    else:
        depth = 0
        while i < length:
            char = text[i]
            if char == "\\":
                i += 2
                continue
            # CommonMark ends a bare destination only on ASCII space or a
            # control character; other Unicode spaces are part of it.
            if char == " " or ord(char) < 0x20 or char == "\x7f":
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    return i
                depth -= 1
            i += 1
        if depth:
            return None
    title_start = _skip_link_whitespace(text, i)
    if title_start < length and text[title_start] == ")":
        return title_start
    if title_start == i or title_start >= length or text[title_start] not in "\"'(":
        return None
    closer = ")" if text[title_start] == "(" else text[title_start]
    j = title_start + 1
    while j < length and text[j] != closer:
        if text[j] == "(" and closer == ")":
            return None
        j += 2 if text[j] == "\\" else 1
    if j >= length:
        return None
    end = _skip_link_whitespace(text, j + 1)
    return end if end < length and text[end] == ")" else None


def _skip_link_whitespace(text: str, i: int) -> int:
    """Skip spaces, tabs and at most one line ending."""
    newline_seen = False
    while i < len(text) and text[i] in " \t\n":
        if text[i] == "\n":
            if newline_seen:
                break
            newline_seen = True
        i += 1
    return i


def _url_label(url: str) -> str:
    if url.lower().startswith("mailto:"):
        return url[len("mailto:"):]
    # A hostless URI (tel:, urn:) is its own visible text; dropping it can
    # leave nothing to embed.
    return _host(url) or url


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
