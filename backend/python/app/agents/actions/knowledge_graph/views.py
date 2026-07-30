"""Pure flat-text renderer for NavigationView and LookupResult.

No I/O — takes models and produces strings. Deterministic and snapshot-testable.

Design goals:
- Self-teaching: the output tells the model exactly what to call next.
- Token-efficient: no repetition, caps on every collection.
- Small-model safe: one output grammar, Record ID always labelled.

`navigate(depth=2|3)` inlines 1-2 extra levels of children under each row
(see `NodeRow.children` and `navigator.GraphNavigator._expand_depth`) —
rendered here as an indented nested listing via `_render_children_lines`,
with condensed per-row metadata (`NodeRow.context_summary`) so the model
sees a hierarchy overview (e.g. an epic, its stories, and their statuses)
without a separate navigate() call per level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import LookupMatch, LookupResult, NavigationView, NodeRef, NodeRow

if TYPE_CHECKING:
    from app.utils.chat_helpers import RecordIdShortener

_MAX_NAME_LEN = 80
# Raised alongside navigate()'s default/max `limit` (see navigator.py's
# _MAX_LIMIT) so a full page of 50-100 condensed rows renders without
# hitting the hard-truncation fallback below.
_MAX_RESPONSE_BYTES = 12_000
# Cap on ids named in one read hint. Matches `build_candidates`' max_candidates
# so both paths offer the model the same number of records to read at once.
_MAX_READ_HINT_IDS = 8
_TRUNCATION_LINE = "... (output truncated — use page= to continue)"


def _trunc(name: str, n: int = _MAX_NAME_LEN) -> str:
    if len(name) <= n:
        return name
    return name[: n - 1] + "…"


def _compact_date(epoch_ms: int | None) -> str | None:
    """`YYYY-MM-DD` for a source timestamp — date only (no time) to keep
    each row cheap in tokens; the full timestamp is already available via
    `fetch_record`/`Record.to_llm_context()` for whichever row the model
    reads next. None/0 (missing-timestamp sentinel — see
    `navigator._node_item_to_row`) renders nothing."""
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _short(node_id: str, shortener: "RecordIdShortener | None") -> str:
    """Shorten `node_id` via `shortener` when one is supplied (TEMPORARY
    token-savings experiment — see `RecordIdShortener`), else pass through
    unchanged. Every id this renderer prints goes through this so the
    model only ever sees one id per record, no matter which tool call
    surfaced it first."""
    return shortener.get_or_create_short_id(node_id) if shortener is not None else node_id


def _read_hint(record_ids: list[str], shortener: "RecordIdShortener | None" = None) -> str:
    """The read step of find → navigate → read.

    `knowledgegraph__fetch_record` is registered as soon as one of these tools
    reports a Record ID (see `hooks/citations.py`), so naming it here is a
    call the model can actually make on its next turn.
    """
    ids = ", ".join(f'"{_short(rid, shortener)}"' for rid in record_ids)
    return f"knowledgegraph__fetch_record(record_ids=[{ids}]) to read the content"


def _row_line(
    row: NodeRow,
    shortener: "RecordIdShortener | None" = None,
    indent: int = 0,
    *,
    collapse_children: bool = False,
) -> str:
    """Render one listing row, indented `indent` levels for nested
    depth>=2 children (see `_render_children_lines`).

    `context_summary` (condensed status/assignee/priority/... — see
    `navigator._attach_context_summaries`) is only ever set at depth >= 2;
    at depth=1 rows never carry it and this is a no-op, same output as
    before this parameter existed.

    `collapse_children`: the byte-budget fallback in
    `render_navigation_view` re-renders a too-large depth>=2 listing
    without descending into any row's `children` at all — but the row
    itself still HAS children, so the "has children" flag must be forced
    back on (normally suppressed once `row.children` is populated) or the
    fallback would silently claim a container is a leaf.
    """
    id_label = row.display_id_label.lower().replace(" ", "_")  # record_id or node_id
    parts = [f"[{row.node_type}/{row.sub_type or '?'}]", _trunc(row.name)]
    parts.append(f"| {id_label}={_short(row.id, shortener)}")
    created_label = _compact_date(row.source_created_at)
    if created_label:
        parts.append(f"| created: {created_label}")
    if row.context_summary:
        parts.append(f"| {row.context_summary}")
    if row.detail:
        parts.append(f"| {row.detail}")
    # Suppressed once children are actually inlined (row.children is not
    # None) — the flag would otherwise repeat information the nested
    # listing right below the row already shows. Forced back on when the
    # caller collapsed rendering for the byte-budget fallback.
    if row.has_children and (row.children is None or collapse_children):
        parts.append("| has children")
    return "  " * indent + "- " + " ".join(parts)


def _render_children_lines(
    row: NodeRow,
    shortener: "RecordIdShortener | None" = None,
    indent: int = 1,
    max_nested_depth: int | None = None,
) -> list[str]:
    """Recursively render `row`'s own nested children (populated by
    depth >= 2 — see `navigator.GraphNavigator._expand_depth`), indented
    one level under their parent. A `children_truncated` row (more
    children exist than the depth-expansion's per-parent cap fetched)
    gets a one-line "+N more" hint pointing back at a plain navigate()
    call for the rest, instead of silently dropping them.

    `max_nested_depth` caps how many further nested levels get rendered
    below `row.children` itself (`None` = unlimited, `0` = render
    `row.children` but not their own grandchildren) — used by
    `render_navigation_view`'s byte-budget fallback to drop the deepest
    level of a too-large listing first, rather than hard-truncating mid
    line. A level dropped this way still says so explicitly rather than
    disappearing silently.
    """
    if row.children is None:
        return []
    lines: list[str] = []
    render_deeper = max_nested_depth is None or max_nested_depth > 0
    next_max = None if max_nested_depth is None else max_nested_depth - 1
    for child in row.children:
        lines.append(_row_line(child, shortener, indent=indent, collapse_children=not render_deeper))
        if render_deeper:
            lines.extend(_render_children_lines(child, shortener, indent=indent + 1, max_nested_depth=next_max))
        elif child.children:
            lines.append(
                "  " * (indent + 1)
                + "(nested children omitted to fit response size — "
                f'navigate(node_id="{_short(child.id, shortener)}", depth=2) to see them)'
            )
    if row.children_truncated:
        remaining = max((row.children_total or 0) - len(row.children), 0)
        more = f"+{remaining} more" if remaining else "more"
        lines.append(
            "  " * indent
            + f'{more} children (navigate(node_id="{_short(row.id, shortener)}") for full list)'
        )
    return lines


def _render_children_block(
    rows: list[NodeRow],
    shortener: "RecordIdShortener | None" = None,
    max_nested_depth: int | None = None,
) -> list[str]:
    """Every top-level row plus its nested children, at a given rendering
    depth (see `_render_children_lines`). `max_nested_depth=-1` collapses
    ALL nesting — including `rows` themselves showing `| has children`
    instead of their inlined listing — the last fallback tier before the
    unconditional hard byte-truncation in `render_navigation_view`."""
    collapse_top = max_nested_depth == -1
    lines: list[str] = []
    for row in rows:
        lines.append(_row_line(row, shortener, collapse_children=collapse_top))
        if not collapse_top:
            lines.extend(_render_children_lines(row, shortener, indent=1, max_nested_depth=max_nested_depth))
    return lines


# ---------------------------------------------------------------------------
# navigate() output
# ---------------------------------------------------------------------------


def render_navigation_view(
    view: NavigationView,
    page: int,
    shortener: "RecordIdShortener | None" = None,
) -> str:
    """Render a NavigationView to the flat-text format the agent sees.

    page > 1 omits header / breadcrumbs / related to save tokens.
    Record nodes print their full `Record.to_llm_context()` block and a
    `Next:` hint for reading their content, so a walk down a hierarchy ends
    somewhere the model can act on rather than at a bare id.
    Response is byte-capped at _MAX_RESPONSE_BYTES.

    `shortener` (TEMPORARY token-savings experiment — see
    `RecordIdShortener`) replaces every id this function prints with a
    short `R<n>` label; `context_block` (pre-rendered `Record ID: <id>`
    text) is shortened via its regex-based text path instead.
    """
    lines: list[str] = []

    if page == 1:
        # Breadcrumbs line
        if view.breadcrumbs or view.current:
            crumb_parts = []
            for bc in view.breadcrumbs:
                crumb_parts.append(f"{bc.name} ({bc.node_type}, id={_short(bc.id, shortener)})")
            if view.current:
                crumb_parts.append(
                    f"{view.current.name} ({view.current.node_type}, id={_short(view.current.id, shortener)})"
                )
            lines.append("Path: " + " > ".join(crumb_parts))
        else:
            lines.append("Path: (root)")

        # Current node header — `context_block` (Record.to_llm_context(),
        # carrying the type-specific `* Status`/`* Assignee`/`* Priority`
        # lines) supersedes the identity-only header when the navigator
        # could load the record. Same precedence as `_render_match`.
        if view.current:
            cur = view.current
            if view.context_block:
                context_block = view.context_block
                if shortener is not None:
                    context_block = shortener.shorten_record_ids_in_text(context_block)
                lines.append(context_block)
                if view.indexing_status and view.indexing_status != "COMPLETED":
                    lines.append(f"Indexed: {view.indexing_status}")
            else:
                lines.append(f"Node: {_trunc(cur.name)}")
                type_str = f"{cur.node_type}/{cur.sub_type}" if cur.sub_type else cur.node_type
                connector_str = f"Connector: {view.connector}" if view.connector else ""
                indexed_str = f"Indexed: {view.indexing_status}" if view.indexing_status else ""
                meta = " | ".join(filter(None, [f"Type: {type_str}", connector_str, indexed_str]))
                if meta:
                    lines.append(meta)
                lines.append(f"{cur.display_id_label}: {_short(cur.id, shortener)}")
                if view.web_url:
                    lines.append(f"URL: {view.web_url}")

    # Children listing
    pag = view.pagination
    if view.rows:
        if pag:
            shown_start = (pag.page - 1) * pag.limit + 1
            shown_end = shown_start + len(view.rows) - 1
            total_label = f" of {pag.total}" if pag.total > 0 else ""
            lines.append(f"\nChildren {shown_start}-{shown_end}{total_label}:")
        else:
            lines.append("\nChildren:")
        # Depth>=2 listings can carry a lot more data than a flat one —
        # try full nesting first, then drop the deepest level, then all
        # nesting, picking the first tier that fits the byte budget
        # alongside everything rendered so far (header/breadcrumbs/etc).
        # Only reached when depth>=2 actually populated `.children`;
        # depth=1 output is identical to before (single, unconditional
        # `_render_children_block(rows, max_nested_depth=None)` pass).
        budget_remaining = _MAX_RESPONSE_BYTES - len("\n".join(lines).encode("utf-8"))
        children_lines = _render_children_block(view.rows, shortener, max_nested_depth=None)
        if len("\n".join(children_lines).encode("utf-8")) > budget_remaining:
            for candidate_depth in (0, -1):
                candidate_lines = _render_children_block(view.rows, shortener, max_nested_depth=candidate_depth)
                if len("\n".join(candidate_lines).encode("utf-8")) <= budget_remaining or candidate_depth == -1:
                    children_lines = candidate_lines
                    break
        lines.extend(children_lines)
    elif view.current:
        lines.append("\n(no children)")
    else:
        lines.append("\n(no items at root — no connected apps or no access)")

    # Related (page 1 only)
    if page == 1 and view.related:
        lines.append("\nRelated:")
        for row in view.related:
            lines.append(_row_line(row, shortener))

    # Next-step hints
    hints: list[str] = []
    # Name the child rows, not just `view.current`: the children are what the
    # caller navigated here to discover, and this listing gives their ids and
    # metadata but nothing they say. Hinting at `view.current` alone let a walk
    # down a hierarchy accumulate ids and never read any content. Every id here
    # is already fetchable — `_record_ids_in_view` remembered it. `view.current`
    # is page 1 only (same record on every page); rows go on every page, since
    # each page shows different ones.
    readable: list[str] = []
    if page == 1 and view.current and view.current.is_record and view.current.id:
        readable.append(view.current.id)
    for row in view.rows:
        if row.is_record and row.id and row.id not in readable:
            readable.append(row.id)
    if readable:
        hints.append(_read_hint(readable[:_MAX_READ_HINT_IDS], shortener))
    if view.rows:
        first_row = view.rows[0]
        id_arg = f'node_id="{_short(first_row.id, shortener)}"'
        hints.append(f'navigate({id_arg}) to open a child')
    # Points back UP the tree — `view.breadcrumbs` is root→parent (not
    # including `current`), so the last entry is the immediate parent.
    # Page 1 only: breadcrumbs aren't recomputed on later pages, and the
    # sibling set doesn't change page to page anyway.
    if page == 1 and view.breadcrumbs:
        parent = view.breadcrumbs[-1]
        hints.append(
            f'navigate(node_id="{_short(parent.id, shortener)}") to see siblings'
        )
    if pag and pag.has_next and view.current:
        hints.append(
            f'navigate(node_id="{_short(view.current.id, shortener)}", page={pag.page + 1}) for more'
        )
    if pag and pag.has_prev and view.current and page > 2:
        hints.append(
            f'navigate(node_id="{_short(view.current.id, shortener)}", page={pag.page - 1}) to go back'
        )
    if hints:
        lines.append("\nNext: " + " | ".join(hints))

    result = "\n".join(lines)
    encoded = result.encode("utf-8")
    if len(encoded) > _MAX_RESPONSE_BYTES:
        result = encoded[:_MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore") + "\n" + _TRUNCATION_LINE
    return result


# ---------------------------------------------------------------------------
# lookup_record() output
# ---------------------------------------------------------------------------


def _render_match(m: LookupMatch, shortener: "RecordIdShortener | None" = None) -> list[str]:
    """Render one match: `context_block` (Record.to_llm_context() output —
    already includes Record ID, Name, External ID, Web URL, and any
    type-specific `* Status`/`* Priority`/`* Type`/`* Assignee` lines) when
    present, else the structured fallback for dict-shaped provider returns
    that never populated it."""
    lines: list[str] = []
    if m.context_block:
        context_block = m.context_block
        if shortener is not None:
            context_block = shortener.shorten_record_ids_in_text(context_block)
        lines.append(context_block)
    else:
        lines.append(f"Found: {_trunc(m.name)}")
        type_str = m.record_type or "?"
        connector_str = f"Connector: {m.connector_name}" if m.connector_name else ""
        meta = " | ".join(filter(None, [f"Type: {type_str}", connector_str]))
        if meta:
            lines.append(meta)
        lines.append(f"Record ID: {_short(m.id, shortener)}")
        if m.external_id:
            lines.append(f"External ID: {m.external_id}")
        if m.web_url:
            lines.append(f"URL: {m.web_url}")
    if m.indexing_status and m.indexing_status != "COMPLETED":
        lines.append(f"Indexed: {m.indexing_status}")
    lines.append(
        f'Next: navigate(node_id="{_short(m.id, shortener)}") to list children and linked records'
        f" | {_read_hint([m.id], shortener)}"
    )
    return lines


def render_lookup_result(result: LookupResult, shortener: "RecordIdShortener | None" = None) -> str:
    """Render a LookupResult to flat text.

    Found records are listed with their full `Record.to_llm_context()`
    block plus a self-teaching `Next:` hint (same pattern as
    `render_navigation_view`) so a hierarchical hit (epic, project, parent
    page) points the model straight at `navigate()` for its children.
    Misses include the connector types that were searched so the model
    can react intelligently (retry with connector_name=, or use navigate()).
    Denied and not-found produce byte-identical text (no info leak).
    Response is byte-capped at _MAX_RESPONSE_BYTES like navigate() output.

    `shortener` is the TEMPORARY token-savings `RecordIdShortener` — see
    `render_navigation_view`.
    """
    lines: list[str] = []

    if result.matches:
        for m in result.matches:
            lines.extend(_render_match(m, shortener))
            if len(result.matches) > 1:
                lines.append(f"Matched by: {m.identifier_used}")
            lines.append("")

    if result.ambiguous:
        lines.append(
            "Multiple accessible records matched one identifier — "
            "use navigate() to browse or add connector_name= to narrow."
        )

    if result.not_found_identifiers:
        for ident in result.not_found_identifiers:
            searched = result.searched_connectors.get(ident) or []
            if searched:
                # Show which connector types were searched so the model can
                # decide whether to retry with a different connector_name.
                searched_str = ", ".join(searched)
                lines.append(f"Not found (or no access): {ident}  [searched: {searched_str}]")
            else:
                lines.append(f"Not found (or no access): {ident}")

    text = "\n".join(lines).strip()
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_RESPONSE_BYTES:
        text = encoded[:_MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore") + "\n" + _TRUNCATION_LINE
    return text
