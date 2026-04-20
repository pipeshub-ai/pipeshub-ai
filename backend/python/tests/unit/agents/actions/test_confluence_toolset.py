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
    UpdatePageInput,
    UpdatePageTitleInput,
)
from app.sources.client.http.exception.exception import HttpStatusCode


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

class TestSearchPages:
    @pytest.mark.asyncio
    async def test_success_with_space_filter_dict_results(self):
        conf = _build_confluence()
        conf.client.get_pages = AsyncMock(
            return_value=_mock_response(200, {
                "results": [{"id": "p1", "spaceId": "99"}],
            }),
        )
        conf.client.get_spaces = AsyncMock(return_value=_spaces_response([
            {"id": "99", "key": "SD"},
        ]))
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan", space_id="99")
        data = json.loads(payload)["data"]
        assert data["results"][0]["url"] == "https://s/wiki/spaces/SD/pages/p1"
        assert conf.client.get_pages.call_args.kwargs["space_id"] == ["99"]

    @pytest.mark.asyncio
    async def test_success_list_results(self):
        conf = _build_confluence()
        conf.client.get_pages = AsyncMock(
            return_value=_mock_response(200, [{"id": "p1", "spaceId": "SD"}]),
        )
        with patch.object(conf, "_get_site_url", AsyncMock(return_value="https://s")):
            ok, payload = await conf.search_pages("plan")
        data = json.loads(payload)["data"]
        assert data[0]["url"] == "https://s/wiki/spaces/SD/pages/p1"

    @pytest.mark.asyncio
    async def test_api_error(self):
        conf = _build_confluence()
        conf.client.get_pages = AsyncMock(return_value=_mock_response(500))
        ok, _ = await conf.search_pages("plan")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conf = _build_confluence()
        conf.client.get_pages = AsyncMock(side_effect=RuntimeError("boom"))
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
