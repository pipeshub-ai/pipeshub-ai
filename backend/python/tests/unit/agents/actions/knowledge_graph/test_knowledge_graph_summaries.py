"""Tests for summary helpers in ``app.agents.actions.knowledge_graph.knowledge_graph``."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    _flatten_row_ids,
    _get_scoping,
    _list_files_result_summary,
    _lookup_args_summary,
    _lookup_result_summary,
    _navigate_args_summary,
    _navigate_result_summary,
    _normalize_identifiers,
    _search_result_summary,
    _time_range_error_message,
)


def _tool_result(content: str = "", is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=content, is_error=is_error)


class TestNavigateArgsSummary:
    def test_root_no_extras(self) -> None:
        result = _navigate_args_summary({})
        assert result == "Navigated to root"

    def test_with_node_id(self) -> None:
        result = _navigate_args_summary({"node_id": "n-1"})
        assert "n-1" in result

    def test_page_greater_than_one(self) -> None:
        result = _navigate_args_summary({"page": 3})
        assert "page 3" in result

    def test_page_one_not_shown(self) -> None:
        result = _navigate_args_summary({"page": 1})
        assert "page" not in result

    def test_depth_shown(self) -> None:
        result = _navigate_args_summary({"depth": 3})
        assert "depth 3" in result

    def test_time_filter_created(self) -> None:
        result = _navigate_args_summary({"created_after": "2026-01-01"})
        assert "created" in result

    def test_time_filter_modified(self) -> None:
        result = _navigate_args_summary({"modified_after": "2026-06-01"})
        assert "modified" in result

    def test_time_filter_both(self) -> None:
        result = _navigate_args_summary({
            "created_after": "2026-01-01",
            "modified_before": "2026-12-31",
        })
        assert "created" in result
        assert "modified" in result

    def test_node_types(self) -> None:
        result = _navigate_args_summary({"node_types": ["record", "folder"]})
        assert "record" in result
        assert "folder" in result


class TestNavigateResultSummary:
    def test_no_children(self) -> None:
        result = _navigate_result_summary({}, _tool_result("No children found here"))
        assert result == "No children found"

    def test_no_items_case_insensitive(self) -> None:
        result = _navigate_result_summary({}, _tool_result("There are NO ITEMS in this view"))
        assert result == "No children found"

    def test_first_line_returned(self) -> None:
        result = _navigate_result_summary({}, _tool_result("Header line\nDetail line"))
        assert result == "Header line"

    def test_empty_content(self) -> None:
        result = _navigate_result_summary({}, _tool_result(""))
        assert result == "Navigation result"


class TestLookupArgsSummary:
    def test_no_identifiers(self) -> None:
        result = _lookup_args_summary({})
        assert "no identifiers" in result

    def test_string_identifier(self) -> None:
        result = _lookup_args_summary({"identifiers": "JIRA-123"})
        assert "JIRA-123" in result

    def test_list_identifiers(self) -> None:
        result = _lookup_args_summary({"identifiers": ["a", "b"]})
        assert "a" in result
        assert "b" in result

    def test_truncation_over_three(self) -> None:
        result = _lookup_args_summary({"identifiers": ["a", "b", "c", "d", "e"]})
        assert "+2 more" in result

    def test_exactly_three(self) -> None:
        result = _lookup_args_summary({"identifiers": ["a", "b", "c"]})
        assert "+0 more" not in result
        assert "more" not in result


class TestLookupResultSummary:
    def test_empty_content(self) -> None:
        result = _lookup_result_summary({}, _tool_result(""))
        assert result == "No results"

    def test_names_extracted(self) -> None:
        content = "Name : My Document\nRecord ID: rec-1"
        result = _lookup_result_summary({}, _tool_result(content))
        assert "My Document" in result

    def test_multiple_names_truncated(self) -> None:
        content = "Name : Doc1\nName : Doc2\nName : Doc3"
        result = _lookup_result_summary({}, _tool_result(content))
        assert "Doc1" in result
        assert "+1 more" in result

    def test_not_found(self) -> None:
        result = _lookup_result_summary({}, _tool_result("Not found in the knowledge graph"))
        assert result == "Not found or no access"

    def test_no_access(self) -> None:
        result = _lookup_result_summary({}, _tool_result("The user has no access to this"))
        assert result == "Not found or no access"

    def test_record_count_fallback(self) -> None:
        content = "Record ID: rec-1\nSome data\nRecord ID: rec-2"
        result = _lookup_result_summary({}, _tool_result(content))
        assert "Found 2 records" in result

    def test_single_record_no_plural(self) -> None:
        content = "Record ID: rec-1\nSome data"
        result = _lookup_result_summary({}, _tool_result(content))
        assert "Found 1 record" in result
        assert "records" not in result

    def test_error_result(self) -> None:
        result = _lookup_result_summary({}, _tool_result("Something went wrong", is_error=True))
        assert result == "Lookup failed"

    def test_generic_content(self) -> None:
        result = _lookup_result_summary({}, _tool_result("Random text"))
        assert result == "Lookup result"


class TestSearchResultSummary:
    def test_none_content(self) -> None:
        result = _search_result_summary({}, _tool_result(""))
        assert result is None

    def test_error_status_in_json(self) -> None:
        import json
        content = json.dumps({"status": "error", "message": "bad query"})
        result = _search_result_summary({}, _tool_result(content))
        assert "bad query" in result

    def test_zero_results_json(self) -> None:
        import json
        content = json.dumps({"result_count": 0, "message": "No results found"})
        result = _search_result_summary({}, _tool_result(content))
        assert "No results found" in result

    def test_empty_results_list(self) -> None:
        import json
        content = json.dumps({"results": [], "message": "No matching docs"})
        result = _search_result_summary({}, _tool_result(content))
        assert "No matching docs" in result

    def test_top_n_blocks_header(self) -> None:
        content = "Top 5 blocks from 3 records\nName : Doc1\nName : Doc2"
        result = _search_result_summary({}, _tool_result(content))
        assert "5 blocks" in result
        assert "3 records" in result
        assert "Doc1" in result

    def test_retrieved_n_blocks_header(self) -> None:
        content = "Retrieved 10 knowledge blocks from 4 documents\nName : Report"
        result = _search_result_summary({}, _tool_result(content))
        assert "10 blocks" in result
        assert "4 records" in result

    def test_no_match_returns_none(self) -> None:
        result = _search_result_summary({}, _tool_result("Random text without header"))
        assert result is None

    def test_header_without_names(self) -> None:
        content = "Top 2 blocks from 1 record\nSome content"
        result = _search_result_summary({}, _tool_result(content))
        assert "2 blocks" in result
        assert "1 record" in result


class TestListFilesResultSummary:
    def test_empty_content(self) -> None:
        result = _list_files_result_summary({}, _tool_result(""))
        assert result is None

    def test_error_in_content(self) -> None:
        result = _list_files_result_summary({}, _tool_result("Error: something broke"))
        assert "Error: something broke" in result

    def test_no_items_found(self) -> None:
        result = _list_files_result_summary({}, _tool_result("No items found for this query"))
        assert result == "No items found"

    def test_normal_first_line(self) -> None:
        result = _list_files_result_summary({}, _tool_result("Found 10 files\nfile1\nfile2"))
        assert result == "Found 10 files"


class TestNormalizeIdentifiers:
    def test_string(self) -> None:
        assert _normalize_identifiers("abc") == ["abc"]

    def test_empty_string(self) -> None:
        assert _normalize_identifiers("  ") == []

    def test_list(self) -> None:
        assert _normalize_identifiers(["a", "b"]) == ["a", "b"]

    def test_list_with_empty(self) -> None:
        assert _normalize_identifiers(["a", "", "b"]) == ["a", "b"]

    def test_non_string_non_list(self) -> None:
        assert _normalize_identifiers(123) == []

    def test_tuple(self) -> None:
        assert _normalize_identifiers(("x", "y")) == ["x", "y"]


class TestGetUserKey:
    @pytest.mark.asyncio
    async def test_returns_key(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _get_user_key
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = {"_key": "k1", "id": "u1"}
        result = await _get_user_key(gp, "u1")
        assert result == "k1"

    @pytest.mark.asyncio
    async def test_returns_id_when_no_key(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _get_user_key
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = {"id": "u1"}
        result = await _get_user_key(gp, "u1")
        assert result == "u1"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_user(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _get_user_key
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = None
        result = await _get_user_key(gp, "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _get_user_key
        gp = AsyncMock()
        gp.get_user_by_user_id.side_effect = RuntimeError("db error")
        result = await _get_user_key(gp, "u1")
        assert result is None


class TestTimeRangeToKhFilters:
    def test_none_input(self) -> None:
        from app.agents.actions.knowledge_graph.ops.time_range import time_range_to_kh_filters
        assert time_range_to_kh_filters(None) == (None, None)

    def test_empty_dict(self) -> None:
        from app.agents.actions.knowledge_graph.ops.time_range import time_range_to_kh_filters
        assert time_range_to_kh_filters({}) == (None, None)

    def test_created_range(self) -> None:
        from app.agents.actions.knowledge_graph.ops.time_range import time_range_to_kh_filters
        result = time_range_to_kh_filters({
            "source_created_after_ms": 1000,
            "source_created_before_ms": 2000,
        })
        assert result == ({"gte": 1000, "lte": 2000}, None)

    def test_updated_range(self) -> None:
        from app.agents.actions.knowledge_graph.ops.time_range import time_range_to_kh_filters
        result = time_range_to_kh_filters({
            "source_updated_after_ms": 3000,
            "source_updated_before_ms": 4000,
        })
        assert result == (None, {"gte": 3000, "lte": 4000})

    def test_both_ranges(self) -> None:
        from app.agents.actions.knowledge_graph.ops.time_range import time_range_to_kh_filters
        c, u = time_range_to_kh_filters({
            "source_created_after_ms": 1000,
            "source_updated_before_ms": 4000,
        })
        assert c == {"gte": 1000, "lte": None}
        assert u == {"gte": None, "lte": 4000}


class TestRecordIdsInView:
    def test_includes_rows_and_related(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _record_ids_in_view
        view = SimpleNamespace(
            rows=[SimpleNamespace(id="r1", is_record=True)],
            related=[SimpleNamespace(id="r2", is_record=True)],
            current=None,
        )
        result = _record_ids_in_view(view)
        assert "r1" in result
        assert "r2" in result

    def test_includes_current_record(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _record_ids_in_view
        view = SimpleNamespace(
            rows=[],
            related=[],
            current=SimpleNamespace(id="cur-1", is_record=True),
        )
        result = _record_ids_in_view(view)
        assert "cur-1" in result

    def test_excludes_non_record_current(self) -> None:
        from app.agents.actions.knowledge_graph.knowledge_graph import _record_ids_in_view
        view = SimpleNamespace(
            rows=[],
            related=[],
            current=SimpleNamespace(id="app-1", is_record=False),
        )
        result = _record_ids_in_view(view)
        assert result == []


class TestFlattenRowIds:
    def test_filters_non_records(self) -> None:
        rows = [
            SimpleNamespace(id="r1", is_record=True),
            SimpleNamespace(id="app-1", is_record=False),
            SimpleNamespace(id="r2", is_record=True),
        ]
        assert _flatten_row_ids(rows) == ["r1", "r2"]

    def test_filters_empty_ids(self) -> None:
        rows = [
            SimpleNamespace(id="", is_record=True),
            SimpleNamespace(id="r1", is_record=True),
        ]
        assert _flatten_row_ids(rows) == ["r1"]


class TestGetScoping:
    def test_returns_connector_and_kb_ids(self) -> None:
        mock_scope = SimpleNamespace(app_ids=("c1", "c2"), kb_ids=("kb1",))
        with patch(
            "app.agents.actions.knowledge_graph.ops.scope.derive_scope",
            return_value=mock_scope,
        ):
            connector_ids, kb_ids = _get_scoping({})
        assert connector_ids == ["c1", "c2"]
        assert kb_ids == ["kb1"]

    def test_returns_empty_lists_when_no_ids(self) -> None:
        mock_scope = SimpleNamespace(app_ids=(), kb_ids=())
        with patch(
            "app.agents.actions.knowledge_graph.ops.scope.derive_scope",
            return_value=mock_scope,
        ):
            connector_ids, kb_ids = _get_scoping({})
        assert connector_ids == []
        assert kb_ids == []


class TestTimeRangeErrorMessage:
    def test_valid_json_with_message(self) -> None:
        import json
        error = json.dumps({"status": "error", "message": "Invalid date"})
        assert _time_range_error_message(error) == "Invalid date"

    def test_valid_json_without_message_key(self) -> None:
        import json
        error = json.dumps({"status": "error", "detail": "something"})
        assert _time_range_error_message(error) == error

    def test_valid_json_non_dict(self) -> None:
        import json
        error = json.dumps(["a", "b"])
        assert _time_range_error_message(error) == error

    def test_invalid_json(self) -> None:
        error = "not valid json at all {"
        assert _time_range_error_message(error) == error

    def test_empty_string(self) -> None:
        assert _time_range_error_message("") == ""


# ---------------------------------------------------------------------------
# lookup_record (KnowledgeGraph method)
# ---------------------------------------------------------------------------


class TestLookupRecord:
    def _make_kg(self, state=None):
        from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph.__new__(KnowledgeGraph)
        kg.state = state
        return kg

    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        kg = self._make_kg(state=None)
        ok, msg = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "not initialized" in msg

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:
        kg = self._make_kg(state={"graph_provider": None})
        ok, msg = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "Graph provider" in msg

    @pytest.mark.asyncio
    async def test_empty_identifiers(self) -> None:
        kg = self._make_kg(state={"graph_provider": AsyncMock()})
        ok, msg = await kg.lookup_record([])
        assert ok is False
        assert "No identifiers" in msg

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value=None)
    async def test_user_not_found(self, mock_uk) -> None:
        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
        })
        ok, msg = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "User not found" in msg

    @pytest.mark.asyncio
    @patch("app.utils.chat_helpers.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.remember_record_ids")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.render_lookup_result", return_value="Match found")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.resolve_scope", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.RecordResolver")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_happy_path(self, mock_uk, mock_catalog_build, mock_resolver_cls,
                              mock_resolve_scope, mock_render, mock_remember, mock_shortener) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = False
        mock_catalog_build.return_value = catalog

        scope = SimpleNamespace(app_ids=("app-1",))
        mock_resolve_scope.return_value = scope

        match = SimpleNamespace(id="rec-1")
        result_obj = SimpleNamespace(matches=[match])
        resolver_instance = AsyncMock()
        resolver_instance.resolve_many.return_value = result_obj
        mock_resolver_cls.return_value = resolver_instance

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
        })
        ok, text = await kg.lookup_record("JIRA-1")
        assert ok is True
        assert text == "Match found"
        mock_remember.assert_called_once_with(kg.state, ["rec-1"])

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_empty_catalog_no_knowledge(self, mock_uk, mock_catalog_build) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = True
        mock_catalog_build.return_value = catalog

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
            "has_knowledge": False,
        })
        ok, msg = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "No knowledge sources" in msg

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_empty_catalog_with_knowledge(self, mock_uk, mock_catalog_build) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = True
        mock_catalog_build.return_value = catalog

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
            "has_knowledge": True,
        })
        ok, msg = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "Not found" in msg

    @pytest.mark.asyncio
    @patch("app.utils.chat_helpers.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.remember_record_ids")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.render_lookup_result", return_value="")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.resolve_scope", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.RecordResolver")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_empty_render_returns_not_found(self, mock_uk, mock_catalog_build, mock_resolver_cls,
                                                   mock_resolve_scope, mock_render, mock_remember, mock_shortener) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = False
        mock_catalog_build.return_value = catalog
        mock_resolve_scope.return_value = SimpleNamespace(app_ids=())
        resolver = AsyncMock()
        resolver.resolve_many.return_value = SimpleNamespace(matches=[])
        mock_resolver_cls.return_value = resolver

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
        })
        ok, text = await kg.lookup_record("JIRA-1")
        assert ok is True
        assert "Not found" in text

    @pytest.mark.asyncio
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.resolve_scope", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.RecordResolver")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_resolver_exception(self, mock_uk, mock_catalog_build, mock_resolver_cls, mock_resolve_scope) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = False
        mock_catalog_build.return_value = catalog
        mock_resolve_scope.return_value = SimpleNamespace(app_ids=())
        resolver = AsyncMock()
        resolver.resolve_many.side_effect = RuntimeError("resolver error")
        mock_resolver_cls.return_value = resolver

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
        })
        ok, text = await kg.lookup_record("JIRA-1")
        assert ok is False
        assert "Not found" in text

    @pytest.mark.asyncio
    @patch("app.utils.chat_helpers.get_record_id_shortener_if_enabled", return_value=None)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.render_lookup_result", return_value="OK")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.remember_record_ids")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.resolve_scope", new_callable=AsyncMock, return_value=SimpleNamespace(app_ids=()))
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.RecordResolver")
    @patch("app.agents.actions.knowledge_graph.knowledge_graph.ConnectorCatalog.build", new_callable=AsyncMock)
    @patch("app.agents.actions.knowledge_graph.knowledge_graph._get_user_key", new_callable=AsyncMock, return_value="k1")
    async def test_identifiers_capped_at_ten(self, mock_uk, mock_catalog_build, mock_resolver_cls,
                                              mock_resolve, mock_remember, mock_render, mock_shortener) -> None:
        catalog = MagicMock()
        catalog.is_empty.return_value = False
        mock_catalog_build.return_value = catalog
        resolver = AsyncMock()
        resolver.resolve_many.return_value = SimpleNamespace(matches=[])
        mock_resolver_cls.return_value = resolver

        kg = self._make_kg(state={
            "graph_provider": AsyncMock(),
            "org_id": "o1",
            "user_id": "u1",
        })
        ids = [f"ID-{i}" for i in range(15)]
        await kg.lookup_record(ids)
        called_idents = resolver.resolve_many.call_args[0][0]
        assert len(called_idents) == 10
