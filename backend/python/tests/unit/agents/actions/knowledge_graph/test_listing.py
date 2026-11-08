"""Tests for ``app.agents.actions.knowledge_graph.ops.listing`` helpers."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.listing import (
    _asks_for_contents,
    _normalize_list,
    _record_ids_in_items,
    _render_flat_text,
    _wants_flat_listing,
    execute_list_files,
)
from app.agents.actions.knowledge_graph.ops.scope import (
    KnowledgeScope,
    browsable_app_ids,
    turn_picked_sources,
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



class TestExecuteListFiles:
    @pytest.mark.asyncio
    async def test_no_state(self) -> None:

        ok, msg = await execute_list_files(None)
        assert ok is False
        assert "not initialized" in msg

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:

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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1", "app-2"), kb_ids=("kb-1",))
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())

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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=())
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

        scope = KnowledgeScope(app_ids=("app-1",), kb_ids=("kb-1",))
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
        assert call_kwargs["connector_ids"] == ["app-1", "kb-1"]
        assert "record_group_ids" not in call_kwargs
        assert call_kwargs["flattened"] is False


# ---------------------------------------------------------------------------
# Knowledge bases are apps: listing what is inside a source
# ---------------------------------------------------------------------------

class TestBrowsableAppIds:
    def test_apps_then_kbs_without_duplicates(self) -> None:
        scope = KnowledgeScope(app_ids=("jira", "kb-a"), kb_ids=("kb-a", "kb-b"))
        assert browsable_app_ids({}, scope) == ["jira", "kb-a", "kb-b"]

    def test_empty(self) -> None:
        assert browsable_app_ids({}, KnowledgeScope(app_ids=(), kb_ids=())) == []

    def test_leaves_out_the_turns_readable_attachments(self) -> None:
        scope = KnowledgeScope(app_ids=("jira",), kb_ids=("vrid-1", "kb-a"))
        assert browsable_app_ids({"attachment_record_ids": ["vrid-1"]}, scope) == ["jira", "kb-a"]

    def test_client_attachment_metadata_cannot_drop_a_kb(self) -> None:
        scope = KnowledgeScope(app_ids=(), kb_ids=("kb-a",))
        state = {"attachments": [{"virtualRecordId": "kb-a"}]}
        assert browsable_app_ids(state, scope) == ["kb-a"]


class TestTurnPickedSources:
    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ({}, False),
            ({"filters": {"apps": [], "kb": []}}, False),
            ({"filters": {"kb": ["vrid-1"]}, "attachment_record_ids": ["vrid-1"]}, False),
            ({"filters": {"kb": ["vrid-1"], "strictScope": True}, "attachment_record_ids": ["vrid-1"]}, True),
            ({"filters": {"kb": ["kb-a"]}}, True),
            ({"filters": {"apps": ["jira"], "kb": ["vrid-1"]}, "attachment_record_ids": ["vrid-1"]}, True),
        ],
    )
    def test_truth_table(self, state: dict[str, Any], expected: bool) -> None:
        assert turn_picked_sources(state) is expected


class TestWantsFlatListing:
    @pytest.mark.parametrize(
        ("query", "matched", "wants_contents", "single_source", "expected"),
        [
            ("policy", ["kb-1"], True, True, None),
            (None, [], False, False, False),
            (None, [], False, True, False),
            (None, [], True, False, False),
            (None, [], True, True, True),
            (None, ["kb-1"], False, False, True),
            (None, ["kb-1"], True, False, True),
        ],
    )
    def test_truth_table(
        self, query: str | None, matched: list[str], wants_contents: bool, single_source: bool,
        expected: bool | None,
    ) -> None:
        assert _wants_flat_listing(
            query, matched, wants_contents=wants_contents, single_source=single_source,
        ) is expected

    @pytest.mark.parametrize(
        ("node_types", "record_types", "expected"),
        [
            (None, None, False),
            (["app"], None, False),
            (["recordGroup", "app"], None, False),
            (["record"], None, True),
            (["app", "folder"], None, True),
            (None, ["FILE"], True),
        ],
    )
    def test_asks_for_contents(
        self, node_types: list[str] | None, record_types: list[str] | None, expected: bool,
    ) -> None:
        assert _asks_for_contents(node_types, record_types) is expected


async def _list_files(
    scope: KnowledgeScope, state_extra: dict[str, Any] | None = None,
    response: SimpleNamespace | None = None, **kwargs: object,
) -> tuple[dict[str, Any] | None, str]:
    """The service call ``list_files`` makes (None when it makes none) and its text."""
    mock_service = AsyncMock()
    mock_service.get_nodes = AsyncMock(return_value=response or _response(items=[]))
    state: dict[str, Any] = {
        "graph_provider": AsyncMock(),
        "org_id": "org-1",
        "user_id": "u-1",
        "has_knowledge": True,
        **(state_extra or {}),
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
        ok, text = await execute_list_files(state, **kwargs)
    assert ok is True
    call = mock_service.get_nodes.call_args
    return (call.kwargs if call else None), text


_JIRA_AND_KB = KnowledgeScope(app_ids=("jira",), kb_ids=("kb-a",))


class TestListFilesInsideKnowledgeBase:
    @pytest.mark.asyncio
    async def test_records_of_a_named_kb_are_listed_flat(self) -> None:
        call, _ = await _list_files(_JIRA_AND_KB, source_ids=["kb-a"], node_types=["record"])
        assert call["flattened"] is True
        assert call["connector_ids"] == ["kb-a"]
        assert call["parent_id"] is None
        assert "record_group_ids" not in call

    @pytest.mark.asyncio
    async def test_no_narrowing_lists_the_root(self) -> None:
        call, text = await _list_files(_JIRA_AND_KB)
        assert call["flattened"] is False
        assert call["connector_ids"] == ["jira", "kb-a"]
        assert "source_ids" not in text

    @pytest.mark.asyncio
    async def test_app_node_type_only_lists_the_root(self) -> None:
        call, _ = await _list_files(_JIRA_AND_KB, node_types=["app"])
        assert call["flattened"] is False

    @pytest.mark.asyncio
    async def test_records_across_sources_list_the_root_and_ask_for_one(self) -> None:
        call, text = await _list_files(_JIRA_AND_KB, node_types=["record"])
        assert call["flattened"] is False
        assert call["connector_ids"] == ["jira", "kb-a"]
        assert text.startswith("To list records or folders, name one source")
        assert call["node_types"] is None

    @pytest.mark.asyncio
    async def test_records_of_the_only_source_are_listed_flat(self) -> None:
        call, _ = await _list_files(KnowledgeScope(app_ids=(), kb_ids=("kb-a",)), record_types=["FILE"])
        assert call["flattened"] is True
        assert call["connector_ids"] == ["kb-a"]

    @pytest.mark.asyncio
    async def test_unknown_source_ids_list_the_root_with_a_note(self) -> None:
        call, text = await _list_files(
            _JIRA_AND_KB, source_ids=["kb-hr-not-readable"], node_types=["record"], query="policy",
        )
        assert call["flattened"] is False
        assert call["q"] is None
        assert call["connector_ids"] == ["jira", "kb-a"]
        assert text.startswith("None of the named sources is available in this conversation")

    @pytest.mark.asyncio
    async def test_unknown_source_in_a_single_source_scope_is_not_flattened(self) -> None:
        call, _ = await _list_files(
            KnowledgeScope(app_ids=(), kb_ids=("kb-a",)), source_ids=["kb-other"], node_types=["record"],
        )
        assert call["flattened"] is False

    @pytest.mark.asyncio
    async def test_query_searches(self) -> None:
        call, _ = await _list_files(
            KnowledgeScope(app_ids=(), kb_ids=("kb-a",)), query="contract", source_ids=["kb-a"],
        )
        assert call["flattened"] is None
        assert call["q"] == "contract"

    @pytest.mark.asyncio
    async def test_too_short_query_with_source_lists_flat(self) -> None:
        call, _ = await _list_files(
            KnowledgeScope(app_ids=(), kb_ids=("kb-a",)), query="a", source_ids=["kb-a"],
        )
        assert call["flattened"] is True
        assert call["q"] is None

    @pytest.mark.asyncio
    async def test_attachment_in_kb_is_not_a_connector_filter(self) -> None:
        call, _ = await _list_files(
            KnowledgeScope(app_ids=("jira",), kb_ids=("kb-a", "vrid-1")),
            {"attachment_record_ids": ["vrid-1"]},
            source_ids=["kb-a", "vrid-1"],
        )
        assert call["connector_ids"] == ["kb-a"]

    @pytest.mark.asyncio
    async def test_default_chat_with_an_attachment_lists_the_users_sources(self) -> None:
        call, _ = await _list_files(
            KnowledgeScope(app_ids=(), kb_ids=("vrid-1",)),
            {"attachment_record_ids": ["vrid-1"], "filters": {"kb": ["vrid-1"]}},
        )
        assert call["connector_ids"] is None
        assert call["flattened"] is False

    @pytest.mark.asyncio
    async def test_strict_attachment_only_turn_lists_nothing(self) -> None:
        call, text = await _list_files(
            KnowledgeScope(app_ids=(), kb_ids=("vrid-1",)),
            {"attachment_record_ids": ["vrid-1"], "filters": {"kb": ["vrid-1"], "strictScope": True}},
            query="report",
        )
        assert call is None
        assert text == "This conversation has no sources to list; only attached files."

    @pytest.mark.asyncio
    async def test_empty_scope_without_knowledge_is_refused(self) -> None:
        mock_state = {"graph_provider": AsyncMock(), "org_id": "org-1", "user_id": "u-1"}
        with patch(
            "app.agents.actions.knowledge_graph.ops.scope.resolve_scope",
            new_callable=AsyncMock,
            return_value=KnowledgeScope(app_ids=(), kb_ids=()),
        ):
            ok, text = await execute_list_files(mock_state)
        assert ok is False
        assert text == "No knowledge sources configured for this agent."


class TestListFilesPagingAndErrors:
    @pytest.mark.asyncio
    async def test_more_pages_end_with_the_next_call(self) -> None:
        response = SimpleNamespace(
            success=True, error=None, total=None,
            items=[_item("record", f"rec-{i}", f"file {i}") for i in range(20)],
            pagination=SimpleNamespace(hasNext=True),
        )
        _, text = await _list_files(_JIRA_AND_KB, response=response, source_ids=["kb-a"], page=2)
        assert "(More items follow: call again with page=3.)" in text

    @pytest.mark.asyncio
    async def test_last_page_has_no_next_call(self) -> None:
        response = SimpleNamespace(
            success=True, error=None, total=None,
            items=[_item("record", "rec-1")], pagination=SimpleNamespace(hasNext=False),
        )
        _, text = await _list_files(_JIRA_AND_KB, response=response, source_ids=["kb-a"])
        assert "More items follow" not in text

    @pytest.mark.asyncio
    async def test_too_large_listing_asks_to_narrow(self) -> None:
        response = SimpleNamespace(
            success=False, error="Open a folder", errorCode=422, items=[], total=None,
        )
        _, text = await _list_files(_JIRA_AND_KB, response=response, source_ids=["jira"])
        assert text.startswith("Error: This listing is too large to read at once. Name one source")
