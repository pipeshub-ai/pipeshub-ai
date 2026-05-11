"""Comprehensive unit tests for `app.agents.actions.confluence.confluence`.

Covers:
* 11 Pydantic input schemas
* `_handle_response` (success/204/JSON-error/non-JSON/parsing-failure paths)
* `_resolve_space_id` (numeric pass-through, key match, `~` prefix variants,
  not-found, exception)
* All 12 `@tool` methods: success + URL-construction + validation + API-error
  + exception paths.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.confluence.confluence import (
    CommentOnPageInput,
    Confluence,
    CreatePageInput,
    GetChildPagesInput,
    GetPageContentInput,
    GetPagesInSpaceInput,
    GetPageVersionsInput,
    GetSpaceInput,
    SearchContentInput,
    SearchPagesInput,
    SearchUsersInput,
    UpdatePageInput,
    UpdatePageTitleInput,
    _ORDER_BY_PATTERN,
)
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.external.confluence.confluence import (
    _escape_cql_literal,
    _validate_authorship_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status: int, body=None):
    resp = MagicMock()
    resp.status = status
    resp.is_json = True
    resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = MagicMock(return_value=json.dumps(body) if body is not None else "")
    return resp


def _build_confluence() -> Confluence:
    conf = Confluence.__new__(Confluence)
    conf.client = MagicMock()
    conf._site_url = None
    return conf


def _spaces_response(entries):
    return _mock_response(200, {"results": entries})


# ===========================================================================
# Input schemas
# ===========================================================================

class TestInputSchemas:
    def test_create_page_input(self):
        data = CreatePageInput(space_id="SD", page_title="t", page_content="<p/>")
        assert data.space_id == "SD"
        assert data.page_title == "t"

    def test_get_page_content_input(self):
        assert GetPageContentInput(page_id="123").page_id == "123"

    def test_get_pages_in_space_input(self):
        assert GetPagesInSpaceInput(space_id="SD").space_id == "SD"

    def test_update_page_title_input(self):
        d = UpdatePageTitleInput(page_id="1", new_title="t2")
        assert d.new_title == "t2"

    def test_search_pages_input_defaults(self):
        assert SearchPagesInput(title="Plan").space_id is None

    def test_search_pages_input_with_space(self):
        assert SearchPagesInput(title="Plan", space_id="SD").space_id == "SD"

    def test_get_space_input(self):
        assert GetSpaceInput(space_id="1").space_id == "1"

    def test_update_page_input_defaults(self):
        d = UpdatePageInput(page_id="1")
        assert d.page_title is None and d.page_content is None

    def test_comment_on_page_input_defaults(self):
        d = CommentOnPageInput(page_id="1", comment_text="hi")
        assert d.parent_comment_id is None

    def test_get_child_pages_input(self):
        assert GetChildPagesInput(page_id="1").page_id == "1"

    def test_get_page_versions_input(self):
        assert GetPageVersionsInput(page_id="1").page_id == "1"

    def test_search_content_input_defaults(self):
        d = SearchContentInput(query="q")
        assert d.space_id is None
        assert d.content_types is None
        assert d.limit == 25

    def test_search_content_input_full(self):
        d = SearchContentInput(query="q", space_id="SD", content_types=["page"], limit=10)
        assert d.content_types == ["page"]
        assert d.limit == 10

    # ---- New filter-slot schema tests ----

    def test_search_content_input_query_optional(self):
        """`query` is Optional[str] — calling with no args should not raise."""
        d = SearchContentInput()
        assert d.query is None
        # Authorship-only call shape (the "what did I update?" path).
        d2 = SearchContentInput(contributor="currentUser()")
        assert d2.contributor == "currentUser()"
        assert d2.query is None

    def test_search_content_input_authorship_slots(self):
        d = SearchContentInput(
            contributor="currentUser()",
            creator='"abc-123"',
            mention="currentUser()",
            last_modifier='"def-456"',
        )
        assert d.contributor == "currentUser()"
        assert d.creator == '"abc-123"'
        assert d.mention == "currentUser()"
        assert d.last_modifier == '"def-456"'

    def test_search_content_input_temporal_slots(self):
        d = SearchContentInput(
            last_modified_after='now("-7d")',
            last_modified_before="2026-05-01",
            created_after='startOfMonth()',
            created_before="2026-04-30",
        )
        assert d.last_modified_after == 'now("-7d")'
        assert d.created_after == 'startOfMonth()'

    def test_search_content_input_labels_and_order(self):
        d = SearchContentInput(labels=["onboarding", "qa"], order_by="lastmodified desc")
        assert d.labels == ["onboarding", "qa"]
        assert d.order_by == "lastmodified desc"

    def test_search_pages_input_filter_slots(self):
        d = SearchPagesInput(
            title="FAQ",
            creator="currentUser()",
            labels=["onboarding"],
            order_by="created desc",
        )
        assert d.creator == "currentUser()"
        assert d.labels == ["onboarding"]
        assert d.order_by == "created desc"

    def test_search_users_input_defaults(self):
        d = SearchUsersInput(query="John Doe")
        assert d.query == "John Doe"
        assert d.max_results == 10

    def test_search_users_input_query_required(self):
        """`query` is required (no default) — Pydantic must reject missing field."""
        with pytest.raises(Exception):  # ValidationError subclass
            SearchUsersInput()

    def test_get_pages_in_space_input_with_sort(self):
        d = GetPagesInSpaceInput(space_id="SD", sort_by="-modified-date", limit=10)
        assert d.sort_by == "-modified-date"
        assert d.limit == 10

    def test_get_pages_in_space_input_defaults(self):
        d = GetPagesInSpaceInput(space_id="SD")
        assert d.sort_by is None
        assert d.limit is None

    def test_get_child_pages_input_with_sort(self):
        d = GetChildPagesInput(page_id="1", sort_by="title", limit=5)
        assert d.sort_by == "title"
        assert d.limit == 5


# ===========================================================================
# _handle_response
# ===========================================================================

class TestHandleResponse:
    def test_success_with_body(self):
        ok, payload = _build_confluence()._handle_response(
            _mock_response(200, {"hello": "world"}), "ok",
        )
        data = json.loads(payload)
        assert ok is True
        assert data["data"] == {"hello": "world"}

    def test_success_201(self):
        ok, _ = _build_confluence()._handle_response(_mock_response(201, {"id": "1"}), "ok")
        assert ok is True

    def test_success_204_returns_empty_data(self):
        ok, payload = _build_confluence()._handle_response(_mock_response(204), "ok")
        assert ok is True
        assert json.loads(payload)["data"] == {}

    def test_success_parse_exception_falls_back(self):
        resp = _mock_response(200)
        resp.json = MagicMock(side_effect=ValueError("broken"))
        ok, payload = _build_confluence()._handle_response(resp, "ok")
        assert ok is True
        assert json.loads(payload)["data"] == {}

    def test_error_with_text_body(self):
        resp = _mock_response(500)
        resp.text = MagicMock(return_value="boom")
        ok, payload = _build_confluence()._handle_response(resp, "ignored")
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "HTTP 500"
        assert data["details"] == "boom"


# ===========================================================================
# _resolve_space_id
# ===========================================================================

class TestResolveSpaceId:
    @pytest.mark.asyncio
    async def test_numeric_passthrough(self):
        conf = _build_confluence()
        assert await conf._resolve_space_id("123456") == "123456"
        conf.client.assert_not_called()

    @pytest.mark.asyncio
    async def test_matches_key_exactly(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD", "name": "Sales"},
        ]))
        assert await conf._resolve_space_id("SD") == "99"

    @pytest.mark.asyncio
    async def test_matches_with_tilde_variant(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "42", "key": "~abc123", "name": "Personal"},
        ]))
        assert await conf._resolve_space_id("abc123") == "42"

    @pytest.mark.asyncio
    async def test_strip_tilde_variant_matches(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "42", "key": "abc123", "name": "Personal"},
        ]))
        assert await conf._resolve_space_id("~abc123") == "42"

    @pytest.mark.asyncio
    async def test_matches_by_space_name(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "5", "key": "ENG", "name": "Engineering"},
        ]))
        assert await conf._resolve_space_id("Engineering") == "5"

    @pytest.mark.asyncio
    async def test_skips_non_dict_entries(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            "garbage", {"id": "7", "key": "OK", "name": "Okay"},
        ]))
        assert await conf._resolve_space_id("OK") == "7"

    @pytest.mark.asyncio
    async def test_returns_original_when_not_found(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "1", "key": "OTHER", "name": "Other"},
        ]))
        assert await conf._resolve_space_id("MISSING") == "MISSING"

    @pytest.mark.asyncio
    async def test_returns_original_on_api_error(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_mock_response(500))
        assert await conf._resolve_space_id("MISSING") == "MISSING"

    @pytest.mark.asyncio
    async def test_exception_returns_original(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(side_effect=RuntimeError("boom"))
        assert await conf._resolve_space_id("MISSING") == "MISSING"


# ===========================================================================
# create_page
# ===========================================================================

class TestCreatePage:
    @pytest.mark.asyncio
    async def test_success_numeric_space_id(self):
        conf = _build_confluence()
        conf.client.create_page = AsyncMock(
            return_value=_mock_response(201, {"id": "p1", "spaceId": "99"}),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="99")), \
             patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.create_page("99", "Title", "<p>hi</p>")
        data = json.loads(payload)["data"]
        assert ok is True
        assert data["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_success_non_numeric_space_passthrough(self):
        """When the resolved space is already a key, the int() branch raises
        ValueError and we skip the lookup."""
        conf = _build_confluence()
        conf.client.create_page = AsyncMock(
            return_value=_mock_response(201, {"id": "p1", "spaceId": "SD"}),
        )
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")), \
             patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.create_page("SD", "T", "<p/>")
        assert ok is True
        assert json.loads(payload)["data"]["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_success_no_site_url_skips_url_addition(self):
        conf = _build_confluence()
        conf.client.create_page = AsyncMock(
            return_value=_mock_response(201, {"id": "p1", "spaceId": "SD"}),
        )
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")), \
             patch.object(conf, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await conf.create_page("SD", "T", "<p/>")
        assert ok is True
        assert "url" not in json.loads(payload)["data"]

    @pytest.mark.asyncio
    async def test_url_build_swallows_exceptions(self):
        """If response.json raises while we're trying to build the URL, we
        still return the success result without the URL."""
        conf = _build_confluence()
        resp = _mock_response(201, {"id": "p1", "spaceId": "SD"})
        conf.client.create_page = AsyncMock(return_value=resp)
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")), \
             patch.object(conf, "_get_site_url", AsyncMock(side_effect=RuntimeError("boom"))):
            ok, _ = await conf.create_page("SD", "T", "<p/>")
        assert ok is True  # URL enrichment is best-effort

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.create_page = AsyncMock(return_value=_mock_response(500))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")):
            ok, _ = await conf.create_page("SD", "T", "<p/>")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.create_page = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")):
            ok, payload = await conf.create_page("SD", "T", "<p/>")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_page_content
# ===========================================================================

class TestGetPageContent:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        conf = _build_confluence()
        ok, payload = await conf.get_page_content("not-a-number")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success_with_url_numeric_space(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "p1", "spaceId": "99", "title": "T"}),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_page_content("1")
        data = json.loads(payload)["data"]
        assert data["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_success_non_numeric_space_skips_lookup(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "p1", "spaceId": "SD"}),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_page_content("1")
        assert json.loads(payload)["data"]["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_success_no_site_url(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "p1", "spaceId": "SD"}),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await conf.get_page_content("1")
        assert ok is True
        assert "url" not in json.loads(payload)["data"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(return_value=_mock_response(404))
        ok, _ = await conf.get_page_content("1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await conf.get_page_content("1")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_pages_in_space
# ===========================================================================

class TestGetPagesInSpace:
    @pytest.mark.asyncio
    async def test_success_dict_results(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(
            return_value=_mock_response(200, {"results": [{"id": "p1"}, {"id": "p2"}]}),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="99")), \
             patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_pages_in_space("SD")
        data = json.loads(payload)["data"]
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD/pages/p1"
        assert data["results"][1]["url"] == "https://s/wiki/spaces/SD/pages/p2"

    @pytest.mark.asyncio
    async def test_success_list_payload(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(
            return_value=_mock_response(200, [{"id": "p1"}, {"id": "p2"}]),
        )
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")), \
             patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_pages_in_space("SD")
        data = json.loads(payload)["data"]
        assert data[0]["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(return_value=_mock_response(404))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")):
            ok, _ = await conf.get_pages_in_space("SD")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="SD")):
            ok, _ = await conf.get_pages_in_space("SD")
        assert ok is False


# ===========================================================================
# update_page_title
# ===========================================================================

class TestUpdatePageTitle:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        ok, payload = await _build_confluence().update_page_title("abc", "t")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success(self):
        conf = _build_confluence()
        conf.client.update_page_title = AsyncMock(return_value=_mock_response(200, {"id": "1"}))
        ok, _ = await conf.update_page_title("1", "new")
        assert ok is True

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.update_page_title = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.update_page_title("1", "new")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.update_page_title = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await conf.update_page_title("1", "new")
        assert ok is False
        assert "boom" in json.loads(payload)["error"]


# ===========================================================================
# get_child_pages
# ===========================================================================

class TestGetChildPages:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        ok, payload = await _build_confluence().get_child_pages("abc")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success_dict_results(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(
            return_value=_mock_response(200, {"results": [{"id": "c1"}]}),
        )
        conf.client.get_page_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "1", "spaceId": "99"}),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_child_pages("1")
        data = json.loads(payload)["data"]
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD/pages/c1"

    @pytest.mark.asyncio
    async def test_success_list_results(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(
            return_value=_mock_response(200, [{"id": "c1"}, {"id": "c2"}]),
        )
        conf.client.get_page_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "1", "spaceId": "SD"}),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_child_pages("1")
        data = json.loads(payload)["data"]
        assert data[0]["url"] == "https://s/wiki/spaces/SD/pages/c1"
        assert data[1]["url"] == "https://s/wiki/spaces/SD/pages/c2"

    @pytest.mark.asyncio
    async def test_parent_fetch_fails_skips_url_addition(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(
            return_value=_mock_response(200, {"results": [{"id": "c1"}]}),
        )
        conf.client.get_page_by_id = AsyncMock(return_value=_mock_response(404))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_child_pages("1")
        assert ok is True
        assert "url" not in json.loads(payload)["data"]["results"][0]

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.get_child_pages("1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.get_child_pages("1")
        assert ok is False


# ===========================================================================
# search_pages
# ===========================================================================

def _cql_search_response(items: list, *, status: int = 200):
    """Build a v1 CQL search response shape: ``{"results": [{"content": {...}, ...}]}``.

    Used to mock ``search_pages_cql`` / ``search_full_text`` in the action-layer tests.
    """
    return _mock_response(status, {"results": items})


def _cql_page_item(*, page_id: str, title: str, space_key: str = "", webui: str = ""):
    """One v1-search-shaped page result item — what the CQL endpoint returns."""
    content: dict = {
        "id": page_id,
        "type": "page",
        "title": title,
    }
    if space_key:
        content["space"] = {"key": space_key, "name": space_key}
    if webui:
        content["_links"] = {"webui": webui}
    return {"content": content}


class TestSearchPages:
    """`search_pages` no-filter mode — CQL title pass first, full-text fallback on zero."""

    @pytest.mark.asyncio
    async def test_success_with_space_filter(self):
        """Resolves space_id to numeric, passes through to search_pages_cql."""
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(
                    page_id="p1", title="Plan", space_key="SD",
                    webui="/spaces/SD/pages/p1/Plan",
                ),
            ]),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan", space_id="99")
        data = json.loads(payload)["data"]
        assert ok is True
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD/pages/p1/Plan"
        assert data["results"][0]["title"] == "Plan"
        # Numeric ID forwarded to the CQL builder.
        assert conf.client.search_pages_cql.call_args.kwargs["space_id"] == "99"

    @pytest.mark.asyncio
    async def test_success_list_results(self):
        """Default space-less search returns CQL hits with constructed URLs."""
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(
                    page_id="p1", title="Plan", space_key="SD",
                    webui="/spaces/SD/pages/p1/Plan",
                ),
            ]),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan")
        data = json.loads(payload)["data"]
        assert ok is True
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD/pages/p1/Plan"

    @pytest.mark.asyncio
    async def test_zero_title_match_falls_back_to_full_text(self):
        """When title CQL returns zero, search_full_text fires with content_types=['page']."""
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(
            return_value=_cql_search_response([]),
        )
        conf.client.search_full_text = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(
                    page_id="p2", title="Body match", space_key="SD",
                    webui="/spaces/SD/pages/p2/Body+match",
                ),
            ]),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan")
        data = json.loads(payload)["data"]
        assert ok is True
        assert data["results"][0]["id"] == "p2"
        # Fallback was actually invoked.
        conf.client.search_full_text.assert_awaited_once()
        assert conf.client.search_full_text.call_args.kwargs["content_types"] == ["page"]

    @pytest.mark.asyncio
    async def test_title_rerank_puts_exact_match_first(self):
        """results[0] must be the closest title match — exact > startswith > contains."""
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(page_id="p1", title="My Plan Doc", space_key="SD"),
                _cql_page_item(page_id="p2", title="Plan", space_key="SD"),
                _cql_page_item(page_id="p3", title="Planning Notes", space_key="SD"),
            ]),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan")
        body = json.loads(payload)
        ids = [p["id"] for p in body["data"]["results"]]
        # "Plan" is exact (rank 0), "Planning Notes" starts-with (rank 1),
        # "My Plan Doc" contains (rank 2).
        assert ids == ["p2", "p3", "p1"]
        # Multiple matches with one exact ⇒ note (not warning).
        assert "note" in body and "Exact match" in body["note"]

    @pytest.mark.asyncio
    async def test_multiple_matches_no_exact_emits_warning(self):
        """No exact title match across multiple hits ⇒ warning, not note."""
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(page_id="p1", title="Planning Notes", space_key="SD"),
                _cql_page_item(page_id="p2", title="My Plans", space_key="SD"),
            ]),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            _, payload = await conf.search_pages("plan")
        body = json.loads(payload)
        assert "warning" in body
        assert "no exact title match" in body["warning"]

    @pytest.mark.asyncio
    async def test_filter_mode_routes_through_full_text(self):
        """When ANY filter slot is set (e.g. contributor=currentUser()), the title CQL
        path is skipped and search_full_text is the only call. CQL must include the
        title as a body query plus the filters."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_cql_search_response([
                _cql_page_item(
                    page_id="p9", title="Plan", space_key="SD",
                    webui="/spaces/SD/pages/p9/Plan",
                ),
            ]),
        )
        # search_pages_cql must NOT be called.
        conf.client.search_pages_cql = AsyncMock(return_value=_cql_search_response([]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages(
                "plan",
                contributor="currentUser()",
                last_modified_after='now("-7d")',
                order_by="lastmodified desc",
            )
        assert ok is True
        conf.client.search_full_text.assert_awaited_once()
        conf.client.search_pages_cql.assert_not_awaited()
        # The title becomes the body query in filter mode.
        kwargs = conf.client.search_full_text.call_args.kwargs
        assert kwargs["query"] == "plan"
        assert kwargs["contributor"] == "currentUser()"
        assert kwargs["order_by"] == "lastmodified desc"

    @pytest.mark.asyncio
    async def test_invalid_order_by_rejected_locally(self):
        """A malformed order_by must NOT reach Confluence — rejected with guidance."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_cql_search_response([]))
        ok, payload = await conf.search_pages("plan", order_by="lastmodified; DROP TABLE")
        body = json.loads(payload)
        assert ok is False
        assert "Invalid order_by" in body["error"]
        assert "guidance" in body
        conf.client.search_full_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_filter_mode_value_error_surfaced_as_guidance(self):
        """Datasource ValueError (e.g. invalid contributor value) becomes a clean
        error+guidance tuple, not an unhandled crash."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            side_effect=ValueError("`contributor` value 'bad' is not valid"),
        )
        ok, payload = await conf.search_pages("plan", contributor="bad")
        body = json.loads(payload)
        assert ok is False
        assert "guidance" in body

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.search_pages("plan")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.search_pages_cql = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.search_pages("plan")
        assert ok is False


# ===========================================================================
# search_content
# ===========================================================================

class TestSearchContent:
    @pytest.mark.asyncio
    async def test_success_uses_links_base(self):
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://acme.atlassian.net/wiki"},
            "totalSize": 1,
            "results": [{
                "excerpt": "hello world",
                "content": {
                    "id": "100", "type": "page", "title": "Hello",
                    "space": {"key": "SD", "name": "Sales"},
                    "_links": {"webui": "/spaces/SD/pages/100/Hello"},
                    "version": {"when": "2026-04-01"},
                },
            }],
        }))
        ok, payload = await conf.search_content("hello")
        data = json.loads(payload)
        assert ok is True
        assert data["total_results"] == 1
        assert data["results"][0]["url"] == "https://acme.atlassian.net/wiki/spaces/SD/pages/100/Hello"
        assert data["results"][0]["last_modified"] == "2026-04-01"

    @pytest.mark.asyncio
    async def test_success_resolves_space_id_when_provided(self):
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://acme.atlassian.net/wiki"},
            "totalSize": 0,
            "results": [],
        }))
        with patch.object(conf, "_resolve_space_id", AsyncMock(return_value="99")) as mock_resolve:
            await conf.search_content("q", space_id="SD")
        mock_resolve.assert_awaited_once_with("SD")
        assert conf.client.search_full_text.call_args.kwargs["space_id"] == "99"

    @pytest.mark.asyncio
    async def test_fallback_base_url_when_links_missing(self):
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "totalSize": 1,
            "results": [{
                "excerpt": "",
                "content": {
                    "id": "100", "type": "page", "title": "T",
                    "space": {"key": "SD"},
                    "_links": {"webui": "/spaces/SD/pages/100"},
                },
            }],
        }))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://acme.atlassian.net")):
            ok, payload = await conf.search_content("q")
        assert ok is True
        assert json.loads(payload)["results"][0]["url"] == "https://acme.atlassian.net/wiki/spaces/SD/pages/100"

    @pytest.mark.asyncio
    async def test_fallback_construct_url_from_id_and_space_key(self):
        """When webui is empty we build the URL manually from space_key + id."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://acme.atlassian.net/wiki"},
            "totalSize": 1,
            "results": [{
                "excerpt": "",
                "content": {
                    "id": "200", "type": "page", "title": "T",
                    "space": {"key": "SD"},
                    "_links": {},
                },
            }],
        }))
        ok, payload = await conf.search_content("q")
        assert ok is True
        assert json.loads(payload)["results"][0]["url"] == "https://acme.atlassian.net/wiki/spaces/SD/pages/200"

    @pytest.mark.asyncio
    async def test_last_resort_uses_webui_path_only(self):
        """No base_url anywhere — fall back to the relative webui path."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "totalSize": 1,
            "results": [{
                "excerpt": "",
                "content": {
                    "id": "300", "type": "page", "title": "T",
                    "space": {"key": "SD"},
                    "_links": {"webui": "/spaces/SD/pages/300"},
                },
            }],
        }))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await conf.search_content("q")
        assert json.loads(payload)["results"][0]["url"] == "/spaces/SD/pages/300"

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        resp = _mock_response(500)
        resp.text = MagicMock(return_value="boom")
        conf.client.search_full_text = AsyncMock(return_value=resp)
        ok, payload = await conf.search_content("q")
        data = json.loads(payload)
        assert ok is False
        assert data["error"] == "HTTP 500"

    @pytest.mark.asyncio
    async def test_json_parse_failure(self):
        conf = _build_confluence()
        resp = _mock_response(200)
        resp.json = MagicMock(side_effect=ValueError("broken"))
        conf.client.search_full_text = AsyncMock(return_value=resp)
        ok, payload = await conf.search_content("q")
        assert ok is False
        assert "Failed to parse" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.search_content("q")
        assert ok is False


# ===========================================================================
# get_spaces / get_space
# ===========================================================================

class TestGetSpaces:
    @pytest.mark.asyncio
    async def test_success_dict_results(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(
            return_value=_mock_response(200, {"results": [{"id": "1", "key": "SD"}]}),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_spaces()
        data = json.loads(payload)["data"]
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD"

    @pytest.mark.asyncio
    async def test_success_list_payload(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_mock_response(200, [{"key": "A"}, {"key": "B"}]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_spaces()
        data = json.loads(payload)["data"]
        assert data[0]["url"] == "https://s/wiki/spaces/A"
        assert data[1]["url"] == "https://s/wiki/spaces/B"

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.get_spaces()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_spaces = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.get_spaces()
        assert ok is False


class TestGetSpace:
    @pytest.mark.asyncio
    async def test_invalid_space_id(self):
        ok, payload = await _build_confluence().get_space("abc")
        assert ok is False
        assert "Invalid space_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success(self):
        conf = _build_confluence()
        conf.client.get_space_by_id = AsyncMock(
            return_value=_mock_response(200, {"id": "1", "key": "SD"}),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_space("1")
        assert json.loads(payload)["data"]["url"] == "https://s/wiki/spaces/SD"

    @pytest.mark.asyncio
    async def test_success_no_key_no_url(self):
        conf = _build_confluence()
        conf.client.get_space_by_id = AsyncMock(return_value=_mock_response(200, {"id": "1"}))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.get_space("1")
        assert "url" not in json.loads(payload)["data"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_space_by_id = AsyncMock(return_value=_mock_response(404))
        ok, _ = await conf.get_space("1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_space_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.get_space("1")
        assert ok is False


# ===========================================================================
# update_page
# ===========================================================================

class TestUpdatePage:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        ok, payload = await _build_confluence().update_page("abc", page_title="T")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_no_updates_rejected(self):
        ok, payload = await _build_confluence().update_page("1")
        assert ok is False
        assert "At least one" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_get_current_page_fails(self):
        conf = _build_confluence()
        resp = _mock_response(404)
        resp.text = MagicMock(return_value="not found")
        conf.client.get_page_by_id = AsyncMock(return_value=resp)
        ok, payload = await conf.update_page("1", page_title="T")
        data = json.loads(payload)
        assert ok is False
        assert "Failed to get current page" in data["error"]

    @pytest.mark.asyncio
    async def test_success_title_only_preserves_body(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(return_value=_mock_response(200, {
            "id": "1", "status": "current", "spaceId": "99",
            "version": {"number": 3}, "title": "Old", "body": {"storage": {"value": "<p>old</p>"}},
        }))
        conf.client.update_page = AsyncMock(return_value=_mock_response(200, {"id": "1", "spaceId": "99"}))
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([{"id": "99", "key": "SD"}]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.update_page("1", page_title="New")
        assert ok is True
        sent_body = conf.client.update_page.call_args.kwargs["body"]
        assert sent_body["title"] == "New"
        assert sent_body["body"] == {"storage": {"value": "<p>old</p>"}}
        assert sent_body["version"]["number"] == 4
        assert json.loads(payload)["data"]["url"] == "https://s/wiki/spaces/SD/pages/1"

    @pytest.mark.asyncio
    async def test_success_content_only_preserves_title(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(return_value=_mock_response(200, {
            "id": "1", "status": "current", "spaceId": "99",
            "version": {"number": 1}, "title": "Keep",
        }))
        conf.client.update_page = AsyncMock(return_value=_mock_response(200, {"id": "1"}))
        ok, _ = await conf.update_page("1", page_content="<p>new</p>")
        assert ok is True
        sent = conf.client.update_page.call_args.kwargs["body"]
        assert sent["title"] == "Keep"
        assert sent["body"]["storage"]["value"] == "<p>new</p>"

    @pytest.mark.asyncio
    async def test_update_api_error(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(return_value=_mock_response(200, {
            "id": "1", "status": "current", "spaceId": "99", "version": {"number": 1}, "title": "T",
        }))
        conf.client.update_page = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.update_page("1", page_title="T2")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_page_by_id = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.update_page("1", page_title="T")
        assert ok is False


# ===========================================================================
# get_page_versions
# ===========================================================================

class TestGetPageVersions:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        ok, payload = await _build_confluence().get_page_versions("abc")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success(self):
        conf = _build_confluence()
        conf.client.get_page_versions = AsyncMock(
            return_value=_mock_response(200, {"results": [{"number": 1}]}),
        )
        ok, _ = await conf.get_page_versions("1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_page_versions = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.get_page_versions("1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_page_versions = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.get_page_versions("1")
        assert ok is False


# ===========================================================================
# comment_on_page
# ===========================================================================

class TestCommentOnPage:
    @pytest.mark.asyncio
    async def test_invalid_page_id(self):
        ok, payload = await _build_confluence().comment_on_page("abc", "hi")
        assert ok is False
        assert "Invalid page_id" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_success_escapes_and_wraps_text(self):
        conf = _build_confluence()
        conf.client.create_footer_comment = AsyncMock(
            return_value=_mock_response(201, {"id": "c1"}),
        )
        ok, _ = await conf.comment_on_page("1", "hello <b> & friends\nline2")
        assert ok is True
        body = conf.client.create_footer_comment.call_args.kwargs["body_body"]
        assert body["storage"]["representation"] == "storage"
        assert "hello &lt;b&gt; &amp; friends<br/>line2" in body["storage"]["value"]

    @pytest.mark.asyncio
    async def test_parent_comment_id_forwarded(self):
        conf = _build_confluence()
        conf.client.create_footer_comment = AsyncMock(
            return_value=_mock_response(201, {"id": "c1"}),
        )
        await conf.comment_on_page("1", "reply", parent_comment_id="parent-42")
        assert conf.client.create_footer_comment.call_args.kwargs["parentCommentId"] == "parent-42"

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.create_footer_comment = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.comment_on_page("1", "hi")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.create_footer_comment = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await conf.comment_on_page("1", "hi")
        assert ok is False


# ===========================================================================
# CQL helpers — `_escape_cql_literal`, `_validate_authorship_value`,
# `_ORDER_BY_PATTERN`. These are the building blocks every CQL search uses;
# regressing one of them breaks every search builder at once.
# ===========================================================================

class TestEscapeCQLLiteral:
    """Centralised CQL string-literal escaper."""

    def test_plain_string_passes_through(self):
        assert _escape_cql_literal("hello world") == "hello world"

    def test_double_quotes_escaped(self):
        assert _escape_cql_literal('with "inner"') == 'with \\"inner\\"'

    def test_backslashes_doubled(self):
        # Single backslash → two backslashes in the literal.
        assert _escape_cql_literal("a\\b") == "a\\\\b"

    def test_backslash_escape_runs_before_quote_escape(self):
        # If quote escape ran first, the inserted `\"` would later have its `\`
        # doubled — corrupting the escape. Order matters: `\` before `"`.
        assert _escape_cql_literal('a\\"b') == 'a\\\\\\"b'

    def test_empty_string_returns_empty(self):
        assert _escape_cql_literal("") == ""

    def test_none_returns_empty(self):
        # Defensive — search builders use `if escaped: ...` and rely on this.
        assert _escape_cql_literal(None) == ""

    def test_non_string_coerced(self):
        # Some call sites iterate label lists where elements may not be str.
        assert _escape_cql_literal(123) == "123"


class TestValidateAuthorshipValue:
    """`contributor` / `creator` / `mention` / `last_modifier` value validator.

    Valid forms: ``currentUser()`` (any case, whitespace tolerated) or
    ``"<accountId>"`` (with double quotes). Anything else raises ValueError —
    this is the guard that catches the email-as-contributor bug at the
    datasource boundary.
    """

    def test_currentuser_function_accepted(self):
        assert _validate_authorship_value("contributor", "currentUser()") == "currentUser()"

    def test_currentuser_case_insensitive(self):
        assert _validate_authorship_value("creator", "CURRENTUSER()") == "CURRENTUSER()"

    def test_currentuser_with_inner_whitespace(self):
        # Be forgiving of LLM-emitted variants like `currentUser ()`.
        assert _validate_authorship_value("mention", "currentUser ()") == "currentUser ()"

    def test_currentuser_outer_whitespace_trimmed(self):
        assert _validate_authorship_value("contributor", "  currentUser()  ") == "currentUser()"

    def test_quoted_accountid_accepted(self):
        v = '"712020:67e751b2-e94f-4bcb-b3ac-e4e857755e8a"'
        assert _validate_authorship_value("contributor", v) == v

    def test_short_quoted_accountid_accepted(self):
        assert _validate_authorship_value("creator", '"abc-123"') == '"abc-123"'

    def test_email_rejected(self):
        # The exact bug from the user's traces — email used as contributor.
        with pytest.raises(ValueError) as ei:
            _validate_authorship_value("contributor", "abhishek@pipeshub.com")
        msg = str(ei.value)
        assert "abhishek@pipeshub.com" in msg
        assert "currentUser()" in msg
        assert "search_users" in msg

    def test_bare_name_rejected(self):
        with pytest.raises(ValueError):
            _validate_authorship_value("creator", "Abhishek Gupta")

    def test_unquoted_accountid_rejected(self):
        # Without surrounding double quotes, CQL treats it as a bare identifier
        # and silently returns 0 results. We catch up front.
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", "abc-123")

    def test_currentuser_missing_parens_rejected(self):
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", "currentUser")

    def test_currentuser_with_arg_rejected(self):
        # CQL's currentUser() takes no args — anything inside the parens is invalid.
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", "currentUser(arg)")

    def test_empty_quoted_rejected(self):
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", '""')

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", "")

    def test_none_rejected(self):
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", None)

    def test_field_name_in_error(self):
        # Tells the planner WHICH slot was bad.
        with pytest.raises(ValueError) as ei:
            _validate_authorship_value("last_modifier", "alice@example.com")
        assert "last_modifier" in str(ei.value)

    # ---- CQL-injection regression tests -----------------------------------
    # Earlier the authorship value was passed through verbatim with no
    # validation — an LLM-controlled string could inject a free OR clause and
    # leak content outside the intended filter. The validator must reject
    # every shape these injection attempts can take.

    @pytest.mark.parametrize("payload", [
        # Classic CQL injection — break out of the literal and add an OR.
        '"x" OR creator = "victim-accountid"',
        # Comment-style escape attempt.
        'currentUser() AND 1=1 -- ',
        # Append clause via close-paren.
        'currentUser()) OR contributor = "x"',
        # Double-quoted but with internal break-out.
        '"abc-123" OR contributor = "v"',
        # Function-call shape but not currentUser().
        'logout()',
        # Nested function-call attempt.
        'currentUser(currentUser())',
        # Whitespace-only.
        '   ',
        # Quote not closed.
        '"abc',
    ])
    def test_cql_injection_attempt_rejected(self, payload):
        with pytest.raises(ValueError):
            _validate_authorship_value("contributor", payload)


class TestOrderByPattern:
    """Module-level regex for the `order_by` parameter.

    Compiled once at import time so a malformed pattern fails the test run,
    not a user request. The tests below also lock down the contract: which
    values are accepted (every shape Confluence CQL ORDER BY supports) and
    which are rejected (anything that could let CQL injection through).
    """

    @pytest.mark.parametrize("value", [
        "lastmodified desc",
        "created asc",
        "title",                              # direction defaults to asc
        "lastmodified DESC",                  # case-insensitive
        "lastmodified desc, created asc",     # multi-key
        "  lastmodified  desc  ",             # surrounding/inner whitespace
    ])
    def test_accepts_valid(self, value):
        assert _ORDER_BY_PATTERN.match(value) is not None

    @pytest.mark.parametrize("value", [
        "lastmodified; DROP TABLE",           # semicolon escape attempt
        "lastmodified desc OR 1=1",           # boolean tail
        "1lastmodified desc",                 # cannot start with digit
        "lastmodified desc, , created asc",   # empty segment
        "",                                   # empty
        "lastmodified asc, created INVALID",  # bad direction word
    ])
    def test_rejects_invalid(self, value):
        assert _ORDER_BY_PATTERN.match(value) is None


# ===========================================================================
# search_content with the new filter slots — args flow through, error paths
# return clean tuples, response carries labels / space fallback / perms note.
# ===========================================================================

class TestSearchContentFilters:
    """Verify each new slot threads through to the datasource and the response
    shape carries the new fields."""

    @pytest.mark.asyncio
    async def test_authorship_slot_forwarded(self):
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {
                "results": [{"content": {"id": "p1", "title": "Plan"}}],
                "totalSize": 1,
            }),
        )
        ok, _ = await conf.search_content(
            contributor="currentUser()", order_by="lastmodified desc",
        )
        assert ok is True
        kwargs = conf.client.search_full_text.call_args.kwargs
        assert kwargs["contributor"] == "currentUser()"
        assert kwargs["order_by"] == "lastmodified desc"

    @pytest.mark.asyncio
    async def test_all_slots_compose(self):
        """Combining query + contributor + date + labels + order in one call —
        each value reaches the datasource verbatim."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {"results": [], "totalSize": 0}),
        )
        await conf.search_content(
            query="deployment",
            contributor='"abc-123"',
            last_modified_after='now("-7d")',
            labels=["onboarding", "qa"],
            order_by="lastmodified desc",
        )
        kwargs = conf.client.search_full_text.call_args.kwargs
        assert kwargs["query"] == "deployment"
        assert kwargs["contributor"] == '"abc-123"'
        assert kwargs["last_modified_after"] == 'now("-7d")'
        assert kwargs["labels"] == ["onboarding", "qa"]
        assert kwargs["order_by"] == "lastmodified desc"

    @pytest.mark.asyncio
    async def test_invalid_order_by_short_circuits(self):
        """A malformed order_by must NOT reach Confluence — rejected with guidance."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock()
        ok, payload = await conf.search_content(
            query="x", order_by="lastmodified; DROP TABLE",
        )
        body = json.loads(payload)
        assert ok is False
        assert "Invalid order_by" in body["error"]
        assert "guidance" in body
        # Confluence is never hit when the local validator rejects.
        conf.client.search_full_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_value_error_from_datasource_surfaced(self):
        """When the datasource raises ValueError (bad contributor / empty
        call), the action returns a clean error+guidance tuple — not a crash."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            side_effect=ValueError("`contributor` value 'foo' is not valid"),
        )
        ok, payload = await conf.search_content(contributor="foo")
        body = json.loads(payload)
        assert ok is False
        assert "guidance" in body
        assert "contributor" in body["error"]

    @pytest.mark.asyncio
    async def test_query_normalised_to_none_when_blank(self):
        """Empty / whitespace-only query becomes None at the boundary so the
        datasource correctly omits the `siteSearch ~ ""` clause (which would 400)."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {"results": [], "totalSize": 0}),
        )
        await conf.search_content(query="   ", contributor="currentUser()")
        assert conf.client.search_full_text.call_args.kwargs["query"] is None

    @pytest.mark.asyncio
    async def test_response_surfaces_labels(self):
        """`metadata.labels.results[].name` should appear in each entry's `labels`."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {
                "totalSize": 1,
                "results": [{
                    "content": {
                        "id": "p1", "type": "page", "title": "Hello",
                        "metadata": {"labels": {"results": [
                            {"name": "onboarding"}, {"name": "qa"},
                        ]}},
                    },
                }],
            }),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            _, payload = await conf.search_content(query="hello")
        body = json.loads(payload)
        assert body["results"][0]["labels"] == ["onboarding", "qa"]

    @pytest.mark.asyncio
    async def test_response_omits_labels_when_none_present(self):
        """No labels in the API response ⇒ no `labels` key on the entry."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {
                "totalSize": 1,
                "results": [{"content": {"id": "p1", "type": "page", "title": "Hello"}}],
            }),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            _, payload = await conf.search_content(query="hello")
        body = json.loads(payload)
        assert "labels" not in body["results"][0]

    @pytest.mark.asyncio
    async def test_space_key_falls_back_to_resultGlobalContainer(self):
        """When `content.space` is empty (the v1-search default) the action
        falls back to the top-level `resultGlobalContainer`. This was the bug
        that left every result with empty space_key/space_name."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {
                "totalSize": 1,
                "results": [{
                    "content": {"id": "p1", "type": "page", "title": "Hello"},
                    "resultGlobalContainer": {
                        "title": "Engineering Space",
                        "displayUrl": "/spaces/ENG",
                    },
                }],
            }),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            _, payload = await conf.search_content(query="hello")
        entry = json.loads(payload)["results"][0]
        assert entry["space_key"] == "ENG"
        assert entry["space_name"] == "Engineering Space"

    @pytest.mark.asyncio
    async def test_content_space_preferred_over_container(self):
        """If both `content.space` and `resultGlobalContainer` are present,
        the content.space value wins (it's the more specific source)."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {
                "totalSize": 1,
                "results": [{
                    "content": {
                        "id": "p1", "type": "page", "title": "Hello",
                        "space": {"key": "SD", "name": "Software"},
                    },
                    "resultGlobalContainer": {
                        "title": "Engineering",
                        "displayUrl": "/spaces/ENG",
                    },
                }],
            }),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            _, payload = await conf.search_content(query="hello")
        entry = json.loads(payload)["results"][0]
        assert entry["space_key"] == "SD"
        assert entry["space_name"] == "Software"

    @pytest.mark.asyncio
    async def test_response_includes_permissions_drift_note(self):
        """The action always appends a `note` mentioning ACL filtering so the
        LLM can tell the user when result counts look unexpectedly low."""
        conf = _build_confluence()
        conf.client.search_full_text = AsyncMock(
            return_value=_mock_response(200, {"results": [], "totalSize": 0}),
        )
        _, payload = await conf.search_content(query="hello")
        body = json.loads(payload)
        assert "note" in body
        assert "currently view" in body["note"]


# ===========================================================================
# search_users — single CQL clause, ranked output, disambiguation flow.
# ===========================================================================

def _users_response(items, *, status=200):
    """v1 user-search response shape: each item wrapped in `{"user": {...}}`."""
    return _mock_response(status, {"results": [{"user": u} for u in items]})


class TestSearchUsers:
    """The action's `search_users` tool — escapes query, uses the
    fullname-only CQL fix (no OR'd `user ~` clause that 400s), and returns
    0 / 1 / many shapes with deterministic disambiguation flags."""

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self):
        conf = _build_confluence()
        ok, payload = await conf.search_users("")
        body = json.loads(payload)
        assert ok is False
        assert "Query is required" in body["error"]

    @pytest.mark.asyncio
    async def test_whitespace_only_query_rejected(self):
        conf = _build_confluence()
        ok, _ = await conf.search_users("   ")
        assert ok is False

    @pytest.mark.asyncio
    async def test_single_match_returns_user(self):
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "abc-123", "displayName": "Vishwjeet Pawar"},
            ]),
        )
        ok, payload = await conf.search_users("Vishwjeet")
        body = json.loads(payload)
        assert ok is True
        assert body["total"] == 1
        assert body["results"][0]["accountId"] == "abc-123"
        assert body.get("message") == "User found"
        # No disambiguation noise on the single-match happy path.
        assert "disambiguation_required" not in body
        assert "warning" not in body

    @pytest.mark.asyncio
    async def test_zero_matches_returns_clean_error(self):
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        ok, payload = await conf.search_users("ghost")
        body = json.loads(payload)
        assert ok is False
        assert body["total"] == 0
        assert "guidance" in body

    @pytest.mark.asyncio
    async def test_exact_match_among_many_proceeds_with_note(self):
        """Multiple matches with exactly one exact name match ⇒ proceed with
        the exact one at index 0, and surface a `note` (not a warning)."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "id-1", "displayName": "Abhishek Gupta"},
                {"accountId": "id-2", "displayName": "Abhishek Sharma"},
                {"accountId": "id-3", "displayName": "Abhishek"},
            ]),
        )
        ok, payload = await conf.search_users("abhishek gupta")
        body = json.loads(payload)
        assert ok is True
        assert body["total"] == 3
        # Exact case-insensitive name match is sorted first.
        assert body["results"][0]["accountId"] == "id-1"
        assert "note" in body
        assert "Exact match" in body["note"]
        assert "disambiguation_required" not in body

    @pytest.mark.asyncio
    async def test_no_exact_match_triggers_disambiguation(self):
        """Multiple matches, none exact ⇒ `disambiguation_required: true` and
        a `warning` field telling the planner to stop and ask the user."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "id-1", "displayName": "Abhishek Gupta"},
                {"accountId": "id-2", "displayName": "Abhishek Sharma"},
            ]),
        )
        ok, payload = await conf.search_users("abhishek")
        body = json.loads(payload)
        assert ok is True
        assert body.get("disambiguation_required") is True
        assert "warning" in body
        assert "Stop and ask" in body["warning"]
        # All candidates' accountIds are returned so the caller can pick.
        assert {u["accountId"] for u in body["results"]} == {"id-1", "id-2"}

    @pytest.mark.asyncio
    async def test_users_without_accountid_are_dropped(self):
        """Anonymized accounts have no accountId — useless as CQL filter
        values, so the action skips them rather than confusing the LLM."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "id-1", "displayName": "Real User"},
                {"displayName": "Anonymized"},  # no accountId
            ]),
        )
        ok, payload = await conf.search_users("user")
        body = json.loads(payload)
        assert ok is True
        assert body["total"] == 1
        assert body["results"][0]["accountId"] == "id-1"

    @pytest.mark.asyncio
    async def test_email_input_does_not_break_cql(self):
        """Email inputs run through the same single-clause CQL — no 400 on
        the user-search endpoint (the OR'd `user ~` bug from earlier)."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "id-1", "displayName": "Vishwjeet"},
            ]),
        )
        ok, _ = await conf.search_users("vishwjeet@pipeshub.com")
        assert ok is True
        cql = conf.client.search_users.call_args.kwargs["cql"]
        assert "user.fullname ~" in cql
        # The fix removed the broken `OR user ~ ...` branch.
        assert " OR user ~" not in cql

    @pytest.mark.asyncio
    async def test_input_with_quote_is_escaped(self):
        """`O"Brien`-style input: the embedded quote must be escaped, not raw."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        ok, _ = await conf.search_users('O"Brien')
        # Zero results ⇒ False, but the call must have been built and reached
        # the datasource with properly escaped CQL.
        assert ok is False
        cql = conf.client.search_users.call_args.kwargs["cql"]
        assert '\\"' in cql

    @pytest.mark.asyncio
    async def test_email_match_ranks_zero(self):
        """Exact email match ⇒ rank 0, sorted to index 0."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(
            return_value=_users_response([
                {"accountId": "id-1", "displayName": "John Smith"},
                {"accountId": "id-2", "displayName": "Jordan", "email": "john@x.com"},
            ]),
        )
        ok, payload = await conf.search_users("john@x.com")
        body = json.loads(payload)
        assert ok is True
        assert body["results"][0]["accountId"] == "id-2"
        assert body["results"][0]["rank"] == 0

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self):
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_mock_response(400, "bad"))
        ok, payload = await conf.search_users("x")
        body = json.loads(payload)
        assert ok is False
        assert body["error"] == "HTTP 400"

    @pytest.mark.asyncio
    async def test_max_results_capped_at_50(self):
        """Confluence caps the user-search endpoint at 50; values above are clamped."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        await conf.search_users("john", max_results=200)
        assert conf.client.search_users.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_default_max_results_is_10(self):
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        await conf.search_users("john")
        assert conf.client.search_users.call_args.kwargs["limit"] == 10

    # ---- Wildcard-only guard ---------------------------------------------

    @pytest.mark.asyncio
    async def test_wildcard_only_input_rejected(self):
        """Input of just `'*'` would strip to empty and produce a
        match-everything query (`user.fullname ~ "*"`). Reject up front."""
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        ok, payload = await conf.search_users("*")
        body = json.loads(payload)
        assert ok is False
        assert "non-wildcard characters" in body["error"]
        assert "guidance" in body
        # Confluence is never hit when the local guard rejects.
        conf.client.search_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_multiple_wildcard_only_input_rejected(self):
        conf = _build_confluence()
        conf.client.search_users = AsyncMock(return_value=_users_response([]))
        ok, _ = await conf.search_users("***")
        assert ok is False
        conf.client.search_users.assert_not_awaited()


# ===========================================================================
# _extract_space_info — shared helper used by search_pages (filter mode)
# and search_content. Locks down the v1-CQL space-info fallback chain
# so a regression in either call site fails this class first.
# ===========================================================================

class TestExtractSpaceInfo:
    """The helper reads ``content.space`` first (when ``expand=space``
    populated it) and falls back to the top-level ``resultGlobalContainer``
    (the typical shape for page/blogpost results)."""

    def test_content_space_used_when_present(self):
        item = {
            "content": {"id": "p1", "space": {"key": "SD", "name": "Software"}},
            "resultGlobalContainer": {"title": "X", "displayUrl": "/spaces/X"},
        }
        key, name = Confluence._extract_space_info(item)
        # content.space wins; container is just a fallback source.
        assert key == "SD"
        assert name == "Software"

    def test_falls_back_to_container_title_for_name(self):
        item = {
            "content": {"id": "p1"},
            "resultGlobalContainer": {"title": "Engineering", "displayUrl": "/spaces/ENG"},
        }
        key, name = Confluence._extract_space_info(item)
        assert key == "ENG"
        assert name == "Engineering"

    def test_extracts_key_from_displayUrl(self):
        item = {
            "content": {"id": "p1"},
            "resultGlobalContainer": {"displayUrl": "/spaces/ABC"},
        }
        key, _ = Confluence._extract_space_info(item)
        assert key == "ABC"

    def test_strips_trailing_path_segments_from_displayUrl(self):
        item = {
            "content": {"id": "p1"},
            "resultGlobalContainer": {"displayUrl": "/spaces/SD/overview"},
        }
        key, _ = Confluence._extract_space_info(item)
        assert key == "SD"

    def test_returns_empty_when_no_source_has_info(self):
        item = {"content": {"id": "p1", "title": "Hello"}}
        key, name = Confluence._extract_space_info(item)
        assert key == ""
        assert name == ""

    def test_handles_garbage_displayUrl(self):
        """displayUrl that doesn't match the `/spaces/<KEY>` pattern shouldn't
        produce a misleading space_key."""
        item = {
            "content": {"id": "p1"},
            "resultGlobalContainer": {"displayUrl": "https://x.atlassian.net/wiki/X"},
        }
        key, _ = Confluence._extract_space_info(item)
        assert key == ""

    def test_partial_content_space_uses_container_title_for_missing_name(self):
        """`content.space` has key but no name — fall back to container title."""
        item = {
            "content": {"space": {"key": "SD"}},
            "resultGlobalContainer": {"title": "Software", "displayUrl": "/spaces/SD"},
        }
        key, name = Confluence._extract_space_info(item)
        assert key == "SD"
        assert name == "Software"


# ===========================================================================
# _temporal_clause whitelist — the regex inside `search_full_text` rejects
# CQL injection vectors like `now()) OR foo()` while accepting every CQL
# date function Atlassian documents.
# ===========================================================================

class TestTemporalClauseWhitelist:
    """Smoke-test the date-function whitelist by driving CQL through the
    datasource and inspecting the wire string. The whitelist lives inside
    `search_full_text`, so we exercise it via that public surface."""

    @staticmethod
    async def _emit_cql(**kwargs):
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock as _MagicMock

        from app.sources.external.confluence.confluence import ConfluenceDataSource

        ds = ConfluenceDataSource.__new__(ConfluenceDataSource)
        ds._client = _MagicMock()
        ds._client.execute = _AsyncMock()
        ds.base_url = "https://x.atlassian.net/wiki/api/v2"
        try:
            await ds.search_full_text(query="x", **kwargs)
        except ValueError as ve:
            return f"ValueError: {ve}"
        return ds._client.execute.await_args.args[0].query_params["cql"]

    @pytest.mark.parametrize("value", [
        'now("-7d")',
        'now()',
        'startOfMonth()',
        'startOfWeek()',
        'startOfDay()',
        'startOfYear()',
        'endOfDay()',
        'endOfMonth()',
        'today()',
        'yesterday()',
        # Numeric offset without unit suffix.
        'now("-30")',
        # Single-quoted offset.
        "now('-7d')",
    ])
    @pytest.mark.asyncio
    async def test_whitelisted_function_passed_unquoted(self, value):
        cql = await self._emit_cql(last_modified_after=value)
        # Function call appears unquoted in the CQL — `lastmodified >= now(...)`.
        assert f"lastmodified >= {value}" in cql

    @pytest.mark.parametrize("value", [
        # Unbalanced parens — old greedy `.*` regex matched these and
        # produced `lastmodified >= now()) OR foo()`.
        'now()) OR foo()',
        # Function not in the whitelist.
        'system("rm -rf /")',
        # Embedded space.
        'now( ) OR foo()',
        # Raw injection without a leading function name.
        ') OR contributor = "x"',
        # Multi-statement attempt.
        'now(); DROP TABLE',
    ])
    @pytest.mark.asyncio
    async def test_injection_attempts_rejected_or_quoted(self, value):
        """A non-whitelisted value must fall to the quoted-ISO-date branch
        (which escapes the input). It must NEVER appear unquoted in the CQL."""
        cql = await self._emit_cql(last_modified_after=value)
        # The value, if it appears at all, must be inside double quotes.
        assert f"lastmodified >= {value}" not in cql
        # And must appear as a quoted ISO-date-like literal — escaping makes
        # the whole thing a harmless string operand. The `"` wrapping plus
        # `_escape_cql_literal` neutralises the would-be injection.
        # We don't assert the exact wire shape (escaping varies) — just that
        # the unquoted form is absent.

    @pytest.mark.asyncio
    async def test_iso_date_quoted(self):
        cql = await self._emit_cql(last_modified_after='2026-05-01')
        assert 'lastmodified >= "2026-05-01"' in cql

    @pytest.mark.asyncio
    async def test_iso_date_with_quote_escaped(self):
        cql = await self._emit_cql(last_modified_after='2026-05-01" OR x="')
        # Embedded `"` is escaped; no break-out into CQL grammar.
        assert 'lastmodified >= "2026-05-01\\" OR x=\\""' in cql


# ===========================================================================
# Sort-by extension on get_pages_in_space and get_child_pages — both v2
# enumeration endpoints already accepted `sort` at the datasource layer; the
# action just exposes it now. Tests here lock down the wiring.
# ===========================================================================

class TestGetPagesInSpaceSort:
    @pytest.mark.asyncio
    async def test_sort_by_forwarded_to_datasource(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(
            return_value=_mock_response(200, {"results": []}),
        )
        await conf.get_pages_in_space("SD", sort_by="-modified-date")
        assert conf.client.get_pages_in_space.call_args.kwargs["sort"] == "-modified-date"

    @pytest.mark.asyncio
    async def test_no_sort_by_default(self):
        """Backward-compat: existing callers that don't pass sort_by ⇒ no
        sort param to v2 endpoint, identical wire behaviour."""
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(
            return_value=_mock_response(200, {"results": []}),
        )
        await conf.get_pages_in_space("SD")
        assert conf.client.get_pages_in_space.call_args.kwargs.get("sort") is None

    @pytest.mark.asyncio
    async def test_limit_forwarded(self):
        conf = _build_confluence()
        conf.client.get_pages_in_space = AsyncMock(
            return_value=_mock_response(200, {"results": []}),
        )
        await conf.get_pages_in_space("SD", limit=10)
        assert conf.client.get_pages_in_space.call_args.kwargs["limit"] == 10


class TestGetChildPagesSort:
    @pytest.mark.asyncio
    async def test_sort_by_forwarded_to_datasource(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(
            return_value=_mock_response(200, {"results": []}),
        )
        await conf.get_child_pages("12345", sort_by="-modified-date")
        assert conf.client.get_child_pages.call_args.kwargs["sort"] == "-modified-date"

    @pytest.mark.asyncio
    async def test_limit_forwarded(self):
        conf = _build_confluence()
        conf.client.get_child_pages = AsyncMock(
            return_value=_mock_response(200, {"results": []}),
        )
        await conf.get_child_pages("12345", limit=5)
        assert conf.client.get_child_pages.call_args.kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_invalid_page_id_rejected(self):
        """Pre-existing behaviour preserved: non-integer page_id is a clean error."""
        conf = _build_confluence()
        ok, _ = await conf.get_child_pages("not-an-int", sort_by="title")
        assert ok is False
