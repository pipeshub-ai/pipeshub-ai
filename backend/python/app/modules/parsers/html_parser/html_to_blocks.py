"""Convert HTML directly into BlocksContainer using Selectolax (Lexbor backend).

Block / group mapping (mirrors markdown_to_blocks.MarkdownToBlocksConverter):

    h1-h6            → Block(TEXT, HEADING, TXT|MARKDOWN)
                         (skipped when sr-only / aria-hidden)

    p / address /
    dt / dd /
    figcaption       → Block(TEXT, PARAGRAPH, TXT|MARKDOWN)
                         + Block(IMAGE) for each embedded <img>
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
                             in a cell → inner rows JSON-stringified and appended to cell text)
                         <caption> text stored in table_metadata.captions

    details          → <summary> → Block(TEXT, HEADING); other children processed normally

    hr               → Block(TEXT, DIVIDER, TXT)  data="---"
    img              → Block(IMAGE)  (relative src resolved via base_url;
                         caption_map alt → base64 uri)

    div / section /… → recurse into children, or emit Block(TEXT, PARAGRAPH) when
                       the node has text but no block-level descendants

    script / style / noscript / template / svg / meta / link / head → skipped
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urljoin
from uuid import uuid4

from selectolax.lexbor import LexborHTMLParser, LexborNode
from markdownify import markdownify

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
    "nav", "aside", "figure",
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


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------

@dataclass
class _OpenGroup:
    index: int
    child_block_indices: list[int] = field(default_factory=list)
    child_group_indices: list[int] = field(default_factory=list)


@dataclass
class NormalizedCell:
    """One slot in an expanded HTML table grid."""

    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    is_origin: bool = True


@dataclass
class NormalizedTable:
    """Collapsed table representation for block emission."""

    column_headers: list[str]
    body_rows: list[list[str]]
    num_cols: int
    num_body_rows: int
    has_header: bool

    def to_markdown(self) -> str:
        """Render this normalized table as a GitHub-flavoured markdown table string."""
        return normalized_table_to_markdown(self)


# ---------------------------------------------------------------------------
# DOM traversal utilities
# ---------------------------------------------------------------------------

def _direct_children(node: LexborNode) -> Iterator[LexborNode]:
    """Yield each direct child node of a DOM element in document order.
    Walks the sibling linked list starting at ``node.child`` until ``next`` is None.
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
    Checks for ``sr-only`` in the class list or ``aria-hidden="true"``.
    """
    attrs = node.attributes or {}
    class_attr = attrs.get("class", "")
    if isinstance(class_attr, list):
        class_attr = " ".join(class_attr)
    if "sr-only" in class_attr.split():
        return True
    return attrs.get("aria-hidden") in {"true", "True", True}


def _language_from_node(node: LexborNode | None) -> str | None:
    """Parse a code-block language from ``class="language-*"`` on the node."""
    if node is None:
        return None
    class_attr = (node.attributes or {}).get("class", "")
    if not class_attr:
        return None
    if isinstance(class_attr, list):
        class_attr = " ".join(class_attr)
    match = _LANGUAGE_CLASS_RE.search(class_attr)
    return match.group(1) if match else None


def _has_block_descendant(node: LexborNode, depth: int = 0) -> bool:
    """Return True if any descendant is a block-level or container element.
    Recurses into container tags; stops at depth 48 to avoid runaway traversal.
    """
    if depth > 48:
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


def _is_shallow_text_container(node: LexborNode) -> bool:
    """True when a container holds text but no nested block-level children."""
    return not _has_block_descendant(node) and bool(_node_text(node))


# ---------------------------------------------------------------------------
# Text extraction and markdown conversion
# ---------------------------------------------------------------------------

def _has_inline_formatting(node: LexborNode, depth: int = 0) -> bool:
    """Return True if the subtree contains inline formatting tags (bold, links, etc.).
    Skips block/container descendants; recurses into other inline elements.
    """
    if depth > 48:
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
    """Render a single inline element as plain text (``<br>`` → newline, ``<img>`` → empty)."""
    tag = _tag_name(node)
    if tag is None:
        return node.text(deep=False, strip=False)
    if tag == "br":
        return "\n"
    if tag == "img":
        return ""
    return node.text(deep=True, separator="").strip()


def _split_element_content(
    node: LexborNode,
    *,
    base_url: str = "",
) -> tuple[str, list[LexborNode], DataFormat]:
    """Split a block element into text, embedded ``<img>`` nodes, and TXT vs MARKDOWN format.
    Uses markdownify when inline formatting is present; otherwise concatenates plain text.
    """
    image_nodes: list[LexborNode] = []
    for child in _direct_children(node):
        if _tag_name(child) == "img":
            image_nodes.append(child)

    if _has_inline_formatting(node):
        return (
            _html_to_markdown(node.inner_html.strip(), base_url=base_url),
            image_nodes,
            DataFormat.MARKDOWN,
        )

    text_parts: list[str] = []
    for child in _direct_children(node):
        tag = _tag_name(child)
        if tag == "img":
            continue
        if tag is None:
            fragment = child.text(deep=False, strip=False)
            if fragment:
                text_parts.append(fragment)
        elif tag == "br":
            text_parts.append("\n")
        else:
            rendered = _inline_text(child)
            if rendered:
                text_parts.append(rendered)
    return "".join(text_parts).strip(), image_nodes, DataFormat.TXT


def _html_to_markdown(html: str, *, base_url: str = "") -> str:
    """Convert an HTML fragment to ATX-style markdown via markdownify.
    Resolves relative anchor hrefs against ``base_url`` before conversion.
    """
    if not html:
        return ""
    if base_url:
        html = _resolve_relative_links(html, base_url)
    return markdownify(html, heading_style="ATX").strip()


def _resolve_relative_links(html: str, base_url: str) -> str:
    """Rewrite relative ``<a href>`` values to absolute URLs using ``urljoin``.
    Parses the fragment in a wrapper div; leaves absolute, mailto, and hash links unchanged.
    """
    parser = LexborHTMLParser(f"<div>{html}</div>")
    wrapper = parser.css_first("div")
    if wrapper is None:
        return html

    for anchor in wrapper.css("a"):
        attrs = anchor.attributes or {}
        href = (attrs.get("href") or "").strip()
        if not href:
            continue
        if href.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        anchor.attributes["href"] = urljoin(base_url, href)

    return wrapper.inner_html


# ---------------------------------------------------------------------------
# Table grid utilities
# ---------------------------------------------------------------------------

def _table_caption(table_node: LexborNode) -> str:
    """Extract the stripped text of the first direct ``<caption>`` child, or empty string."""
    for child in _direct_children(table_node):
        if _tag_name(child) == "caption":
            return _node_text(child)
    return ""


def _table_section_rows(
    table_node: LexborNode,
) -> tuple[list[LexborNode], list[LexborNode]]:
    """Split a ``<table>`` into header and body ``<tr>`` node lists.
    Uses explicit ``<thead>``/``<tbody>`` when present; otherwise infers headers from leading ``<th>`` rows.
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
    """Return direct ``<tr>`` children of a table section (``thead``, ``tbody``, etc.)."""
    return [child for child in _direct_children(section_node) if _tag_name(child) == "tr"]


def _count_leading_header_rows(row_nodes: list[LexborNode]) -> int:
    """Count consecutive leading rows that contain at least one ``<th>`` cell."""
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
    """Parse ``rowspan``/``colspan`` attribute as a positive integer (default 1)."""
    raw = attrs.get(name, 1)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _pad_grid(grid: list[list[NormalizedCell]]) -> list[list[NormalizedCell]]:
    """Pad every row in a grid to the widest row's column count with empty cells."""
    if not grid:
        return []
    width = max(len(row) for row in grid)
    return _pad_grid_to_width(grid, width)


def _pad_grid_to_width(
    grid: list[list[NormalizedCell]],
    width: int,
) -> list[list[NormalizedCell]]:
    """Extend each grid row with empty origin cells until it reaches ``width`` columns."""
    if not grid:
        return []
    for row in grid:
        while len(row) < width:
            row.append(NormalizedCell(text="", is_origin=True))
    return grid


def _grid_width(*grids: list[list[NormalizedCell]]) -> int:
    """Return the maximum row length across one or more normalized cell grids."""
    widths = [len(row) for grid in grids for row in grid]
    return max(widths) if widths else 0


def _column_groups(
    header_grid: list[list[NormalizedCell]],
    width: int,
) -> list[tuple[int, int]]:
    """Derive logical column ranges from the top header row's colspan origins.
    Each group is ``(start_col, end_col)``; falls back to one column per index when no header grid.
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
    Joins same-row parts with `` | ``, stacks header levels with newlines, and deduplicates repeats.
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
    """Collapse expanded body grid rows into output rows with merged logical columns.
    Groups consecutive HTML rows by the first column's rowspan, then formats each column group.
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
    """How many HTML rows merge into one collapsed output row.
    Only the first column's rowspan drives grouping so middle-column spans do not fold unrelated rows.
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
    """Format one logical column across one or more HTML body rows.
    Single-column groups delegate to ``_collapse_single_column_cell``; multi-column groups join parts with `` | ``.
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
    """Format a single-column logical group, handling row-spanning labels and stacked values.
    Rowspanning origin cells win outright; otherwise first-column labels or newline-joined values are returned.
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
    """Build a natural-language row string as ``Header: value`` pairs joined by commas."""
    if headers:
        parts = [
            f"{headers[i] if i < len(headers) else f'Column {i + 1}'}: {cell}"
            for i, cell in enumerate(cells)
        ]
        return ", ".join(parts)
    return ", ".join(cells)


def _escape_markdown_cell(value: str) -> str:
    """Escape pipe characters and newlines so a cell is safe inside a markdown table."""
    escaped = value.replace("|", "\\|").replace("\n", "<br>")
    return re.sub(r" +", " ", escaped).strip()


def _render_table_markdown(table: NormalizedTable) -> str:
    """Render a normalized table as a GitHub-flavoured markdown pipe table with header separator."""
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


def _stringify_nested_table_rows(table: NormalizedTable) -> str:
    """Serialize a nested table as a JSON array of row cell arrays (headers first, then body).
    Trims trailing empty cells from each body row before encoding.
    """
    rows: list[list[str]] = []
    if table.column_headers:
        rows.append(table.column_headers)
    for body_row in table.body_rows:
        trimmed = list(body_row)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        rows.append(trimmed)
    if not rows:
        return ""
    return json.dumps(rows, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Table normalizer
# ---------------------------------------------------------------------------

class HtmlTableNormalizer:
    """Collapse an HTML table into logical column rows for block emission.

    Nested ``<table>`` elements are normalized recursively and their collapsed
    row arrays are JSON-stringified into the parent cell text.
    Inline / block markup inside cells is converted to markdown.
    """

    def normalize(self, table_node: LexborNode) -> NormalizedTable:
        """Expand rowspan/colspan into a grid, then collapse headers and body into logical columns."""
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
            text=self._cell_content(cell_node),
            rowspan=_span_int(attrs, "rowspan"),
            colspan=_span_int(attrs, "colspan"),
            is_header=is_header or tag == "th",
        )

    def _cell_content(self, cell_node: LexborNode) -> str:
        """Build cell text from child nodes; nested ``<table>`` values are JSON-stringified rows.
        Inline/block markup is converted to markdown; plain text nodes are appended as-is.
        """
        parts: list[str] = []
        for child in _direct_children(cell_node):
            tag = _tag_name(child)
            if tag == "table":
                serialized = _stringify_nested_table_rows(self.normalize(child))
                if serialized:
                    parts.append(serialized)
            elif tag is None:
                text = child.text(deep=False, strip=False).strip()
                if text:
                    parts.append(text)
            else:
                markdown = _html_to_markdown(child.html)
                if markdown:
                    parts.append(markdown)
        return "\n".join(parts).strip()


def normalize_html_table(table_node: LexborNode) -> NormalizedTable:
    """Public entry: normalize one ``<table>`` DOM node into collapsed logical-column rows."""
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


# ---------------------------------------------------------------------------
# Public converter
# ---------------------------------------------------------------------------

class HtmlToBlocksConverter:
    """Convert HTML content directly into BlocksContainer."""

    def convert(
        self,
        html_content: str,
        *,
        base_url: str | None = None,
        caption_map: dict[str, str] | None = None,
    ) -> BlocksContainer:
        """Parse HTML with Lexbor and walk the DOM tree into a ``BlocksContainer``."""
        parser = LexborHTMLParser(html_content)
        root = parser.body or parser.root
        if root is None:
            return BlocksContainer()
        walker = _DomWalker(base_url=base_url, caption_map=caption_map)
        return walker.walk(root)


# ---------------------------------------------------------------------------
# DOM walker
# ---------------------------------------------------------------------------

class _DomWalker:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        caption_map: dict[str, str] | None = None,
    ) -> None:
        """Initialize block/group accumulators and optional URL and image-caption lookup maps."""
        self.base_url = base_url or ""
        self.caption_map = caption_map or {}
        self.blocks: list[Block] = []
        self.block_groups: list[BlockGroup] = []
        self.group_stack: list[_OpenGroup] = []

    # ------------------------------------------------------------------ traversal

    def walk(self, root: LexborNode) -> BlocksContainer:
        """Traverse the DOM from ``root`` and return the collected blocks and block groups."""
        self._walk_children(root)
        return BlocksContainer(blocks=self.blocks, block_groups=self.block_groups)

    def _walk_children(self, parent: LexborNode) -> None:
        """Dispatch each direct child of ``parent`` through ``_process_node``."""
        for child in _direct_children(parent):
            self._process_node(child)

    def _process_node(self, node: LexborNode) -> None:  # noqa: C901
        """Route one DOM node to the correct block emitter based on its HTML tag.
        Skips hidden/utility tags; recurses into containers; maps semantic tags to block types.
        """
        tag = _tag_name(node)
        if not tag or tag in _SKIP_TAGS:
            return

        if tag in _HEADING_TAGS:
            if not _is_hidden(node):
                self._emit_text_block(node, BlockSubType.HEADING)
            return

        if tag in {"p", "address", "dt", "dd", "figcaption"}:
            self._emit_text_block(node, BlockSubType.PARAGRAPH)
            return

        if tag == "pre":
            if node.css_first("code") is not None:
                # Each <code> child gets its own BlockGroup(CODE) + Block.
                # Other elements (lists, tables, blockquotes) inside <pre>
                # are processed exactly as if they appeared at the top level.
                self._walk_children(node)
            else:
                # <pre> with no <code> children — treat entire content as
                # a single code block (e.g. <pre>bare text</pre>).
                content = node.text(deep=True, separator="").rstrip("\n")
                if content:
                    self._emit_code_group(content, _language_from_node(node))
            return
        if tag == "code":
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
            self._process_details(node)
            return

        if tag == "summary":
            self._emit_text_block(node, BlockSubType.HEADING)
            return

        if tag == "hr":
            self._add_block(Block(
                id=str(uuid4()),
                index=0,
                type=BlockType.TEXT,
                sub_type=BlockSubType.DIVIDER,
                format=DataFormat.TXT,
                data="---",
                parent_index=self._current_parent_index(),
            ))
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
                self._walk_children(node)
            return

        self._walk_children(node)

    # ------------------------------------------------------------------ group management

    def _current_parent_index(self) -> int | None:
        """Return the index of the innermost open block group, or None at the root."""
        return self.group_stack[-1].index if self.group_stack else None

    def _open_group(
        self,
        group_type: GroupType,
        sub_type: GroupSubType | None = None,
    ) -> None:
        """Push a new block group onto the stack and link it to the current parent group."""
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
        """Pop the top open group and attach its collected child block/group indices."""
        if not self.group_stack:
            return
        open_group = self.group_stack.pop()
        group = self.block_groups[open_group.index]
        group.children = BlockGroupChildren.from_indices(
            block_indices=open_group.child_block_indices,
            block_group_indices=open_group.child_group_indices,
        )

    def _add_block(self, block: Block) -> Block:
        """Append a block with the next index and register it under the current open group."""
        block.index = len(self.blocks)
        self.blocks.append(block)
        if self.group_stack:
            self.group_stack[-1].child_block_indices.append(block.index)
        return block

    # ------------------------------------------------------------------ lists

    def _process_list(self, list_node: LexborNode, *, ordered: bool) -> None:
        """Open a LIST or ORDERED_LIST group and emit one LIST_ITEM block per ``<li>``."""
        group_type = GroupType.ORDERED_LIST if ordered else GroupType.LIST
        self._open_group(group_type)
        for child in _direct_children(list_node):
            tag = _tag_name(child)
            if tag == "li":
                self._process_list_item(child)
            elif tag in _CONTAINER_TAGS:
                self._emit_list_items_from_container(child)
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

    def _process_list_item(self, li_node: LexborNode) -> None:
        """Emit one LIST_ITEM block with the ``<li>`` inner HTML converted to markdown."""
        html = li_node.inner_html.strip()
        if not html:
            return
        self._add_block(Block(
            id=str(uuid4()),
            index=0,
            type=BlockType.TEXT,
            sub_type=BlockSubType.LIST_ITEM,
            format=DataFormat.MARKDOWN,
            data=_html_to_markdown(html, base_url=self.base_url),
            parent_index=self._current_parent_index(),
        ))

    # ------------------------------------------------------------------ code

    def _emit_code_group(self, content: str, language: str | None) -> None:
        """Wrap bare ``<code>``/``<pre>`` content in a CODE group with language metadata."""
        if not content:
            return
        self._open_group(GroupType.CODE)
        self._add_block(Block(
            id=str(uuid4()),
            index=0,
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
        """Open a QUOTE group and store the blockquote inner HTML as one markdown block."""
        self._open_group(GroupType.TEXT_SECTION, GroupSubType.QUOTE)
        html = node.inner_html.strip()
        if html:
            self._add_block(Block(
                id=str(uuid4()),
                index=0,
                type=BlockType.TEXT,
                sub_type=BlockSubType.QUOTE,
                format=DataFormat.MARKDOWN,
                data=_html_to_markdown(html, base_url=self.base_url),
                parent_index=self._current_parent_index(),
            ))
        self._close_group()

    def _process_details(self, node: LexborNode) -> None:
        """Emit ``<summary>`` as a heading, then process all other ``<details>`` children normally."""
        for child in _direct_children(node):
            if _tag_name(child) == "summary":
                self._emit_text_block(child, BlockSubType.HEADING)
                break

        for child in _direct_children(node):
            if _tag_name(child) != "summary":
                self._process_node(child)

    # ------------------------------------------------------------------ text

    def _emit_text_block(self, node: LexborNode, sub_type: BlockSubType) -> None:
        """Emit a TEXT block (heading, paragraph, etc.) plus any embedded ``<img>`` children."""
        text, image_nodes, data_format = _split_element_content(node, base_url=self.base_url)
        if text:
            self._add_block(Block(
                id=str(uuid4()),
                index=0,
                type=BlockType.TEXT,
                sub_type=sub_type,
                format=data_format,
                data=text,
                parent_index=self._current_parent_index(),
            ))
        for img in image_nodes:
            self._emit_image(img)

    def _emit_inline_block(self, node: LexborNode) -> None:
        """Emit a standalone paragraph for orphaned inline elements (e.g. a bare ``<a>`` tag)."""
        html = node.html.strip()
        if not html:
            return
        text = _html_to_markdown(html, base_url=self.base_url)
        if not text:
            return
        self._add_block(Block(
            id=str(uuid4()),
            index=0,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            data=text,
            parent_index=self._current_parent_index(),
        ))

    # ------------------------------------------------------------------ image

    def _emit_image(self, node: LexborNode) -> None:
        """Emit an IMAGE block with resolved src URL and optional caption-map base64 URI."""
        attrs = node.attributes or {}
        alt_text = (attrs.get("alt") or "").strip()
        src = (attrs.get("src") or "").strip()
        if not src and attrs.get("srcset"):
            src = attrs["srcset"].split(",")[0].split()[0].strip()
        if src and self.base_url:
            src = urljoin(self.base_url, src)

        data: dict[str, str] | None = None
        if alt_text and alt_text in self.caption_map:
            data = {"uri": self.caption_map[alt_text]}
        elif src:
            data = {"url": src}

        self._add_block(Block(
            id=str(uuid4()),
            index=0,
            type=BlockType.IMAGE,
            format=DataFormat.BASE64 if data and "uri" in data else DataFormat.TXT,
            data=data,
            parent_index=self._current_parent_index(),
            image_metadata=ImageMetadata(captions=[alt_text] if alt_text else []),
        ))

    # ------------------------------------------------------------------ table

    def _process_table(self, table_node: LexborNode) -> None:
        """Normalize a ``<table>``, emit JSON TABLE_ROW blocks, and attach ``TableMetadata`` to the group."""
        self._open_group(GroupType.TABLE)
        open_group = self.group_stack[-1]
        group = self.block_groups[open_group.index]

        normalized = HtmlTableNormalizer().normalize(table_node)
        headers = normalized.column_headers
        caption = _table_caption(table_node)
        body_rows = normalized.body_rows

        row_block_indices: list[int] = []
        for row_number, row_cells in enumerate(body_rows, start=1):
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
        group.children = BlockGroupChildren.from_indices(
            block_indices=row_block_indices,
            block_group_indices=open_group.child_group_indices,
        )
        self.group_stack.pop()


# ---------------------------------------------------------------------------
# Debug / introspection
# ---------------------------------------------------------------------------

def get_parsed_tree(html_content: str) -> list[dict]:
    """Return a JSON-serializable tree of the parsed HTML for debugging and introspection."""
    parser = LexborHTMLParser(html_content)
    root = parser.body or parser.root
    if root is None:
        return []

    def _extract_node(node) -> dict:
        """Recursively convert one Lexbor node into a dict with tag, attrs, and children."""
        if node.tag == "-text":
            return {
                "type": "text",
                "content": (node.text() or "").strip(),
            }

        children = []
        for child in node.iter(include_text=True):
            if child.parent == node:
                extracted = _extract_node(child)
                # skip empty text nodes
                if extracted["type"] == "text" and not extracted["content"]:
                    continue
                children.append(extracted)

        return {
            "type": "element",
            "tag": node.tag,
            "attrs": dict(node.attributes) if node.attributes else {},
            "children": children,
        }

    return _extract_node(root)
