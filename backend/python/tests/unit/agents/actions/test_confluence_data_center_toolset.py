"""Comprehensive unit tests for `app.agents.actions.confluence_data_center.confluence_data_center`.

The Data Center toolset is a standalone sibling of the Cloud ``Confluence`` action.
It wraps ``ConfluenceDataSource`` and calls only its ``*_v1`` (REST v1) methods,
applying the Data Center deltas: space KEYs (not numeric v2 ids), ``username``/
``userKey`` identity (not ``accountId``), storage-format bodies via v1 expand, and
browse URLs built from the response ``_links`` against the instance ``baseUrl``.

Covers the input schemas, the pure helpers (`_handle_response`, `_get_error_guidance`,
`_url_from_links`, `_page_url`, `_to_storage_body`, `_clean_user`, `_simplify_page`,
`_simplify_search_results`), the async `_get_site_url`, and every `@tool` method's
success + API-error + exception paths plus the DC-specific branches.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.confluence_data_center.confluence_data_center import (
    AddCommentInput,
    ConfluenceDataCenter,
    CreatePageInput,
    GetChildPagesInput,
    GetCommentsInput,
    GetPageContentInput,
    GetPagesInSpaceInput,
    GetPageVersionsInput,
    GetSpaceInput,
    SearchContentInput,
    SearchPagesInput,
    SearchUsersInput,
    UpdatePageInput,
)
from app.sources.client.http.exception.exception import HttpStatusCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status: int, body=None, is_json: bool = True):
    """Mimic `HTTPResponse` enough for `_handle_response` and callers."""
    resp = MagicMock()
    resp.status = status
    resp.is_json = is_json
    resp.json = MagicMock(return_value=body if body is not None else {})
    resp.text = MagicMock(return_value=json.dumps(body) if body is not None else "")
    return resp


def _build(client_methods: dict | None = None) -> ConfluenceDataCenter:
    """Instantiate the toolset bypassing the ToolsetBuilder decorator; stub the
    underlying `ConfluenceDataSource` (``self.client``) with a MagicMock."""
    tool = ConfluenceDataCenter.__new__(ConfluenceDataCenter)
    client = MagicMock()
    for name, value in (client_methods or {}).items():
        setattr(client, name, value)
    tool.client = client
    tool._site_url = None
    return tool


# ===========================================================================
# Pydantic input schemas
# ===========================================================================

class TestSchemas:
    def test_create_page_input(self):
        m = CreatePageInput(space_key="DS", page_title="t", page_content="<p>x</p>")
        assert m.space_key == "DS" and m.parent_page_id is None

    def test_get_page_content_input(self):
        assert GetPageContentInput(page_id="1").page_id == "1"

    def test_update_page_input_optionals(self):
        assert UpdatePageInput(page_id="1").page_title is None

    def test_comment_input(self):
        assert AddCommentInput(page_id="1", comment_text="hi").parent_comment_id is None

    def test_get_comments_input(self):
        assert GetCommentsInput(page_id="1").page_id == "1"

    def test_get_pages_in_space_input(self):
        assert GetPagesInSpaceInput(space_key="DS").limit is None

    def test_search_pages_input(self):
        assert SearchPagesInput(title="x").space_key is None

    def test_search_content_input_default_limit(self):
        assert SearchContentInput().limit == 25

    def test_get_space_input(self):
        assert GetSpaceInput(space_key="DS").space_key == "DS"

    def test_search_users_input_default(self):
        assert SearchUsersInput(query="jo").max_results == 10

    def test_get_child_pages_input(self):
        assert GetChildPagesInput(page_id="1").limit is None

    def test_get_page_versions_input(self):
        assert GetPageVersionsInput(page_id="1").page_id == "1"


# ===========================================================================
# Construction
# ===========================================================================

class TestInit:
    def test_wraps_datasource_and_inits_cache(self):
        with patch(
            "app.agents.actions.confluence_data_center.confluence_data_center.ConfluenceDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            tool = ConfluenceDataCenter(client=MagicMock())
        assert tool.client is MockDS.return_value
        assert tool._site_url is None


# ===========================================================================
# _handle_response
# ===========================================================================

class TestHandleResponse:
    def test_success_200_with_body(self):
        ok, payload = _build()._handle_response(_mock_response(200, {"a": 1}), "ok")
        data = json.loads(payload)
        assert ok is True and data["data"] == {"a": 1}

    def test_success_204_empty_data(self):
        ok, payload = _build()._handle_response(_mock_response(HttpStatusCode.NO_CONTENT.value), "ok")
        assert ok is True and json.loads(payload)["data"] == {}

    def test_success_json_parse_error_falls_back(self):
        resp = _mock_response(200)
        resp.json = MagicMock(side_effect=ValueError("broken"))
        ok, payload = _build()._handle_response(resp, "ok")
        assert ok is True and json.loads(payload)["data"] == {}

    def test_error_message_key(self):
        ok, payload = _build()._handle_response(_mock_response(400, {"message": "bad cql"}), "x")
        data = json.loads(payload)
        assert ok is False and data["error"] == "bad cql" and data["status_code"] == 400

    def test_error_dict_without_known_keys_dumps_body(self):
        ok, payload = _build()._handle_response(_mock_response(500, {"weird": "shape"}), "x")
        data = json.loads(payload)
        assert ok is False and "HTTP 500" in data["error"] and "weird" in data["details"]

    def test_error_non_dict_json_body(self):
        ok, payload = _build()._handle_response(_mock_response(500, "string body"), "x")
        assert json.loads(payload)["error"] == "HTTP 500"

    def test_error_non_json_response(self):
        resp = _mock_response(500, is_json=False)
        resp.text = MagicMock(return_value="plain text error")
        ok, payload = _build()._handle_response(resp, "x")
        assert "plain text error" in json.loads(payload)["details"]

    def test_error_guidance_attached(self):
        ok, payload = _build()._handle_response(_mock_response(401, {"message": "unauth"}), "x", include_guidance=True)
        assert "Authentication" in json.loads(payload)["guidance"]

    def test_error_parse_exception_fallback(self):
        resp = _mock_response(500, is_json=True)
        resp.json = MagicMock(side_effect=RuntimeError("parse fail"))
        resp.text = MagicMock(return_value="raw text")
        ok, payload = _build()._handle_response(resp, "x")
        assert "raw text" in json.loads(payload)["details"]


class TestGetErrorGuidance:
    @pytest.mark.parametrize("status", [401, 403, 404, 400])
    def test_known(self, status):
        assert _build()._get_error_guidance(status) is not None

    def test_unknown(self):
        assert _build()._get_error_guidance(418) is None


# ===========================================================================
# Pure DC helpers
# ===========================================================================

class TestUrlFromLinks:
    def test_base_plus_webui(self):
        url = _build()._url_from_links({"base": "https://c.co/", "webui": "/display/DS/Page"}, None)
        assert url == "https://c.co/display/DS/Page"

    def test_falls_back_to_site_url(self):
        url = _build()._url_from_links({"webui": "/display/DS/Page"}, "https://site.co")
        assert url == "https://site.co/display/DS/Page"

    def test_none_when_not_dict(self):
        assert _build()._url_from_links("nope", "https://x") is None

    def test_none_when_no_webui(self):
        assert _build()._url_from_links({"base": "https://c.co"}, None) is None

    def test_none_when_no_base_and_no_site(self):
        assert _build()._url_from_links({"webui": "/x"}, None) is None

    def test_derives_base_from_self_link_when_no_base(self):
        # v1 list items carry webui + self but no base; derive the root from self.
        links = {"webui": "/display/DS/P", "self": "https://c.co/rest/api/content/123"}
        assert _build()._url_from_links(links, None) == "https://c.co/display/DS/P"


class TestResponseBase:
    def test_prefers_response_root_base(self):
        data = {"_links": {"base": "https://real.co"}, "results": []}
        assert _build()._response_base(data, "https://configured.co") == "https://real.co"

    def test_falls_back_to_site_url(self):
        assert _build()._response_base({"results": []}, "https://configured.co") == "https://configured.co"

    def test_non_dict(self):
        assert _build()._response_base("bad", "https://x") == "https://x"


class TestPageUrl:
    def test_prefers_links(self):
        url = _build()._page_url({"base": "https://c.co", "webui": "/display/DS/P"}, "123", "https://site")
        assert url == "https://c.co/display/DS/P"

    def test_viewpage_fallback(self):
        url = _build()._page_url(None, "123", "https://site.co")
        assert url == "https://site.co/pages/viewpage.action?pageId=123"

    def test_none_when_nothing(self):
        assert _build()._page_url(None, None, None) is None


class TestToStorageBody:
    def test_plain_text_escaped_and_wrapped(self):
        assert _build()._to_storage_body("a & b") == "<p>a &amp; b</p>"

    def test_xhtml_passed_through(self):
        assert _build()._to_storage_body("<p>hi</p>") == "<p>hi</p>"


class TestCleanUser:
    def test_keeps_dc_identity_fields(self):
        out = _build()._clean_user({"username": "jdoe", "userKey": "k1", "displayName": "J Doe", "email": "j@x", "extra": "drop"})
        assert out == {"username": "jdoe", "userKey": "k1", "displayName": "J Doe", "email": "j@x"}

    def test_emailAddress_alias(self):
        out = _build()._clean_user({"username": "jdoe", "emailAddress": "j@x"})
        assert out["email"] == "j@x"

    def test_empty(self):
        assert _build()._clean_user({}) == {}


class TestSimplifyPage:
    def test_extracts_fields_and_url(self):
        data = {
            "id": "123", "title": "Page", "type": "page", "status": "current",
            "space": {"key": "DS"}, "version": {"number": 4},
            "body": {"storage": {"value": "<p>body</p>"}},
            "_links": {"base": "https://c.co", "webui": "/display/DS/Page"},
        }
        out = _build()._simplify_page(data, "https://site")
        assert out["space_key"] == "DS"
        assert out["version"] == 4
        assert out["body"] == "<p>body</p>"
        assert out["url"] == "https://c.co/display/DS/Page"

    def test_non_dict(self):
        assert _build()._simplify_page("bad", None) == {}


class TestSimplifySearchResults:
    def test_content_with_top_level_base_and_rel_url(self):
        data = {
            "_links": {"base": "https://c.co"},
            "results": [
                {"content": {"id": "1", "title": "P1", "type": "page"}, "url": "/display/DS/P1", "excerpt": "ex"},
                "not-a-dict",
            ],
        }
        out = _build()._simplify_search_results(data, None)
        assert len(out) == 1
        assert out[0]["url"] == "https://c.co/display/DS/P1"
        assert out[0]["excerpt"] == "ex"

    def test_fallback_to_content_links(self):
        data = {"results": [{"content": {"id": "9", "title": "P", "type": "page", "_links": {"webui": "/display/X/P"}}}]}
        out = _build()._simplify_search_results(data, "https://site")
        assert out[0]["url"] == "https://site/display/X/P"

    def test_enriches_updated_and_labels_and_strips_highlight(self):
        data = {"results": [{
            "content": {"id": "1", "title": "P", "type": "page",
                        "metadata": {"labels": {"results": [{"name": "hr"}, {"name": "policy"}]}}},
            "excerpt": "the @@@hl@@@budget@@@endhl@@@ plan",
            "lastModified": "2026-07-04T09:00:00.000Z",
            "url": "/display/DS/P",
        }], "_links": {"base": "https://c.co"}}
        out = _build()._simplify_search_results(data, None)[0]
        assert out["updated"] == "2026-07-04T09:00:00.000Z"
        assert out["labels"] == ["hr", "policy"]
        assert out["excerpt"] == "the budget plan"  # highlight markers stripped

    def test_empty_excerpt_omitted(self):
        data = {"results": [{"content": {"id": "1", "title": "P", "type": "page"}, "excerpt": ""}]}
        out = _build()._simplify_search_results(data, None)[0]
        assert "excerpt" not in out


# ===========================================================================
# _get_site_url
# ===========================================================================

class TestGetSiteUrl:
    @pytest.mark.asyncio
    async def test_uses_base_url_stripped(self):
        tool = _build()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(return_value="https://c.co/")
        tool.client._client = client_obj
        assert await tool._get_site_url() == "https://c.co"
        assert tool._site_url == "https://c.co"

    @pytest.mark.asyncio
    async def test_cached(self):
        tool = _build()
        tool._site_url = "https://cached.co"
        assert await tool._get_site_url() == "https://cached.co"

    @pytest.mark.asyncio
    async def test_no_attr(self):
        tool = _build()
        tool.client._client = MagicMock(spec=[])
        assert await tool._get_site_url() is None

    @pytest.mark.asyncio
    async def test_empty_base_url(self):
        tool = _build()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(return_value="")
        tool.client._client = client_obj
        assert await tool._get_site_url() is None

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        client_obj = MagicMock(spec=["get_base_url"])
        client_obj.get_base_url = MagicMock(side_effect=RuntimeError("boom"))
        tool.client._client = client_obj
        assert await tool._get_site_url() is None


# ===========================================================================
# validate_connection / get_current_user
# ===========================================================================

class TestValidateConnection:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(return_value=_mock_response(200, {"username": "jdoe", "userKey": "k", "displayName": "J"}))
        ok, payload = await tool.validate_connection()
        data = json.loads(payload)
        assert ok is True and data["data"]["username"] == "jdoe"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(return_value=_mock_response(401, {"message": "x"}))
        ok, payload = await tool.validate_connection()
        assert ok is False and "guidance" in json.loads(payload)

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, payload = await tool.validate_connection()
        assert ok is False and "boom" in json.loads(payload)["error"]


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(return_value=_mock_response(200, {"username": "jdoe"}))
        ok, payload = await tool.get_current_user()
        assert ok is True and json.loads(payload)["data"]["username"] == "jdoe"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(return_value=_mock_response(403, {"message": "x"}))
        ok, _ = await tool.get_current_user()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_current_user_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_current_user()
        assert ok is False


# ===========================================================================
# get_spaces / get_space / get_pages_in_space
# ===========================================================================

class TestGetSpaces:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [
                {"id": 1, "key": "DS", "name": "Docs", "type": "global", "_links": {"webui": "/spaces/DS"}},
                "not-a-dict",
            ],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_spaces()
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        assert data["data"]["results"][0]["url"] == "https://c.co/spaces/DS"

    @pytest.mark.asyncio
    async def test_uses_response_root_base_over_site_url(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://real.co/confluence"},
            "results": [{"id": 1, "key": "DS", "name": "Docs", "_links": {"webui": "/display/DS"}}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://configured.co")):
            ok, payload = await tool.get_spaces()
        # Response-root base wins over the configured site URL (proxy/context-path safety).
        assert json.loads(payload)["data"]["results"][0]["url"] == "https://real.co/confluence/display/DS"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await tool.get_spaces()
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_spaces()
        assert ok is False


class TestGetSpace:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": 1, "key": "DS", "name": "Docs", "type": "global",
                         "description": {"plain": {"value": "desc"}}, "_links": {"webui": "/spaces/DS"}}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_space("DS")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["key"] == "DS"
        assert data["data"]["description"] == "desc"
        assert data["data"]["url"] == "https://c.co/spaces/DS"

    @pytest.mark.asyncio
    async def test_not_found(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(200, {"results": []}))
        ok, payload = await tool.get_space("NOPE")
        assert ok is False and "not found" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.get_space("DS")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_spaces_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_space("DS")
        assert ok is False


class TestGetPagesInSpace:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": "10", "title": "P", "type": "page", "_links": {"webui": "/display/DS/P"}}, "bad"],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_pages_in_space("DS", limit=5)
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        assert data["data"]["results"][0]["url"] == "https://c.co/display/DS/P"
        assert tool.client.get_pages_v1.call_args.kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_url_fallback_to_viewpage(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(return_value=_mock_response(200, {"results": [{"id": "10", "title": "P"}]}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_pages_in_space("DS")
        assert json.loads(payload)["data"]["results"][0]["url"] == "https://c.co/pages/viewpage.action?pageId=10"

    @pytest.mark.asyncio
    async def test_orders_newest_first_and_enriches(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": "10", "title": "P", "type": "page", "status": "current",
                         "version": {"when": "2026-07-04T08:00:00.000Z"}}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_pages_in_space("DS", limit=5)
        data = json.loads(payload)
        assert ok is True
        page = data["data"]["results"][0]
        assert page["status"] == "current"
        assert page["updated"] == "2026-07-04T08:00:00.000Z"
        # Ordering is requested server-side, newest first.
        kwargs = tool.client.get_pages_v1.call_args.kwargs
        assert kwargs["order_by"] == "lastModified" and kwargs["sort_order"] == "desc"

    @pytest.mark.asyncio
    async def test_truncation_note_when_page_full(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": str(i), "title": f"P{i}"} for i in range(3)],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_pages_in_space("DS", limit=3)
        data = json.loads(payload)
        assert "note" in data and "more may exist" in data["note"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await tool.get_pages_in_space("DS")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_pages_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_pages_in_space("DS")
        assert ok is False


# ===========================================================================
# get_page_content
# ===========================================================================

class TestGetPageContent:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {
            "id": "123", "title": "P", "type": "page", "space": {"key": "DS"},
            "version": {"number": 3}, "body": {"storage": {"value": "<p>b</p>"}},
            "_links": {"webui": "/display/DS/P"},
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_page_content("123")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["body"] == "<p>b</p>"
        assert data["data"]["version"] == 3
        assert data["data"]["url"] == "https://c.co/display/DS/P"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.get_page_content("123")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_page_content("123")
        assert ok is False


# ===========================================================================
# create_page
# ===========================================================================

class TestCreatePage:
    @pytest.mark.asyncio
    async def test_success_minimal(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(return_value=_mock_response(200, {
            "id": "500", "title": "New", "space": {"key": "DS"}, "version": {"number": 1},
            "_links": {"webui": "/display/DS/New"},
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.create_page("DS", "New", "<p>x</p>")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["id"] == "500"
        assert data["data"]["url"] == "https://c.co/display/DS/New"
        body = tool.client.create_content_v1.call_args.kwargs["body"]
        assert body["type"] == "page"
        assert body["space"] == {"key": "DS"}
        assert body["body"]["storage"]["representation"] == "storage"
        assert "ancestors" not in body

    @pytest.mark.asyncio
    async def test_with_parent(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(return_value=_mock_response(201, {"id": "1"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.create_page("DS", "New", "<p>x</p>", parent_page_id="99")
        assert ok is True
        assert tool.client.create_content_v1.call_args.kwargs["body"]["ancestors"] == [{"id": "99"}]

    @pytest.mark.asyncio
    async def test_wraps_and_escapes_plain_text(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(return_value=_mock_response(201, {"id": "1"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            await tool.create_page("DS", "New", "A & B")
        body = tool.client.create_content_v1.call_args.kwargs["body"]
        assert body["body"]["storage"]["value"] == "<p>A &amp; B</p>"

    @pytest.mark.asyncio
    async def test_passes_xhtml_through(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(return_value=_mock_response(201, {"id": "1"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            await tool.create_page("DS", "New", "<h1>Title</h1>")
        body = tool.client.create_content_v1.call_args.kwargs["body"]
        assert body["body"]["storage"]["value"] == "<h1>Title</h1>"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(return_value=_mock_response(400, {"message": "bad"}))
        ok, _ = await tool.create_page("DS", "New", "<p>x</p>")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.create_content_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.create_page("DS", "New", "<p>x</p>")
        assert ok is False


# ===========================================================================
# update_page
# ===========================================================================

class TestUpdatePage:
    @pytest.mark.asyncio
    async def test_no_updates(self):
        tool = _build()
        ok, payload = await tool.update_page("123")
        assert ok is False and "No updates provided" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_content_update_bumps_version(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 3}}))
        tool.client.update_content_v1 = AsyncMock(return_value=_mock_response(200, {"id": "123", "title": "Old", "version": {"number": 4}}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.update_page("123", page_content="<p>new</p>")
        assert ok is True
        sent = tool.client.update_content_v1.call_args.kwargs["body"]
        assert sent["version"]["number"] == 4
        assert sent["title"] == "Old"  # preserved from current
        assert sent["body"]["storage"]["value"] == "<p>new</p>"

    @pytest.mark.asyncio
    async def test_reparent_sets_ancestors(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 1}}))
        tool.client.update_content_v1 = AsyncMock(return_value=_mock_response(200, {"id": "123", "title": "Old"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.update_page("123", parent_page_id="999")
        assert ok is True
        sent = tool.client.update_content_v1.call_args.kwargs["body"]
        assert sent["ancestors"] == [{"id": "999"}]
        assert sent["title"] == "Old"      # unchanged
        assert "body" not in sent          # move-only, no content touched

    @pytest.mark.asyncio
    async def test_retries_once_on_conflict(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 3}}))
        tool.client.update_content_v1 = AsyncMock(side_effect=[
            _mock_response(409, {"message": "conflict"}),
            _mock_response(200, {"id": "123", "title": "Old", "version": {"number": 4}}),
        ])
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.update_page("123", page_content="<p>new</p>")
        assert ok is True
        assert tool.client.update_content_v1.await_count == 2
        assert tool.client.get_page_content_v1.await_count == 2  # fresh version re-read before retry

    @pytest.mark.asyncio
    async def test_non_conflict_error_does_not_retry(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 1}}))
        tool.client.update_content_v1 = AsyncMock(return_value=_mock_response(400, {"message": "bad"}))
        ok, _ = await tool.update_page("123", page_content="<p>x</p>")
        assert ok is False
        assert tool.client.update_content_v1.await_count == 1

    @pytest.mark.asyncio
    async def test_title_only_no_body(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 1}}))
        tool.client.update_content_v1 = AsyncMock(return_value=_mock_response(200, {"id": "123"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.update_page("123", page_title="New Title")
        sent = tool.client.update_content_v1.call_args.kwargs["body"]
        assert sent["title"] == "New Title"
        assert "body" not in sent

    @pytest.mark.asyncio
    async def test_current_fetch_fails(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.update_page("123", page_title="X")
        assert ok is False

    @pytest.mark.asyncio
    async def test_update_api_error(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(return_value=_mock_response(200, {"title": "Old", "version": {"number": 1}}))
        tool.client.update_content_v1 = AsyncMock(return_value=_mock_response(409, {"message": "conflict"}))
        ok, _ = await tool.update_page("123", page_content="<p>x</p>")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_page_content_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.update_page("123", page_title="X")
        assert ok is False


class TestGetChildPages:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_child_pages_v1 = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://c.co"},
            "results": [{"id": "20", "title": "Child", "type": "page", "_links": {"webui": "/display/DS/Child"}}, "bad"],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.get_child_pages("10", limit=5)
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        assert data["data"]["results"][0]["url"] == "https://c.co/display/DS/Child"
        assert data["data"]["results"][0]["status"] is None  # status surfaced (absent here)
        assert tool.client.get_child_pages_v1.call_args.kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_truncation_note_when_page_full(self):
        tool = _build()
        tool.client.get_child_pages_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": str(i), "title": f"C{i}", "type": "page", "status": "current"} for i in range(3)],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.get_child_pages("10", limit=3)
        data = json.loads(payload)
        assert data["data"]["results"][0]["status"] == "current"
        assert "note" in data and "more may exist" in data["note"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_child_pages_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.get_child_pages("10")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_child_pages_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_child_pages("10")
        assert ok is False


class TestGetPageVersions:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_content_versions_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [
                {"number": 3, "when": "2026-01-02", "minorEdit": False, "message": "edit",
                 "by": {"username": "jdoe", "displayName": "J Doe"}},
                "bad",
            ],
        }))
        ok, payload = await tool.get_page_versions("123")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        v = data["data"]["results"][0]
        assert v["number"] == 3
        assert v["message"] == "edit"
        assert v["author"]["username"] == "jdoe"

    @pytest.mark.asyncio
    async def test_anonymous_author_and_empty_message_omitted(self):
        tool = _build()
        tool.client.get_content_versions_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [
                {"number": 1, "when": "2024-03-05", "minorEdit": False, "message": "",
                 "by": {"type": "anonymous", "displayName": "Anonymous"}},
            ],
        }))
        ok, payload = await tool.get_page_versions("123")
        v = json.loads(payload)["data"]["results"][0]
        assert "message" not in v                        # empty message omitted
        assert v["author"] == {"displayName": "Anonymous"}  # anonymous handled

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_content_versions_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.get_page_versions("123")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_content_versions_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_page_versions("123")
        assert ok is False


# ===========================================================================
# add_comment / get_comments
# ===========================================================================

class TestCommentOnPage:
    @pytest.mark.asyncio
    async def test_success_plain_text_wrapped(self):
        tool = _build()
        tool.client.create_comment_v1 = AsyncMock(return_value=_mock_response(200, {"id": "c1", "_links": {"webui": "/x"}}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.add_comment("123", "hello & bye")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["url"] == "https://c.co/x"
        assert tool.client.create_comment_v1.call_args.kwargs["body_value"] == "<p>hello &amp; bye</p>"

    @pytest.mark.asyncio
    async def test_reply_passes_parent(self):
        tool = _build()
        tool.client.create_comment_v1 = AsyncMock(return_value=_mock_response(201, {"id": "c2"}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.add_comment("123", "reply", parent_comment_id="c1")
        assert ok is True
        assert tool.client.create_comment_v1.call_args.kwargs["parent_comment_id"] == "c1"

    @pytest.mark.asyncio
    async def test_empty_rejected(self):
        tool = _build()
        ok, payload = await tool.add_comment("123", "   ")
        assert ok is False and "required" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.create_comment_v1 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await tool.add_comment("123", "hi")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.create_comment_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.add_comment("123", "hi")
        assert ok is False


class TestGetComments:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.get_content_comments_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [
                {"id": "c1", "body": {"storage": {"value": "<p>hi</p>"}},
                 "version": {"by": {"username": "jdoe", "displayName": "J"}}, "_links": {"webui": "/c1"}},
                "not-a-dict",
            ],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value="https://c.co")):
            ok, payload = await tool.get_comments("123")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        c = data["data"]["results"][0]
        assert c["body"] == "<p>hi</p>"
        assert c["author"]["username"] == "jdoe"
        assert c["url"] == "https://c.co/c1"
        assert "note" not in data  # single comment, well under the page size

    @pytest.mark.asyncio
    async def test_surfaces_timestamp(self):
        tool = _build()
        tool.client.get_content_comments_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": "c1", "body": {"storage": {"value": "<p>hi</p>"}},
                         "version": {"when": "2026-07-04T09:00:00.000Z"}}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.get_comments("123")
        assert json.loads(payload)["data"]["results"][0]["updated"] == "2026-07-04T09:00:00.000Z"

    @pytest.mark.asyncio
    async def test_truncation_note_when_full_page(self):
        tool = _build()
        tool.client.get_content_comments_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [{"id": str(i), "body": {"storage": {"value": "x"}}} for i in range(100)],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.get_comments("123")
        data = json.loads(payload)
        assert "note" in data and "more may exist" in data["note"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.get_content_comments_v1 = AsyncMock(return_value=_mock_response(404, {"message": "x"}))
        ok, _ = await tool.get_comments("123")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.get_content_comments_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.get_comments("123")
        assert ok is False


# ===========================================================================
# search_pages / search_content
# ===========================================================================

class TestSearchPages:
    @pytest.mark.asyncio
    async def test_success(self):
        tool = _build()
        tool.client.search_pages_cql = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://c.co"},
            "results": [{"content": {"id": "1", "title": "Runbook", "type": "page"}, "url": "/display/DS/Runbook"}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.search_pages("run", space_key="DS", limit=5)
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["results"][0]["url"] == "https://c.co/display/DS/Runbook"
        assert tool.client.search_pages_cql.call_args.kwargs["space_id"] == "DS"

    @pytest.mark.asyncio
    async def test_truncation_note_from_total_size(self):
        tool = _build()
        tool.client.search_pages_cql = AsyncMock(return_value=_mock_response(200, {
            "totalSize": 120,
            "results": [{"content": {"id": "1", "title": "P", "type": "page"}}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.search_pages("p", limit=1)
        data = json.loads(payload)
        assert data["data"]["total_available"] == 120
        assert "note" in data and "1 of 120" in data["note"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.search_pages_cql = AsyncMock(return_value=_mock_response(400, {"message": "bad cql"}))
        ok, _ = await tool.search_pages("run")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.search_pages_cql = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.search_pages("run")
        assert ok is False


class TestSearchContent:
    @pytest.mark.asyncio
    async def test_success_with_query(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(return_value=_mock_response(200, {
            "_links": {"base": "https://c.co"},
            "results": [{"content": {"id": "1", "title": "Doc", "type": "page"}, "url": "/display/DS/Doc"}],
        }))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, payload = await tool.search_content(query="onboarding", space_key="DS")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_labels_only_allowed(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(return_value=_mock_response(200, {"results": []}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.search_content(labels=["qa"])
        assert ok is True

    @pytest.mark.asyncio
    async def test_no_filter_rejected(self):
        tool = _build()
        ok, payload = await tool.search_content()
        assert ok is False and "required" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_invalid_order_by_rejected(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(return_value=_mock_response(200, {"results": []}))
        ok, payload = await tool.search_content(query="x", order_by="lastmodified desc; DROP")
        assert ok is False and "Invalid order_by" in json.loads(payload)["error"]
        tool.client.search_full_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_order_by_passes_through(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(return_value=_mock_response(200, {"results": []}))
        with patch.object(tool, "_get_site_url", AsyncMock(return_value=None)):
            ok, _ = await tool.search_content(query="x", order_by="lastmodified desc, title asc")
        assert ok is True
        assert tool.client.search_full_text.call_args.kwargs["order_by"] == "lastmodified desc, title asc"

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(return_value=_mock_response(400, {"message": "x"}))
        ok, _ = await tool.search_content(query="x")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.search_full_text = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.search_content(query="x")
        assert ok is False


# ===========================================================================
# search_users
# ===========================================================================

class TestSearchUsers:
    @pytest.mark.asyncio
    async def test_returns_matches_with_profile_url(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(return_value=_mock_response(200, {
            "results": [
                {"user": {"username": "darshan", "userKey": "k1", "displayName": "Darshan godase"},
                 "url": "/display/~darshan"},
                "not-a-dict",              # skipped
                {"no_user": True},          # item without a "user" object -> skipped
            ],
            "_links": {"base": "http://localhost:8090"},
        }))
        ok, payload = await tool.search_users("dar")
        data = json.loads(payload)
        assert ok is True
        assert data["data"]["total"] == 1
        user = data["data"]["results"][0]
        assert user["username"] == "darshan"
        assert user["userKey"] == "k1"
        assert user["url"] == "http://localhost:8090/display/~darshan"

    @pytest.mark.asyncio
    async def test_passes_trimmed_query_and_default_limit(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(return_value=_mock_response(200, {"results": []}))
        await tool.search_users("  dar  ")
        kwargs = tool.client.search_users_v1.await_args.kwargs
        assert kwargs["query"] == "dar"
        assert kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_caps_limit_at_50(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(return_value=_mock_response(200, {"results": []}))
        await tool.search_users("x", max_results=999)
        assert tool.client.search_users_v1.await_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_no_matches(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(return_value=_mock_response(200, {"results": [], "_links": {"base": "http://x"}}))
        ok, payload = await tool.search_users("ghost")
        data = json.loads(payload)
        assert ok is True and data["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_empty_query(self):
        tool = _build()
        ok, payload = await tool.search_users("  ")
        assert ok is False and "required" in json.loads(payload)["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(return_value=_mock_response(500, {"message": "x"}))
        ok, _ = await tool.search_users("john")
        assert ok is False

    @pytest.mark.asyncio
    async def test_exception(self):
        tool = _build()
        tool.client.search_users_v1 = AsyncMock(side_effect=RuntimeError("boom"))
        ok, _ = await tool.search_users("john")
        assert ok is False
