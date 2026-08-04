"""Tests for ``app.agents.actions.knowledge_graph.models`` — Pydantic model construction and properties."""
from __future__ import annotations

from app.agents.actions.knowledge_graph.models import (
    LookupMatch,
    LookupResult,
    NavigationView,
    NodeRef,
    NodeRow,
    PaginationInfo,
)


class TestNodeRef:
    def test_display_id_label_record(self) -> None:
        ref = NodeRef(id="r1", name="Doc", node_type="record", sub_type="TICKET", is_record=True)
        assert ref.display_id_label == "Record ID"

    def test_display_id_label_non_record(self) -> None:
        ref = NodeRef(id="a1", name="App", node_type="app", sub_type="JIRA", is_record=False)
        assert ref.display_id_label == "Node ID"

    def test_display_id_label_folder_is_record(self) -> None:
        ref = NodeRef(id="f1", name="Folder", node_type="folder", sub_type=None, is_record=True)
        assert ref.display_id_label == "Record ID"

    def test_sub_type_none(self) -> None:
        ref = NodeRef(id="x", name="X", node_type="app", sub_type=None, is_record=False)
        assert ref.sub_type is None


class TestNodeRow:
    def test_defaults(self) -> None:
        row = NodeRow(
            id="r1", name="R", node_type="record", sub_type=None,
            is_record=True, has_children=False, detail=None,
        )
        assert row.level == 1
        assert row.web_url is None
        assert row.indexing_status is None
        assert row.source_created_at is None
        assert row.source_modified_at is None
        assert row.context_summary is None

    def test_explicit_fields(self) -> None:
        row = NodeRow(
            id="r1", name="R", node_type="record", sub_type="TICKET",
            is_record=True, has_children=True, detail="size=42",
            web_url="https://example.com", indexing_status="IN_PROGRESS",
            source_created_at=1000, source_modified_at=2000,
            level=3, context_summary="Status: OPEN",
        )
        assert row.level == 3
        assert row.web_url == "https://example.com"
        assert row.source_created_at == 1000
        assert row.context_summary == "Status: OPEN"

    def test_inherits_display_id_label(self) -> None:
        row = NodeRow(
            id="r1", name="R", node_type="record", sub_type=None,
            is_record=True, has_children=False, detail=None,
        )
        assert row.display_id_label == "Record ID"


class TestPaginationInfo:
    def test_construction(self) -> None:
        pag = PaginationInfo(page=2, limit=20, total=100, has_next=True, has_prev=True)
        assert pag.page == 2
        assert pag.limit == 20
        assert pag.total == 100
        assert pag.has_next is True
        assert pag.has_prev is True


class TestNavigationView:
    def test_minimal_root_view(self) -> None:
        view = NavigationView(
            current=None, breadcrumbs=[], rows=[], related=[],
            pagination=None, web_url=None, indexing_status=None, connector=None,
        )
        assert view.current is None
        assert view.context_block is None

    def test_context_block_default(self) -> None:
        view = NavigationView(
            current=NodeRef(id="r1", name="R", node_type="record", sub_type=None, is_record=True),
            breadcrumbs=[], rows=[], related=[],
            pagination=None, web_url=None, indexing_status=None, connector=None,
        )
        assert view.context_block is None

    def test_context_block_set(self) -> None:
        view = NavigationView(
            current=NodeRef(id="r1", name="R", node_type="record", sub_type=None, is_record=True),
            breadcrumbs=[], rows=[], related=[],
            pagination=None, web_url=None, indexing_status=None, connector=None,
            context_block="Record ID: r1\nName: R\n* Status: OPEN",
        )
        assert "Status: OPEN" in view.context_block


class TestLookupMatch:
    def test_optional_defaults(self) -> None:
        m = LookupMatch(
            id="r1", name="Doc", record_type="TICKET", connector_name="JIRA",
            web_url=None, indexing_status=None, identifier_used="PA-1",
        )
        assert m.external_id is None
        assert m.context_block is None

    def test_with_optional_fields(self) -> None:
        m = LookupMatch(
            id="r1", name="Doc", record_type="TICKET", connector_name="JIRA",
            web_url="https://example.com", indexing_status="IN_PROGRESS",
            identifier_used="PA-1", external_id="PA-1", context_block="block text",
        )
        assert m.external_id == "PA-1"
        assert m.context_block == "block text"


class TestLookupResult:
    def test_empty(self) -> None:
        r = LookupResult(matches=[], ambiguous=False, not_found_identifiers=[])
        assert r.searched_connectors == {}

    def test_with_searched_connectors(self) -> None:
        r = LookupResult(
            matches=[], ambiguous=False, not_found_identifiers=["PA-9999"],
            searched_connectors={"PA-9999": ["JIRA", "weburl-fallback"]},
        )
        assert r.searched_connectors["PA-9999"] == ["JIRA", "weburl-fallback"]
