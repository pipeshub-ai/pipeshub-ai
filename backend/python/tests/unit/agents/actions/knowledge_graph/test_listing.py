"""Tests for ``app.agents.actions.knowledge_graph.ops.listing`` helpers."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.listing import (
    _normalize_list,
    _record_ids_in_items,
    _render_flat_text,
    execute_list_files,
)


def _item(node_type: str, item_id: str = "id-1", name: str = "Item", sub_type: str | None = None, web_url: str | None = None) -> SimpleNamespace:
    ns = SimpleNamespace(nodeType=node_type, id=item_id, name=name, subType=sub_type)
    if web_url is not None:
        ns.webUrl = web_url
    return ns


def _item_with_enum(enum_val: str, item_id: str = "id-1") -> SimpleNamespace:
    enum_like = SimpleNamespace(value=enum_val)
    return SimpleNamespace(nodeType=enum_like, id=item_id, name="Item")


def _response(success: bool = True, items: list | None = None, error: str | None = None, total: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(success=success, items=items, error=error, total=total)


# ---------------------------------------------------------------------------
# _record_ids_in_items
# ---------------------------------------------------------------------------

class TestRecordIdsInItems:
    def test_none_items(self) -> None:
        assert _record_ids_in_items(None) == []

    def test_empty_list(self) -> None:
        assert _record_ids_in_items([]) == []

    def test_record_type_included(self) -> None:
        item = _item_with_enum("record", "rec-1")
        assert _record_ids_in_items([item]) == ["rec-1"]

    def test_folder_type_included(self) -> None:
        item = _item_with_enum("folder", "fol-1")
        assert _record_ids_in_items([item]) == ["fol-1"]

    def test_app_type_excluded(self) -> None:
        item = _item_with_enum("app", "app-1")
        assert _record_ids_in_items([item]) == []

    def test_record_group_excluded(self) -> None:
        item = _item_with_enum("recordGroup", "rg-1")
        assert _record_ids_in_items([item]) == []

    def test_empty_id_excluded(self) -> None:
        item = _item_with_enum("record", "")
        assert _record_ids_in_items([item]) == []

    def test_mixed_types(self) -> None:
        items = [
            _item_with_enum("record", "rec-1"),
            _item_with_enum("app", "app-1"),
            _item_with_enum("folder", "fol-1"),
            _item_with_enum("recordGroup", "rg-1"),
        ]
        assert _record_ids_in_items(items) == ["rec-1", "fol-1"]

    def test_string_node_type_fallback(self) -> None:
        item = _item("record", "rec-str")
        assert _record_ids_in_items([item]) == ["rec-str"]


# ---------------------------------------------------------------------------
# _normalize_list
# ---------------------------------------------------------------------------

class TestNormalizeList:
    def test_none(self) -> None:
        assert _normalize_list(None) is None

    def test_non_empty_string(self) -> None:
        assert _normalize_list("abc") == ["abc"]

    def test_whitespace_string(self) -> None:
        assert _normalize_list("  ") is None

    def test_empty_string(self) -> None:
        assert _normalize_list("") is None

    def test_string_stripped(self) -> None:
        assert _normalize_list("  foo  ") == ["foo"]

    def test_non_empty_list(self) -> None:
        assert _normalize_list(["a", "b"]) == ["a", "b"]

    def test_list_with_falsy_removed(self) -> None:
        assert _normalize_list(["a", "", None, "b"]) == ["a", "b"]

    def test_all_falsy_list(self) -> None:
        assert _normalize_list(["", None, 0]) is None

    def test_non_string_non_list(self) -> None:
        assert _normalize_list(42) is None

    def test_dict_returns_none(self) -> None:
        assert _normalize_list({"a": 1}) is None

    def test_list_int_coerced(self) -> None:
        assert _normalize_list([1, 2]) == ["1", "2"]


# ---------------------------------------------------------------------------
# _render_flat_text
# ---------------------------------------------------------------------------

class TestRenderFlatText:
    def test_failure_with_error(self) -> None:
        resp = _response(success=False, error="boom")
        assert _render_flat_text(resp, None) == "Error: boom"

    def test_failure_no_error_message(self) -> None:
        resp = _response(success=False, error=None)
        assert _render_flat_text(resp, None) == "Error: Failed to browse knowledge files"

    def test_empty_items_no_query(self) -> None:
        resp = _response(items=[])
        assert _render_flat_text(resp, None) == "No items found."

    def test_empty_items_with_query(self) -> None:
        resp = _response(items=[])
        assert _render_flat_text(resp, "test") == 'No items found matching "test".'

    def test_none_items_no_query(self) -> None:
        resp = _response(items=None)
        assert _render_flat_text(resp, None) == "No items found."

    def test_single_record_item(self) -> None:
        item = _item("record", "rec-1", "My Doc")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "Found 1 item." in text
        assert "[Record] My Doc" in text
        assert "record_id=rec-1" in text

    def test_multiple_items_plural(self) -> None:
        items = [_item("record", f"rec-{i}", f"Doc {i}") for i in range(3)]
        resp = _response(items=items)
        text = _render_flat_text(resp, None)
        assert "Found 3 items." in text

    def test_query_in_header(self) -> None:
        item = _item("record", "rec-1", "Doc")
        resp = _response(items=[item])
        text = _render_flat_text(resp, "search term")
        assert 'matching "search term"' in text

    def test_folder_uses_record_id_label(self) -> None:
        item = _item("folder", "fol-1", "Folder A")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "record_id=fol-1" in text

    def test_app_uses_node_id_label(self) -> None:
        item = _item("app", "app-1", "My App")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "node_id=app-1" in text

    def test_sub_type_shown(self) -> None:
        item = _item("record", "rec-1", "Ticket", sub_type="TICKET")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "[Record/TICKET]" in text

    def test_web_url_shown(self) -> None:
        item = _item("record", "rec-1", "Doc", web_url="https://example.com")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "url=https://example.com" in text

    def test_no_web_url_attribute(self) -> None:
        item = _item("record", "rec-1", "Doc")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "url=" not in text

    def test_total_more_than_items_shows_remaining(self) -> None:
        items = [_item("record", f"rec-{i}", f"Doc {i}") for i in range(2)]
        resp = _response(items=items, total=10)
        text = _render_flat_text(resp, None)
        assert "8 more items" in text

    def test_total_equal_items_no_remaining(self) -> None:
        items = [_item("record", f"rec-{i}", f"Doc {i}") for i in range(2)]
        resp = _response(items=items, total=2)
        text = _render_flat_text(resp, None)
        assert "more items" not in text

    def test_total_none_no_remaining(self) -> None:
        items = [_item("record", "rec-1", "Doc")]
        resp = _response(items=items, total=None)
        text = _render_flat_text(resp, None)
        assert "more items" not in text

    def test_next_hint_always_present(self) -> None:
        item = _item("record", "rec-1", "Doc")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "knowledgegraph__navigate()" in text
        assert "knowledgegraph__fetch_record()" in text

    def test_shortener_used(self) -> None:
        shortener = MagicMock()
        shortener.get_or_create_short_id = MagicMock(return_value="R1")
        item = _item("record", "rec-1", "Doc")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None, shortener)
        assert "record_id=R1" in text
        shortener.get_or_create_short_id.assert_called_once_with("rec-1")

    def test_unknown_node_type_label_passthrough(self) -> None:
        item = _item("customType", "ct-1", "Custom")
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "[customType]" in text
        assert "node_id=ct-1" in text

    def test_enum_like_node_type(self) -> None:
        item = _item_with_enum("record", "rec-e")
        item.name = "EnumDoc"
        resp = _response(items=[item])
        text = _render_flat_text(resp, None)
        assert "[Record] EnumDoc" in text
        assert "record_id=rec-e" in text


# ---------------------------------------------------------------------------
# execute_list_files
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, patch


class TestExecuteListFiles:
    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        ok, msg = await execute_list_files(None)
        assert ok is False
        assert "not initialized" in msg

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        state: dict[str, Any] = {"graph_provider": None}
        with patch(
            "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
            new_callable=AsyncMock,
        ):
            ok, msg = await execute_list_files(state)
        assert ok is False
        assert "Graph provider" in msg

    @pytest.mark.asyncio
    async def test_empty_scope(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=(), kb_ids=(), is_empty=lambda: True)
        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with patch(
            "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
            new_callable=AsyncMock,
            return_value=scope,
        ):
            ok, msg = await execute_list_files(state)
        assert ok is False
        assert "No knowledge sources" in msg

    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        item = _item_with_enum("record", "rec-1")
        item.name = "Doc A"
        resp = _response(items=[item])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
            "logger": MagicMock(),
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch(
                "app.modules.agents.qna.chat_state.remember_record_ids",
            ),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, text = await execute_list_files(state)
        assert ok is True
        assert "Doc A" in text

    @pytest.mark.asyncio
    async def test_query_too_short_ignored(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, text = await execute_list_files(state, query="a")
        assert ok is True
        mock_service.get_nodes.assert_called_once()
        call_kwargs = mock_service.get_nodes.call_args
        assert call_kwargs.kwargs.get("q") is None

    @pytest.mark.asyncio
    async def test_invalid_sort_defaults(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(
                state, sort_by="invalid", sort_order="invalid"
            )
        assert ok is True
        call_kwargs = mock_service.get_nodes.call_args.kwargs
        assert call_kwargs["sort_by"] == "updatedAt"
        assert call_kwargs["sort_order"] == "desc"

    @pytest.mark.asyncio
    async def test_source_ids_matched(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(
            app_ids=("app-1", "app-2"), kb_ids=("kb-1",), is_empty=lambda: False
        )
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(state, source_ids=["app-1"])
        assert ok is True
        call_kwargs = mock_service.get_nodes.call_args.kwargs
        assert call_kwargs["connector_ids"] == ["app-1"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                side_effect=RuntimeError("service boom"),
            ),
        ):
            ok, text = await execute_list_files(state)
        assert ok is False
        assert "error" in text.lower()

    @pytest.mark.asyncio
    async def test_page_limit_clamped(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(state, page=-5, limit=999)
        assert ok is True
        call_kwargs = mock_service.get_nodes.call_args.kwargs
        assert call_kwargs["page"] == 1
        assert call_kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_node_types_filtered(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(
                state, node_types=["record", "INVALID_TYPE"]
            )
        assert ok is True
        call_kwargs = mock_service.get_nodes.call_args.kwargs
        assert call_kwargs["node_types"] == ["record"]

    @pytest.mark.asyncio
    async def test_query_long_truncated(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(app_ids=("app-1",), kb_ids=(), is_empty=lambda: False)
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(state, query="x" * 600)
        assert ok is True
        assert len(mock_service.get_nodes.call_args.kwargs["q"]) == 500

    @pytest.mark.asyncio
    async def test_source_ids_no_match_falls_back(self) -> None:
        from app.agents.actions.knowledge_graph.ops.listing import execute_list_files

        scope = SimpleNamespace(
            app_ids=("app-1",), kb_ids=("kb-1",), is_empty=lambda: False
        )
        resp = _response(items=[])

        mock_service = AsyncMock()
        mock_service.get_nodes = AsyncMock(return_value=resp)

        state: dict[str, Any] = {
            "graph_provider": AsyncMock(),
            "org_id": "org-1",
            "user_id": "u-1",
        }
        with (
            patch(
                "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
                new_callable=AsyncMock,
                return_value=scope,
            ),
            patch(
                "app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService",
                return_value=mock_service,
            ),
            patch("app.modules.agents.qna.chat_state.remember_record_ids"),
            patch(
                "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                return_value=None,
            ),
        ):
            ok, _ = await execute_list_files(state, source_ids=["unknown-id"])
        assert ok is True
        call_kwargs = mock_service.get_nodes.call_args.kwargs
        assert call_kwargs["connector_ids"] == ["app-1"]
        assert call_kwargs["record_group_ids"] == ["kb-1"]
