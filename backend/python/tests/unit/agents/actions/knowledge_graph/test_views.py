"""Unit tests for views.py — pure snapshot tests, no I/O.

Tests:
- root listing: no current node, no breadcrumbs
- app node listing: shows breadcrumbs, rows with node_id=
- recordGroup node: shows breadcrumbs, rows with record_id= or node_id=
- record node with children and related: header, breadcrumbs, rows, related
- page > 1: omits header/breadcrumbs/related, prints rows only
- empty listing: prints "(no children)"
- truncation: response byte-cap
- record row prints record_id=; container row prints node_id=
"""

from datetime import datetime, timezone

import pytest

from app.agents.actions.knowledge_graph.models import (
    NavigationView,
    NodeRef,
    NodeRow,
    PaginationInfo,
)
from app.agents.actions.knowledge_graph.views import render_navigation_view, render_lookup_result
from app.agents.actions.knowledge_graph.models import LookupMatch, LookupResult


def _ref(id: str, name: str, node_type: str, sub_type: str | None = None) -> NodeRef:
    return NodeRef(
        id=id,
        name=name,
        node_type=node_type,
        sub_type=sub_type,
        is_record=node_type in ("record", "folder"),
    )


def _row(
    id: str,
    name: str,
    node_type: str,
    sub_type: str | None = None,
    has_children: bool = False,
    detail: str | None = None,
    source_created_at: int | None = None,
    source_modified_at: int | None = None,
) -> NodeRow:
    return NodeRow(
        id=id,
        name=name,
        node_type=node_type,
        sub_type=sub_type,
        is_record=node_type in ("record", "folder"),
        has_children=has_children,
        detail=detail,
        source_created_at=source_created_at,
        source_modified_at=source_modified_at,
    )


def _pag(page: int = 1, limit: int = 20, total: int = 5, has_next: bool = False, has_prev: bool = False) -> PaginationInfo:
    return PaginationInfo(page=page, limit=limit, total=total, has_next=has_next, has_prev=has_prev)


class TestRootView:
    def test_root_shows_root_path(self):
        view = NavigationView(
            current=None,
            breadcrumbs=[],
            rows=[_row("app1", "Jira", "app", "JIRA")],
            related=[],
            pagination=_pag(total=1),
            web_url=None,
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "Path: (root)" in text
        assert "app1" in text
        assert "node_id=app1" in text

    def test_root_no_apps(self):
        view = NavigationView(
            current=None,
            breadcrumbs=[],
            rows=[],
            related=[],
            pagination=None,
            web_url=None,
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "Path: (root)" in text
        assert "no items" in text.lower()


class TestAppView:
    def test_app_shows_node_id_for_container(self):
        view = NavigationView(
            current=_ref("app1", "Jira", "app", "JIRA"),
            breadcrumbs=[],
            rows=[
                _row("rg1", "Payments Project", "recordGroup", "PROJECT"),
                _row("rg2", "Auth Project", "recordGroup", "PROJECT"),
            ],
            related=[],
            pagination=_pag(total=2),
            web_url="https://pipeshub.atlassian.net",
            indexing_status=None,
            connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        # App node itself: node_id label
        assert "Node ID: app1" in text
        # Record group rows: also node_id label (containers)
        assert "node_id=rg1" in text
        assert "node_id=rg2" in text


class TestRecordGroupView:
    def test_record_group_record_rows_use_record_id(self):
        view = NavigationView(
            current=_ref("rg1", "Payments Project", "recordGroup", "PROJECT"),
            breadcrumbs=[_ref("app1", "Jira", "app", "JIRA")],
            rows=[
                _row("rec1", "PA-1700 Billing Epic", "record", "TICKET", has_children=True),
                _row("rec2", "PA-1701 Auth Epic", "record", "TICKET"),
            ],
            related=[],
            pagination=_pag(total=2),
            web_url=None,
            indexing_status=None,
            connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        # Breadcrumb should include the parent
        assert "Jira" in text
        # Record rows use record_id
        assert "record_id=rec1" in text
        assert "record_id=rec2" in text
        # has_children flag
        assert "has children" in text


class TestRecordViewWithRelated:
    def test_record_shows_header_breadcrumbs_related_on_page1(self):
        view = NavigationView(
            current=_ref("rec1", "PA-1787 Payment outage", "record", "TICKET"),
            breadcrumbs=[
                _ref("app1", "Jira", "app", "JIRA"),
                _ref("rg1", "Payments Project", "recordGroup", "PROJECT"),
            ],
            rows=[
                _row("child1", "PA-1801 Fix retry logic", "record", "TICKET", has_children=True),
            ],
            related=[
                _row("ext1", "Agent Loop Implementation", "record", "CONFLUENCE_PAGE", detail="LINKED_TO"),
            ],
            pagination=_pag(total=1),
            web_url="https://pipeshub.atlassian.net/browse/PA-1787",
            indexing_status="COMPLETED",
            connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        # Header
        assert "Record ID: rec1" in text
        assert "Indexed: COMPLETED" in text
        assert "URL: https://pipeshub.atlassian.net/browse/PA-1787" in text
        # Breadcrumbs
        assert "Jira" in text
        assert "Payments Project" in text
        # Children
        assert "record_id=child1" in text
        assert "has children" in text
        # Related
        assert "Related:" in text
        assert "record_id=ext1" in text
        assert "LINKED_TO" in text

    def test_page2_omits_header_breadcrumbs_related(self):
        view = NavigationView(
            current=_ref("rec1", "PA-1787 Payment outage", "record", "TICKET"),
            breadcrumbs=[_ref("rg1", "Payments Project", "recordGroup", "PROJECT")],
            rows=[_row("child2", "PA-1802 Second task", "record", "TICKET")],
            related=[_row("ext1", "Doc", "record", "CONFLUENCE_PAGE", detail="LINKED_TO")],
            pagination=_pag(page=2, total=5, has_prev=True),
            web_url="https://example.com",
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=2)
        # Must NOT have header/breadcrumbs/related
        assert "Record ID:" not in text
        assert "URL:" not in text
        assert "Related:" not in text
        assert "Payments Project" not in text
        # Must have children row
        assert "child2" in text


class TestEmptyListing:
    def test_empty_children_for_record_node(self):
        view = NavigationView(
            current=_ref("rec1", "Leaf record", "record", "TICKET"),
            breadcrumbs=[],
            rows=[],
            related=[],
            pagination=None,
            web_url=None,
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "no children" in text.lower()


class TestTruncation:
    def test_very_long_output_is_capped(self):
        rows = [_row(f"rec{i}", f"Record with a very long name that goes on and on {'x' * 50} #{i}", "record", "TICKET") for i in range(200)]
        view = NavigationView(
            current=_ref("rg1", "Big Group", "recordGroup"),
            breadcrumbs=[],
            rows=rows,
            related=[],
            pagination=_pag(total=200),
            web_url=None,
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert len(text.encode("utf-8")) <= 12_300  # slightly over cap due to truncation line
        assert "truncated" in text.lower()


class TestNestedChildrenRendering:
    """`navigate(depth=2|3)` populates `NodeRow.children` — see
    `views._render_children_lines`/`_render_children_block`."""

    def test_depth2_children_rendered_indented_with_summary(self):
        child = _row("sub1", "Stripe setup", "record", "SUBTASK")
        story = _row("story1", "Payment Integration", "record", "STORY", has_children=True)
        story.children = [child]
        story.context_summary = "Status: Done, Assignee: Bob"
        view = NavigationView(
            current=_ref("epic1", "SaaS Launch Product", "record", "EPIC"),
            breadcrumbs=[],
            rows=[story],
            related=[],
            pagination=_pag(total=1),
            web_url=None,
            indexing_status=None,
            connector=None,
        )
        text = render_navigation_view(view, page=1)
        lines = text.splitlines()
        top_line = next(l for l in lines if "story1" in l)
        nested_line = next(l for l in lines if "sub1" in l)

        assert "Status: Done, Assignee: Bob" in top_line
        # Suppressed: children are inlined right below, no need for the flag
        assert "has children" not in top_line
        assert nested_line.startswith("  -")
        assert "record_id=sub1" in nested_line

    def test_depth3_grandchildren_rendered_two_levels_indented(self):
        grandchild = _row("gc1", "Stripe webhook", "record", "TASK")
        subtask = _row("sub1", "Stripe setup", "record", "SUBTASK", has_children=True)
        subtask.children = [grandchild]
        story = _row("story1", "Payment Integration", "record", "STORY", has_children=True)
        story.children = [subtask]
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[story], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        lines = text.splitlines()
        grandchild_line = next(l for l in lines if "gc1" in l)
        assert grandchild_line.startswith("    -")  # two levels of indent

    def test_children_truncated_shows_more_hint(self):
        child = _row("sub1", "Stripe setup", "record", "SUBTASK")
        story = _row("story1", "Payment Integration", "record", "STORY", has_children=True)
        story.children = [child]
        story.children_truncated = True
        story.children_total = 5
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[story], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "+4 more" in text
        assert 'navigate(node_id="story1")' in text

    def test_depth1_output_unaffected_when_children_is_none(self):
        """Default (`children=None`) rows render byte-identical to before
        this feature: no nested lines, "has children" flag intact."""
        row = _row("rec1", "PA-1700 Billing Epic", "record", "TICKET", has_children=True)
        view = NavigationView(
            current=_ref("rg1", "Payments Project", "recordGroup", "PROJECT"),
            breadcrumbs=[], rows=[row], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        assert "record_id=rec1" in text
        assert "has children" in text


class TestNestedChildrenByteBudgetFallback:
    """A too-large depth>=2/3 listing drops the deepest level first rather
    than hard-truncating mid-line — see `render_navigation_view`'s
    progressive `_render_children_block` retry."""

    def test_drops_grandchildren_before_hard_truncating(self):
        rows = []
        # 60 grandchildren/child (not 30) — with _MAX_RESPONSE_BYTES raised
        # to 12_000 (see navigate()'s raised default/max `limit`), the
        # fully-nested render of 30/child now fits under budget outright,
        # so this needs enough volume to still force the drop-grandchildren
        # fallback tier this test exists to exercise.
        for i in range(3):
            grandchildren = [
                _row(f"g{i}_{j}", f"Grandchild long name {'x' * 60} {j}", "record", "TASK")
                for j in range(60)
            ]
            child = _row(f"c{i}", f"Child {i}", "record", "STORY", has_children=True)
            child.children = grandchildren
            row = _row(f"r{i}", f"Row {i}", "record", "EPIC", has_children=True)
            row.children = [child]
            rows.append(row)

        view = NavigationView(
            current=None, breadcrumbs=[], rows=rows, related=[],
            pagination=_pag(total=3), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)

        assert len(text.encode("utf-8")) <= 12_300
        assert "omitted to fit response size" in text
        # Level-2 children still shown...
        assert "c0" in text
        # ...but grandchildren dropped, not silently truncated mid-line
        assert "g0_0" not in text
        assert "truncated" not in text.lower()

    def test_collapses_top_level_when_even_level2_is_too_big(self):
        """When even the level-2-only render doesn't fit, fall back all
        the way to a flat listing (children not rendered at all, `has
        children` flag forced back on)."""
        rows = []
        for i in range(80):
            child = _row(
                f"c{i}", f"Child with a fairly long name {'x' * 40} {i}", "record", "STORY",
            )
            row = _row(
                f"r{i}", f"Row with a fairly long name too {'x' * 40} {i}", "record", "EPIC",
                has_children=True,
            )
            row.children = [child]
            rows.append(row)

        view = NavigationView(
            current=None, breadcrumbs=[], rows=rows, related=[],
            pagination=_pag(total=80), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)

        # Top rows still identifiable and correctly flagged as containers...
        assert "record_id=r0" in text
        assert "has children" in text
        # ...but nested rendering fell all the way back to a flat listing:
        # no child rows inlined, and no grandchild-omission note (nothing
        # was rendered to omit in the first place).
        assert "record_id=c0" not in text
        assert "omitted to fit response size" not in text


class TestRowTimestampRendering:
    """`_row_line()` renders a compact `created: YYYY-MM-DD` when
    `NodeRow.source_created_at` is set — see `navigator._node_item_to_row`."""

    def test_created_date_rendered_when_set(self):
        created_ms = int(datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
        row = _row("rec1", "PA-1787", "record", "TICKET", source_created_at=created_ms)
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[row], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "| created: 2026-07-15" in text

    def test_omitted_when_none(self):
        row = _row("rec1", "PA-1787", "record", "TICKET", source_created_at=None)
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[row], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "created:" not in text

    def test_omitted_when_zero(self):
        """0 is the "missing timestamp" sentinel some docs default to —
        must not render as 1970-01-01."""
        row = _row("rec1", "PA-1787", "record", "TICKET", source_created_at=0)
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[row], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "created:" not in text
        assert "1970" not in text

    def test_only_created_at_rendered_not_modified_at(self):
        """Only source_created_at is surfaced per row today (Phase 2 scope) —
        source_modified_at is stored on the model but not yet rendered."""
        created_ms = int(datetime(2026, 7, 15, tzinfo=timezone.utc).timestamp() * 1000)
        modified_ms = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
        row = _row(
            "rec1", "PA-1787", "record", "TICKET",
            source_created_at=created_ms, source_modified_at=modified_ms,
        )
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[row], related=[],
            pagination=_pag(total=1), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert "| created: 2026-07-15" in text
        assert "2026-08-01" not in text

    def test_byte_budget_not_exceeded_with_dates_on_large_listing(self):
        rows = [
            _row(f"rec{i}", f"Record {i}", "record", "TICKET",
                 source_created_at=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
            for i in range(200)
        ]
        view = NavigationView(
            current=_ref("rg1", "Big Group", "recordGroup"),
            breadcrumbs=[], rows=rows, related=[],
            pagination=_pag(total=200), web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=1)
        assert len(text.encode("utf-8")) <= 12_300  # slightly over cap due to truncation line


class TestSiblingHint:
    """navigate() output points back at the parent so the model can browse
    siblings the current listing didn't include — see
    `render_navigation_view`'s `Next:` hints."""

    def test_sibling_hint_shown_when_breadcrumbs_present(self):
        view = NavigationView(
            current=_ref("rec1", "PA-1787 Payment outage", "record", "TICKET"),
            breadcrumbs=[
                _ref("app1", "Jira", "app", "JIRA"),
                _ref("rg1", "Payments Project", "recordGroup", "PROJECT"),
            ],
            rows=[_row("child1", "PA-1801 Fix retry", "record", "TICKET")],
            related=[],
            pagination=_pag(total=1),
            web_url=None, indexing_status=None, connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        next_line = text.split("Next:")[-1]
        assert 'navigate(node_id="rg1") to see siblings' in next_line

    def test_sibling_hint_absent_at_root(self):
        """Root/app-level nodes with no breadcrumbs have no parent to browse
        siblings under."""
        view = NavigationView(
            current=_ref("app1", "Jira", "app", "JIRA"),
            breadcrumbs=[],
            rows=[_row("rg1", "Payments Project", "recordGroup", "PROJECT")],
            related=[],
            pagination=_pag(total=1),
            web_url=None, indexing_status=None, connector="JIRA",
        )
        text = render_navigation_view(view, page=1)
        assert "to see siblings" not in text

    def test_sibling_hint_absent_on_page_greater_than_one(self):
        """Breadcrumbs aren't recomputed on page > 1, and the sibling set
        doesn't change page to page — the hint is page 1 only."""
        view = NavigationView(
            current=_ref("rec1", "PA-1787 Payment outage", "record", "TICKET"),
            breadcrumbs=[_ref("rg1", "Payments Project", "recordGroup", "PROJECT")],
            rows=[_row("child2", "PA-1802 Second task", "record", "TICKET")],
            related=[],
            pagination=_pag(page=2, total=5, has_prev=True),
            web_url=None, indexing_status=None, connector=None,
        )
        text = render_navigation_view(view, page=2)
        assert "to see siblings" not in text


class TestLookupResultRenderer:
    def test_single_match(self):
        result = LookupResult(
            matches=[LookupMatch(
                id="abc123",
                name="PA-1787 Payment outage",
                record_type="TICKET",
                connector_name="JIRA",
                web_url="https://example.atlassian.net/browse/PA-1787",
                indexing_status="COMPLETED",
                identifier_used="PA-1787",
            )],
            ambiguous=False,
            not_found_identifiers=[],
        )
        from app.agents.actions.knowledge_graph.views import render_lookup_result
        text = render_lookup_result(result)
        assert "Record ID: abc123" in text
        assert "PA-1787 Payment outage" in text
        assert "JIRA" in text

    def test_not_found(self):
        result = LookupResult(matches=[], ambiguous=False, not_found_identifiers=["PA-9999"])
        text = render_lookup_result(result)
        assert "PA-9999" in text
        assert "Not found" in text or "no access" in text.lower()

    def test_not_found_with_searched_connectors_label(self):
        """Miss lines should include the connector types that were searched."""
        result = LookupResult(
            matches=[],
            ambiguous=False,
            not_found_identifiers=["PA-9999"],
            searched_connectors={"PA-9999": ["JIRA", "weburl-fallback"]},
        )
        text = render_lookup_result(result)
        assert "PA-9999" in text
        assert "Not found" in text
        assert "searched" in text.lower()
        assert "JIRA" in text

    def test_ambiguous(self):
        result = LookupResult(
            matches=[
                LookupMatch(id="r1", name="A", record_type="TICKET", connector_name="JIRA",
                            web_url=None, indexing_status=None, identifier_used="PA-1"),
                LookupMatch(id="r2", name="B", record_type="TICKET", connector_name="JIRA",
                            web_url=None, indexing_status=None, identifier_used="PA-1"),
            ],
            ambiguous=True,
            not_found_identifiers=[],
        )
        text = render_lookup_result(result)
        assert "Multiple" in text or "ambiguous" in text.lower() or "navigate" in text.lower()
