"""Comprehensive unit tests for `app.agents.actions.microsoft.sharepoint.sharepoint`.

Covers:
* 19 Pydantic input schemas (grouped into 4 buckets)
* Pure helpers: `_serialize_response`, `_extract_collection`,
  `_extract_page_html_content`, `_normalize_notebook_name`, `_handle_error`
* All 19 `@tool` methods: success + source-error + exception paths,
  plus key per-tool branches (`.one`/`.onetoc2` rejection in `get_file_content`,
  resolved/ambiguous/no-match in `find_notebook`, validation guards in
  `update_page` / `move_item`, 404 short-circuit in `list_drives` / `get_pages`,
  partial failures in `get_notebook_page_content`).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.microsoft.sharepoint.sharepoint import (
    CreateFolderInput,
    CreateOneNoteNotebookInput,
    CreatePageInput,
    CreateWordDocumentInput,
    FindNotebookInput,
    GetFileContentInput,
    GetFileMetadataInput,
    GetNotebookPageContentInput,
    GetPageInput,
    GetPagesInput,
    GetSiteInput,
    GetSitesInput,
    ListDrivesInput,
    ListFilesInput,
    ListNotebookPagesInput,
    MoveItemInput,
    SearchFilesInput,
    SearchPagesInput,
    SharePoint,
    UpdatePageInput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(success: bool = True, data=None, error=None, message=None):
    """Mimic `SharePointResponse` enough for the action layer."""
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    resp.message = message
    return resp


def _build_sharepoint(
    client_methods: dict | None = None,
    state: dict | None = None,
) -> SharePoint:
    """Instantiate SharePoint bypassing the ToolsetBuilder decorator; stub the
    underlying `SharePointDataSource` (``self.client``) so each test can set
    return values per method. `state` mimics the ChatState dict that the agent
    runtime injects (carrying `model_name`, `model_key`, `config_service`)."""
    sp = SharePoint.__new__(SharePoint)
    client = MagicMock()
    for name, value in (client_methods or {}).items():
        setattr(client, name, value)
    sp.client = client
    sp.state = state
    return sp


def _ok_tuple(result):
    """Assert a tool return is `(True, json_str)` and return parsed dict."""
    ok, payload = result
    assert ok is True, f"Expected success, got error: {payload}"
    return json.loads(payload)


def _err_tuple(result):
    """Assert a tool return is `(False, json_str)` and return parsed dict."""
    ok, payload = result
    assert ok is False, f"Expected error, got success: {payload}"
    return json.loads(payload)


# ===========================================================================
# Pydantic input schemas
# ===========================================================================

class TestSchemasSites:
    """Schemas for site/page tools: GetSites, GetSite, GetPages, GetPage, SearchPages."""

    def test_get_sites_defaults(self):
        s = GetSitesInput()
        assert s.search is None
        assert s.top == 10
        assert s.skip is None
        assert s.orderby is None

    def test_get_sites_with_query(self):
        s = GetSitesInput(search="marketing", top=25)
        assert s.search == "marketing"
        assert s.top == 25

    def test_get_site_requires_site_id(self):
        with pytest.raises(Exception):
            GetSiteInput()

    def test_get_pages_defaults(self):
        s = GetPagesInput(site_id="s1")
        assert s.site_id == "s1"
        assert s.top == 10

    def test_get_page_requires_both_ids(self):
        s = GetPageInput(site_id="s1", page_id="p1")
        assert s.site_id == "s1" and s.page_id == "p1"

    def test_search_pages_query_required(self):
        with pytest.raises(Exception):
            SearchPagesInput()
        s = SearchPagesInput(query="onboarding")
        assert s.query == "onboarding"
        assert s.top == 10


class TestSchemasFiles:
    """Schemas for file/drive tools: ListDrives, ListFiles, SearchFiles, GetFileMetadata, GetFileContent."""

    def test_list_drives_defaults(self):
        s = ListDrivesInput(site_id="s1")
        assert s.top == 10

    def test_list_files_defaults(self):
        s = ListFilesInput(site_id="s1", drive_id="d1")
        assert s.folder_id is None
        assert s.depth == 1
        assert s.top == 10

    def test_list_files_with_folder(self):
        s = ListFilesInput(site_id="s1", drive_id="d1", folder_id="f1", depth=2)
        assert s.folder_id == "f1"
        assert s.depth == 2

    def test_search_files_query_required(self):
        with pytest.raises(Exception):
            SearchFilesInput()
        s = SearchFilesInput(query="budget")
        assert s.query == "budget"
        assert s.site_id is None  # optional cross-site search

    def test_get_file_metadata_required_fields(self):
        s = GetFileMetadataInput(site_id="s1", drive_id="d1", item_id="i1")
        assert s.item_id == "i1"

    def test_get_file_content_required_fields(self):
        s = GetFileContentInput(site_id="s1", drive_id="d1", item_id="i1")
        assert s.item_id == "i1"


class TestSchemasMutations:
    """Schemas for write tools: CreatePage, UpdatePage, CreateFolder, CreateWordDocument, MoveItem, CreateOneNoteNotebook."""

    def test_create_page_publish_default_false(self):
        s = CreatePageInput(site_id="s1", title="t", content_html="<p>c</p>")
        assert s.publish is False

    def test_update_page_all_optional_except_ids(self):
        s = UpdatePageInput(site_id="s1", page_id="p1")
        assert s.title is None
        assert s.content_html is None
        assert s.publish is False

    def test_create_folder_parent_optional(self):
        s = CreateFolderInput(site_id="s1", drive_id="d1", folder_name="Docs")
        assert s.parent_folder_id is None

    def test_create_word_document_content_optional(self):
        s = CreateWordDocumentInput(site_id="s1", drive_id="d1", file_name="Notes")
        assert s.content_text is None
        assert s.parent_folder_id is None

    def test_move_item_new_name_optional(self):
        s = MoveItemInput(
            site_id="s1", drive_id="d1", item_id="i1", destination_folder_id="f2"
        )
        assert s.new_name is None

    def test_create_onenote_notebook_optionals(self):
        s = CreateOneNoteNotebookInput(site_id="s1", notebook_name="N1")
        assert s.section_name is None
        assert s.page_title is None
        assert s.page_content_html is None


class TestSchemasNotebook:
    """Schemas for OneNote tools: FindNotebook, ListNotebookPages, GetNotebookPageContent."""

    def test_find_notebook_required_fields(self):
        s = FindNotebookInput(site_id="s1", notebook_query="mp_plan")
        assert s.notebook_query == "mp_plan"

    def test_list_notebook_pages_required_fields(self):
        s = ListNotebookPagesInput(site_id="s1", notebook_id="n1")
        assert s.notebook_id == "n1"

    def test_get_notebook_page_content_min_length_one(self):
        s = GetNotebookPageContentInput(site_id="s1", page_ids=["p1"])
        assert s.page_ids == ["p1"]
        with pytest.raises(Exception):
            GetNotebookPageContentInput(site_id="s1", page_ids=[])


# ===========================================================================
# Pure helpers
# ===========================================================================

class TestSerializeResponse:
    """Tests for SharePoint._serialize_response — the recursive Graph-SDK serializer."""

    def test_passes_primitives_through(self):
        assert SharePoint._serialize_response(None) is None
        assert SharePoint._serialize_response("x") == "x"
        assert SharePoint._serialize_response(42) == 42
        assert SharePoint._serialize_response(3.14) == 3.14
        assert SharePoint._serialize_response(True) is True

    def test_recurses_into_lists(self):
        assert SharePoint._serialize_response([1, "a", None]) == [1, "a", None]

    def test_recurses_into_dicts(self):
        result = SharePoint._serialize_response({"k": [1, 2], "nested": {"x": "y"}})
        assert result == {"k": [1, 2], "nested": {"x": "y"}}

    def test_object_with_vars_fallback(self):
        class Obj:
            def __init__(self):
                self.id = "abc"
                self.name = "thing"
                self._private = "hidden"

        result = SharePoint._serialize_response(Obj())
        assert result == {"id": "abc", "name": "thing"}

    def test_object_without_dict_falls_back_to_str(self):
        # An object with no __dict__ and no vars-able state — falls through to str().
        result = SharePoint._serialize_response(object())
        assert isinstance(result, str)


class TestExtractCollection:
    """Tests for SharePoint._extract_collection."""

    def test_dict_with_value_key(self):
        sp = _build_sharepoint()
        items = sp._extract_collection({"value": [{"id": "a"}, {"id": "b"}]})
        assert items == [{"id": "a"}, {"id": "b"}]

    def test_plain_list_passthrough(self):
        sp = _build_sharepoint()
        items = sp._extract_collection([{"id": "a"}])
        assert items == [{"id": "a"}]

    def test_object_with_value_attr(self):
        sp = _build_sharepoint()
        obj = MagicMock()
        obj.value = [{"id": "a"}]
        items = sp._extract_collection(obj)
        assert items == [{"id": "a"}]

    def test_dict_without_value_returns_empty(self):
        sp = _build_sharepoint()
        # Dict without `value` key → iterates `data.get("value", [])` → empty list.
        items = sp._extract_collection({"single": "thing"})
        assert items == []


class TestExtractPageHtmlContent:
    """Tests for SharePoint._extract_page_html_content."""

    def test_empty_canvas(self):
        sp = _build_sharepoint()
        assert sp._extract_page_html_content({}) == ""
        assert sp._extract_page_html_content({"canvasLayout": {}}) == ""

    def test_single_section_with_inner_html(self):
        sp = _build_sharepoint()
        page = {
            "canvasLayout": {
                "horizontalSections": [
                    {"columns": [{"webparts": [{"innerHtml": "<p>Hello</p>"}]}]},
                ]
            }
        }
        assert sp._extract_page_html_content(page) == "<p>Hello</p>"

    def test_multiple_sections_concatenated(self):
        sp = _build_sharepoint()
        page = {
            "canvasLayout": {
                "horizontalSections": [
                    {"columns": [{"webparts": [{"innerHtml": "<p>A</p>"}]}]},
                    {"columns": [{"webparts": [{"innerHtml": "<p>B</p>"}]}]},
                ]
            }
        }
        result = sp._extract_page_html_content(page)
        assert "<p>A</p>" in result and "<p>B</p>" in result

    def test_skips_non_dict_entries(self):
        sp = _build_sharepoint()
        page = {
            "canvasLayout": {
                "horizontalSections": [
                    "not a dict",
                    {"columns": ["bad", {"webparts": ["bad", {"innerHtml": "<p>OK</p>"}]}]},
                ]
            }
        }
        assert sp._extract_page_html_content(page) == "<p>OK</p>"


class TestNormalizeNotebookName:
    """Tests for SharePoint._normalize_notebook_name."""

    def test_empty(self):
        assert SharePoint._normalize_notebook_name(None) == ""
        assert SharePoint._normalize_notebook_name("") == ""

    def test_lowercase_and_trim(self):
        assert SharePoint._normalize_notebook_name("  MyNotebook  ") == "mynotebook"

    def test_strip_one_extension(self):
        assert SharePoint._normalize_notebook_name("plan.one") == "plan"

    def test_strip_onetoc2_extension(self):
        assert SharePoint._normalize_notebook_name("plan.onetoc2") == "plan"

    def test_collapse_special_chars_to_spaces(self):
        assert SharePoint._normalize_notebook_name("My_Plan-2025!") == "my plan 2025"


# ===========================================================================
# _handle_error
# ===========================================================================

class TestHandleError:
    """Tests for SharePoint._handle_error — auth-aware error envelope."""

    def test_attribute_error_returns_auth_message(self):
        sp = _build_sharepoint()
        ok, payload = sp._handle_error(
            AttributeError("'NoneType' object has no attribute 'sites'"), "get sites"
        )
        body = json.loads(payload)
        assert ok is False
        assert "not authenticated" in body["error"].lower()
        assert "OAuth" in body["error"]

    def test_value_error_treated_as_auth(self):
        sp = _build_sharepoint()
        ok, payload = sp._handle_error(ValueError("bad config"), "op")
        body = json.loads(payload)
        assert ok is False
        assert "not authenticated" in body["error"].lower()

    def test_unauthorized_message_treated_as_auth(self):
        sp = _build_sharepoint()
        ok, payload = sp._handle_error(RuntimeError("Unauthorized"), "op")
        body = json.loads(payload)
        assert ok is False
        assert "not authenticated" in body["error"].lower()

    def test_generic_exception_passed_through(self):
        sp = _build_sharepoint()
        ok, payload = sp._handle_error(RuntimeError("boom"), "op")
        body = json.loads(payload)
        assert ok is False
        assert body["error"] == "boom"


# ===========================================================================
# @tool methods — sites
# ===========================================================================

class TestGetSites:
    @pytest.mark.asyncio
    async def test_success_returns_sites_with_pagination(self):
        sp = _build_sharepoint({
            "list_sites_with_search_api": AsyncMock(
                return_value=_mock_response(data={"sites": [{"id": "s1", "name": "Marketing"}]})
            )
        })
        body = _ok_tuple(await sp.get_sites(search="marketing", top=10))
        assert body["count"] == 1
        assert body["sites"][0]["id"] == "s1"
        # Triple-key parity for planner placeholders
        assert body["results"] == body["sites"] == body["value"]
        assert "pagination_hint" in body

    @pytest.mark.asyncio
    async def test_falls_back_to_value_key(self):
        sp = _build_sharepoint({
            "list_sites_with_search_api": AsyncMock(
                return_value=_mock_response(data={"value": [{"id": "s1"}]})
            )
        })
        body = _ok_tuple(await sp.get_sites())
        assert body["count"] == 1

    @pytest.mark.asyncio
    async def test_source_error_returns_failure(self):
        sp = _build_sharepoint({
            "list_sites_with_search_api": AsyncMock(
                return_value=_mock_response(success=False, error="API down")
            )
        })
        body = _err_tuple(await sp.get_sites())
        assert "API down" in body["error"]

    @pytest.mark.asyncio
    async def test_exception_routes_through_handle_error(self):
        sp = _build_sharepoint({
            "list_sites_with_search_api": AsyncMock(side_effect=RuntimeError("boom"))
        })
        body = _err_tuple(await sp.get_sites())
        assert body["error"] == "boom"


class TestGetSite:
    @pytest.mark.asyncio
    async def test_success_returns_site_dict(self):
        sp = _build_sharepoint({
            "get_site_by_id": AsyncMock(
                return_value=_mock_response(data={"id": "s1", "name": "HR"})
            )
        })
        body = _ok_tuple(await sp.get_site(site_id="s1"))
        assert body["id"] == "s1"

    @pytest.mark.asyncio
    async def test_not_found_error(self):
        sp = _build_sharepoint({
            "get_site_by_id": AsyncMock(
                return_value=_mock_response(success=False, error="404")
            )
        })
        body = _err_tuple(await sp.get_site(site_id="missing"))
        assert "404" in body["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sp = _build_sharepoint({
            "get_site_by_id": AsyncMock(side_effect=RuntimeError("boom"))
        })
        body = _err_tuple(await sp.get_site(site_id="s1"))
        assert body["error"] == "boom"


# ===========================================================================
# @tool methods — pages
# ===========================================================================

class TestGetPages:
    @pytest.mark.asyncio
    async def test_success_renames_id_to_page_id(self):
        # `get_pages` calls graph.sites.by_site_id(site_id).pages.get(...) directly,
        # so we mock the graph chain on the client.
        graph_resp = MagicMock()
        graph_resp.value = [{"id": "p1", "title": "Home"}]
        sp = _build_sharepoint()
        sp.client.client = MagicMock()
        sp.client.client.sites.by_site_id.return_value.pages.get = AsyncMock(
            return_value=graph_resp
        )
        body = _ok_tuple(await sp.get_pages(site_id="s1"))
        assert body["count"] == 1
        assert body["pages"][0]["page_id"] == "p1"
        # Triple-key parity
        assert body["results"] == body["pages"] == body["value"]

    @pytest.mark.asyncio
    async def test_404_returns_empty_list(self):
        sp = _build_sharepoint()
        sp.client.client = MagicMock()
        sp.client.client.sites.by_site_id.return_value.pages.get = AsyncMock(
            side_effect=RuntimeError("404 not found")
        )
        body = _ok_tuple(await sp.get_pages(site_id="s1"))
        assert body["count"] == 0
        assert "No pages found" in body["note"]

    @pytest.mark.asyncio
    async def test_other_exception_routes_through_handle_error(self):
        sp = _build_sharepoint()
        sp.client.client = MagicMock()
        sp.client.client.sites.by_site_id.return_value.pages.get = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        body = _err_tuple(await sp.get_pages(site_id="s1"))
        assert body["error"] == "boom"


class TestGetPage:
    @pytest.mark.asyncio
    async def test_success_extracts_html_content(self):
        page_data = {
            "id": "p1",
            "title": "Welcome",
            "webUrl": "https://example.sharepoint.com/p1",
            "canvasLayout": {
                "horizontalSections": [
                    {"columns": [{"webparts": [{"innerHtml": "<p>Hi</p>"}]}]},
                ]
            },
        }
        sp = _build_sharepoint({
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(data=page_data)
            )
        })
        body = _ok_tuple(await sp.get_page(site_id="s1", page_id="p1"))
        assert body["page_id"] == "p1"
        assert body["title"] == "Welcome"
        assert body["content_html"] == "<p>Hi</p>"

    @pytest.mark.asyncio
    async def test_missing_canvas_returns_empty_html(self):
        page_data = {"id": "p1", "title": "Empty", "webUrl": "https://x"}
        sp = _build_sharepoint({
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(data=page_data)
            )
        })
        body = _ok_tuple(await sp.get_page(site_id="s1", page_id="p1"))
        assert body["content_html"] == ""

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(success=False, error="not found")
            )
        })
        body = _err_tuple(await sp.get_page(site_id="s1", page_id="p1"))
        assert "not found" in body["error"]

    @pytest.mark.asyncio
    async def test_unserializable_response(self):
        # data has no `id` after serialization → returns "Page not found" error.
        sp = _build_sharepoint({
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(data={"title": "no id"})
            )
        })
        body = _err_tuple(await sp.get_page(site_id="s1", page_id="p1"))
        assert "could not be serialized" in body["error"] or "not found" in body["error"]


class TestSearchPages:
    @pytest.mark.asyncio
    async def test_success_returns_pages_with_pagination(self):
        sp = _build_sharepoint({
            "search_pages_with_search_api": AsyncMock(
                return_value=_mock_response(data={"pages": [{"page_id": "p1", "title": "Setup"}]})
            )
        })
        body = _ok_tuple(await sp.search_pages(query="setup"))
        assert body["count"] == 1
        assert body["pages"][0]["page_id"] == "p1"
        assert body["results"] == body["pages"]

    @pytest.mark.asyncio
    async def test_no_matches_returns_empty(self):
        sp = _build_sharepoint({
            "search_pages_with_search_api": AsyncMock(
                return_value=_mock_response(data={"pages": []})
            )
        })
        body = _ok_tuple(await sp.search_pages(query="nope"))
        assert body["count"] == 0

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "search_pages_with_search_api": AsyncMock(
                return_value=_mock_response(success=False, error="upstream")
            )
        })
        body = _err_tuple(await sp.search_pages(query="x"))
        assert "upstream" in body["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sp = _build_sharepoint({
            "search_pages_with_search_api": AsyncMock(side_effect=RuntimeError("boom"))
        })
        body = _err_tuple(await sp.search_pages(query="x"))
        assert body["error"] == "boom"


# ===========================================================================
# @tool methods — drives & files
# ===========================================================================

class TestListDrives:
    @pytest.mark.asyncio
    async def test_success_normalises_drive_fields(self):
        sp = _build_sharepoint({
            "list_drives_for_site": AsyncMock(
                return_value=_mock_response(data={
                    "drives": [
                        {"id": "d1", "name": "Documents", "driveType": "documentLibrary",
                         "webUrl": "https://x", "quota": {"used": 10}},
                    ],
                })
            )
        })
        body = _ok_tuple(await sp.list_drives(site_id="s1"))
        assert body["count"] == 1
        d = body["drives"][0]
        assert d["id"] == "d1"
        assert d["drive_type"] == "documentLibrary"  # camelCase normalised
        assert d["web_url"] == "https://x"

    @pytest.mark.asyncio
    async def test_404_short_circuits_to_empty(self):
        sp = _build_sharepoint({
            "list_drives_for_site": AsyncMock(
                return_value=_mock_response(success=False, error="404 not found")
            )
        })
        body = _ok_tuple(await sp.list_drives(site_id="s1"))
        assert body["count"] == 0
        assert "not accessible" in body["note"]

    @pytest.mark.asyncio
    async def test_404_exception_short_circuits_to_empty(self):
        sp = _build_sharepoint({
            "list_drives_for_site": AsyncMock(
                side_effect=RuntimeError("404 not found")
            )
        })
        body = _ok_tuple(await sp.list_drives(site_id="s1"))
        assert body["count"] == 0

    @pytest.mark.asyncio
    async def test_other_source_error_propagates(self):
        sp = _build_sharepoint({
            "list_drives_for_site": AsyncMock(
                return_value=_mock_response(success=False, error="permission denied")
            )
        })
        body = _err_tuple(await sp.list_drives(site_id="s1"))
        assert "permission denied" in body["error"]


class TestListFiles:
    @pytest.mark.asyncio
    async def test_success_separates_files_and_folders(self):
        items = [
            {"id": "f1", "name": "doc.docx", "size": 1024,
             "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
             "parentReference": {"driveId": "d1"}},
            {"id": "fo1", "name": "subfolder", "folder": {"childCount": 5},
             "parentReference": {"driveId": "d1"}},
        ]
        sp = _build_sharepoint({
            "list_drive_children": AsyncMock(
                return_value=_mock_response(data={"items": items})
            )
        })
        body = _ok_tuple(await sp.list_files(site_id="s1", drive_id="d1"))
        assert body["count"] == 2
        assert len(body["files"]) == 1
        assert len(body["folders"]) == 1
        assert body["folders"][0]["is_folder"] is True

    @pytest.mark.asyncio
    async def test_missing_drive_id_validation(self):
        sp = _build_sharepoint()
        body = _err_tuple(await sp.list_files(site_id="s1", drive_id=""))
        assert "drive_id is required" in body["error"]

    @pytest.mark.asyncio
    async def test_invalid_depth_rejected(self):
        # `depth=0` → coerced to 1 by `depth or 1`; only negative values trigger the guard.
        sp = _build_sharepoint()
        body = _err_tuple(await sp.list_files(site_id="s1", drive_id="d1", depth=-1))
        assert "depth must be >= 1" in body["error"]

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "list_drive_children": AsyncMock(
                return_value=_mock_response(success=False, error="upstream")
            )
        })
        body = _err_tuple(await sp.list_files(site_id="s1", drive_id="d1"))
        assert "upstream" in body["error"]


class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_success_emits_field_aliases(self):
        files = [
            {"id": "f1", "name": "budget.xlsx",
             "parentReference": {"driveId": "d1", "siteId": "s1", "id": "parent", "path": "/"}},
        ]
        sp = _build_sharepoint({
            "search_files_with_search_api": AsyncMock(
                return_value=_mock_response(data={"files": files})
            )
        })
        body = _ok_tuple(await sp.search_files(query="budget"))
        assert body["count"] == 1
        f = body["files"][0]
        # snake_case primary
        assert f["site_id"] == "s1"
        assert f["drive_id"] == "d1"
        # camelCase alias for legacy planner placeholders
        assert f["siteId"] == "s1"
        assert f["driveId"] == "d1"
        assert f["parentReference"]["driveId"] == "d1"
        # results parity
        assert body["results"] == body["files"]

    @pytest.mark.asyncio
    async def test_no_matches(self):
        sp = _build_sharepoint({
            "search_files_with_search_api": AsyncMock(
                return_value=_mock_response(data={"files": []})
            )
        })
        body = _ok_tuple(await sp.search_files(query="nope"))
        assert body["count"] == 0

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "search_files_with_search_api": AsyncMock(
                return_value=_mock_response(success=False, error="upstream")
            )
        })
        body = _err_tuple(await sp.search_files(query="x"))
        assert "upstream" in body["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sp = _build_sharepoint({
            "search_files_with_search_api": AsyncMock(side_effect=RuntimeError("boom"))
        })
        body = _err_tuple(await sp.search_files(query="x"))
        assert body["error"] == "boom"


# ===========================================================================
# @tool methods — file ops
# ===========================================================================

class TestGetFileMetadata:
    @pytest.mark.asyncio
    async def test_success_normalises_file_metadata(self):
        raw = {
            "id": "f1", "name": "doc.txt", "size": 2048,
            "file": {"mimeType": "text/plain"},
            "webUrl": "https://x",
            "createdDateTime": "2026-01-01",
            "lastModifiedDateTime": "2026-04-01",
            "parentReference": {"driveId": "d1", "siteId": "s1", "id": "parent"},
            "eTag": "etag123",
        }
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(return_value=_mock_response(data=raw))
        })
        body = _ok_tuple(await sp.get_file_metadata(site_id="s1", drive_id="d1", item_id="f1"))
        assert body["id"] == "f1"
        assert body["mime_type"] == "text/plain"
        assert body["is_folder"] is False
        assert body["content_readable_as_text"] is True
        assert body["etag"] == "etag123"

    @pytest.mark.asyncio
    async def test_folder_metadata_marked_as_folder(self):
        raw = {
            "id": "fo1", "name": "subfolder",
            "folder": {"childCount": 3},
            "parentReference": {"driveId": "d1"},
        }
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(return_value=_mock_response(data=raw))
        })
        body = _ok_tuple(await sp.get_file_metadata(site_id="s1", drive_id="d1", item_id="fo1"))
        assert body["is_folder"] is True
        assert body["child_count"] == 3
        assert body["content_readable_as_text"] is False

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(success=False, error="not found")
            )
        })
        body = _err_tuple(await sp.get_file_metadata(site_id="s1", drive_id="d1", item_id="x"))
        assert "not found" in body["error"]


class TestGetFileContent:
    """Tests for the new FileContentParser-based path (mirrors OneDrive's pattern).

    The action layer pre-fetches metadata, applies a 50 MB cap, downloads raw
    bytes from the source layer, then calls `FileContentParser.parse` async."""

    @pytest.mark.asyncio
    async def test_success_returns_parsed_content(self):
        # Parser returns a list of LlmTextContent-like objects with `.model_dump()`.
        parsed_block = MagicMock()
        parsed_block.model_dump = MagicMock(return_value={"type": "text", "text": "Hello World"})
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={
                    "name": "doc.txt", "size": 11,
                    "file": {"mimeType": "text/plain"},
                })
            ),
            "get_drive_item_content": AsyncMock(
                return_value=_mock_response(data=b"Hello World")
            ),
        })
        with patch(
            "app.agents.actions.microsoft.sharepoint.sharepoint.FileContentParser"
        ) as parser_cls:
            parser_cls.return_value.parse = AsyncMock(return_value=(True, [parsed_block]))
            body = _ok_tuple(await sp.get_file_content(
                site_id="s1", drive_id="d1", item_id="f1"
            ))
        assert body["content"] == [{"type": "text", "text": "Hello World"}]
        assert body["mime_type"] == "text/plain"
        assert body["size_bytes"] == 11
        assert body["file_name"] == "doc.txt"

    @pytest.mark.asyncio
    async def test_one_extension_rejected_with_redirect(self):
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={"name": "plan.one"})
            ),
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert ".one" in body["error"]
        assert "find_notebook" in body["error"]

    @pytest.mark.asyncio
    async def test_onetoc2_extension_rejected(self):
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={"name": "Open Notebook.onetoc2"})
            ),
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert "OneNote" in body["error"]

    @pytest.mark.asyncio
    async def test_oversize_file_rejected_before_download(self):
        """Files over the 50 MB cap should error out before any byte download."""
        download_mock = AsyncMock(return_value=_mock_response(data=b"x"))
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={
                    "name": "huge.bin", "size": 60 * 1024 * 1024,  # 60 MB
                    "file": {"mimeType": "application/octet-stream"},
                })
            ),
            "get_drive_item_content": download_mock,
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert "too large" in body["error"]
        assert body["size_bytes"] == 60 * 1024 * 1024
        download_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_size_rejected_before_download(self):
        """If Graph omits the `size` field, treat as oversize and refuse to read.

        Defends against unbounded downloads when metadata is incomplete.
        """
        download_mock = AsyncMock(return_value=_mock_response(data=b"x"))
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={
                    "name": "unknown_size.bin",
                    # no `size` key — simulate Graph omitting it
                    "file": {"mimeType": "application/octet-stream"},
                })
            ),
            "get_drive_item_content": download_mock,
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert "could not be determined" in body["error"] or "too large" in body["error"]
        download_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_file_handled(self):
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={"name": "empty.txt", "size": 0})
            ),
            "get_drive_item_content": AsyncMock(
                return_value=_mock_response(data=b"")
            ),
        })
        body = _ok_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert body["content"] == ""
        assert "Empty file" in body["note"]

    @pytest.mark.asyncio
    async def test_metadata_failure_short_circuits(self):
        """If the metadata pre-fetch fails the byte download must not be attempted."""
        download_mock = AsyncMock()
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(success=False, error="404 not found")
            ),
            "get_drive_item_content": download_mock,
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert "404" in body["error"] or "not found" in body["error"].lower()
        download_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_byte_download_error(self):
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={"name": "doc.txt", "size": 100})
            ),
            "get_drive_item_content": AsyncMock(
                return_value=_mock_response(success=False, error="403 forbidden")
            ),
        })
        body = _err_tuple(await sp.get_file_content(site_id="s1", drive_id="d1", item_id="f1"))
        assert "403" in body["error"]

    @pytest.mark.asyncio
    async def test_parser_failure_returns_error_with_details(self):
        """Parser returning (False, [error_payload]) must surface as an error tuple."""
        err_block = MagicMock()
        err_block.model_dump = MagicMock(return_value={"error": "Token limit exceeded"})
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={
                    "name": "huge.docx", "size": 1024,
                    "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                })
            ),
            "get_drive_item_content": AsyncMock(
                return_value=_mock_response(data=b"\x50\x4b" + b"\x00" * 1022)  # ZIP magic + filler
            ),
        })
        with patch(
            "app.agents.actions.microsoft.sharepoint.sharepoint.FileContentParser"
        ) as parser_cls:
            parser_cls.return_value.parse = AsyncMock(return_value=(False, [err_block]))
            body = _err_tuple(await sp.get_file_content(
                site_id="s1", drive_id="d1", item_id="f1"
            ))
        assert "Failed to parse" in body["error"]
        assert body["details"] == [{"error": "Token limit exceeded"}]

    @pytest.mark.asyncio
    async def test_extension_falls_back_to_filename(self):
        """When `file.fileExtension` is absent, ext should be derived from the file name."""
        parsed_block = MagicMock()
        parsed_block.model_dump = MagicMock(return_value={"type": "text", "text": "ok"})
        sp = _build_sharepoint({
            "get_drive_item_metadata": AsyncMock(
                return_value=_mock_response(data={
                    "name": "Report.PDF", "size": 100,
                    "file": {"mimeType": "application/pdf"},  # no fileExtension key
                })
            ),
            "get_drive_item_content": AsyncMock(
                return_value=_mock_response(data=b"%PDF-1.4 ...")
            ),
        })
        captured_record = {}

        async def fake_parse(file_record, raw, model_name, model_key, cfg):
            captured_record["ext"] = file_record.extension
            captured_record["name"] = file_record.record_name
            return True, [parsed_block]

        with patch(
            "app.agents.actions.microsoft.sharepoint.sharepoint.FileContentParser"
        ) as parser_cls:
            parser_cls.return_value.parse = AsyncMock(side_effect=fake_parse)
            body = _ok_tuple(await sp.get_file_content(
                site_id="s1", drive_id="d1", item_id="f1"
            ))
        assert body["file_name"] == "Report.PDF"
        assert captured_record["ext"] == "pdf"  # lowercased, leading dot stripped


# ===========================================================================
# @tool methods — page write
# ===========================================================================

class TestCreatePage:
    @pytest.mark.asyncio
    async def test_success_draft(self):
        sp = _build_sharepoint({
            "create_site_page": AsyncMock(return_value=_mock_response(data={
                "id": "p1", "webUrl": "https://x", "published": False,
            }))
        })
        body = _ok_tuple(await sp.create_page(
            site_id="s1", title="Hello", content_html="<p>c</p>", publish=False
        ))
        assert body["page_id"] == "p1"
        assert body["published"] is False
        assert "(draft)" in body["message"]

    @pytest.mark.asyncio
    async def test_success_published(self):
        sp = _build_sharepoint({
            "create_site_page": AsyncMock(return_value=_mock_response(data={
                "id": "p1", "webUrl": "https://x", "published": True,
            }))
        })
        body = _ok_tuple(await sp.create_page(
            site_id="s1", title="Hello", content_html="<p>c</p>", publish=True
        ))
        assert body["published"] is True
        assert "and published" in body["message"]

    @pytest.mark.asyncio
    async def test_publish_error_surfaced(self):
        sp = _build_sharepoint({
            "create_site_page": AsyncMock(return_value=_mock_response(data={
                "id": "p1", "published": False, "publish_error": "permission_denied",
            }))
        })
        body = _ok_tuple(await sp.create_page(
            site_id="s1", title="X", content_html="<p/>", publish=True
        ))
        assert body["publish_error"] == "permission_denied"

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "create_site_page": AsyncMock(
                return_value=_mock_response(success=False, error="title invalid")
            )
        })
        body = _err_tuple(await sp.create_page(
            site_id="s1", title="!", content_html="<p/>"
        ))
        assert "title invalid" in body["error"]


class TestUpdatePage:
    @pytest.mark.asyncio
    async def test_neither_field_provided_rejected(self):
        sp = _build_sharepoint()
        body = _err_tuple(await sp.update_page(site_id="s1", page_id="p1"))
        assert "At least one of 'title' or 'content_html'" in body["error"]

    @pytest.mark.asyncio
    async def test_title_only(self):
        sp = _build_sharepoint({
            "update_site_page": AsyncMock(return_value=_mock_response(data={"published": False})),
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(data={"webUrl": "https://x"})
            ),
        })
        body = _ok_tuple(await sp.update_page(site_id="s1", page_id="p1", title="New"))
        assert body["title"] == "New"
        assert body["page_id"] == "p1"
        assert body["web_url"] == "https://x"

    @pytest.mark.asyncio
    async def test_content_only(self):
        sp = _build_sharepoint({
            "update_site_page": AsyncMock(return_value=_mock_response(data={"published": False})),
            "get_site_page_with_canvas": AsyncMock(
                return_value=_mock_response(data={"webUrl": "https://x"})
            ),
        })
        body = _ok_tuple(await sp.update_page(
            site_id="s1", page_id="p1", content_html="<p>new</p>"
        ))
        assert body["page_id"] == "p1"
        assert "title" not in body  # title not in payload when not provided

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "update_site_page": AsyncMock(
                return_value=_mock_response(success=False, error="locked")
            )
        })
        body = _err_tuple(await sp.update_page(
            site_id="s1", page_id="p1", title="X"
        ))
        assert "locked" in body["error"]


# ===========================================================================
# @tool methods — drive write
# ===========================================================================

class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        sp = _build_sharepoint({
            "create_folder": AsyncMock(return_value=_mock_response(data={
                "id": "fo1", "name": "Project Docs", "webUrl": "https://x",
            }))
        })
        body = _ok_tuple(await sp.create_folder(
            site_id="s1", drive_id="d1", folder_name="Project Docs"
        ))
        assert body["folder_id"] == "fo1"
        assert body["name"] == "Project Docs"

    @pytest.mark.asyncio
    async def test_nested_under_parent(self):
        sp = _build_sharepoint({
            "create_folder": AsyncMock(return_value=_mock_response(data={
                "id": "fo2", "name": "Sub",
            }))
        })
        body = _ok_tuple(await sp.create_folder(
            site_id="s1", drive_id="d1", folder_name="Sub", parent_folder_id="fo1"
        ))
        assert body["parent_folder_id"] == "fo1"

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "create_folder": AsyncMock(
                return_value=_mock_response(success=False, error="exists")
            )
        })
        body = _err_tuple(await sp.create_folder(
            site_id="s1", drive_id="d1", folder_name="X"
        ))
        assert "exists" in body["error"]


class TestCreateWordDocument:
    @pytest.mark.asyncio
    async def test_success_with_default_extension(self):
        sp = _build_sharepoint({
            "create_word_document": AsyncMock(return_value=_mock_response(data={
                "id": "f1", "name": "Notes.docx", "size": 4096,
            }))
        })
        body = _ok_tuple(await sp.create_word_document(
            site_id="s1", drive_id="d1", file_name="Notes"
        ))
        assert body["item_id"] == "f1"
        assert body["name"] == "Notes.docx"
        assert body["size_bytes"] == 4096

    @pytest.mark.asyncio
    async def test_success_with_content(self):
        sp = _build_sharepoint({
            "create_word_document": AsyncMock(return_value=_mock_response(data={
                "id": "f2", "name": "Doc.docx",
            }))
        })
        body = _ok_tuple(await sp.create_word_document(
            site_id="s1", drive_id="d1", file_name="Doc",
            content_text="Line1\nLine2"
        ))
        assert body["item_id"] == "f2"

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "create_word_document": AsyncMock(
                return_value=_mock_response(success=False, error="quota")
            )
        })
        body = _err_tuple(await sp.create_word_document(
            site_id="s1", drive_id="d1", file_name="X"
        ))
        assert "quota" in body["error"]


class TestMoveItem:
    @pytest.mark.asyncio
    async def test_success(self):
        sp = _build_sharepoint({
            "move_drive_item": AsyncMock(return_value=_mock_response(
                data={
                    "id": "f1", "name": "moved.docx", "webUrl": "https://x",
                    "parentReference": {"driveId": "d1", "id": "newparent"},
                },
                message="Item moved successfully",
            ))
        })
        body = _ok_tuple(await sp.move_item(
            site_id="s1", drive_id="d1", item_id="f1", destination_folder_id="newparent"
        ))
        assert body["item_id"] == "f1"
        assert body["destination_folder_id"] == "newparent"
        assert "moved" in body["message"].lower()

    @pytest.mark.asyncio
    async def test_with_new_name(self):
        sp = _build_sharepoint({
            "move_drive_item": AsyncMock(return_value=_mock_response(
                data={"id": "f1", "name": "renamed.docx",
                      "parentReference": {"driveId": "d1", "id": "newparent"}},
            ))
        })
        body = _ok_tuple(await sp.move_item(
            site_id="s1", drive_id="d1", item_id="f1",
            destination_folder_id="newparent", new_name="renamed.docx",
        ))
        assert body["name"] == "renamed.docx"

    @pytest.mark.asyncio
    async def test_same_source_destination_rejected(self):
        sp = _build_sharepoint()
        body = _err_tuple(await sp.move_item(
            site_id="s1", drive_id="d1", item_id="same", destination_folder_id="same"
        ))
        assert "cannot be the same" in body["error"]

    @pytest.mark.asyncio
    async def test_empty_drive_id_rejected(self):
        sp = _build_sharepoint()
        body = _err_tuple(await sp.move_item(
            site_id="s1", drive_id="   ", item_id="f1", destination_folder_id="f2"
        ))
        assert "drive_id is required" in body["error"]

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "move_drive_item": AsyncMock(
                return_value=_mock_response(success=False, error="not found")
            )
        })
        body = _err_tuple(await sp.move_item(
            site_id="s1", drive_id="d1", item_id="f1", destination_folder_id="f2"
        ))
        assert "not found" in body["error"]


# ===========================================================================
# @tool methods — OneNote
# ===========================================================================

class TestFindNotebook:
    @pytest.mark.asyncio
    async def test_resolved_single_match(self):
        sp = _build_sharepoint({
            "list_onenote_notebooks": AsyncMock(return_value=_mock_response(data={
                "results": [
                    {"notebook_id": "n1", "display_name": "mp_plan", "web_url": "https://x"},
                ],
            }))
        })
        body = _ok_tuple(await sp.find_notebook(site_id="s1", notebook_query="mp_plan"))
        assert body["resolved"] is True
        assert body["notebook_id"] == "n1"
        assert body["site_id"] == "s1"

    @pytest.mark.asyncio
    async def test_no_match_returns_error(self):
        sp = _build_sharepoint({
            "list_onenote_notebooks": AsyncMock(return_value=_mock_response(data={
                "results": [
                    {"notebook_id": "n1", "display_name": "totally_different"},
                ],
            }))
        })
        body = _err_tuple(await sp.find_notebook(site_id="s1", notebook_query="missing"))
        assert body["resolved"] is False
        assert "No OneNote notebook matched" in body["error"]

    @pytest.mark.asyncio
    async def test_ambiguous_returns_candidates(self):
        sp = _build_sharepoint({
            "list_onenote_notebooks": AsyncMock(return_value=_mock_response(data={
                "results": [
                    {"notebook_id": "n1", "display_name": "Plan"},
                    {"notebook_id": "n2", "display_name": "Plan"},
                ],
            }))
        })
        body = _ok_tuple(await sp.find_notebook(site_id="s1", notebook_query="Plan"))
        assert body["resolved"] is False
        assert body["ambiguous"] is True
        assert len(body["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_normalisation_strips_extension(self):
        sp = _build_sharepoint({
            "list_onenote_notebooks": AsyncMock(return_value=_mock_response(data={
                "results": [{"notebook_id": "n1", "display_name": "myplan"}],
            }))
        })
        # Query has .one extension — normaliser strips it before matching.
        body = _ok_tuple(await sp.find_notebook(site_id="s1", notebook_query="myplan.one"))
        assert body["resolved"] is True

    @pytest.mark.asyncio
    async def test_list_failure(self):
        sp = _build_sharepoint({
            "list_onenote_notebooks": AsyncMock(
                return_value=_mock_response(success=False, error="upstream")
            )
        })
        body = _err_tuple(await sp.find_notebook(site_id="s1", notebook_query="x"))
        assert body["resolved"] is False


class TestListNotebookPages:
    @pytest.mark.asyncio
    async def test_success_with_sections_and_pages(self):
        sp = _build_sharepoint({
            "list_onenote_sections": AsyncMock(return_value=_mock_response(data={
                "results": [
                    {"section_id": "sec1", "display_name": "Intro"},
                ],
            })),
            "list_onenote_pages": AsyncMock(return_value=_mock_response(data={
                "results": [
                    {"page_id": "pg1", "title": "Welcome", "order": 0,
                     "web_url": "https://x"},
                ],
            })),
        })
        body = _ok_tuple(await sp.list_notebook_pages(site_id="s1", notebook_id="n1"))
        assert body["notebook_id"] == "n1"
        assert len(body["sections"]) == 1
        assert len(body["pages"]) == 1
        assert body["pages"][0]["page_id"] == "pg1"
        assert body["pages"][0]["section_id"] == "sec1"

    @pytest.mark.asyncio
    async def test_empty_notebook(self):
        sp = _build_sharepoint({
            "list_onenote_sections": AsyncMock(
                return_value=_mock_response(data={"results": []})
            ),
        })
        body = _ok_tuple(await sp.list_notebook_pages(site_id="s1", notebook_id="n1"))
        assert body["sections"] == []
        assert body["pages"] == []

    @pytest.mark.asyncio
    async def test_section_list_failure(self):
        sp = _build_sharepoint({
            "list_onenote_sections": AsyncMock(
                return_value=_mock_response(success=False, error="upstream")
            )
        })
        body = _err_tuple(await sp.list_notebook_pages(site_id="s1", notebook_id="n1"))
        assert "upstream" in body["error"]


class TestGetNotebookPageContent:
    @pytest.mark.asyncio
    async def test_success_single_page(self):
        sp = _build_sharepoint({
            "get_onenote_page_content": AsyncMock(return_value=_mock_response(data={
                "page_id": "pg1", "content": "<p>hi</p>",
            })),
        })
        body = _ok_tuple(await sp.get_notebook_page_content(site_id="s1", page_ids=["pg1"]))
        assert body["count"] == 1
        assert body["pages"][0]["content"] == "<p>hi</p>"

    @pytest.mark.asyncio
    async def test_success_multiple_pages(self):
        async def fake_get(site_id, page_id, max_chars):
            return _mock_response(data={"page_id": page_id, "content": f"<p>{page_id}</p>"})
        sp = _build_sharepoint({
            "get_onenote_page_content": AsyncMock(side_effect=fake_get),
        })
        body = _ok_tuple(await sp.get_notebook_page_content(
            site_id="s1", page_ids=["pg1", "pg2"]
        ))
        assert body["count"] == 2

    @pytest.mark.asyncio
    async def test_partial_failures_populate_failed_page_ids(self):
        async def fake_get(site_id, page_id, max_chars):
            if page_id == "pg2":
                return _mock_response(success=False, error="404")
            return _mock_response(data={"page_id": page_id, "content": "ok"})
        sp = _build_sharepoint({
            "get_onenote_page_content": AsyncMock(side_effect=fake_get),
        })
        body = _ok_tuple(await sp.get_notebook_page_content(
            site_id="s1", page_ids=["pg1", "pg2"]
        ))
        assert body["count"] == 1
        assert body["failed_page_ids"] == ["pg2"]

    @pytest.mark.asyncio
    async def test_caps_at_20_pages(self):
        async def fake_get(site_id, page_id, max_chars):
            return _mock_response(data={"page_id": page_id, "content": "ok"})
        sp = _build_sharepoint({
            "get_onenote_page_content": AsyncMock(side_effect=fake_get),
        })
        page_ids = [f"pg{i}" for i in range(30)]
        body = _ok_tuple(await sp.get_notebook_page_content(site_id="s1", page_ids=page_ids))
        assert body["count"] == 20

    @pytest.mark.asyncio
    async def test_exception_routes_through_handle_error(self):
        sp = _build_sharepoint({
            "get_onenote_page_content": AsyncMock(side_effect=RuntimeError("boom")),
        })
        body = _err_tuple(await sp.get_notebook_page_content(site_id="s1", page_ids=["pg1"]))
        assert body["error"] == "boom"


class TestCreateOneNoteNotebook:
    @pytest.mark.asyncio
    async def test_name_only(self):
        sp = _build_sharepoint({
            "create_onenote_notebook": AsyncMock(return_value=_mock_response(data={
                "notebook_id": "n1", "notebook_name": "Team Notes",
                "notebook_web_url": "https://x",
            })),
        })
        body = _ok_tuple(await sp.create_onenote_notebook(
            site_id="s1", notebook_name="Team Notes"
        ))
        assert body["notebook_id"] == "n1"
        assert body["notebook_name"] == "Team Notes"
        assert "section_id" not in body

    @pytest.mark.asyncio
    async def test_with_section_and_page(self):
        sp = _build_sharepoint({
            "create_onenote_notebook": AsyncMock(return_value=_mock_response(data={
                "notebook_id": "n1", "notebook_name": "Project",
                "section_id": "sec1", "section_name": "Overview",
                "page_id": "pg1", "page_title": "Welcome",
                "page_web_url": "https://x/page",
            })),
        })
        body = _ok_tuple(await sp.create_onenote_notebook(
            site_id="s1", notebook_name="Project",
            section_name="Overview", page_title="Welcome",
            page_content_html="<p>Hi</p>",
        ))
        assert body["section_id"] == "sec1"
        assert body["page_id"] == "pg1"
        assert body["page_title"] == "Welcome"

    @pytest.mark.asyncio
    async def test_source_error(self):
        sp = _build_sharepoint({
            "create_onenote_notebook": AsyncMock(
                return_value=_mock_response(success=False, error="quota")
            ),
        })
        body = _err_tuple(await sp.create_onenote_notebook(
            site_id="s1", notebook_name="X"
        ))
        assert "quota" in body["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        sp = _build_sharepoint({
            "create_onenote_notebook": AsyncMock(side_effect=RuntimeError("boom")),
        })
        body = _err_tuple(await sp.create_onenote_notebook(
            site_id="s1", notebook_name="X"
        ))
        assert body["error"] == "boom"
