"""Typed view contracts shared by navigator, resolver, and views.

These models define the shape of data produced by the navigator/resolver
and consumed by the flat-text renderer in views.py.
No I/O — pure Pydantic models.
"""

from __future__ import annotations

from pydantic import BaseModel


class NodeRef(BaseModel):
    """Minimal identity reference for a graph node."""

    id: str
    name: str
    node_type: str          # app | recordGroup | folder | record
    sub_type: str | None    # e.g. TICKET, CONFLUENCE_PAGE, PROJECT, COLLECTION
    is_record: bool         # True when node_type is record or folder

    @property
    def display_id_label(self) -> str:
        """Label to use when printing the node id in output."""
        return "Record ID" if self.is_record else "Node ID"


class NodeRow(NodeRef):
    """A row in a listing — extends NodeRef with display detail."""

    has_children: bool
    detail: str | None      # e.g. size, indexing status, relationship type
    web_url: str | None = None
    indexing_status: str | None = None
    # Populated only when navigate() was called with depth >= 2: this row's
    # own children, fetched and rendered inline instead of requiring a
    # separate navigate() call. None at depth=1 (default) — distinct from an
    # empty list, which means "fetched, but this node has no children".
    children: list["NodeRow"] | None = None
    # One-line condensed metadata (e.g. "Status: In Progress, Assignee: Dana")
    # for a record row rendered at depth >= 2, where the full
    # `Record.to_llm_context()` block would be too expensive to inline for
    # every node in the subtree. None at depth=1, where the *current* node
    # already gets the full block via `NavigationView.context_block`.
    context_summary: str | None = None
    # True when this row's own `children` list was cut short by the
    # per-level cap at depth >= 2 (more children exist than were fetched) —
    # lets the renderer point the model at navigate(id) for the rest.
    children_truncated: bool = False
    # Total number of children this row has, when known from the same
    # provider call that fetched `children` (depth >= 2 only) — lets the
    # renderer say "+2 more" instead of just "more exist".
    children_total: int | None = None
    # Source-system timestamps (epoch ms), when known — e.g. when a file was
    # created/modified in Google Drive, not when PipesHub indexed it. None
    # when the underlying NodeItem/dict didn't carry one (e.g. related-record
    # rows from get_linked_records, which don't project timestamps).
    source_created_at: int | None = None
    source_modified_at: int | None = None


class PaginationInfo(BaseModel):
    """Pagination envelope."""

    page: int
    limit: int
    total: int
    has_next: bool
    has_prev: bool


class NavigationView(BaseModel):
    """The complete view for one navigate() call."""

    current: NodeRef | None         # None at root
    breadcrumbs: list[NodeRef]      # root → parent (not including current)
    rows: list[NodeRow]             # children / items to display
    related: list[NodeRow]          # cross-reference edges (non-containment)
    pagination: PaginationInfo | None
    web_url: str | None
    indexing_status: str | None
    connector: str | None
    # Record.to_llm_context() for the current node when it is a record —
    # the type-specific metadata (ticket status, assignee, priority, dates)
    # the node-access projection does not carry. Same field and rendering
    # as LookupMatch.context_block.
    context_block: str | None = None


class LookupMatch(BaseModel):
    """A single resolved record from lookup_record."""

    id: str                         # record _key (= Record ID)
    name: str
    record_type: str | None
    connector_name: str | None
    web_url: str | None
    indexing_status: str | None
    identifier_used: str            # which input identifier produced this
    external_id: str | None = None      # PA-1787, Confluence page id, Drive file id
    context_block: str | None = None    # Record.to_llm_context() output


class LookupResult(BaseModel):
    """Complete result of one lookup_record() call."""

    matches: list[LookupMatch]
    ambiguous: bool                 # True when >1 accessible record for same identifier
    not_found_identifiers: list[str]
    # Per-identifier labels of what was searched (for actionable miss messages)
    searched_connectors: dict[str, list[str]] = {}
