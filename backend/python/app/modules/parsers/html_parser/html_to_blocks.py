"""Convert HTML directly into BlocksContainer using Selectolax (Lexbor backend).

Block / group mapping (mirrors markdown_to_blocks.MarkdownToBlocksConverter):

    h1-h6            → Block(TEXT, HEADING, TXT|MARKDOWN)
                         (skipped when sr-only / aria-hidden)
                         When followed by a paragraph-like sibling (p, address,
                         dt, dd, figcaption, shallow div) the heading is merged
                         into the paragraph block as a markdown heading prefix
                         (e.g. "## Title\nParagraph text").
                         NOT merged when followed by table, list, quote, code,
                         or other group-producing elements.

    p / address /
    dt / dd /
    figcaption       → Block(TEXT, PARAGRAPH, TXT|MARKDOWN)
                         When images are present: empty container block plus
                         ordered TEXT/IMAGE children (parent_block_index).
                         TXT when no inline formatting tags; MARKDOWN otherwise

    pre              → if <code> children: walk children (each <code> → CODE group;
                         other block elements processed as at top level)
                       else: BlockGroup(CODE)
                         └─ Block(TEXT, CODE, CODE)  from full <pre> text
    code             → BlockGroup(CODE)
                         └─ Block(TEXT, CODE, CODE)  (language from class)

    blockquote       → BlockGroup(TEXT_SECTION / QUOTE)
                         └─ Block(TEXT, QUOTE, MARKDOWN)  (whole inner HTML)

    ul               → BlockGroup(LIST)
                         └─ Block(TEXT, LIST_ITEM, MARKDOWN)  per <li>
    ol               → BlockGroup(ORDERED_LIST)  (same; nested lists stay markdown)

    table            → BlockGroup(TABLE)  + TableMetadata (column_names, captions, …)
                         └─ Block(TABLE_ROW, JSON)  per collapsed body row
                            (colspan/rowspan merged into logical columns; nested <table>
                             in a cell → markdown pipe table appended to cell text)
                         Rows with images split into empty TABLE_ROW container plus
                         TEXT/IMAGE children (same as markdown_to_blocks).
                         <caption> text stored in table_metadata.captions

    details          → <summary> → Block(TEXT, HEADING); other children processed normally

    hr               → skipped (no block emitted)
    img              → Block(IMAGE)  (uri from caption_map alt → base64, or inline
                         data:image src; HTTP src alone does not emit a block)

    div / section /… → recurse into children, or emit Block(TEXT, PARAGRAPH) when
                       the node has text but no block-level descendants

    script / style / noscript / template / svg / meta / link / head → skipped
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urljoin
from uuid import uuid4

from selectolax.lexbor import LexborHTMLParser, LexborNode
from markdownify import markdownify

from app.modules.parsers.markdown.markdown_to_blocks import (
    _HTML_IMG_ALT_RE,
    _MD_IMAGE_RE,
    _Segment,
    _TableCell,
    _split_cell_into_segments,
    _split_raw_markdown_into_segments,
    _strip_inline_images_from_markdown,
)
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    CodeMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    ImageMetadata,
    TableMetadata,
)
from app.modules.parsers.text_splitting import split_long_text

# ---------------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------------

_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "template",
    "svg", "meta", "link", "head",
})

_HEADING_TAGS = frozenset({f"h{i}" for i in range(1, 7)})

_CONTAINER_TAGS = frozenset({
    "div", "section", "article", "main", "header", "footer",
    "nav", "aside", "figure", "details",
    "dl", "form", "fieldset", "body", "html", "span", "center",
})

_BLOCK_TAGS = _HEADING_TAGS | {
    "p", "pre", "blockquote", "ul", "ol", "table", "hr", "img",
    "li", "figcaption", "address", "dt", "dd",
}

_INLINE_FORMAT_TAGS = frozenset({
    "strong", "b", "em", "i", "u", "s", "strike", "del", "ins",
    "sub", "sup", "mark", "small", "abbr", "cite", "q", "code",
    "a", "span", "kbd", "var", "samp", "time", "ruby", "rt", "rp",
    "bdi", "bdo", "font",
})

_LANGUAGE_CLASS_RE = re.compile(r"language-([\w+#.-]+)", re.IGNORECASE)

_CELL_SEP = " | "
_LEVEL_SEP = "\n"

_MAX_DOM_PROBE_DEPTH = 64

_PARAGRAPH_LIKE_TAGS = frozenset({"p", "address", "dt", "dd", "figcaption"})

# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------

@dataclass
class _OpenGroup:
    """Tracks an in-progress ``BlockGroup`` while walking the DOM.

    Child block and group indices are collected here and written to
    ``BlockGroup.children`` only when the group is closed, because nested
    nodes may arrive before we know the full membership of the group.
    """

    index: int
    child_block_indices: list[int] = field(default_factory=list)
    child_group_indices: list[int] = field(default_factory=list)


@dataclass
class NormalizedCell:
    """One slot in an expanded HTML table grid.

    ``is_origin`` distinguishes the cell that owns content from colspan/rowspan
    placeholder slots so collapse logic can merge spans without duplicating text.
    """

    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    is_origin: bool = True


@dataclass
class NormalizedTable:
    """Collapsed table representation ready for block emission.

    Header and body rows are already merged across colspan/rowspan so the DOM
    walker can emit one ``TABLE_ROW`` block per logical row without re-parsing
    span attributes at emission time.
    """

    column_headers: list[str]
    body_rows: list[list[str]]
    num_cols: int
    num_body_rows: int
    has_header: bool

    def to_markdown(self) -> str:
        """Render this table as a GitHub-flavoured markdown pipe table.

        Convenience wrapper used when a normalized table must be embedded inside
        another cell or exported as markdown alongside block emission.
        """
        return normalized_table_to_markdown(self)


# ---------------------------------------------------------------------------
# DOM traversal utilities
# ---------------------------------------------------------------------------

def _direct_children(node: LexborNode) -> Iterator[LexborNode]:
    """Yield each direct child node of a DOM element in document order.

    Selectolax exposes children as a sibling linked list (``child`` → ``next``),
    so we walk that chain rather than assuming a Python list of children.
    """
    child = node.child
    while child is not None:
        yield child
        child = child.next


def _tag_name(node: LexborNode) -> str | None:
    """Return the lowercased element tag name, or None for text/comment nodes."""
    if not node.tag or node.tag.startswith("-text"):
        return None
    return node.tag.lower()


def _node_text(node: LexborNode) -> str:
    """Extract all descendant text from a node, joined with spaces and stripped."""
    return node.text(deep=True, separator=" ").strip()


def _is_hidden(node: LexborNode) -> bool:
    """Detect screen-reader-only or aria-hidden elements that should be skipped.

    Accessibility helper text (``sr-only``) and explicitly hidden nodes would
    pollute search results if emitted as visible content blocks.
    """
    attrs = node.attributes or {}
    class_attr = attrs.get("class") or ""
    if isinstance(class_attr, list):
        class_attr = " ".join(class_attr)
    if "sr-only" in class_attr.split():
        return True
    return attrs.get("aria-hidden") in {"true", "True", True}


def _language_from_node(node: LexborNode | None) -> str | None:
    """Parse a code-block language from ``class="language-*"`` on the node."""
    if node is None:
        return None
    class_attr = (node.attributes or {}).get("class") or ""
    if not class_attr:
        return None
    if isinstance(class_attr, list):
        class_attr = " ".join(class_attr)
    match = _LANGUAGE_CLASS_RE.search(class_attr)
    return match.group(1) if match else None


def _has_block_descendant(node: LexborNode, depth: int = 0) -> bool:
    """Return True if any descendant is a block-level or container element.

    Used to decide whether a container should recurse into children or emit a
    single shallow paragraph block. Depth is capped to avoid runaway traversal
    on pathological markup.
    """
    if depth > _MAX_DOM_PROBE_DEPTH:
        return False
    for child in _direct_children(node):
        tag = _tag_name(child)
        if not tag or tag in _SKIP_TAGS:
            continue
        if tag in _BLOCK_TAGS:
            return True
        if tag in _CONTAINER_TAGS and _has_block_descendant(child, depth + 1):
            return True
    return False


def _has_wrapped_block_descendant(node: LexborNode) -> bool:
    """True when node has block-level descendants other than direct-child ``<img>``.

    Direct-child ``<img>`` tags are excluded because ``_element_to_segments``
    already handles them as image segments. Only block wrappers (``<figure>``,
    ``<div>``, nested ``<p>``, ``<table>``, etc.) require ``_walk_children``
    recursion to preserve structure.
    """
    for child in _direct_children(node):
        tag = _tag_name(child)
        if not tag or tag in _SKIP_TAGS or tag == "img":
            continue
        if tag in _BLOCK_TAGS:
            return True
        if tag in _CONTAINER_TAGS and _has_block_descendant(child):
            return True
    return False


def _is_shallow_text_container(node: LexborNode) -> bool:
    """True when a container holds text but no nested block-level children.

    Bare ``<div>text</div>`` nodes should become paragraph blocks instead of
    being silently skipped during container recursion.
    """
    return not _has_block_descendant(node) and bool(_node_text(node))


def _is_paragraph_like(tag: str | None, node: LexborNode) -> bool:
    """True when the node would produce a standalone paragraph block.

    Used by heading-merge lookahead: headings are only merged into
    paragraph-like siblings, never into tables, lists, quotes, or code.
    """
    if tag in _PARAGRAPH_LIKE_TAGS:
        return True
    if tag in _CONTAINER_TAGS and _is_shallow_text_container(node):
        return True
    return False


# ---------------------------------------------------------------------------
# Text extraction and markdown conversion
# ---------------------------------------------------------------------------

def _has_inline_formatting(node: LexborNode, depth: int = 0) -> bool:
    """Check whether a DOM subtree contains inline formatting tags (bold, links, etc.).

    Decides whether the element's ``inner_html`` must be rendered to markdown
    (formatting tags present) or can be walked as plain text while still
    stored with ``DataFormat.MARKDOWN``. Block/container descendants are
    skipped — they have their own emitters.
    Depth is capped at ``_MAX_DOM_PROBE_DEPTH`` to prevent stack overflows.
    """
    if depth > _MAX_DOM_PROBE_DEPTH:
        return False
    tag = _tag_name(node)
    if tag in _INLINE_FORMAT_TAGS:
        return True
    for child in _direct_children(node):
        child_tag = _tag_name(child)
        if child_tag is None:
            continue
        if child_tag in _BLOCK_TAGS or child_tag in _CONTAINER_TAGS:
            continue
        if child_tag in _INLINE_FORMAT_TAGS or _has_inline_formatting(child, depth + 1):
            return True
    return False


def _inline_text(node: LexborNode) -> str:
    """Render a single inline or text node as plain text.

    Special cases: ``<br>`` → newline, ``<img>`` → empty (images are extracted
    separately as ``BlockType.IMAGE`` blocks), bare text nodes → verbatim.
    Used by ``_element_to_segments`` on the plain-text path to assemble
    paragraph text one child at a time.
    """
    tag = _tag_name(node)
    if tag is None:
        return node.text(deep=False, strip=False)
    if tag == "br":
        return "\n"
    if tag == "img":
        return ""
    return node.text(deep=True, separator="").strip()


_TABLE_TAG_RE = re.compile(r"<table\b", re.IGNORECASE)


def _html_to_markdown(html: str) -> str:
    """Convert an HTML fragment to ATX-style markdown via ``markdownify``.

    Central HTML→markdown bridge for the parser. Called whenever inline
    formatting must be preserved (paragraphs, list items, blockquotes, etc.).
    Relative ``<a href>`` values must already be absolutised on the main DOM
    tree (see ``_resolve_relative_links_on_tree``) before this runs.

    Fragments that contain ``<table>`` render tables through
    ``HtmlTableNormalizer`` so ``<img>`` cells keep ``![alt](uri)`` syntax;
    ``markdownify`` alone would reduce table images to plain alt text.
    """
    if not html:
        return ""
    if not _TABLE_TAG_RE.search(html):
        return markdownify(html, heading_style="ATX").strip()
    parser = LexborHTMLParser(f"<ph-pipes-table-root>{html}</ph-pipes-table-root>")
    root = parser.css_first("ph-pipes-table-root")
    if root is None:
        return markdownify(html, heading_style="ATX").strip()
    return _lexbor_subtree_to_markdown(root).strip()


def _html_fragment_to_segments(
    html: str,
) -> tuple[list[_Segment], DataFormat, str]:
    """Convert an HTML fragment to ordered text/image segments.

    Renders *html* to markdown via ``_html_to_markdown``, then splits on
    ``![alt](uri)`` boundaries using the same rules as
    ``markdown_to_blocks._split_raw_markdown_into_segments``.

    Returns:
        ``(segments, DataFormat.MARKDOWN, markdown_source)`` where
        *markdown_source* is the rendered markdown string (used later for
        inline ``data:image`` URI lookup when *caption_map* has no entry).
    """
    if not html.strip():
        return [], DataFormat.MARKDOWN, ""
    markdown = _html_to_markdown(html)
    return _split_raw_markdown_into_segments(markdown), DataFormat.MARKDOWN, markdown


def _element_to_segments(
    node: LexborNode,
) -> tuple[list[_Segment], DataFormat, str, list[LexborNode]]:
    """Split a block-level DOM element into ordered text/image segments.

    Two extraction paths; both emit ``DataFormat.MARKDOWN``:

    * **Markdown render path** — inline formatting tags are present; the
      element's ``inner_html`` is rendered to markdown and split on image syntax.
    * **Plain walk path** — direct children are walked in document order; text
      nodes and ``<br>`` accumulate into text segments and each direct-child
      ``<img>`` becomes an image segment.

    Returns:
        ``(segments, data_format, markdown_source, image_nodes)`` where
        *markdown_source* supports ``data:image`` lookup on the render path,
        and *image_nodes* aligns one-to-one with image segments on the plain
        walk path (empty on the render path).
    """
    if _has_inline_formatting(node):
        markdown = _html_to_markdown(node.inner_html.strip())
        return (
            _split_raw_markdown_into_segments(markdown),
            DataFormat.MARKDOWN,
            markdown,
            [],
        )

    segments: list[_Segment] = []
    image_nodes: list[LexborNode] = []
    text_buf: list[str] = []

    def flush_text() -> None:
        if not text_buf:
            return
        text = "".join(text_buf)
        if text.strip():
            segments.append(_Segment(kind="text", text=text))
        text_buf.clear()

    for child in _direct_children(node):
        tag = _tag_name(child)
        if tag == "img":
            flush_text()
            alt = ((child.attributes or {}).get("alt") or "").strip()
            image_nodes.append(child)
            segments.append(_Segment(kind="image", alt_text=alt))
        elif tag is None:
            fragment = child.text(deep=False, strip=False)
            if fragment:
                text_buf.append(fragment)
        elif tag == "br":
            text_buf.append("\n")
        else:
            nested_imgs = child.css("img")
            if nested_imgs:
                flush_text()
                for img in nested_imgs:
                    alt = ((img.attributes or {}).get("alt") or "").strip()
                    image_nodes.append(img)
                    segments.append(_Segment(kind="image", alt_text=alt))
            else:
                rendered = _inline_text(child)
                if rendered:
                    text_buf.append(rendered)

    flush_text()
    return segments, DataFormat.MARKDOWN, "", image_nodes


def _merge_heading_content_segments(
    level: int,
    heading_segments: list[_Segment],
    content_segments: list[_Segment],
) -> list[_Segment]:
    """Merge heading and paragraph-like segments with a markdown heading prefix.

    Prepends ``#`` * level to the first heading text segment (or inserts a bare
    prefix segment when the heading has no leading text), then joins the first
    content text segment onto the last heading text segment with a newline when
    both are text. Image segments from either side are preserved in order.

    Args:
        level: Heading level (1–6) derived from the HTML tag name.
        heading_segments: Segments extracted from the ``<hN>`` node.
        content_segments: Segments extracted from the following paragraph-like node.

    Returns:
        Combined segment list ready for ``_emit_with_image_splits``.
    """
    merged: list[_Segment] = []
    if heading_segments and heading_segments[0].kind == "text":
        merged.append(
            _Segment(kind="text", text="#" * level + " " + heading_segments[0].text)
        )
        merged.extend(heading_segments[1:])
    elif level > 0:
        merged.append(_Segment(kind="text", text="#" * level + " "))
        merged.extend(heading_segments)
    else:
        merged.extend(heading_segments)

    if merged and content_segments:
        last = merged[-1]
        first_content = content_segments[0]
        if last.kind == "text" and first_content.kind == "text":
            last.text = last.text + "\n" + first_content.text
            merged.extend(content_segments[1:])
        else:
            merged.extend(content_segments)
    else:
        merged.extend(content_segments)
    return merged


def _resolve_relative_links_on_tree(root: LexborNode, base_url: str) -> None:
    """Rewrite relative ``<a href>`` values to absolute URLs on the live DOM tree.

    Relative links break once content is stored outside its original page
    context (RAG prompts, search results). Resolving once on the main parse
    avoids re-parsing every HTML fragment during the block walk. Already-
    absolute, ``mailto:``, ``#hash``, and ``data:`` URIs are left untouched.
    """
    if not base_url:
        return
    for anchor in root.css("a"):
        attrs = anchor.attributes or {}
        href = (attrs.get("href") or "").strip()
        if not href:
            continue
        if href.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        anchor.attrs["href"] = urljoin(base_url, href)


# ---------------------------------------------------------------------------
# Table grid utilities
# ---------------------------------------------------------------------------

def _table_caption(table_node: LexborNode) -> str:
    """Extract the first ``<caption>`` text from a ``<table>``, or empty string.

    Caption text is stored in ``TableMetadata.captions`` so RAG and search can
    use it for ranking without re-parsing the table structure.
    """
    for child in _direct_children(table_node):
        if _tag_name(child) == "caption":
            return _node_text(child)
    return ""


def _table_section_rows(
    table_node: LexborNode,
) -> tuple[list[LexborNode], list[LexborNode]]:
    """Split a ``<table>`` into (header_rows, body_rows) lists of ``<tr>`` nodes.

    Uses explicit ``<thead>``/``<tbody>`` when present. Otherwise infers headers
    by counting leading rows with ``<th>`` cells (handles Lexbor auto-inserted
    ``<tbody>`` transparently). Header rows feed ``TableMetadata.column_names``;
    body rows become individual ``TABLE_ROW`` blocks.
    """
    thead_rows: list[LexborNode] = []
    body_rows: list[LexborNode] = []
    bare_rows: list[LexborNode] = []

    for child in _direct_children(table_node):
        tag = _tag_name(child)
        if tag == "thead":
            thead_rows.extend(_row_children(child))
        elif tag in {"tbody", "tfoot"}:
            body_rows.extend(_row_children(child))
        elif tag == "tr":
            bare_rows.append(child)

    if thead_rows:
        return thead_rows, body_rows + bare_rows

    all_rows = body_rows + bare_rows
    if not all_rows:
        return [], []

    # Lexbor may auto-insert <tbody>; infer headers from leading <th> rows.
    split_at = _count_leading_header_rows(all_rows)
    return all_rows[:split_at], all_rows[split_at:]


def _row_children(section_node: LexborNode) -> list[LexborNode]:
    """Return direct ``<tr>`` children of a table section (``<thead>``, ``<tbody>``, etc.).

    Filters out stray non-``<tr>`` nodes (whitespace, scripts) to give callers
    a clean row list for grid expansion.
    """
    return [child for child in _direct_children(section_node) if _tag_name(child) == "tr"]


def _count_leading_header_rows(row_nodes: list[LexborNode]) -> int:
    """Count consecutive leading rows that contain at least one ``<th>`` cell.

    Heuristic for tables without explicit ``<thead>``/``<tbody>``: the first
    all-``<td>`` row ends the header region. Used by ``_table_section_rows``
    to split rows into header and body segments.
    """
    count = 0
    for row in row_nodes:
        cells = [child for child in _direct_children(row) if _tag_name(child) in {"th", "td"}]
        if not cells:
            break
        if any(_tag_name(cell) == "th" for cell in cells):
            count += 1
        else:
            break
    return count


def _span_int(attrs: dict, name: str) -> int:
    """Parse a ``rowspan``/``colspan`` attribute as a positive int (min 1).

    Handles missing, empty, non-numeric, or zero values by falling back to 1
    (the HTML spec default). Used during grid expansion to size cell slots.
    """
    raw = attrs.get(name, 1)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _pad_grid(grid: list[list[NormalizedCell]]) -> list[list[NormalizedCell]]:
    """Pad every row to the widest row's column count with empty cells.

    After colspan/rowspan expansion, rows may differ in length. Downstream
    collapse logic requires a rectangular grid, so shorter rows are extended
    with empty ``NormalizedCell`` placeholders. Mutates in place.
    """
    if not grid:
        return []
    width = max(len(row) for row in grid)
    return _pad_grid_to_width(grid, width)


def _pad_grid_to_width(
    grid: list[list[NormalizedCell]],
    width: int,
) -> list[list[NormalizedCell]]:
    """Pad each grid row with empty origin cells to an explicit ``width``.

    Like ``_pad_grid`` but with a caller-specified target width — used when
    header and body grids must align to a shared column count. Padding cells
    are ``is_origin=True`` so collapse logic treats them as blank data, not
    span continuations. Mutates in place.
    """
    if not grid:
        return []
    for row in grid:
        while len(row) < width:
            row.append(NormalizedCell(text="", is_origin=True))
    return grid


def _grid_width(*grids: list[list[NormalizedCell]]) -> int:
    """Return the maximum row length across one or more cell grids.

    Used to find the shared target width before padding header and body grids
    to the same column count. Returns 0 if all grids are empty.
    """
    widths = [len(row) for grid in grids for row in grid]
    return max(widths) if widths else 0


def _column_groups(
    header_grid: list[list[NormalizedCell]],
    width: int,
) -> list[tuple[int, int]]:
    """Derive ``(start_col, end_col)`` logical column ranges from the top header row.

    Walks the top header row's origin cells and uses each cell's colspan to
    define a group range. Collapse functions use these groups to merge multiple
    physical columns into one logical output column. Falls back to one group
    per physical column when no header grid exists.
    """
    if not header_grid:
        return [(col, col + 1) for col in range(width)]

    top_row = header_grid[0]
    groups: list[tuple[int, int]] = []
    col = 0
    while col < width:
        while col < width and not top_row[col].is_origin:
            col += 1
        if col >= width:
            break
        span = top_row[col].colspan
        groups.append((col, col + span))
        col += span
    return groups or [(col, col + 1) for col in range(width)]


def _collapse_header_row(
    header_grid: list[list[NormalizedCell]],
    column_groups: list[tuple[int, int]],
) -> list[str]:
    """Merge multi-row headers into one label per logical column group.

    For each group, origin-cell texts within the same row are joined with
    ``" | "``, and levels across rows are stacked with newlines. Consecutive
    duplicate levels are deduplicated (from rowspan repeats). Produces the
    flat header list needed by ``TableMetadata.column_names`` and markdown
    rendering.
    """
    collapsed: list[str] = []
    for col_start, col_end in column_groups:
        group_width = col_end - col_start
        levels: list[str] = []
        for row in header_grid:
            parts: list[str] = []
            for col in range(col_start, col_end):
                cell = row[col]
                if not cell.is_origin:
                    continue
                text = cell.text.strip()
                if text:
                    parts.append(text)
            if not parts:
                continue
            level = (
                _CELL_SEP.join(parts)
                if group_width > 1 and len(parts) > 1
                else parts[0]
            )
            if levels and level == levels[-1]:
                continue
            levels.append(level)
        collapsed.append(_LEVEL_SEP.join(levels))
    return collapsed


def _collapse_body_rows(
    body_grid: list[list[NormalizedCell]],
    column_groups: list[tuple[int, int]],
) -> list[list[str]]:
    """Collapse the expanded body grid into output rows with merged column groups.

    Applies two merges: (1) consecutive HTML rows sharing a first-column
    rowspan are grouped into one output row, and (2) physical columns within
    each logical column group are merged per cell. The result is a list of
    string-valued rows ready for ``TABLE_ROW`` block emission.
    """
    if not body_grid:
        return []

    output: list[list[str]] = []
    row_idx = 0
    while row_idx < len(body_grid):
        span = _body_output_row_span(body_grid[row_idx])
        group_rows = body_grid[row_idx:row_idx + span]
        output.append([
            _collapse_body_cell(group_rows, col_start, col_end)
            for col_start, col_end in column_groups
        ])
        row_idx += span
    return output


def _body_output_row_span(row: list[NormalizedCell]) -> int:
    """Return how many grid rows merge into one output row (first column's rowspan).

    Only the first column drives grouping — middle-column spans must not fold
    unrelated rows together, which would produce incorrect records.
    """
    if not row:
        return 1
    label = row[0]
    if label.is_origin and label.rowspan > 1:
        return label.rowspan
    return 1


def _collapse_body_cell(
    group_rows: list[list[NormalizedCell]],
    col_start: int,
    col_end: int,
) -> str:
    """Format one logical column group across one or more grouped HTML rows.

    Single-column groups delegate to ``_collapse_single_column_cell`` (handles
    rowspan labels and stacked values). Multi-column groups join origin cells
    within the range with ``" | "`` per row, then stack rows with newlines.
    """
    group_width = col_end - col_start
    if group_width == 1:
        return _collapse_single_column_cell(group_rows, col_start)

    lines: list[str] = []
    for html_row in group_rows:
        parts: list[str] = []
        for col in range(col_start, col_end):
            cell = html_row[col]
            if not cell.is_origin:
                continue
            text = cell.text.strip()
            if text:
                parts.append(text)
        if parts:
            lines.append(_CELL_SEP.join(parts))
    return _LEVEL_SEP.join(lines)


def _collapse_single_column_cell(
    group_rows: list[list[NormalizedCell]],
    col: int,
) -> str:
    """Format a single column across grouped rows, handling rowspan and label semantics.

    Single row → text directly. Rowspan > 1 origin → that cell wins (covers
    the full group). First column (label) → first non-empty value only. Other
    columns → all values newline-joined.
    """
    if len(group_rows) == 1:
        return group_rows[0][col].text.strip()

    values: list[str] = []
    for html_row in group_rows:
        cell = html_row[col]
        if not cell.is_origin:
            continue
        text = cell.text.strip()
        if not text:
            continue
        if cell.rowspan > 1:
            return text
        if col == 0:
            return text
        values.append(text)
    return _LEVEL_SEP.join(values)


def _format_table_row(headers: list[str], cells: list[str]) -> str:
    """Build a ``Header: value`` sentence for one table row (for RAG / full-text search).

    Pairs each cell with its column header (e.g. ``"Name: Alice, Age: 30"``).
    Falls back to ``Column N`` labels for ragged tables, or plain comma-join
    when no headers exist.
    """
    if headers:
        parts = [
            f"{headers[i] if i < len(headers) else f'Column {i + 1}'}: {cell}"
            for i, cell in enumerate(cells)
        ]
        return ", ".join(parts)
    return ", ".join(cells)


def _escape_markdown_cell(value: str) -> str:
    """Escape pipe characters, newlines, and extra spaces for markdown table cells.

    ``|`` → ``\\|``, ``\\n`` → ``<br>``, consecutive spaces collapsed. Ensures
    the value won't break GFM pipe table structure.
    """
    escaped = value.replace("|", "\\|").replace("\n", "<br>")
    return re.sub(r" +", " ", escaped).strip()


def _render_table_markdown(table: NormalizedTable) -> str:
    """Render a ``NormalizedTable`` as a GFM pipe table (header + separator + body rows).

    Generates synthetic ``Column N`` headers when none exist. Body rows are
    padded/truncated to header width. Used by ``NormalizedTable.to_markdown()``
    and standalone markdown export.
    """
    headers = list(table.column_headers)
    if not headers and table.body_rows:
        headers = [f"Column {index + 1}" for index in range(len(table.body_rows[0]))]
    if not headers:
        return ""

    lines = [
        "| " + " | ".join(_escape_markdown_cell(header) for header in headers) + " |",
        "|" + "|".join(" --- " for _ in headers) + "|",
    ]
    for row in table.body_rows:
        padded = row + [""] * (len(headers) - len(row))
        lines.append(
            "| " + " | ".join(
                _escape_markdown_cell(cell) for cell in padded[: len(headers)]
            ) + " |"
        )
    return "\n".join(lines)


class HtmlTableNormalizer:
    """Collapse an HTML table into logical column rows for block emission.

    Nested ``<table>`` elements are normalized recursively and rendered as
    markdown pipe tables inside the parent cell text.
    Inline / block markup inside cells is converted to markdown.
    """

    def normalize(self, table_node: LexborNode) -> NormalizedTable:
        """Expand rowspan/colspan into a grid, then collapse into logical columns.

        Produces the same collapsed shape the block walker expects: one header
        label per logical column and one body row per merged HTML row group.
        """
        header_row_nodes, body_row_nodes = _table_section_rows(table_node)
        header_grid = self._expand_rows(header_row_nodes, is_header=True)
        body_grid = self._expand_rows(body_row_nodes, is_header=False)

        width = _grid_width(header_grid, body_grid)
        header_grid = _pad_grid_to_width(header_grid, width)
        body_grid = _pad_grid_to_width(body_grid, width)

        column_groups = _column_groups(header_grid, width)
        headers = _collapse_header_row(header_grid, column_groups) if header_grid else []
        body_rows = _collapse_body_rows(body_grid, column_groups)

        return NormalizedTable(
            column_headers=headers,
            body_rows=body_rows,
            num_cols=len(column_groups),
            num_body_rows=len(body_rows),
            has_header=bool(headers),
        )

    def _expand_rows(
        self,
        row_nodes: list[LexborNode],
        *,
        is_header: bool,
    ) -> list[list[NormalizedCell]]:
        """Expand HTML table rows into a 2D grid, filling rowspan/colspan placeholder slots.
        Tracks pending rowspans per column and pads the final grid to a uniform width.
        """
        grid: list[list[NormalizedCell]] = []
        rowspan_pending: dict[int, tuple[int, NormalizedCell]] = {}

        for row_node in row_nodes:
            cells = [
                child for child in _direct_children(row_node)
                if _tag_name(child) in {"th", "td"}
            ]
            if not cells:
                continue

            row: list[NormalizedCell] = []
            col = 0
            cell_idx = 0

            while cell_idx < len(cells) or col in rowspan_pending:
                while col in rowspan_pending:
                    remaining, source = rowspan_pending[col]
                    row.append(NormalizedCell(
                        text=source.text,
                        rowspan=source.rowspan,
                        colspan=source.colspan,
                        is_header=source.is_header,
                        is_origin=False,
                    ))
                    if remaining > 1:
                        rowspan_pending[col] = (remaining - 1, source)
                    else:
                        del rowspan_pending[col]
                    col += 1

                if cell_idx >= len(cells):
                    break

                cell_node = cells[cell_idx]
                cell_idx += 1
                normalized = self._normalize_cell(cell_node, is_header=is_header)
                colspan = normalized.colspan
                rowspan = normalized.rowspan

                for offset in range(colspan):
                    if offset == 0:
                        slot = normalized
                    else:
                        slot = NormalizedCell(
                            text="",
                            colspan=colspan,
                            is_header=is_header,
                            is_origin=False,
                        )
                    row.append(slot)
                    if rowspan > 1:
                        rowspan_pending[col + offset] = (rowspan - 1, normalized)
                col += colspan

            grid.append(row)

        return _pad_grid(grid)

    def _normalize_cell(self, cell_node: LexborNode, *, is_header: bool) -> NormalizedCell:
        """Build one ``NormalizedCell`` from a ``<th>``/``<td>`` node including span attributes."""
        attrs = cell_node.attributes or {}
        tag = _tag_name(cell_node)
        return NormalizedCell(
            text=self._cell_content(cell_node, is_header=is_header),
            rowspan=_span_int(attrs, "rowspan"),
            colspan=_span_int(attrs, "colspan"),
            is_header=is_header or tag == "th",
        )

    def _cell_content(self, cell_node: LexborNode, *, is_header: bool = False) -> str:
        """Build cell text from child nodes; nested ``<table>`` values are markdown tables.

        Inline/block markup is converted to markdown; plain text nodes are appended as-is.
        Images inside header cells are skipped so column labels stay text-only.
        """
        parts: list[str] = []
        for child in _direct_children(cell_node):
            tag = _tag_name(child)
            if tag == "table":
                nested = self.normalize(child)
                serialized = normalized_table_to_markdown(nested)
                if serialized:
                    parts.append(serialized)
            elif tag == "img" and is_header:
                continue
            elif tag is None:
                text = child.text(deep=False, strip=False).strip()
                if text:
                    parts.append(text)
            else:
                markdown = _html_to_markdown(child.html)
                if is_header:
                    markdown = _strip_inline_images_from_markdown(markdown)
                if markdown:
                    parts.append(markdown)
        return "\n".join(parts).strip()


def normalize_html_table(table_node: LexborNode) -> NormalizedTable:
    """Public entry: normalize one ``<table>`` DOM node into collapsed logical columns.

    Thin wrapper around ``HtmlTableNormalizer`` for callers that need the
    collapsed grid without walking the full block conversion pipeline.
    """
    return HtmlTableNormalizer().normalize(table_node)


def normalized_table_to_markdown(
    table: NormalizedTable,
    *,
    caption: str = "",
) -> str:
    """Render a normalized table as markdown, optionally prefixing a caption on its own line."""
    table_md = _render_table_markdown(table)
    if not table_md:
        return caption.strip()
    if caption.strip():
        return f"{caption.strip()}\n\n{table_md}"
    return table_md


def _lexbor_subtree_to_markdown(node: LexborNode) -> str:
    """Render a DOM subtree to markdown, normalizing ``<table>`` nodes in-place.

    Walks children recursively whenever a ``<table>`` appears anywhere in the
    subtree so surrounding text and inline markup still pass through
    ``markdownify`` while each table is rendered with ``HtmlTableNormalizer``
    (preserving ``![alt](uri)`` in cells). Subtrees with no table at any depth
    short-circuit through a single ``markdownify`` call.
    """
    tag = _tag_name(node)
    if tag == "table":
        return normalized_table_to_markdown(normalize_html_table(node))

    children = list(_direct_children(node))
    if not children:
        if tag is None:
            return node.text(deep=False, strip=False).strip()
        inner = node.inner_html.strip()
        if not inner:
            return ""
        return markdownify(inner, heading_style="ATX").strip()

    # Only short-circuit when *no* descendant is a table. Checking only direct
    # children would route subtrees like ``<ul><li><table>...</table></li></ul>``
    # through ``markdownify`` for the whole fragment, which strips ``<img>`` in
    # ``<td>`` down to bare alt text.
    if not _TABLE_TAG_RE.search(node.html):
        return markdownify(node.html, heading_style="ATX").strip()

    parts: list[str] = []
    for child in children:
        child_tag = _tag_name(child)
        if child_tag == "table":
            piece = normalized_table_to_markdown(normalize_html_table(child))
        elif child_tag is None:
            piece = child.text(deep=False, strip=False).strip()
        else:
            piece = _lexbor_subtree_to_markdown(child)
        if piece:
            parts.append(piece)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public converter
# ---------------------------------------------------------------------------

class HtmlToBlocksConverter:
    """Convert HTML content directly into BlocksContainer without Docling.

    Uses Selectolax (Lexbor) for DOM parsing so we can map HTML structures to
    the platform's block schema without running the heavier Docling pipeline.
    Block/group mapping mirrors ``markdown_to_blocks.MarkdownToBlocksConverter``.
    """

    def convert(
        self,
        html_content: str,
        *,
        base_url: str | None = None,
        caption_map: dict[str, str] | None = None,
    ) -> BlocksContainer:
        """Parse HTML and walk the DOM tree into a ``BlocksContainer``.

        Args:
            html_content: HTML source string.
            base_url: Optional base URL for resolving relative ``<a href>`` and
                ``<img src>`` values.
            caption_map: Optional mapping of image alt-text to base-64 data URIs.
                When present, matching alts use the mapped URI in the image block.

        Returns:
            Populated BlocksContainer with blocks and block_groups.
        """
        parser = LexborHTMLParser(html_content)
        root = parser.body or parser.root
        if root is None:
            return BlocksContainer()
        if base_url:
            _resolve_relative_links_on_tree(root, base_url)
        walker = _DomWalker(caption_map=caption_map)
        return walker.walk(root)


# ---------------------------------------------------------------------------
# DOM walker
# ---------------------------------------------------------------------------

class _DomWalker:
    """Stateful visitor that turns Lexbor DOM nodes into blocks and groups.

    Maintains a group nesting stack so list, quote, code, and table structures
    mirror HTML hierarchy. Tag dispatch in ``_process_node`` follows the mapping
    documented in this module's module-level docstring.
    """

    def __init__(
        self,
        *,
        caption_map: dict[str, str] | None = None,
    ) -> None:
        """Initialize walker state for one conversion pass.

        Args:
            caption_map: Alt-text to base64 URI map for inlined images.
        """
        self.caption_map = caption_map or {}
        self.blocks: list[Block] = []
        self.block_groups: list[BlockGroup] = []
        self.group_stack: list[_OpenGroup] = []

    # ------------------------------------------------------------------ traversal

    def walk(self, root: LexborNode) -> BlocksContainer:
        """Traverse the DOM from ``root`` and return the assembled container.

        Only direct children are walked from ``root``; callers pass ``body`` or
        the document root so the full tree is covered in one pass.
        """
        self._walk_children(root, depth=0)
        return BlocksContainer(blocks=self.blocks, block_groups=self.block_groups)

    def _walk_children(self, parent: LexborNode, depth: int = 0) -> None:
        """Dispatch each direct child of ``parent`` through ``_process_node``.

        Performs lookahead when a heading is encountered: if the next element
        sibling is paragraph-like, the heading text is merged into the
        paragraph block as a markdown heading prefix instead of emitting a
        separate heading block.

        Depth is capped at ``_MAX_DOM_PROBE_DEPTH``; deeper containers are
        emitted as a flat paragraph so pathological nesting cannot overflow
        the Python stack.
        """
        if depth > _MAX_DOM_PROBE_DEPTH:
            if _node_text(parent) or parent.css("img"):
                self._emit_text_block(parent, BlockSubType.PARAGRAPH)
            return

        children = list(_direct_children(parent))
        i = 0
        while i < len(children):
            node = children[i]
            tag = _tag_name(node)

            if tag in _HEADING_TAGS and not _is_hidden(node):
                next_node, next_idx = self._find_next_element_sibling(children, i + 1)
                next_tag = _tag_name(next_node) if next_node is not None else None
                if next_node is not None and _is_paragraph_like(next_tag, next_node):
                    self._emit_heading_with_content(node, next_node, tag)
                    i = next_idx + 1
                    continue

            self._process_node(node, depth)
            i += 1

    @staticmethod
    def _find_next_element_sibling(
        children: list[LexborNode],
        start: int,
    ) -> tuple[LexborNode | None, int]:
        """Return the next element sibling after ``start``, skipping text nodes and skip tags.

        Text nodes between block elements are whitespace in practice and are
        already dropped by ``_process_node``, so they are safely skipped here.
        Returns ``(None, -1)`` when no element sibling remains.
        """
        for idx in range(start, len(children)):
            child = children[idx]
            tag = _tag_name(child)
            if tag is None or tag in _SKIP_TAGS:
                continue
            return child, idx
        return None, -1

    def _emit_heading_with_content(
        self,
        heading_node: LexborNode,
        content_node: LexborNode,
        heading_tag: str,
    ) -> None:
        """Merge a heading into the following paragraph-like block.

        The heading text is prepended as a markdown heading prefix
        (``## Title\\nParagraph text``) so the content block carries its
        section context. Images split into container plus TEXT/IMAGE children.
        """
        level = int(heading_tag[1])
        heading_segments, _, heading_md, heading_images = _element_to_segments(
            heading_node
        )
        content_segments, content_fmt, content_md, content_images = _element_to_segments(
            content_node
        )

        if not heading_segments and not content_segments:
            return

        if not heading_segments:
            self._emit_with_image_splits(
                block_type=BlockType.TEXT,
                sub_type=BlockSubType.PARAGRAPH,
                format=content_fmt,
                segments=content_segments,
                markdown_source=content_md,
                image_nodes=content_images,
            )
            return

        segments = _merge_heading_content_segments(
            level, heading_segments, content_segments
        )
        markdown_source = heading_md
        if heading_md and content_md:
            markdown_source = heading_md + "\n" + content_md
        elif content_md:
            markdown_source = content_md

        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            segments=segments,
            markdown_source=markdown_source,
            image_nodes=heading_images + content_images,
        )

    def _process_node(self, node: LexborNode, depth: int = 0) -> None:  # noqa: C901
        """Route one DOM node to the correct block emitter based on its HTML tag.

        Skips utility/hidden tags, recurses into containers, and maps semantic
        tags to block types per the module docstring. ``<pre>`` with nested
        ``<code>`` children is special-cased so code blocks and surrounding
        structure are both preserved.
        """
        tag = _tag_name(node)
        if not tag or tag in _SKIP_TAGS:
            return

        if tag in _HEADING_TAGS:
            if not _is_hidden(node):
                self._emit_text_block(node, BlockSubType.HEADING)
            return

        if tag in _PARAGRAPH_LIKE_TAGS:
            if _has_wrapped_block_descendant(node):
                self._walk_children(node, depth + 1)
            else:
                self._emit_text_block(node, BlockSubType.PARAGRAPH)
            return

        if tag == "pre":
            if node.css_first("code") is not None:
                # Each <code> child gets its own BlockGroup(CODE) + Block.
                # Other elements (lists, tables, blockquotes) inside <pre>
                # are processed exactly as if they appeared at the top level.
                self._walk_children(node, depth + 1)
            else:
                # <pre> with no <code> children — treat entire content as
                # a single code block (e.g. <pre>bare text</pre>).
                for img in node.css("img"):
                    self._emit_image(img)
                content = node.text(deep=True, separator="").rstrip("\n")
                if content:
                    self._emit_code_group(content, _language_from_node(node))
            return
        if tag == "code":
            for img in node.css("img"):
                self._emit_image(img)
            self._emit_code_group(_node_text(node), _language_from_node(node))
            return

        if tag == "blockquote":
            self._emit_blockquote(node)
            return

        if tag == "ul":
            self._process_list(node, ordered=False)
            return
        if tag == "ol":
            self._process_list(node, ordered=True)
            return

        if tag == "table":
            self._process_table(node)
            return

        if tag == "details":
            self._process_details(node, depth)
            return

        if tag == "summary":
            self._emit_text_block(node, BlockSubType.HEADING)
            return

        if tag == "hr":
            return

        if tag == "img":
            self._emit_image(node)
            return

        if tag in _INLINE_FORMAT_TAGS:
            self._emit_inline_block(node)
            return

        if tag in _CONTAINER_TAGS:
            if _is_shallow_text_container(node):
                self._emit_text_block(node, BlockSubType.PARAGRAPH)
            else:
                self._walk_children(node, depth + 1)
            return

        self._walk_children(node, depth + 1)

    # ------------------------------------------------------------------ group management

    def _current_parent_index(self) -> int | None:
        """Index of the innermost open group, or ``None`` at document root."""
        return self.group_stack[-1].index if self.group_stack else None

    def _open_group(
        self,
        group_type: GroupType,
        sub_type: GroupSubType | None = None,
    ) -> None:
        """Create a ``BlockGroup`` and push it onto the nesting stack.

        The new group is linked as a child of the current parent (if any) so
        the tree mirrors HTML nesting (e.g. list inside blockquote).
        """
        parent_index = self._current_parent_index()
        group = BlockGroup(
            index=len(self.block_groups),
            type=group_type,
            sub_type=sub_type,
            parent_index=parent_index,
        )
        self.block_groups.append(group)
        if self.group_stack:
            self.group_stack[-1].child_group_indices.append(group.index)
        self.group_stack.append(_OpenGroup(index=group.index))

    def _close_group(self) -> None:
        """Pop the innermost group and attach collected child indices.

        Child membership is finalized here because blocks and sub-groups may be
        appended throughout the open group's DOM span.
        """
        if not self.group_stack:
            return
        open_group = self.group_stack.pop()
        group = self.block_groups[open_group.index]
        group.children = BlockGroupChildren.from_indices(
            block_indices=open_group.child_block_indices,
            block_group_indices=open_group.child_group_indices,
        )

    def _add_block(self, block: Block) -> Block:
        """Append a block and record it as a child of the current open group."""
        block.index = len(self.blocks)
        self.blocks.append(block)
        if self.group_stack:
            self.group_stack[-1].child_block_indices.append(block.index)
        return block

    # ------------------------------------------------------------------ lists

    def _process_list(self, list_node: LexborNode, *, ordered: bool) -> None:
        """Open a LIST or ORDERED_LIST group and emit one LIST_ITEM per ``<li>``.

        Also scans wrapper containers inside the list because malformed HTML
        sometimes nests ``<li>`` elements inside extra ``<div>`` layers.
        Orphan text nodes and stray elements are emitted as implicit list items
        so content is not silently dropped.
        """
        group_type = GroupType.ORDERED_LIST if ordered else GroupType.LIST
        self._open_group(group_type)
        for child in _direct_children(list_node):
            tag = _tag_name(child)
            if tag == "li":
                self._process_list_item(child)
            elif tag in _CONTAINER_TAGS:
                self._emit_list_items_from_container(child)
            else:
                self._emit_orphan_as_list_item(child)
        self._close_group()

    def _emit_list_items_from_container(self, container: LexborNode) -> None:
        """Find ``<li>`` elements nested inside wrapper containers within a list.
        Recurses through container tags because some malformed HTML wraps list items.
        """
        for child in _direct_children(container):
            tag = _tag_name(child)
            if tag == "li":
                self._process_list_item(child)
            elif tag in _CONTAINER_TAGS:
                self._emit_list_items_from_container(child)
            else:
                self._emit_orphan_as_list_item(child)

    def _emit_orphan_as_list_item(self, node: LexborNode) -> None:
        """Emit a stray node inside a list as a plain paragraph block.

        Handles bare text nodes and non-container elements (e.g. ``<p>``,
        ``<a>``) that appear directly inside ``<ul>``/``<ol>`` or wrapper
        containers without a surrounding ``<li>``.
        """
        tag = _tag_name(node)
        if tag is None:
            data = node.text(deep=False, strip=False).strip()
            if not data:
                return
            self._emit_with_image_splits(
                block_type=BlockType.TEXT,
                sub_type=BlockSubType.PARAGRAPH,
                format=DataFormat.MARKDOWN,
                segments=[_Segment(kind="text", text=data)],
            )
            return

        html = (node.html or "").strip()
        if not html:
            return
        segments, data_format, markdown_source = _html_fragment_to_segments(html)
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=data_format,
            segments=segments,
            markdown_source=markdown_source,
        )

    def _process_list_item(self, li_node: LexborNode) -> None:
        """Emit one LIST_ITEM block with the ``<li>`` inner HTML as markdown.

        Stores the full inner HTML (not just text) so nested lists, links, and
        inline formatting inside the item survive for display and re-export.
        When the ``<li>`` body is exactly one image, the IMAGE block stands in
        for the list item itself (no empty container, no fragmentation).
        """
        html = li_node.inner_html.strip()
        if not html:
            return
        segments, data_format, markdown_source = _html_fragment_to_segments(html)
        if (
            len(segments) == 1
            and segments[0].kind == "image"
        ):
            if image_block := self._build_image_block(
                segments[0].alt_text,
                markdown_source=markdown_source,
                sub_type=BlockSubType.LIST_ITEM,
            ):
                self._add_block(image_block)
            return
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.LIST_ITEM,
            format=data_format,
            segments=segments,
            markdown_source=markdown_source,
        )

    # ------------------------------------------------------------------ code

    def _emit_code_group(self, content: str, language: str | None) -> None:
        """Wrap bare ``<code>``/``<pre>`` content in a CODE group with language metadata.

        The group exists so code is treated consistently with other structured
        block types (list, quote, table) and can carry group-level metadata.
        """
        if not content:
            return
        self._open_group(GroupType.CODE)
        self._add_block(Block(
            id=str(uuid4()),
            type=BlockType.TEXT,
            sub_type=BlockSubType.CODE,
            format=DataFormat.CODE,
            data=content.rstrip("\n"),
            parent_index=self._current_parent_index(),
            code_metadata=CodeMetadata(language=language),
        ))
        self._close_group()

    # ------------------------------------------------------------------ quote & details

    def _emit_blockquote(self, node: LexborNode) -> None:
        """Open a QUOTE group and store the blockquote inner HTML as one markdown block.

        The entire inner HTML is kept as a single block (like markdown list
        items) so nested structure stays faithful to the source.
        """
        self._open_group(GroupType.TEXT_SECTION, GroupSubType.QUOTE)
        html = node.inner_html.strip()
        if html:
            segments, data_format, markdown_source = _html_fragment_to_segments(html)
            self._emit_with_image_splits(
                block_type=BlockType.TEXT,
                sub_type=BlockSubType.QUOTE,
                format=data_format,
                segments=segments,
                markdown_source=markdown_source,
            )
        self._close_group()

    def _process_details(self, node: LexborNode, depth: int) -> None:
        """Emit ``<summary>`` as a heading, then process sibling children normally.

        ``<details>`` has no direct block-group mapping; the summary is surfaced
        as a heading so disclosure widgets remain navigable in search.
        """
        for child in _direct_children(node):
            if _tag_name(child) == "summary":
                self._emit_text_block(child, BlockSubType.HEADING)
                break

        for child in _direct_children(node):
            if _tag_name(child) != "summary":
                self._process_node(child, depth + 1)

    # ------------------------------------------------------------------ image splits

    def _uri_from_img_node(self, node: LexborNode) -> str | None:
        """Resolve a base64 image URI from a live ``<img>`` DOM node.

        Checks ``caption_map`` by alt text first, then ``src`` / the first
        entry in ``srcset``. Only ``data:image`` URIs are returned; HTTP(S)
        URLs are ignored so behaviour matches standalone ``_emit_image``.
        """
        attrs = node.attributes or {}
        alt_text = (attrs.get("alt") or "").strip()
        if alt_text and alt_text in self.caption_map:
            caption_uri = (self.caption_map[alt_text] or "").strip()
            if caption_uri.startswith("data:image"):
                return caption_uri
        src = (attrs.get("src") or "").strip()
        if not src and attrs.get("srcset"):
            srcset_parts = attrs["srcset"].split(",")
            if srcset_parts:
                first_part = srcset_parts[0].split()
                if first_part:
                    src = first_part[0].strip()
        if src.startswith("data:image"):
            return src
        return None

    def _uri_from_markdown(self, alt_text: str, markdown_source: str) -> str | None:
        """Extract an inline ``data:image`` URI for *alt_text* from rendered markdown.

        Scans *markdown_source* for ``![alt_text](uri)`` patterns (via
        ``_MD_IMAGE_RE``) and returns the URI when it starts with
        ``data:image``. Uses case-insensitive, whitespace-normalised comparison
        so minor ``markdownify`` transformations don't cause mismatches.
        Also handles empty alt text (``![](data:image/...)``).
        """
        if not markdown_source:
            return None
        normalized_alt = (alt_text or "").strip().lower()
        for match in _MD_IMAGE_RE.finditer(markdown_source):
            if match.group(1).strip().lower() == normalized_alt:
                src = match.group(2).strip()
                if src.startswith("data:image"):
                    return src
        return None

    def _resolve_image_uri(
        self,
        alt_text: str,
        *,
        img_node: LexborNode | None = None,
        markdown_source: str = "",
    ) -> str | None:
        """Resolve a storable base64 URI for an image alt, trying all HTML sources.

        Resolution order: ``caption_map`` lookup, live ``<img>`` node attributes,
        then ``data:image`` URI embedded in *markdown_source*. Returns ``None``
        when no ``data:image`` URI can be found (HTTP-only images are skipped).
        """
        if alt_text and alt_text in self.caption_map:
            uri = (self.caption_map[alt_text] or "").strip()
            if uri.startswith("data:image"):
                return uri
        if img_node is not None:
            uri = self._uri_from_img_node(img_node)
            if uri:
                return uri
        return self._uri_from_markdown(alt_text, markdown_source)

    def _build_image_block(
        self,
        alt_text: str,
        *,
        parent_block_index: int | None = None,
        img_node: LexborNode | None = None,
        markdown_source: str = "",
        sub_type: BlockSubType | None = None,
    ) -> Block | None:
        """Build an ``BlockType.IMAGE`` block when a resolvable URI exists.

        Args:
            alt_text: Image alt text used for ``caption_map`` lookup and metadata.
            parent_block_index: Container block index when this image is a split
                child; ``None`` for standalone top-level images.
            img_node: Optional live ``<img>`` node for URI resolution on the TXT path.
            markdown_source: Rendered markdown for URI resolution on the markdown path.
            sub_type: Optional ``BlockSubType`` to attach (e.g. ``LIST_ITEM`` when
                this image *is* the list item itself rather than a split child).

        Returns:
            A populated ``Block``, or ``None`` when no ``data:image`` URI resolves.
        """
        uri = self._resolve_image_uri(
            alt_text,
            img_node=img_node,
            markdown_source=markdown_source,
        )
        if not uri:
            return None

        image_fmt = None
        if uri.startswith("data:"):
            header = uri.split(",", 1)[0]
            mime = header.replace("data:", "").split(";")[0]
            parts = mime.split("/")
            image_fmt = parts[1] if len(parts) > 1 else None

        return Block(
            id=str(uuid4()),
            type=BlockType.IMAGE,
            sub_type=sub_type,
            format=DataFormat.BASE64,
            data={"uri": uri},
            parent_index=(
                None
                if parent_block_index is not None
                else self._current_parent_index()
            ),
            parent_block_index=parent_block_index,
            image_metadata=ImageMetadata(
                captions=[alt_text] if alt_text else [],
                image_format=image_fmt,
            ),
        )

    def _append_block_only(self, block: Block) -> Block:
        """Append a block to ``self.blocks`` without registering it as a group child.

        Used for image-split fragments and IMAGE siblings that are linked to
        their container via ``parent_block_index`` instead of ``BlockGroup.children``.
        """
        block.index = len(self.blocks)
        self.blocks.append(block)
        return block

    def _append_fragment_block(self, block: Block, parent_block_index: int) -> Block:
        """Append a TEXT fragment block parented to a split container."""
        block.parent_block_index = parent_block_index
        return self._append_block_only(block)

    def _emit_text_chunks(
        self,
        *,
        text: str,
        block_type: BlockType,
        sub_type: BlockSubType | None,
        format: DataFormat,
        parent_block_index: int | None = None,
    ) -> Block | None:
        """Emit one or more TEXT blocks, splitting oversized content at parse time."""
        if not text or not text.strip():
            return None

        chunks = split_long_text(text)
        if parent_block_index is not None:
            last: Block | None = None
            for chunk in chunks:
                last = self._append_fragment_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        format=format,
                        data=chunk,
                    ),
                    parent_block_index,
                )
            return last

        if len(chunks) == 1:
            return self._add_block(
                Block(
                    id=str(uuid4()),
                    type=block_type,
                    sub_type=sub_type,
                    format=format,
                    data=chunks[0],
                    parent_index=self._current_parent_index(),
                )
            )

        if self._uses_image_split_container():
            container = self._add_block(
                Block(
                    id=str(uuid4()),
                    type=block_type,
                    sub_type=sub_type,
                    format=format,
                    data="",
                    parent_index=self._current_parent_index(),
                )
            )
            for chunk in chunks:
                self._append_fragment_block(
                    Block(
                        id=str(uuid4()),
                        type=BlockType.TEXT,
                        format=format,
                        data=chunk,
                    ),
                    container.index,
                )
            return container

        last = None
        for chunk in chunks:
            last = self._add_block(
                Block(
                    id=str(uuid4()),
                    type=block_type,
                    sub_type=sub_type,
                    format=format,
                    data=chunk,
                    parent_index=self._current_parent_index(),
                )
            )
        return last

    def _uses_image_split_container(self) -> bool:
        """Return True when inline images should use an empty container block.

        Container + ``parent_block_index`` children apply inside list and
        blockquote groups. Top-level paragraphs emit flat sibling blocks instead.
        """
        if not self.group_stack:
            return False
        group = self.block_groups[self.group_stack[-1].index]
        if group.type in (GroupType.LIST, GroupType.ORDERED_LIST):
            return True
        if group.type == GroupType.TEXT_SECTION and group.sub_type == GroupSubType.QUOTE:
            return True
        return False

    def _emit_with_image_splits(
        self,
        *,
        block_type: BlockType,
        sub_type: BlockSubType | None,
        format: DataFormat,
        segments: list[_Segment],
        container_data: str = "",
        markdown_source: str = "",
        image_nodes: list[LexborNode] | None = None,
    ) -> None:
        """Emit blocks from text/image segments.

        Without images: one block with joined text. With images inside a list or
        quote group: empty container plus TEXT/IMAGE children linked via
        ``parent_block_index``. Fragment children omit ``sub_type``; only the
        container carries the parent semantic type (e.g. ``LIST_ITEM``).

        Args:
            block_type: Block type for unsplit content or the empty container.
            sub_type: Sub-type (e.g. ``PARAGRAPH``, ``LIST_ITEM``).
            format: ``DataFormat`` for TEXT blocks.
            segments: Ordered text/image segments.
            container_data: Payload for an empty container block (defaults to ``""``).
            markdown_source: Rendered markdown for ``data:image`` URI lookup.
            image_nodes: ``<img>`` nodes aligned with image segments on the TXT path.
        """
        image_node_iter = iter(image_nodes or [])
        has_images = any(seg.kind == "image" for seg in segments)
        if not has_images:
            full_text = "".join(seg.text for seg in segments if seg.kind == "text").strip()
            if not full_text and not container_data:
                return
            data = container_data or full_text
            if isinstance(data, str) and not data.strip():
                return
            self._emit_text_chunks(
                text=data,
                block_type=block_type,
                sub_type=sub_type,
                format=format,
            )
            return

        if not self._uses_image_split_container():
            for seg in segments:
                if seg.kind == "text":
                    if not seg.text.strip():
                        continue
                    self._emit_text_chunks(
                        text=seg.text,
                        block_type=BlockType.TEXT,
                        sub_type=sub_type,
                        format=format,
                    )
                elif seg.kind == "image":
                    img_node = next(image_node_iter, None)
                    if image_block := self._build_image_block(
                        seg.alt_text,
                        img_node=img_node,
                        markdown_source=markdown_source,
                    ):
                        self._add_block(image_block)
            return

        container = self._add_block(
            Block(
                id=str(uuid4()),
                type=block_type,
                sub_type=sub_type,
                format=format,
                data=container_data,
                parent_index=self._current_parent_index(),
            )
        )
        container_index = container.index

        for seg in segments:
            if seg.kind == "text":
                if not seg.text.strip():
                    continue
                self._emit_text_chunks(
                    text=seg.text,
                    block_type=BlockType.TEXT,
                    sub_type=None,
                    format=format,
                    parent_block_index=container_index,
                )
            elif seg.kind == "image":
                img_node = next(image_node_iter, None)
                if image_block := self._build_image_block(
                    seg.alt_text,
                    parent_block_index=container_index,
                    img_node=img_node,
                    markdown_source=markdown_source,
                ):
                    self._append_block_only(image_block)

    def _row_has_images(self, row_cells: list[_TableCell]) -> bool:
        """Return ``True`` when any cell in *row_cells* contains an inline image.

        Detects markdown ``![alt](uri)`` syntax and raw ``<img alt=…>`` HTML left
        in cell text after normalization.
        """
        return any(
            cell.images
            or _MD_IMAGE_RE.search(cell.markdown)
            or _HTML_IMG_ALT_RE.search(cell.markdown)
            for cell in row_cells
        )

    def _split_table_row_into_segments(
        self,
        headers: list[str],
        row_cells: list[_TableCell],
    ) -> list[_Segment]:
        """Split one table row into alternating TEXT and IMAGE segments.

        Walks logical columns left-to-right, accumulating ``header: value`` pairs
        into partial ``row_natural_language_text`` fragments. When a cell contains
        an image, flushes the accumulated text as a TEXT segment, emits the IMAGE
        segment, then resumes accumulation. Mirrors
        ``markdown_to_blocks.MarkdownToBlocksConverter._split_table_row_into_segments``.
        """
        pending_pairs: list[tuple[str, str]] = []
        fragments: list[_Segment] = []

        def flush_text_fragment() -> None:
            if not pending_pairs:
                return
            hdrs = [pair[0] for pair in pending_pairs]
            vals = [pair[1] for pair in pending_pairs]
            text = _format_table_row(hdrs, vals)
            if text.strip():
                fragments.append(_Segment(kind="text", text=text))
            pending_pairs.clear()

        for col_idx, cell in enumerate(row_cells):
            header = (
                headers[col_idx]
                if col_idx < len(headers)
                else f"Column {col_idx + 1}"
            )
            cell_segments = _split_cell_into_segments(cell)
            if not any(seg.kind == "image" for seg in cell_segments):
                value = ""
                if cell_segments and cell_segments[0].kind == "text":
                    value = cell_segments[0].text.strip()
                elif cell.plain:
                    value = cell.plain.strip()
                if value:
                    pending_pairs.append((header, value))
                continue

            for seg in cell_segments:
                if seg.kind == "text":
                    value = seg.text.strip()
                    if value:
                        pending_pairs.append((header, value))
                else:
                    flush_text_fragment()
                    fragments.append(seg)

        flush_text_fragment()
        return fragments

    def _emit_table_row_with_image_splits(
        self,
        *,
        group_index: int,
        row_number: int,
        headers: list[str],
        row_cells: list[_TableCell],
        row_block_indices: list[int],
        row_markdown: str,
    ) -> None:
        """Emit an empty ``TABLE_ROW`` container plus TEXT/IMAGE row children.

        The container carries only ``{"row_number": row_number}``; TEXT children
        hold partial ``row_natural_language_text`` fragments and IMAGE children
        are linked via ``parent_block_index``. Only the container index is
        appended to *row_block_indices* for ``BlockGroup.children``.

        Args:
            group_index: Parent table ``BlockGroup`` index.
            row_number: 1-based row number stored on the container.
            headers: Collapsed column header labels for fragment formatting.
            row_cells: Per-column cell content (plain and markdown are identical).
            row_block_indices: Mutable list; receives the container block index.
            row_markdown: Joined cell markdown used for ``data:image`` URI lookup.
        """
        segments = self._split_table_row_into_segments(headers, row_cells)
        container = Block(
            id=str(uuid4()),
            index=len(self.blocks),
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            parent_index=group_index,
            data={"row_number": row_number},
        )
        self.blocks.append(container)
        row_block_indices.append(container.index)
        container_index = container.index

        for seg in segments:
            if seg.kind == "text":
                if not seg.text.strip():
                    continue
                self._emit_text_chunks(
                    text=seg.text,
                    block_type=BlockType.TEXT,
                    sub_type=None,
                    format=DataFormat.MARKDOWN,
                    parent_block_index=container_index,
                )
            elif seg.kind == "image":
                if image_block := self._build_image_block(
                    seg.alt_text,
                    parent_block_index=container_index,
                    markdown_source=row_markdown,
                ):
                    self._append_block_only(image_block)

    # ------------------------------------------------------------------ text

    def _emit_text_block(self, node: LexborNode, sub_type: BlockSubType) -> None:
        """Emit a TEXT block, splitting embedded images into child blocks when present.

        Delegates to ``_element_to_segments`` and ``_emit_with_image_splits`` so
        paragraphs, headings, and similar block elements follow the same
        container / fragment / IMAGE structure as the markdown parser.
        """
        segments, data_format, markdown_source, image_nodes = _element_to_segments(node)
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=sub_type,
            format=data_format,
            segments=segments,
            markdown_source=markdown_source,
            image_nodes=image_nodes,
        )

    def _emit_inline_block(self, node: LexborNode) -> None:
        """Emit a standalone paragraph for orphaned inline elements.

        Handles bare ``<a>``, ``<strong>``, etc. that appear outside a block
        wrapper in malformed or CMS-generated HTML.
        """
        html = node.html.strip()
        if not html:
            return
        segments, data_format, markdown_source = _html_fragment_to_segments(html)
        if not segments:
            return
        self._emit_with_image_splits(
            block_type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=data_format,
            segments=segments,
            markdown_source=markdown_source,
        )

    # ------------------------------------------------------------------ image

    def _emit_image(self, node: LexborNode) -> None:
        """Emit a standalone IMAGE block when a base64 ``data:image`` URI is available.

        Used for top-level ``<img>`` tags outside a text container. Split children
        inside paragraphs, lists, and tables are emitted via ``_build_image_block``
        from ``_emit_with_image_splits`` instead.
        """
        if image_block := self._build_image_block(
            (node.attributes or {}).get("alt", "").strip(),
            img_node=node,
        ):
            self._add_block(image_block)

    # ------------------------------------------------------------------ table

    def _process_table(self, table_node: LexborNode) -> None:
        """Normalize a ``<table>`` and emit TABLE_ROW blocks with metadata.

        Rows without images become single ``TABLE_ROW`` blocks with full
        ``row_natural_language_text`` and ``cells`` data. Rows containing inline
        images are split into an empty container ``TABLE_ROW`` plus TEXT/IMAGE
        children (see ``_emit_table_row_with_image_splits``).

        Rows are appended directly to ``self.blocks`` (not via ``_add_block``)
        because the table group wires row indices in ``group.children`` and
        then pops itself off the stack without propagating row indices upward.
        """
        self._open_group(GroupType.TABLE)
        open_group = self.group_stack[-1]
        group = self.block_groups[open_group.index]

        normalized = HtmlTableNormalizer().normalize(table_node)
        headers = [
            _strip_inline_images_from_markdown(header)
            for header in normalized.column_headers
        ]
        caption = _table_caption(table_node)
        body_rows = normalized.body_rows

        row_block_indices: list[int] = []
        for row_number, row_cells in enumerate(body_rows, start=1):
            table_cells = [
                _TableCell(plain=cell, markdown=cell) for cell in row_cells
            ]
            row_markdown = " ".join(row_cells)
            if self._row_has_images(table_cells):
                self._emit_table_row_with_image_splits(
                    group_index=group.index,
                    row_number=row_number,
                    headers=headers,
                    row_cells=table_cells,
                    row_block_indices=row_block_indices,
                    row_markdown=row_markdown,
                )
                continue

            block = Block(
                id=str(uuid4()),
                index=len(self.blocks),
                type=BlockType.TABLE_ROW,
                format=DataFormat.JSON,
                parent_index=group.index,
                data={
                    "row_natural_language_text": _format_table_row(headers, row_cells),
                    "row_number": row_number,
                    "cells": row_cells,
                },
            )
            self.blocks.append(block)
            row_block_indices.append(block.index)

        num_cols = normalized.num_cols
        group.table_metadata = TableMetadata(
            num_of_rows=len(body_rows),
            num_of_cols=num_cols,
            num_of_cells=sum(len(row) for row in body_rows),
            has_header=normalized.has_header,
            column_names=headers or None,
            captions=[caption] if caption else [],
        )
        group.data = {"table_summary": caption, "column_headers": headers}
        group.children = BlockGroupChildren.from_indices(
            block_indices=row_block_indices,
            block_group_indices=open_group.child_group_indices,
        )
        self.group_stack.pop()
