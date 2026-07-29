import re
from dataclasses import dataclass
from typing import Any

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import (
    generate_text_fragment_url,
    get_enhanced_metadata,
    is_base64_image,
    valid_group_labels,
)
from app.utils.logger import create_logger

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

# Matches [refN] — a bracketed bare ref without a link target — which LLMs
# commonly emit instead of proper [source](refN).  Must run BEFORE
# _BARE_CITATION_NORMALIZE_RE to prevent double-bracket malformation
# (e.g. [ref3] → [[source](ref3)] when _wrap_bare_refs acts on the inner token).
_BRACKET_REF_RE = re.compile(
    r'(\[[^\]]*?\]\((?:ref\d+|https?://[^)]+)\))'  # Grp 1: valid md link — skip
    r'|\[(ref\d+)\](?!\()'                          # Grp 2: [refN] not followed by (
)

# Single-pass pattern used to wrap bare refN tokens and bare https://refN.xyz
# URLs that the LLM emitted outside proper markdown link syntax.
# Alternative 1 (Grp 1) matches an already-valid markdown link so the cursor
# skips over it — preventing double-processing of refs inside `[text](refN)`.
# Alternative 2 (Grp 2) matches a bare tiny web-ref URL.
# Alternative 3 (Grp 3) matches a bare refN word token.
_BARE_CITATION_NORMALIZE_RE = re.compile(
    r'(\[[^\]]*?\]\((?:ref\d+|https?://[^)]+)\))'  # Grp 1: existing valid md link — skip
    r'|(https?://ref\d+\.xyz/?)'                    # Grp 2: bare tiny URL
    r'|\b(ref\d+)\b'                               # Grp 3: bare refN token
)


def extract_tiny_ref(target: str) -> str | None:
    """Return the inner refN from a tiny web-ref URL like https://ref1.xyz, else None."""
    if not target:
        return None
    m = _TINY_WEB_REF_PATTERN.match(target)
    return m.group(1) if m else None


def build_tiny_web_ref_url(ref: str) -> str:
    """Wrap a refN token into its LLM-facing tiny URL form (https://refN.xyz)."""
    return f"https://{ref}.xyz"


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

    * Bracketed bare ref         ``[ref3]``                 →  ``[source](ref3)``
    * Multiple refs in one link  ``[source](ref1, ref2)``  →  ``[source](ref1) [source](ref2)``
    * Bare refN token            ``ref5``                   →  ``[source](ref5)``
    * Bare tiny web-ref URL      ``https://ref5.xyz``       →  ``[source](https://ref5.xyz)``
    """
    text = _expand_multi_ref_links(text)
    text = _normalize_bracket_refs(text)
    text = _wrap_bare_refs(text)
    return text


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

_UNRESOLVED_REF_LINK_RE = re.compile(r'\[([^\]]*?)\]\(ref\d+\)')


def _renumber_citation_links(
    text: str,
    md_matches: list,
    url_to_citation_num: dict[str, int],
    ref_to_url: dict[str, str] | None = None,
) -> str:
    """
    Replace citation markdown links with sequential ``[N](url)`` badges.
    Resolves tiny refs to full URLs in the output so the frontend receives full URLs.
    Processes matches in reverse order to preserve string positions.

    All matched links — whether their text is a generic label ("source") or
    descriptive ("India - Wikipedia") — are replaced with ``[N](url)`` so the
    frontend can render them as numbered citation chips.  The original link text
    is discarded because the citation metadata already stores the title/content.
    """
    for match in reversed(md_matches):
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        new_num = url_to_citation_num.get(full_url)
        if new_num is not None:
            replacement = f"[{new_num}]({full_url})"
            text = text[:match.start()] + replacement + text[match.end():]
    text = _strip_unresolved_ref_citations(text)
    return text


def _strip_unresolved_ref_citations(text: str) -> str:
    """Remove ``[text](refN)`` links that survived renumbering.

    These are dead references — typically carried over from a prior
    conversation turn whose ``CitationRefMapper`` no longer exists.
    Leaving them renders as broken ``[source](ref209)`` in the UI.
    """
    return _UNRESOLVED_REF_LINK_RE.sub('', text)


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


def _append_record_page_citation(
    record: dict[str, Any],
    url: str,
    new_citations: list[dict[str, Any]],
    url_to_citation_num: dict[str, int],
    citation_num: int,
) -> int:
    """Build citation metadata for /record/{id} (header / landing page), not a specific block."""
    snippet = (record.get("semantic_metadata") or {}).get("summary") or record.get("record_name") or "Record"
    if not isinstance(snippet, str):
        snippet = _safe_stringify_content(value=snippet)
    snippet = snippet.strip() or (record.get("record_name") or "Record")
    display_content = snippet
    meta = get_enhanced_metadata(
        record,
        None,
        {"webUrl": url, "blockText": display_content, },
    )
    new_citations.append({
        "content": display_content,
        "chunkIndex": citation_num,
        "metadata": meta,
        "citationType": "vectordb|document",
    })
    url_to_citation_num[url] = citation_num
    return citation_num + 1


_TEXT_FRAGMENT_DIRECTIVE_PREFIX = "#:~:text="


def _page_of(url: str) -> str:
    """Strip a `#:~:text=` fragment so a bare page URL can be compared
    against fragment-keyed web records (see `generate_text_fragment_url`)."""
    return url.split(_TEXT_FRAGMENT_DIRECTIVE_PREFIX, 1)[0]


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
    new_citations.append({
        "content": _safe_stringify_content(value=web_content),
        "chunkIndex": citation_num,
        "metadata": {
            "recordId": _page_of(url),
            "mimeType": "text/html",
            "recordName": _page_of(url),
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
    children = [b for b in blocks if b.get("parent_block_index") == container_index]
    if not children:
        return "", ""
    children.sort(key=lambda b: b.get("index", 0))
    display_parts: list[str] = []
    text_parts: list[str] = []
    for child in children:
        child_type = child.get("type")
        child_data = child.get("data")
        if child_type == BlockType.IMAGE.value:
            display_parts.append("Image")
        elif isinstance(child_data, str) and child_data.strip():
            text = child_data.strip()
            display_parts.append(text)
            text_parts.append(text)
    if not display_parts:
        return "", ""
    if not text_parts:
        return "Image", ""
    return " ".join(display_parts), " ".join(text_parts)


def _enrich_metadata_from_fragment(
    metadata: dict[str, Any],
    record: dict[str, Any],
    display_content: str,
    fragment_text: str,
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
    metadata["webUrl"] = generate_text_fragment_url(base_url, fragment_text)


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

    return answer_text, []


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
        if block_type == GroupType.TABLE.value or block_type in valid_group_labels:
            _, child_results = doc.get("content", ("", []))
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
                enhanced_metadata, record, display, fragment_text
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

    return answer_text, []

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
        if block_type == GroupType.TABLE.value or block_type in valid_group_labels:
            _, child_results = doc.get("content", ("", []))
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
                                    enhanced_metadata, r, display, fragment_text
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
                                        enhanced_metadata, rec, display, fragment_text
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
