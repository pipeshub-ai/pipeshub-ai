"""Unit tests for ``app.utils.record_tool_helpers`` — shared plumbing for the
``lookup_record``/``navigate`` graph-navigation tools.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.utils.record_tool_helpers import (
    NodeRefMapper,
    build_pagination,
    check_record_access,
    describe_tool_call,
    register_tool_describer,
    render_node_view_markdown,
    resolve_org_connector_ids,
    resolve_user_key,
    truncate_name,
)


# ---------------------------------------------------------------------------
# check_record_access — permission gate must not leak existence
# ---------------------------------------------------------------------------


class TestCheckRecordAccess:
    @pytest.mark.asyncio
    async def test_returns_document_when_accessible(self) -> None:
        provider = MagicMock()
        provider.check_record_access_with_details = AsyncMock(return_value={"allowed": True})
        provider.get_document = AsyncMock(return_value={"_key": "r1", "recordName": "Doc"})

        result = await check_record_access(provider, "user-1", "org-1", "r1")

        assert result == {"_key": "r1", "recordName": "Doc"}

    @pytest.mark.asyncio
    async def test_returns_none_when_access_denied(self) -> None:
        provider = MagicMock()
        provider.check_record_access_with_details = AsyncMock(return_value=None)
        provider.get_document = AsyncMock(return_value={"_key": "r1"})

        result = await check_record_access(provider, "user-1", "org-1", "r1")

        assert result is None
        provider.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_and_denied_are_indistinguishable(self) -> None:
        """Both a nonexistent record and one the caller can't see must return
        None — callers must never be able to tell the two apart."""
        provider_missing = MagicMock()
        provider_missing.check_record_access_with_details = AsyncMock(return_value=None)

        provider_denied = MagicMock()
        provider_denied.check_record_access_with_details = AsyncMock(return_value=None)

        result_missing = await check_record_access(provider_missing, "u", "o", "does-not-exist")
        result_denied = await check_record_access(provider_denied, "u", "o", "exists-but-denied")

        assert result_missing == result_denied is None

    @pytest.mark.asyncio
    async def test_permission_check_exception_returns_none(self) -> None:
        provider = MagicMock()
        provider.check_record_access_with_details = AsyncMock(side_effect=RuntimeError("db down"))

        result = await check_record_access(provider, "u", "o", "r1")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_exception_returns_none(self) -> None:
        provider = MagicMock()
        provider.check_record_access_with_details = AsyncMock(return_value={"allowed": True})
        provider.get_document = AsyncMock(side_effect=RuntimeError("db down"))

        result = await check_record_access(provider, "u", "o", "r1")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_required_args_returns_none_without_calling_provider(self) -> None:
        provider = MagicMock()
        provider.check_record_access_with_details = AsyncMock()

        result = await check_record_access(provider, "", "org-1", "r1")

        assert result is None
        provider.check_record_access_with_details.assert_not_awaited()


# ---------------------------------------------------------------------------
# resolve_org_connector_ids — org scoping
# ---------------------------------------------------------------------------


class TestResolveOrgConnectorIds:
    @pytest.mark.asyncio
    async def test_no_hint_returns_all_org_apps(self) -> None:
        provider = MagicMock()
        apps = [{"_key": "c1", "type": "JIRA"}, {"_key": "c2", "type": "DRIVE"}]
        provider.get_org_apps = AsyncMock(return_value=apps)

        result = await resolve_org_connector_ids(provider, "org-1")

        assert result == apps

    @pytest.mark.asyncio
    async def test_hint_narrows_to_matching_type(self) -> None:
        provider = MagicMock()
        apps = [{"_key": "c1", "type": "JIRA", "name": "Jira"}, {"_key": "c2", "type": "DRIVE", "name": "Drive"}]
        provider.get_org_apps = AsyncMock(return_value=apps)

        result = await resolve_org_connector_ids(provider, "org-1", "jira")

        assert result == [apps[0]]

    @pytest.mark.asyncio
    async def test_hint_matches_by_name_too(self) -> None:
        provider = MagicMock()
        apps = [{"_key": "c1", "type": "CUSTOM", "name": "My Jira Instance"}]
        provider.get_org_apps = AsyncMock(return_value=apps)

        result = await resolve_org_connector_ids(provider, "org-1", "JIRA")

        assert result == apps

    @pytest.mark.asyncio
    async def test_unmatched_hint_falls_back_to_all_apps_not_empty(self) -> None:
        """An unmatched connector_name hint is advisory, not a hard filter —
        it must never silently narrow the search to nothing."""
        provider = MagicMock()
        apps = [{"_key": "c1", "type": "JIRA"}]
        provider.get_org_apps = AsyncMock(return_value=apps)

        result = await resolve_org_connector_ids(provider, "org-1", "NONEXISTENT_CONNECTOR")

        assert result == apps

    @pytest.mark.asyncio
    async def test_empty_apps_returns_empty(self) -> None:
        provider = MagicMock()
        provider.get_org_apps = AsyncMock(return_value=[])

        result = await resolve_org_connector_ids(provider, "org-1", "JIRA")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_org_apps_exception_returns_empty(self) -> None:
        provider = MagicMock()
        provider.get_org_apps = AsyncMock(side_effect=RuntimeError("boom"))

        result = await resolve_org_connector_ids(provider, "org-1")

        assert result == []

    @pytest.mark.asyncio
    async def test_never_returns_apps_outside_the_given_org(self) -> None:
        """resolve_org_connector_ids must only ever return what get_org_apps(org_id)
        returns — it has no path to pull in another org's connectors."""
        provider = MagicMock()
        org_a_apps = [{"_key": "a1", "type": "JIRA"}]
        provider.get_org_apps = AsyncMock(return_value=org_a_apps)

        result = await resolve_org_connector_ids(provider, "org-a", connector_name=None)

        provider.get_org_apps.assert_awaited_once_with("org-a")
        assert result == org_a_apps


# ---------------------------------------------------------------------------
# resolve_user_key
# ---------------------------------------------------------------------------


class TestResolveUserKey:
    @pytest.mark.asyncio
    async def test_returns_key_when_found(self) -> None:
        provider = MagicMock()
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u-key-1"})

        result = await resolve_user_key(provider, "auth-user-id")

        assert result == "u-key-1"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self) -> None:
        provider = MagicMock()
        provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await resolve_user_key(provider, "auth-user-id")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        provider = MagicMock()
        provider.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("boom"))

        result = await resolve_user_key(provider, "auth-user-id")

        assert result is None


# ---------------------------------------------------------------------------
# truncate_name
# ---------------------------------------------------------------------------


class TestTruncateName:
    def test_none_returns_untitled(self) -> None:
        assert truncate_name(None) == "(untitled)"

    def test_empty_string_returns_untitled(self) -> None:
        assert truncate_name("   ") == "(untitled)"

    def test_short_name_unchanged(self) -> None:
        assert truncate_name("Short name") == "Short name"

    def test_long_name_truncated_with_ellipsis(self) -> None:
        name = "x" * 100
        result = truncate_name(name, max_len=80)
        assert len(result) <= 80
        assert result.endswith("…")

    def test_strips_surrounding_whitespace(self) -> None:
        assert truncate_name("  Padded  ") == "Padded"


# ---------------------------------------------------------------------------
# NodeRefMapper
# ---------------------------------------------------------------------------


class TestNodeRefMapper:
    def test_mints_sequential_refs(self) -> None:
        mapper = NodeRefMapper()
        assert mapper.get_or_create_ref("id-1") == "n1"
        assert mapper.get_or_create_ref("id-2") == "n2"

    def test_ref_minting_is_stable_for_same_id(self) -> None:
        mapper = NodeRefMapper()
        first = mapper.get_or_create_ref("id-1")
        second = mapper.get_or_create_ref("id-1")
        assert first == second == "n1"

    def test_get_or_create_ref_with_none_returns_empty_string(self) -> None:
        mapper = NodeRefMapper()
        assert mapper.get_or_create_ref(None) == ""

    def test_resolve_ref_returns_underlying_id(self) -> None:
        mapper = NodeRefMapper()
        ref = mapper.get_or_create_ref("some-uuid")
        assert mapper.resolve(ref) == "some-uuid"

    def test_resolve_unknown_ref_returns_input_unchanged(self) -> None:
        """A model that forgets to use the short ref (or pastes a raw UUID)
        must still work — resolve() falls back to treating input as an ID."""
        mapper = NodeRefMapper()
        assert mapper.resolve("some-raw-uuid-not-a-ref") == "some-raw-uuid-not-a-ref"

    def test_resolve_none_returns_none(self) -> None:
        mapper = NodeRefMapper()
        assert mapper.resolve(None) is None

    def test_ref_to_id_property_reflects_all_mappings(self) -> None:
        mapper = NodeRefMapper()
        mapper.get_or_create_ref("id-1")
        mapper.get_or_create_ref("id-2")
        assert mapper.ref_to_id == {"n1": "id-1", "n2": "id-2"}

    def test_ref_to_id_returns_a_copy(self) -> None:
        mapper = NodeRefMapper()
        mapper.get_or_create_ref("id-1")
        snapshot = mapper.ref_to_id
        snapshot["n99"] = "tampered"
        assert "n99" not in mapper.ref_to_id


# ---------------------------------------------------------------------------
# build_pagination
# ---------------------------------------------------------------------------


class TestBuildPagination:
    def test_first_page_of_multiple(self) -> None:
        result = build_pagination(page=1, limit=20, total_items=45)
        assert result == {
            "page": 1,
            "limit": 20,
            "totalItems": 45,
            "totalPages": 3,
            "hasNext": True,
            "hasPrev": False,
        }

    def test_last_page_has_no_next(self) -> None:
        result = build_pagination(page=3, limit=20, total_items=45)
        assert result["hasNext"] is False
        assert result["hasPrev"] is True

    def test_zero_items(self) -> None:
        result = build_pagination(page=1, limit=20, total_items=0)
        assert result["totalPages"] == 0
        assert result["hasNext"] is False
        assert result["hasPrev"] is False

    def test_exact_multiple_of_limit(self) -> None:
        result = build_pagination(page=2, limit=10, total_items=20)
        assert result["totalPages"] == 2
        assert result["hasNext"] is False


# ---------------------------------------------------------------------------
# render_node_view_markdown
# ---------------------------------------------------------------------------


class TestRenderNodeViewMarkdown:
    def test_renders_header_breadcrumbs_and_items(self) -> None:
        view = {
            "current": {"ref": "n3", "id": "uuid-3", "type": "TICKET", "name": "Payment outage", "webUrl": "https://x/PA-1", "status": "OPEN", "connector": "Jira"},
            "breadcrumb_path": "Jira › Payments › PA-1787",
            "items": [{"ref": "n4", "type": "COMMENT", "name": "Root cause", "expandable": False, "detail": "2026-07-20"}],
            "pagination": build_pagination(1, 20, 1),
        }
        markdown = render_node_view_markdown(view)

        assert "[TICKET] Payment outage" in markdown
        assert "{n3}" in markdown
        assert "Path: Jira › Payments › PA-1787" in markdown
        assert "URL: https://x/PA-1" in markdown
        assert "Status: OPEN" in markdown
        assert "Connector: Jira" in markdown
        assert "n4 [COMMENT] Root cause" in markdown

    def test_expandable_item_gets_marker(self) -> None:
        view = {"items": [{"ref": "n1", "type": "FOLDER", "name": "Docs", "expandable": True}]}
        markdown = render_node_view_markdown(view)
        assert "▸" in markdown

    def test_empty_items_shows_placeholder(self) -> None:
        view = {"items": []}
        markdown = render_node_view_markdown(view)
        assert "(empty)" in markdown

    def test_related_section_rendered_when_present(self) -> None:
        view = {
            "items": [],
            "related": [{"ref": "n9", "type": "CONFLUENCE_PAGE", "name": "Agent Loop Doc", "detail": "LINKED_TO, Confluence"}],
        }
        markdown = render_node_view_markdown(view)
        assert "## Related" in markdown
        assert "n9 [CONFLUENCE_PAGE] Agent Loop Doc" in markdown

    def test_delta_page_skips_header_breadcrumbs_and_related(self) -> None:
        """page > 1 responses must be cheap: no header/breadcrumbs/related, items only."""
        view = {
            "current": {"ref": "n3", "id": "uuid-3", "type": "TICKET", "name": "Payment outage"},
            "breadcrumb_path": "Jira › Payments › PA-1787",
            "items": [{"ref": "n10", "type": "COMMENT", "name": "Second comment"}],
            "related": [{"ref": "n9", "type": "CONFLUENCE_PAGE", "name": "Linked doc"}],
            "pagination": build_pagination(2, 20, 40),
            "is_delta": True,
        }
        markdown = render_node_view_markdown(view)
        assert "# [TICKET]" not in markdown
        assert "Path:" not in markdown
        assert "## Related" not in markdown
        assert "n10 [COMMENT] Second comment" in markdown

    def test_pagination_hint_uses_current_ref_when_present(self) -> None:
        view = {
            "current": {"ref": "n3", "id": "uuid-3"},
            "items": [{"ref": "n4", "type": "FILE", "name": "a.txt"}],
            "pagination": build_pagination(1, 1, 2),
        }
        markdown = render_node_view_markdown(view)
        assert 'navigate(node_id="n3", page=2)' in markdown
        assert 'fetch_full_record(record_ids=["uuid-3"])' in markdown

    def test_pagination_hint_without_current_omits_node_id(self) -> None:
        view = {
            "items": [{"ref": "n1", "type": "APP", "name": "Jira"}],
            "pagination": build_pagination(1, 1, 2),
        }
        markdown = render_node_view_markdown(view)
        assert "navigate(page=2)" in markdown

    def test_no_hints_when_last_page_and_no_current(self) -> None:
        view = {"items": [{"ref": "n1", "type": "APP", "name": "Jira"}]}
        markdown = render_node_view_markdown(view)
        assert "More:" not in markdown
        assert "Read:" not in markdown


# ---------------------------------------------------------------------------
# Tool-describer registry
# ---------------------------------------------------------------------------


class TestToolDescriberRegistry:
    def test_registered_describer_is_used(self) -> None:
        register_tool_describer("test_tool_xyz", lambda args: f"Doing {args.get('thing')}")
        assert describe_tool_call("test_tool_xyz", {"thing": "stuff"}) == "Doing stuff"

    def test_unregistered_tool_gets_generic_fallback(self) -> None:
        assert describe_tool_call("some_unregistered_tool_abc", {}) == "Using some_unregistered_tool_abc…"

    def test_describer_exception_falls_back_to_generic(self) -> None:
        register_tool_describer("test_tool_raises", lambda args: 1 / 0)
        assert describe_tool_call("test_tool_raises", {}) == "Using test_tool_raises…"

    def test_none_args_does_not_raise(self) -> None:
        register_tool_describer("test_tool_none_ok", lambda args: f"args={args}")
        assert describe_tool_call("test_tool_none_ok", None) == "args={}"
