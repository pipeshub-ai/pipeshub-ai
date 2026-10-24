import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import (
    as_blob_record,
    generate_text_fragment_url,
    get_enhanced_metadata,
    group_child_results,
    is_base64_image,
    valid_group_labels,
)
from app.utils.logger import create_logger
from app.utils.text_fragments import SourceFormat, split_fragment_directive
from app.web.url import display_domain

# Initialize logger
logger = create_logger(__name__)

# Regex matching markdown links whose target is any of:
#   - internal tiny ref (ref1, ref2, ...)
#   - tiny web-ref URL (https://ref1.xyz)
#   - legacy full block web URL (/record/<id>/preview#blockIndex=N)
#   - any http(s):// URL used as a web citation target
# Kept permissive because web citations are arbitrary external URLs.
_MD_LINK_PATTERN = r'\[([^\]]*?)\]\((ref\d+|https?://[^)]+)\)'

# Tiny web-ref URL format exposed to the LLM when the real URL exceeds TINY_URL_THRESHOLD.
# The embedded refN resolves through the shared CitationRefMapper, same as internal-search refs.
_TINY_WEB_REF_PATTERN = re.compile(r'^https?://(ref\d+)\.xyz/?$')

# URLs with length <= this are shown to the LLM verbatim; longer URLs are aliased as https://refN.xyz.
TINY_URL_THRESHOLD = 0

# Matches a markdown link [text](target) immediately followed by a parenthesized URL or
# bare ref that duplicates the same citation.  LLMs emit this when they see both the full
# URL (in sub-agent analyses) and the tiny-ref alias (in web-result context blocks):
#   [source](https://example.com/page) (https://ref123.xyz)
#   [source](ref5) (https://example.com/page)
_TRAILING_PAREN_URL_RE = re.compile(
    r'(\[[^\]]+\]\((ref\d+|https?://[^)]+)\))'   # Grp 1: md link; Grp 2: target
    r'\s*'
    r'\((https?://[^\s)]+|ref\d+)\)'              # Grp 3: trailing URL/ref
)

# Matches two consecutive markdown links that may share the same URL target, e.g.:
#   [Bellandur Restaurants](https://ref6.xyz) [source](https://ref6.xyz)
#   [Title](ref3)[source](ref3)
_CONSECUTIVE_MD_LINKS_RE = re.compile(
    r'(\[[^\]]+\]\((ref\d+|https?://[^)]+)\))'   # Grp 1: first md link; Grp 2: first target
    r'(\s*)'                                       # Grp 3: optional whitespace between links
    r'(\[[^\]]+\]\((ref\d+|https?://[^)]+)\))'   # Grp 4: second md link; Grp 5: second target
)

# ── Malformed citation normalization ──────────────────────────────────────────
# Matches a markdown link whose target is a comma- or whitespace-separated list
# of two or more refs/URLs — a common LLM slip-up e.g. [source](ref1, ref2).
_MULTI_REF_IN_LINK_RE = re.compile(
    r'\[([^\]]*?)\]\(('
    r'(?:ref\d+|https?://[^\s,)]+)'
    r'(?:(?:\s*,\s*|\s+)(?:ref\d+|https?://[^\s,)]+))+'
    r')\)'
)

# Any markdown link, whatever its target. Matched first by the bare-ref passes
# so a ref inside an existing link's text or URL is never rewritten into a
# nested link.
_ANY_MD_LINK = r'\[[^\]]*?\]\([^)\n]*\)'

# Matches [refN] — a bracketed bare ref without a link target — which LLMs
# commonly emit instead of proper [source](refN).  Must run BEFORE
# _BARE_CITATION_NORMALIZE_RE to prevent double-bracket malformation
# (e.g. [ref3] → [[source](ref3)] when _wrap_bare_refs acts on the inner token).
_BRACKET_REF_RE = re.compile(
    rf'({_ANY_MD_LINK})'                            # Grp 1: existing md link — skip
    r'|\[(ref\d+)\](?!\()'                          # Grp 2: [refN] not followed by (
)

# Link target that looks like a citation ref: ``ref`` followed by hex digits and
# dashes, at least one of them a digit.  Covers the minted form ``ref12`` (see
# ``CitationRefMapper.get_or_create_ref`` in chat_helpers.py) and the
# unresolvable forms models invent from record ids, e.g.
# ``ref444d5fad-0bf9-4daa-8c29-cc133ca4a3a6``.  Ordinary words such as
# ``refund-policy`` or ``refs`` do not match.
_ANY_REF_TARGET = r'ref(?=[0-9A-Fa-f-]*\d)[0-9A-Fa-f-]+'

# Single-pass pattern used to wrap bare refN tokens and bare https://refN.xyz
# URLs that the LLM emitted outside proper markdown link syntax.
# Alternative 1 (Grp 1) matches any existing markdown link so the cursor skips
# over it — preventing double-processing of refs inside `[text](refN)`.
# Alternative 2 (Grp 2) matches a bare tiny web-ref URL.
# Alternative 3 (Grp 3) matches a bare refN word token.
_BARE_CITATION_NORMALIZE_RE = re.compile(
    rf'({_ANY_MD_LINK})'                            # Grp 1: existing md link — skip
    r'|(https?://ref\d+\.xyz/?)'                    # Grp 2: bare tiny URL
    r'|\b(ref\d+)\b'                               # Grp 3: bare refN token
)

# Includes dead ids like ref444d5fad-… so a wrapped one is unwrapped and then
# removed by _strip_unresolved_ref_citations instead of left half-stripped.
_REF_TOKEN = rf'(?:https?://ref\d+\.xyz/?|{_ANY_REF_TARGET})'

# A citation link written with extra wrapping: brackets/quotes around the
# ref, a link (or list of links) as the target, or brackets around the link,
# e.g. [source]([ref2]], [source](<ref2>), [source](ref1, [source](ref2)),
# [[source](ref2)]. Parsed by _parse_cited_link, not one regex, because the
# wrapping nests to any depth and a sentence paren after it must be kept.
_CITED_LINK_START_RE = re.compile(r'(\[*)\[[^\[\]\n]*\]\(')
_CITED_LINK_HEAD_RE = re.compile(r'\[([^\[\]\n]*)\]\(')
_TARGET_GAP_RE = re.compile(r'[ \t.,;:]*')
_TARGET_SEP_RE = re.compile(r'[ \t]*[,;]?[ \t]*')
# LLMs mismatch ( and [, so either closes the other; quotes and <> only close
# their own opener, so [notes](ref2's-page) is not taken for a citation.
_LINK_TARGET_CLOSERS = ')]'
_WRAPPER_CLOSERS = {'[': '])', '(': ')]', '<': '>', '`': '`', '"': '"', "'": "'"}
_MAX_CITED_LINK_DEPTH = 8
_REF_TOKEN_RE = re.compile(_REF_TOKEN)


def extract_tiny_ref(target: str) -> str | None:
    """Return the inner refN from a tiny web-ref URL like https://ref1.xyz, else None."""
    if not target:
        return None
    m = _TINY_WEB_REF_PATTERN.match(target)
    return m.group(1) if m else None


def build_tiny_web_ref_url(ref: str) -> str:
    """Wrap a refN token into its LLM-facing tiny URL form (https://refN.xyz)."""
    return f"https://{ref}.xyz"


# A citation ref as a tool writes it for the model: the tiny web URL (with or
# without its scheme), or a bare ``refN`` that is not part of a word, a path, an
# assignment or a longer id. Only a whole token matches: nothing host- or
# path-like may follow (``https://ref1.xyz.evil``, ``…/path``, ``ref1.xyz``), or
# the rewrite would splice a URL into the middle of something else. Sentence
# punctuation may follow. A ref running into "…" was cut by a preview cap
# ("ref12" kept as "ref1"), so it is matched as `cut` and never taken for the
# ref it now reads as.
_REQUEST_REF_RE = re.compile(
    r'(?:https?://|(?<![\w/.=-]))(?P<web>ref\d+)'
    r'(?:\.xyz(?:/(?![\w-]))?(?![\w/-]|\.[\w-])|(?P<webcut>(?:\.x?y?)?…))'
    r'|(?<![\w/.=-])(?P<bare>ref\d+)(?:(?P<cut>…)|(?![\w/-]|\.[\w-]))'
)

# What a ref becomes when no mapping says what it stood for. Neither a ref nor
# a URL, so the model cannot cite it and nothing resolves it.
EARLIER_TURN_REF = "earlier-turn source"


def rewrite_request_refs(text: str, ref_to_url: Mapping[str, str] | None) -> tuple[str, int, int]:
    """Make `text` safe to keep beyond the request whose refs it carries.

    ``refN`` ids are numbered per request, so a ref that outlives its request
    (a saved tool-result preview, a durable artifact) would resolve to whatever
    a later request numbers the same. Each ref `ref_to_url` knows becomes its
    URL, which is valid in any request; any other becomes ``EARLIER_TURN_REF``.
    Returns the text and the counts of refs expanded and made uncitable.
    """
    expanded = unresolved = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal expanded, unresolved
        if m.group("webcut") or m.group("cut"):
            unresolved += 1
            return f"{EARLIER_TURN_REF}…"
        url = (ref_to_url or {}).get(m.group("web") or m.group("bare"))
        if url:
            expanded += 1
            return url
        unresolved += 1
        return EARLIER_TURN_REF

    return _REQUEST_REF_RE.sub(_replace, text), expanded, unresolved


def _expand_multi_ref_links(text: str) -> str:
    """Split ``[label](ref1, ref2)`` style links into separate ``[label](ref1) [label](ref2)`` links.

    Handles comma-separated and whitespace-separated ref lists inside a single
    markdown link target — a common LLM formatting mistake.
    """
    def _replacer(m: re.Match) -> str:
        link_text = m.group(1)
        targets = [t.strip() for t in re.split(r'[\s,]+', m.group(2)) if t.strip()]
        if len(targets) <= 1:
            return m.group(0)
        return ' '.join(f'[{link_text}]({t})' for t in targets)

    return _MULTI_REF_IN_LINK_RE.sub(_replacer, text)


def _parse_target_close(text: str, i: int, closers: str) -> int | None:
    j = _TARGET_GAP_RE.match(text, i).end()
    if j < len(text) and text[j] in closers:
        return j + 1
    # A truncated answer can end before the wrapper is closed.
    if j == len(text) or text[j] == '\n':
        return i
    return None


def _parse_ref_list(text: str, i: int, closers: str, depth: int) -> tuple[list[str], int] | None:
    """Parse ``item (sep item)* closer``; return the refs and the end past the closer."""
    refs: list[str] = []
    while True:
        item = _parse_ref_item(text, i, depth)
        if item is None:
            return None
        refs.extend(item[0])
        i = item[1]
        end = _parse_target_close(text, i, closers)
        if end is not None:
            return refs, end
        sep_end = _TARGET_SEP_RE.match(text, i).end()
        if sep_end == i and not text.startswith('[', i):
            return None
        i = sep_end


def _parse_ref_item(text: str, i: int, depth: int) -> tuple[list[str], int] | None:
    while i < len(text) and text[i] in ' \t':
        i += 1
    token = _REF_TOKEN_RE.match(text, i)
    if token:
        return [token.group(0)], token.end()
    if depth >= _MAX_CITED_LINK_DEPTH or i >= len(text) or text[i] not in _WRAPPER_CLOSERS:
        return None
    if text[i] == '[':
        link = _parse_cited_link(text, i, depth + 1)
        if link is not None:
            return link[1], link[2]
    return _parse_ref_list(text, i + 1, _WRAPPER_CLOSERS[text[i]], depth + 1)


def _parse_cited_link(text: str, i: int, depth: int) -> tuple[str, list[str], int] | None:
    """Parse ``[label](target)`` whose target holds only (wrapped) refs."""
    head = _CITED_LINK_HEAD_RE.match(text, i)
    if head is None:
        return None
    parsed = _parse_ref_list(text, head.end(), _LINK_TARGET_CLOSERS, depth)
    if parsed is None:
        return None
    return head.group(1), parsed[0], parsed[1]


def _unwrap_ref_link_targets(text: str) -> str:
    """Rewrite citation links with wrapped targets into plain ``[label](refN)`` links.

    Without this the bare-ref passes would turn the ref inside the target into
    its own link, leaving ``[source]([2](url))`` in the final answer.
    """
    out: list[str] = []
    pos = 0
    for m in _CITED_LINK_START_RE.finditer(text):
        if m.start() < pos:
            continue
        parsed = _parse_cited_link(text, m.end(1), 0)
        if parsed is None:
            continue
        label, refs, end = parsed
        outer = len(m.group(1))
        while outer and end < len(text) and text[end] == ']':
            outer -= 1
            end += 1
        links = ' '.join(f'[{label}]({ref})' for ref in refs)
        out.append(text[pos:m.start()] + '[' * outer + links)
        pos = end
    out.append(text[pos:])
    return ''.join(out)


def _normalize_bracket_refs(text: str) -> str:
    """Convert ``[refN]`` (bracketed bare ref) to ``[source](refN)``.

    LLMs sometimes emit ``[ref3]`` instead of ``[source](ref3)``.  If this is
    not handled first, ``_wrap_bare_refs`` converts the inner token and produces
    ``[[source](ref3)]`` — a double-bracket form that breaks renumbering because
    the regex captures the leading ``[`` as part of the link text.
    """
    def _replacer(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)
        return f'[source]({m.group(2)})'

    return _BRACKET_REF_RE.sub(_replacer, text)


def _wrap_bare_refs(text: str) -> str:
    """Wrap bare ``refN`` tokens and bare ``https://refN.xyz`` URLs in markdown link syntax.

    Already-valid markdown links are left untouched (matched by the first
    alternative in ``_BARE_CITATION_NORMALIZE_RE`` so the cursor advances past them).
    """
    def _replacer(m: re.Match) -> str:
        if m.group(1) is not None:
            return m.group(1)   # existing valid markdown link — keep as-is
        if m.group(2):
            url = m.group(2)
            inner = extract_tiny_ref(url) or url
            return f'[source]({url})'
        if m.group(3):
            ref = m.group(3)
            return f'[source]({ref})'
        return m.group(0)

    return _BARE_CITATION_NORMALIZE_RE.sub(_replacer, text)


def normalize_malformed_citations(text: str) -> str:
    """Coerce malformed LLM citation formats into standard ``[label](refN)`` links.

    Runs before the main citation resolution pipeline so that all subsequent
    steps see only well-formed markdown links.

    Handled patterns (non-exhaustive):

    * Wrapped ref in a link      ``[source]([ref3])``       →  ``[source](ref3)``
    * Bracketed bare ref         ``[ref3]``                 →  ``[source](ref3)``
    * Multiple refs in one link  ``[source](ref1, ref2)``  →  ``[source](ref1) [source](ref2)``
    * Bare refN token            ``ref5``                   →  ``[source](ref5)``
    * Bare tiny web-ref URL      ``https://ref5.xyz``       →  ``[source](https://ref5.xyz)``
    """
    text = _normalize_context_label_refs(text)
    text = _unwrap_ref_link_targets(text)
    text = _expand_multi_ref_links(text)
    text = _normalize_bracket_refs(text)
    text = _wrap_bare_refs(text)
    return text


# A context block is shown to the model as ``[12|ref5] text``. A model that cites
# by copying that label leaves ``[12|`` and ``]`` around the link once the ref is
# wrapped, so the whole label (or a comma list of them) becomes the links.
_CONTEXT_LABEL_REFS_RE = re.compile(r'\[(\d+\|ref\d+(?:\s*,\s*\d+\|ref\d+)*)\]')


def _normalize_context_label_refs(text: str) -> str:
    return _CONTEXT_LABEL_REFS_RE.sub(
        lambda m: " ".join(f"[source]({label.split('|', 1)[1].strip()})" for label in m.group(1).split(",")),
        text,
    )


def display_url_for_llm(url: str, ref_mapper: "object | None") -> str:
    """Return the citation form the LLM should see for a web URL.

    Short URLs (<= TINY_URL_THRESHOLD) are emitted verbatim so the LLM can cite them
    directly. Longer URLs are aliased through the ref_mapper as https://refN.xyz so
    small models do not mangle them.
    """
    if not url:
        return url
    if len(url) <= TINY_URL_THRESHOLD or ref_mapper is None:
        return url
    ref = ref_mapper.get_or_create_ref(url)
    return build_tiny_web_ref_url(ref)

def _link_label(md_link: str) -> str:
    return md_link[1:md_link.index('](')]


def _clean_duplicate_citation_links(
    text: str,
    ref_to_url: dict[str, str] | None = None,
) -> str:
    """Remove duplicate citation links that refer to the same URL.

    Handles two LLM emission patterns:
    1. A markdown link followed by a parenthesized duplicate URL/ref:
           [source](https://example.com/page) (https://ref123.xyz)
    2. Two consecutive markdown links with different labels but the same URL:
           [Bellandur Restaurants](https://ref6.xyz) [source](https://ref6.xyz)
    In both cases the second (redundant) reference is removed.
    """
    def _trailing_paren_replacer(m: re.Match) -> str:
        md_link = m.group(1)
        link_target = m.group(2)
        trailing = m.group(3)

        if _TINY_WEB_REF_PATTERN.match(trailing):
            return md_link

        if re.match(r'^ref\d+$', link_target) or _TINY_WEB_REF_PATTERN.match(link_target):
            return md_link

        resolved_link = _resolve_ref(link_target, ref_to_url)
        resolved_trailing = _resolve_ref(trailing, ref_to_url)
        if resolved_link == resolved_trailing:
            return md_link

        return m.group(0)

    def _consecutive_links_replacer(m: re.Match) -> str:
        first_link = m.group(1)
        first_target = m.group(2)
        second_target = m.group(5)

        resolved_first = _resolve_ref(first_target, ref_to_url)
        resolved_second = _resolve_ref(second_target, ref_to_url)
        if resolved_first == resolved_second:
            second_link = m.group(4)
            # "[source](ref1) [Pricing review](ref1)": keep the link whose text names the source.
            # A dead target gets no badge, so there a URL is text worth keeping.
            first_label, second_label = _link_label(first_link), _link_label(second_link)
            if _REF_TOKEN_RE.fullmatch(resolved_first):
                if _is_real_url_text(second_label) and not _is_real_url_text(first_label):
                    return second_link
                disposable = _is_label_or_id
            else:
                disposable = _is_citation_label
            if disposable(first_label) and not disposable(second_label):
                return second_link
            return first_link

        return m.group(0)

    text = _TRAILING_PAREN_URL_RE.sub(_trailing_paren_replacer, text)
    text = _CONSECUTIVE_MD_LINKS_RE.sub(_consecutive_links_replacer, text)
    return text

@dataclass
class ChatDocCitation:
    content: str
    metadata: dict[str, Any]
    chunkindex: int



def fix_json_string(json_str) -> str:
    """Fix control characters in JSON string values without parsing."""
    result = ""
    in_string = False
    escaped = False
    ascii_start = 32
    ascii_end = 127
    extended_ascii_end = 159
    for c in json_str:
        if escaped:
            # Previous character was a backslash, this character is escaped
            result += c
            escaped = False
            continue

        if c == "\\":
            # This is a backslash, next character will be escaped
            result += c
            escaped = True
            continue

        if c == '"':
            # This is a quote, toggle whether we're in a string
            in_string = not in_string
            result += c
            continue

        if in_string:
            # We're inside a string, escape control characters properly
            if c == "\n":
                result += "\\n"
            elif c == "\r":
                result += "\\r"
            elif c == "\t":
                result += "\\t"
            elif ord(c) < ascii_start or (ord(c) >= ascii_end and ord(c) <= extended_ascii_end):
                # Other control characters as unicode escapes
                result += f"\\u{ord(c):04x}"
            else:
                result += c
        else:
            # Not in a string, keep as is
            result += c
    return result


def _resolve_ref(target: str, ref_to_url: dict[str, str] | None) -> str:
    """Resolve a citation target — if it's a tiny ref (refN) or tiny web-ref URL (https://refN.xyz),
    return the full URL from the mapping; otherwise return as-is."""
    if not ref_to_url:
        return target
    if target in ref_to_url:
        return ref_to_url[target]
    inner_ref = extract_tiny_ref(target)
    if inner_ref and inner_ref in ref_to_url:
        return ref_to_url[inner_ref]
    return target

# A ``[text](ref…)`` or ``[text](https://refN.xyz)`` link.  Resolvable refs were already
# rewritten to ``[N](url)`` by the time this runs, so whatever still matches is dead.  One
# pattern, so a later link sees every earlier one already stripped.
_UNRESOLVED_REF_LINK_RE = re.compile(r'\[([^\]]*?)\]\(' + _REF_TOKEN + r'\)')
_CITATION_LABEL_RE = re.compile(
    r'^\s*(?:(?:sources?|refs?|links?|citations?)\s*#?\s*\d*|\d+(?:\s*[,;]\s*\d+)*)?\s*$', re.IGNORECASE,
)
# Link text that only names the target: a ref token or a record id.  Meaningless to a reader,
# so it goes even when the link is dead and no badge replaces it.
_LINK_ID_TEXT_RE = re.compile(
    rf'^(?:{_REF_TOKEN}'
    r'|[0-9A-Fa-f]{8}(?:-?[0-9A-Fa-f]{4}){3}-?[0-9A-Fa-f]{12}'
    r'|[0-9A-Fa-f]{24})$'
)
# Link text that is a URL: redundant next to a badge that links there, but a dead link's only trace.
_LINK_URL_TEXT_RE = re.compile(r'^<?(?:https?://|www\.)\S*>?$')
_LINK_TEXT_WRAPPERS = ' \t\n`\'"[]()<>'
_CITATION_BADGE = r'\[\d+\]\([^)]*\)'
# How far back an already-written copy of the link text is looked for.
_LINK_TEXT_ECHO_WINDOW = 2000


def _is_label_or_id(link_text: str) -> bool:
    return bool(_CITATION_LABEL_RE.match(link_text) or _LINK_ID_TEXT_RE.match(link_text.strip(_LINK_TEXT_WRAPPERS)))


def _is_url_text(link_text: str) -> bool:
    return bool(_LINK_URL_TEXT_RE.match(link_text.strip(_LINK_TEXT_WRAPPERS)))


def _is_real_url_text(link_text: str) -> bool:
    """A URL that is not itself a ref token: "https://ref99.xyz" is a dead reference, not an address."""
    return _is_url_text(link_text) and not _is_label_or_id(link_text)


def _is_citation_label(link_text: str) -> bool:
    """True when link text carries no prose: a generic label ("source", "ref 2")
    or the target itself (a URL, ref or record id)."""
    return _is_label_or_id(link_text) or _is_url_text(link_text)


def _already_written(kept: str, preceding: str) -> bool:
    """True when ``preceding`` ends with ``kept``, as plain text or another link's
    text, optionally followed by badges."""
    echo = re.compile(rf'(?<!\w){re.escape(kept)}(?:\]\([^)]*\))?\s*(?:{_CITATION_BADGE}\s*)*$', re.IGNORECASE)
    return bool(echo.search(preceding[-(len(kept) + _LINK_TEXT_ECHO_WINDOW):]))


def kept_citation_link_text(link_text: str, preceding: str) -> str:
    """The prose a citation link keeps in the sentence once its link becomes a badge.

    Empty for a label, and for text the answer already wrote just before the
    link (``the Q3 review [Q3 review](ref1)``) or before the badges in front of
    it (``[Q3 review](ref1, ref2)`` expands to one link per ref).
    """
    if _is_citation_label(link_text):
        return ''
    kept = link_text.strip()
    return '' if _already_written(kept, preceding) else kept


def sub_in_order(pattern: re.Pattern[str], text: str, replace: Callable[[re.Match[str], str], str]) -> str:
    """``pattern.sub`` whose ``replace(match, written)`` also gets the output
    written so far, so it sees earlier replacements rather than the raw text."""
    out = ''
    pos = 0
    for match in pattern.finditer(text):
        out += text[pos:match.start()]
        out += replace(match, out)
        pos = match.end()
    return out + text[pos:]


def _badge_url(url: str) -> str:
    """``url`` with ``(`` and ``)`` percent-encoded, so ``[N](url)`` cannot end early in a
    reader that stops a link at the first ``)``. Browsers treat both forms as the same address."""
    return url.replace("(", "%28").replace(")", "%29")


def _renumber_citation_links(
    text: str,
    md_matches: list,
    url_to_citation_num: dict[str, int],
    ref_to_url: dict[str, str] | None = None,
) -> str:
    """
    Replace citation markdown links with sequential ``[N](url)`` badges.
    Resolves tiny refs to full URLs in the output so the frontend receives full URLs.

    A generic label ("source", "3") is replaced by the badge.  Descriptive text
    ("the Velora renewal") is part of the sentence, so it stays as plain text
    in front of the badge: ``the Velora renewal [1](url)``.
    """
    out = ''
    pos = 0
    for match in md_matches:
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        new_num = url_to_citation_num.get(full_url)
        # Looked up raw above; written into the badge with parentheses encoded.
        full_url = _badge_url(full_url)
        out += text[pos:match.start()]
        if new_num is None:
            out += match.group(0)
        else:
            # Dead links before this one are stripped only after the walk; strip them in
            # the window the echo check reads, so their kept text counts as written.
            written = _strip_unresolved_ref_citations(out[-(len(match.group(1)) + _LINK_TEXT_ECHO_WINDOW):])
            kept = kept_citation_link_text(match.group(1), written)
            out += f"{kept} [{new_num}]({full_url})" if kept else f"[{new_num}]({full_url})"
        pos = match.end()
    out += text[pos:]
    return _strip_unresolved_ref_citations(out)


def _strip_unresolved_ref_citations(text: str) -> str:
    """Remove ``[text](ref…)`` links that survived renumbering.

    These are dead references — typically carried over from a prior
    conversation turn whose ``CitationRefMapper`` no longer exists, or
    invented by the model from a record id (``[source](ref444d5fad-…)``),
    which no mapper ever mints.  Leaving them renders as broken
    ``[source](ref209)`` links in the UI.  The link goes; descriptive text
    ("India – Wikipedia") stays in the sentence, a label goes with it.
    """
    def _keep_text(m: re.Match[str], written: str) -> str:
        if _is_label_or_id(m.group(1)):
            return ''
        kept = m.group(1).strip()
        return '' if _already_written(kept, written) else kept

    return sub_in_order(_UNRESOLVED_REF_LINK_RE, text, _keep_text)


def _extract_block_index_from_url(url: str) -> int | None:
    """Extract blockIndex value from a block web URL like /record/abc/preview#blockIndex=5"""
    m = re.search(r'blockIndex=(\d+)', url)
    if m:
        return int(m.group(1))
    return None

def _extract_record_id_from_url(url: str) -> str | None:
    """Extract recordId from block preview or record landing URLs."""
    m = re.search(r'/record/([^/]+)/preview', url)
    if m:
        return m.group(1)
    m = re.search(r'/record/([^/?#]+)', url)
    if m:
        return m.group(1)
    return None


def _find_record_by_id(
    record_id: str,
    records: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    
    if virtual_record_id_to_result:
        for rec in virtual_record_id_to_result.values():
            if rec and rec.get("id") == record_id:
                return rec
    for r in records:
        if r.get("id") == record_id:
            return r
    return None


# `blockType` of a record-page citation, which cites no block: its text is the
# record's summary (`BlockType.RECORD_SUMMARY`), the text the keyword (grep)
# search matched in the record, or only the record's name. Only matched text
# is quoted from the record itself.
RECORD_PAGE_MATCHED_TEXT = "matched_text"
RECORD_PAGE_NAME_ONLY = "record_name"


def _record_page_text(record: dict[str, Any]) -> tuple[str, str]:
    """The text a record-page citation shows, and the ``blockType`` naming it."""
    name = record.get("record_name") or "Record"
    # A grep hit has no summary until its blob is read; the text it matched
    # says more than its name, which the citation already shows as its title.
    summary = (record.get("semantic_metadata") or {}).get("summary")
    matched = record.get("_match_snippet")
    if summary:
        snippet, kind = summary, BlockType.RECORD_SUMMARY.value
    elif matched:
        snippet, kind = matched, RECORD_PAGE_MATCHED_TEXT
    else:
        snippet, kind = name, RECORD_PAGE_NAME_ONLY
    if not isinstance(snippet, str):
        snippet = _safe_stringify_content(value=snippet)
    snippet = snippet.strip()
    if not snippet:
        return name, RECORD_PAGE_NAME_ONLY
    return snippet, kind


def _append_record_page_citation(
    record: dict[str, Any],
    url: str,
    new_citations: list[dict[str, Any]],
    url_to_citation_num: dict[str, int],
    citation_num: int,
) -> int:
    """Build citation metadata for /record/{id} (header / landing page), not a specific block."""
    record = as_blob_record(record)
    display_content, text_kind = _record_page_text(record)
    meta = get_enhanced_metadata(
        record,
        None,
        {"webUrl": url, "blockText": display_content, },
    )
    # Set after the metadata is built, so the citation's link is made exactly
    # as before: a caller can tell the record's own words from a summary.
    meta["blockType"] = text_kind
    new_citations.append({
        "content": display_content,
        "chunkIndex": citation_num,
        "metadata": meta,
        "citationType": "vectordb|document",
    })
    url_to_citation_num[url] = citation_num
    return citation_num + 1


def _page_of(url: str) -> str:
    """Strip the fragment directive so a bare page URL can be compared
    against fragment-keyed web records (see `generate_text_fragment_url`)."""
    return split_fragment_directive(url)[0]


def _build_web_record_page_index(
    web_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Maps page URL (fragment stripped) -> the first web record for that
    page. Built once per normalization call so the page-level fallback
    below doesn't rescan `web_records` for every citation target —
    `TerminalAnswerStreamer` re-runs the containing normalizer on every
    streamed token, so an O(web_records) rescan per URL would make citation
    resolution O(tokens x unique_urls x web_records)."""
    index: dict[str, dict[str, Any]] = {}
    for rec in web_records:
        page = _page_of(rec.get("url", ""))
        if page and page not in index:
            index[page] = rec
    return index


def _find_web_record_by_url(
    url: str,
    web_records: list[dict[str, Any]],
    page_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Find a web record for `url`. Exact match first (per-block
    precision), then a page-level fallback: models sometimes paraphrase a
    per-block `#:~:text=` citation down to the bare page URL, which would
    otherwise never match and silently drop the citation."""
    if not url:
        return None
    for rec in web_records:
        if rec.get("url") == url:
            return rec
    page = _page_of(url)
    if page_index is not None:
        return page_index.get(page)
    for rec in web_records:
        if _page_of(rec.get("url", "")) == page:
            return rec
    return None


def _try_append_web_citation(
    url: str,
    web_records: list[dict[str, Any]],
    page_index: dict[str, dict[str, Any]],
    new_citations: list[dict[str, Any]],
    url_to_citation_num: dict[str, int],
    citation_num: int,
) -> tuple[bool, int]:
    """Shared web-record branch for `_normalize_markdown_link_citations`
    and its `_for_agent` twin: web citations are keyed by URL, not by the
    `final_results`/`records` block indexes the rest of this module
    resolves against, so they need their own lookup before falling through
    to record/vectordb resolution.

    Returns `(handled, next_citation_num)`. `handled=True` means the
    caller should move on to the next URL regardless of whether a citation
    was appended — a web record with no content is dropped, not passed
    through to record-based matching (a URL that happens to also look like
    a record URL should not be double-resolved).
    """
    if not web_records:
        return False, citation_num
    web_rec = _find_web_record_by_url(url, web_records, page_index)
    if not web_rec:
        return False, citation_num
    web_content = web_rec.get("content", "")
    if not web_content:
        return True, citation_num
    page = _page_of(url)
    new_citations.append({
        "content": _safe_stringify_content(value=web_content),
        "chunkIndex": citation_num,
        "metadata": {
            "recordId": page,
            "mimeType": "text/html",
            # The page's title, else its site: the URL itself is what the UI showed before.
            "recordName": web_rec.get("title") or display_domain(page) or page,
            "webUrl": url,
            "origin": "WEB_SEARCH",
            "orgId": web_rec.get("org_id", ""),
            "connector": "WEB",
            "recordType": "WEBPAGE",
        },
        "citationType": "web|url",
    })
    url_to_citation_num[url] = citation_num
    return True, citation_num + 1


def _safe_stringify_content(value: Any) -> str:
    """Convert citation content to string without raising."""
    try:
        return str(value)
    except Exception as exc:
        logger.warning("Failed to cast citation content to string: %s", exc)
        return ""


def _resolve_fragment_content(
    blocks: list[dict[str, Any]], container_index: int
) -> tuple[str, str]:
    """Assemble citation content from child fragment blocks of a container.

    When a block (table row, list item, etc.) contains inline images, the parser
    splits it into an empty container plus TEXT/IMAGE children linked via
    parent_block_index.

    Returns:
        (display_content, fragment_text) — display may include "Image" placeholders;
        fragment_text is text-only for #:~:text= URL highlighting on the source page.
    """
    display_parts: list[str] = []
    text_parts: list[str] = []
    for child in _fragment_children(blocks, container_index):
        if child.get("type") == BlockType.IMAGE.value:
            display_parts.append("Image")
        elif _contributes_fragment_text(child):
            text = child["data"].strip()
            display_parts.append(text)
            text_parts.append(text)
    if not display_parts:
        return "", ""
    if not text_parts:
        return "Image", ""
    return " ".join(display_parts), " ".join(text_parts)


def _fragment_children_format(
    blocks: list[dict[str, Any]], container_index: int
) -> SourceFormat | None:
    """Format of the first child whose text `_resolve_fragment_content` joined."""
    for child in _fragment_children(blocks, container_index):
        if _contributes_fragment_text(child):
            return SourceFormat.from_data_format(child.get("format"))
    return None


def _fragment_children(blocks: list[dict[str, Any]], container_index: int) -> list[dict[str, Any]]:
    children = [b for b in blocks if b.get("parent_block_index") == container_index]
    children.sort(key=lambda b: b.get("index", 0))
    return children


def _contributes_fragment_text(child: dict[str, Any]) -> bool:
    data = child.get("data")
    return child.get("type") != BlockType.IMAGE.value and isinstance(data, str) and bool(data.strip())


def _enrich_metadata_from_fragment(
    metadata: dict[str, Any],
    record: dict[str, Any],
    display_content: str,
    fragment_text: str,
    source_format: SourceFormat | None = None,
) -> None:
    """Fill blockText/webUrl when primary block data was empty (image-split container).

    No-op when blockText is already set, so the normal (non-fragment) path is unchanged.
    """
    if metadata.get("blockText"):
        return
    metadata["blockText"] = display_content
    if not fragment_text or metadata.get("hideWeburl"):
        return
    origin = metadata.get("origin") or record.get("origin") or ""
    record_type = metadata.get("recordType") or record.get("record_type") or ""
    if origin == "UPLOAD" or record_type == "MAIL":
        return
    base_url = record.get("weburl") or ""
    if not base_url:
        return
    metadata["webUrl"] = generate_text_fragment_url(base_url, fragment_text, source_format)


def detect_hallucinated_citation_urls(
    answer_text: str,
    records: list[dict[str, Any]] | None = None,
    flattened_final_results: list[dict[str, Any]] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    ref_to_url: dict[str, str] | None = None,
) -> list[str]:
    """
    Detect citation targets in answer_text that don't match any known block.

    Handles both tiny refs (ref1, ref2) and legacy full URLs.

    Returns:
            - hallucinated: targets found in answer that could not be resolved
    """
    if records is None:
        records = []
    if flattened_final_results is None:
        flattened_final_results = []

    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))
    if not md_matches:
        return []

    unique_targets = []
    seen = set()
    for match in md_matches:
        target = match.group(2).strip()
        if target not in seen:
            unique_targets.append(target)
            seen.add(target)

    hallucinated = []
    for target in unique_targets:
        # Tiny ref: valid iff it exists in the ref_to_url mapping
        if re.match(r'^ref\d+$', target):
            if ref_to_url and target in ref_to_url:
                continue
            hallucinated.append(target)
        else:
            continue

    return hallucinated


def normalize_citations_and_chunks(
    answer_text: str,
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]] = None,
    ref_to_url: dict[str, str] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports tiny refs (ref1, ref2), legacy full URL citations, and web citations (#wcite=).
    """

    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}

    answer_text = normalize_malformed_citations(answer_text)
    answer_text = _clean_duplicate_citation_links(answer_text, ref_to_url)
    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations(
            answer_text, md_matches, final_results, records,
            ref_to_url=ref_to_url,
            virtual_record_id_to_result=virtual_record_id_to_result,
            web_records=web_records,
        )

    return _strip_unresolved_ref_citations(answer_text), []


def _normalize_markdown_link_citations(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]],
    ref_to_url: dict[str, str] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations [text](target) where target is a tiny ref or full URL.
    Maps each citation to the corresponding block in final_results, records, or web_records
    and renumbers sequentially.
    """
    if web_records is None:
        web_records = []

    url_to_doc_index = {}
    flattened_final_results = []

    for doc in final_results:
        block_type = doc.get("block_type")
        # "code" labels both BlockType.CODE (leaf) and GroupType.CODE (group).
        # Only groups have content shaped as a (summary, children) tuple;
        # leaf blocks carry a plain string that must not be unpacked as a tuple.
        child_results = group_child_results(doc)
        if (
            block_type == GroupType.TABLE.value or block_type in valid_group_labels
        ) and child_results is not None:
            if child_results:
                for child in child_results:
                    child_url = child.get("block_web_url")
                    if child_url:
                        flattened_final_results.append(child)
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
        else:
            doc_url = doc.get("block_web_url")
            if doc_url:
                flattened_final_results.append(doc)
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    # Collect unique citation targets, resolving refs to full URLs
    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        if full_url not in seen_urls:
            unique_urls.append(full_url)
            seen_urls.add(full_url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1
    web_page_index = _build_web_record_page_index(web_records)

    def _append_citation_from_record(
        record: dict[str, Any],
        record_id: str,
        block_index: int,
        empty_data_log_prefix: str,
        url: str,
    ) -> bool:
        nonlocal new_citation_num

        block_container = record.get("block_containers", {}) or {}
        blocks = block_container.get("blocks", []) or []
        if not (0 <= block_index < len(blocks)):
            return False

        block = blocks[block_index]
        enhanced_metadata = get_enhanced_metadata(record, block, {})
        block_type = block.get("type")
        data = block.get("data")
        
        if data is None:
            logger.warning(
                "🔎 [KB-CITE] normalize(chat): %s | record_id=%s block_index=%s block_type=%s",
                empty_data_log_prefix, record_id, block_index, block_type,
            )
            return False

        if block_type == BlockType.TABLE_ROW.value:
            data = data.get("row_natural_language_text", "")
        elif block_type == BlockType.IMAGE.value:
            data = data.get("uri", "")

        if not data:
            display, fragment_text = _resolve_fragment_content(blocks, block_index)
            if not display:
                logger.warning(
                    "🔎 [KB-CITE] normalize(chat): %s | record_id=%s block_index=%s block_type=%s",
                    empty_data_log_prefix, record_id, block_index, block_type,
                )
                return False
            _enrich_metadata_from_fragment(
                enhanced_metadata, record, display, fragment_text, _fragment_children_format(blocks, block_index)
            )
            data = display
        citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
        if not citation_content:
            return False

        new_citations.append({
            "content": citation_content,
            "chunkIndex": new_citation_num,
            "metadata": enhanced_metadata,
            "citationType": "vectordb|document",
        })
        url_to_citation_num[url] = new_citation_num
        new_citation_num += 1
        return True

    for url in unique_urls:
        handled, new_citation_num = _try_append_web_citation(
            url, web_records, web_page_index, new_citations, url_to_citation_num, new_citation_num,
        )
        if handled:
            continue

        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
            content = doc.get("content", "")
            if isinstance(content, tuple):
                content = content[0]
            elif not isinstance(content, str):
                content = _safe_stringify_content(value=content)

            if not content:
                continue

            new_citations.append({
                "content": "Image" if is_base64_image(content) else content,
                "chunkIndex": new_citation_num,
                "metadata": doc.get("metadata", {}),
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1
        else:
            # Try matching by record_id + block_index extracted from URL,
            # or record landing URL /record/{id} (record-level / header citation).
            record_id = _extract_record_id_from_url(url)
            block_index = _extract_block_index_from_url(url)
            if record_id is not None and block_index is None:
                rec = _find_record_by_id(record_id, records, virtual_record_id_to_result)
                if rec and url not in url_to_citation_num:
                    new_citation_num = _append_record_page_citation(
                        rec, url, new_citations, url_to_citation_num, new_citation_num
                    )
                elif not rec:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(chat): DROPPED record-page citation | url=%s record_id=%s records_len=%d vrid_map_len=%d",
                        url, record_id,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )
            elif record_id is not None and block_index is not None:
                _matched = False
                for r in records:
                    if r.get("id") == record_id:
                        _matched = _append_citation_from_record(
                            record=r,
                            record_id=record_id,
                            block_index=block_index,
                            empty_data_log_prefix="records fallback empty data",
                            url=url,
                        )
                        break

                if not _matched and virtual_record_id_to_result:
                    for rec in virtual_record_id_to_result.values():
                        if rec and rec.get("id") == record_id:
                            _matched = _append_citation_from_record(
                                record=rec,
                                record_id=record_id,
                                block_index=block_index,
                                empty_data_log_prefix="vrid_map fallback empty data",
                                url=url,
                            )
                            break

                if not _matched:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(chat): DROPPED citation | url=%s record_id=%s block_index=%s records_len=%d vrid_map_len=%d",
                        url, record_id, block_index,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )


    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num, ref_to_url=ref_to_url)

    return answer_text, new_citations


def normalize_citations_and_chunks_for_agent(
    answer_text: str,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
    ref_to_url: dict[str, str] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports both tiny refs (ref1, ref2) and legacy full URL citations.
    """
    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}

    answer_text = normalize_malformed_citations(answer_text)
    answer_text = _clean_duplicate_citation_links(answer_text, ref_to_url)
    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations_for_agent(
            answer_text, md_matches, final_results, virtual_record_id_to_result, records,
            ref_to_url=ref_to_url, web_records=web_records,
        )

    return _strip_unresolved_ref_citations(answer_text), []

def _normalize_markdown_link_citations_for_agent(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    ref_to_url: dict[str, str] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations for agent workflow.
    Maps each citation to the corresponding block and renumbers sequentially.
    Enhances metadata from virtual_record_id_to_result.
    """
    if web_records is None:
        web_records = []

    url_to_doc_index = {}
    flattened_final_results = []

    for doc in final_results:
        virtual_record_id = doc.get("virtual_record_id")
        block_type = doc.get("block_type")
        # "code" labels both BlockType.CODE (leaf) and GroupType.CODE (group).
        # Only groups have content shaped as a (summary, children) tuple;
        # leaf blocks carry a plain string that must not be unpacked as a tuple.
        child_results = group_child_results(doc)
        if (
            block_type == GroupType.TABLE.value or block_type in valid_group_labels
        ) and child_results is not None:
            if child_results:
                for child in child_results:
                    child_url = child.get("block_web_url")
                    if child_url:
                        flattened_final_results.append(child)
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
        else:
            doc_url = doc.get("block_web_url")
            if doc_url:
                flattened_final_results.append(doc)
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    # Collect unique citation targets, resolving refs to full URLs
    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        if full_url not in seen_urls:
            unique_urls.append(full_url)
            seen_urls.add(full_url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1
    web_page_index = _build_web_record_page_index(web_records)

    for url in unique_urls:
        handled, new_citation_num = _try_append_web_citation(
            url, web_records, web_page_index, new_citations, url_to_citation_num, new_citation_num,
        )
        if handled:
            continue

        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
            content = doc.get("content", "")
            metadata = doc.get("metadata", {}) or {}

            if virtual_record_id_to_result:
                virtual_record_id = doc.get("virtual_record_id") or metadata.get("virtualRecordId")
                if virtual_record_id and virtual_record_id in virtual_record_id_to_result:
                    record = virtual_record_id_to_result[virtual_record_id]
                    if not metadata.get("origin"):
                        metadata["origin"] = record.get("origin", "")
                    if not metadata.get("recordName"):
                        metadata["recordName"] = record.get("record_name", "")
                    if not metadata.get("recordId"):
                        metadata["recordId"] = record.get("id", "")
                    if not metadata.get("mimeType"):
                        metadata["mimeType"] = record.get("mime_type", "")

            # Ensure required fields
            metadata["origin"] = metadata.get("origin") or ""
            metadata["recordName"] = metadata.get("recordName") or ""
            metadata["recordId"] = metadata.get("recordId") or ""
            metadata["mimeType"] = metadata.get("mimeType") or ""
            metadata["orgId"] = metadata.get("orgId") or ""

            if isinstance(content, tuple):
                content = content[0]
            elif not isinstance(content, str):
                content = _safe_stringify_content(value=content)

            if not content:
                continue

            new_citations.append({
                "content": "Image" if is_base64_image(content) else content,
                "chunkIndex": new_citation_num,
                "metadata": metadata,
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1
        else:
            record_id = _extract_record_id_from_url(url)
            block_index = _extract_block_index_from_url(url)
            if record_id is not None and block_index is None:
                rec = _find_record_by_id(record_id, records, virtual_record_id_to_result)
                if rec and url not in url_to_citation_num:
                    new_citation_num = _append_record_page_citation(
                        rec, url, new_citations, url_to_citation_num, new_citation_num
                    )
                elif not rec:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(agent): DROPPED record-page citation | url=%s record_id=%s records_len=%d vrid_map_len=%d",
                        url, record_id,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )
            elif record_id is not None and block_index is not None:
                # Search in records from tool calls
                for r in records:
                    if r.get("id") == record_id:
                        block_container = r.get("block_containers", {}) or {}
                        blocks = block_container.get("blocks", []) or []
                        if 0 <= block_index < len(blocks):
                            block = blocks[block_index]
                            enhanced_metadata = get_enhanced_metadata(r, block, {})
                            enhanced_metadata["origin"] = enhanced_metadata.get("origin") or ""
                            enhanced_metadata["recordName"] = enhanced_metadata.get("recordName") or ""
                            enhanced_metadata["recordId"] = enhanced_metadata.get("recordId") or ""
                            enhanced_metadata["mimeType"] = enhanced_metadata.get("mimeType") or ""
                            enhanced_metadata["orgId"] = enhanced_metadata.get("orgId") or ""
                            bt = block.get("type")
                            data = block.get("data")
                            if bt == BlockType.TABLE_ROW.value:
                                data = data.get("row_natural_language_text", "")
                            elif bt == BlockType.IMAGE.value:
                                data = data.get("uri", "")
                            if not data:
                                display, fragment_text = _resolve_fragment_content(
                                    blocks, block_index
                                )
                                if not display:
                                    continue
                                _enrich_metadata_from_fragment(
                                    enhanced_metadata, r, display, fragment_text, _fragment_children_format(blocks, block_index)
                                )
                                data = display
                            citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
                            if not citation_content:
                                continue
                            new_citations.append({
                                "content": citation_content,
                                "chunkIndex": new_citation_num,
                                "metadata": enhanced_metadata,
                                "citationType": "vectordb|document",
                            })
                            url_to_citation_num[url] = new_citation_num
                            new_citation_num += 1
                        break

                # Also search in virtual_record_id_to_result
                if url not in url_to_citation_num:
                    for rec in virtual_record_id_to_result.values():
                        if rec and rec.get("id") == record_id:
                            block_container = rec.get("block_containers", {}) or {}
                            blocks = block_container.get("blocks", []) or []
                            if 0 <= block_index < len(blocks):
                                block = blocks[block_index]
                                enhanced_metadata = get_enhanced_metadata(rec, block, {})
                                enhanced_metadata["origin"] = enhanced_metadata.get("origin") or ""
                                enhanced_metadata["recordName"] = enhanced_metadata.get("recordName") or ""
                                enhanced_metadata["recordId"] = enhanced_metadata.get("recordId") or ""
                                enhanced_metadata["mimeType"] = enhanced_metadata.get("mimeType") or ""
                                enhanced_metadata["orgId"] = enhanced_metadata.get("orgId") or ""
                                bt = block.get("type")
                                data = block.get("data")
                                if data is None:
                                    logger.warning(
                                        " Data is None for block_index=%s",
                                        block_index,
                                    )
                                    continue
                                if bt == BlockType.TABLE_ROW.value:
                                    data = data.get("row_natural_language_text", "")
                                elif bt == BlockType.IMAGE.value:
                                    data = data.get("uri", "")
                                if not data:
                                    display, fragment_text = _resolve_fragment_content(
                                        blocks, block_index
                                    )
                                    if not display:
                                        continue
                                    _enrich_metadata_from_fragment(
                                        enhanced_metadata, rec, display, fragment_text, _fragment_children_format(blocks, block_index)
                                    )
                                    data = display
                                citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
                                if not citation_content:
                                    continue
                                new_citations.append({
                                    "content": citation_content,
                                    "chunkIndex": new_citation_num,
                                    "metadata": enhanced_metadata,
                                    "citationType": "vectordb|document",
                                })
                                url_to_citation_num[url] = new_citation_num
                                new_citation_num += 1
                            break
                if url not in url_to_citation_num:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(agent): DROPPED citation | url=%s record_id=%s block_index=%s records_len=%d vrid_map_len=%d",
                        url, record_id, block_index,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )
                continue




    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num, ref_to_url=ref_to_url)

    return answer_text, new_citations
