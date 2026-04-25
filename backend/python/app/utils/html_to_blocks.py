from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    content: str = ""


@dataclass
class ImageBlock:
    type: Literal["image"] = "image"
    url: str = ""
    alt: str = ""


Block = TextBlock | ImageBlock

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_TEXT_CHARS = 1000        # flush a paragraph only when it's at least this long
MAX_TEXT_CHARS = 20000     # split a chunk if it exceeds this length
SPLIT_TAGS = {             # HTML tags that should always start a new block
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "td", "th", "figcaption",
    "pre", "code",
}
SKIP_TAGS = {              # tags whose subtree we ignore completely
    "script", "style", "noscript", "nav", "footer",
    "header", "aside", "form", "svg",
    "meta", "head",
}
BLOCK_LEVEL_TAGS = {       # block-level tags that need separator but not a new block
    "div", "section", "article", "main", "details", "summary",
    "dl", "dt", "dd", "figure", "address",
    "ol", "ul", "table", "tbody", "thead", "tfoot", "tr",
}

BOILERPLATE_ROLES = frozenset({
    "navigation", "complementary", "banner", "contentinfo", "search",
})

_BOILERPLATE_CLS_RE = re.compile(
    r"sidebar|side-?bar|infobox|info-?box"
    r"|navbox|nav-?box|breadcrumb"
    r"|cookie|consent|gdpr"
    r"|popup|modal|overlay"
    r"|ad-slot|ad-container|ad-banner|advert"
    r"|social.?share|share.?button"
    r"|skip.?link|skip.?nav|mw-jump"
    r"|site.?notice|announcement.?banner"
    r"|dropdown.?menu"
    r"|table.?of.?contents|toc-container",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Collapse whitespace and strip."""
    return text.strip()


def _resolve_url(src: str, base_url: str) -> str:
    if not src:
        return ""
    if src.startswith("data:"):
        return ""          # skip inline base64 blobs
    return urljoin(base_url, src)


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    """Remove boilerplate elements by ARIA role, hidden state, and class/ID patterns."""
    for role in BOILERPLATE_ROLES:
        for el in list(soup.find_all(attrs={"role": role})):
            el.decompose()

    for el in list(soup.find_all(attrs={"hidden": True})):
        el.decompose()
    for el in list(soup.find_all(attrs={"aria-hidden": "true"})):
        el.decompose()

    for el in list(soup.find_all(True)):
        if el.decomposed:
            continue
        classes = " ".join(el.get("class", []))
        el_id = el.get("id", "")
        haystack = f"{classes} {el_id}"
        if haystack.strip() and _BOILERPLATE_CLS_RE.search(haystack):
            el.decompose()


def _split_long_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> list[str]:
    """Split a long text block at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    # Try to split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], []
    length = 0

    for sentence in sentences:
        if length + len(sentence) > max_chars and current:
            chunks.append(" ".join(current))
            current, length = [sentence], len(sentence)
        else:
            current.append(sentence)
            length += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]

# ---------------------------------------------------------------------------
# Core walker
# ---------------------------------------------------------------------------

def _walk(node: Tag, base_url: str, blocks: list[Block], buffer: list[str],is_multimodal_llm: bool = False) -> None:
    """
    Recursively walk the DOM tree, appending to `blocks`.
    `buffer` accumulates text fragments until a flush point is reached.
    """
    for child in node.children:
        # ── Plain text node ──────────────────────────────────────────────
        if isinstance(child, NavigableString):
            text = str(child)
            if text.strip():      # skip pure-whitespace text nodes
                buffer.append(text)
            continue

        tag_name = child.name.lower() if child.name else ""

        # ── Skip unwanted subtrees ───────────────────────────────────────
        if tag_name in SKIP_TAGS:
            continue

        # ── Image ────────────────────────────────────────────────────────
        if tag_name == "img" and is_multimodal_llm:
            # Flush pending text first
            _flush_buffer(buffer, blocks)

            # Prefer srcset's first candidate, fall back to src
            src = (child.get("src") or "").strip()
            srcset = child.get("srcset", "")
            if srcset:
                first_candidate = srcset.split(",")[0].split()[0]
                src = first_candidate or src

            url = _resolve_url(src, base_url)
            if url:
                alt = _clean(child.get("alt", ""))
                blocks.append(ImageBlock(url=url, alt=alt))
            continue

        # ── Line-break elements ───────────────────────────────────────────
        if tag_name in ("br", "hr"):
            buffer.append("\n")
            continue

        # ── Block-level text containers ──────────────────────────────────
        if tag_name in SPLIT_TAGS:
            _flush_buffer(buffer, blocks)
            for br in child.find_all("br"):
                br.replace_with("\n")
            inner = child.get_text("")
            if inner:
                for chunk in _split_long_text(inner):
                    blocks.append(TextBlock(content=chunk))
            # Still walk for nested images
            if is_multimodal_llm:
                _walk_images_only(child, base_url, blocks)
            continue

        # ── Block-level wrappers (div, section, etc.) ────────────────────
        if tag_name in BLOCK_LEVEL_TAGS:
            buffer.append("\n")
            _walk(child, base_url, blocks, buffer, is_multimodal_llm)
            buffer.append("\n")
            continue

        # ── Recurse into everything else ─────────────────────────────────
        _walk(child, base_url, blocks, buffer, is_multimodal_llm)


def _walk_images_only(node: Tag, base_url: str, blocks: list[Block]) -> None:
    """Collect images nested inside an already-processed block element."""
    for img in node.find_all("img"):
        src = (img.get("src") or "").strip()
        url = _resolve_url(src, base_url)
        if url:
            alt = _clean(img.get("alt", ""))
            blocks.append(ImageBlock(url=url, alt=alt))


def _flush_buffer(buffer: list[str], blocks: list[Block]) -> None:
    """Join buffered text fragments into one or more TextBlocks."""
    if not buffer:
        return
    combined = re.sub(r"\n{3,}", "\n\n", "".join(buffer)).strip()
    buffer.clear()
    if not combined:
        return

    for chunk in _split_long_text(combined):
        blocks.append(TextBlock(content=chunk))

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def html_to_blocks(
    html: str,
    base_url: str = "",
    use_trafilatura: bool = True,
    is_multimodal_llm: bool = False,
) -> list[Block]:
    """
    Parse an HTML page and return a list of TextBlock / ImageBlock objects
    that capture the main content of the page.

    Parameters
    ----------
    html        : Raw HTML string of the page.
    base_url    : Used to resolve relative image URLs (e.g. "https://example.com").
    use_trafilatura : If True, uses trafilatura to extract the main content
                  subtree before parsing, which greatly reduces noise
                  (ads, nav, sidebars). Falls back to full-page parse on failure.

    Returns
    -------
    List of Block objects (TextBlock | ImageBlock), in document order.
    """
    main_html = html

    # ── 1. Main-content extraction ───────────────────────────────────────
    if use_trafilatura:
        extracted = trafilatura.extract(
            html,
            include_images=True,
            output_format="xml",
            favor_precision=True,
        )
        if extracted:
            # trafilatura XML → re-parse with BS4 for structured walking
            main_html = extracted

    # ── 2. Parse ─────────────────────────────────────────────────────────
    soup = BeautifulSoup(main_html, "lxml")

    # Remove known noise tags from the soup directly
    for tag in soup(list(SKIP_TAGS)):
        tag.decompose()

    if not use_trafilatura:
        _strip_boilerplate(soup)

    # ── 3. Walk the tree ─────────────────────────────────────────────────
    blocks: list[Block] = []
    buffer: list[str] = []
    root = soup.find("body") or soup
    _walk(root, base_url, blocks, buffer, is_multimodal_llm)
    _flush_buffer(buffer, blocks)   # flush any remaining text

    # ── 4. Post-process: merge tiny adjacent text blocks ─────────────────
    blocks = _merge_tiny_blocks(blocks)

    return blocks


def _merge_tiny_blocks(blocks: list[Block]) -> list[Block]:
    """Merge consecutive TextBlocks that are individually too small."""
    merged: list[Block] = []
    for block in blocks:
        if (
            isinstance(block, TextBlock)
            and merged
            and isinstance(merged[-1], TextBlock)
            and len(merged[-1].content) + len(block.content) < MAX_TEXT_CHARS
            and len(merged[-1].content) < MIN_TEXT_CHARS
        ):
            merged[-1].content = _clean(merged[-1].content + "\n\n" + block.content)
        else:
            merged.append(block)
    return merged

