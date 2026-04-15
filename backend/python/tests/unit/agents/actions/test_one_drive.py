"""
Unit tests for app.agents.actions.microsoft.one_drive.one_drive

Tests the OneDrive toolset exposed to agents. All external dependencies
(MSGraphClient, OneDriveDataSource) are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.microsoft.one_drive.one_drive import (
    OneDrive,
    _generate_word_docx_bytes,
    _normalize_odata,
    _response_json,
    _serialize_graph_obj,
    # Pydantic schemas
    CopyItemInput,
    CreateFolderInput,
    CreateOfficeFileInput,
    CreateOneNoteNotebookInput,
    CreateOneNotePageInput,
    CreateOneNoteSectionInput,
    GetDownloadUrlInput,
    GetDriveInput,
    GetDrivesInput,
    GetFileContentInput,
    GetFileInput,
    GetFilesInput,
    GetFolderChildrenInput,
    GetOneNotePageContentInput,
    GetOneNotePagesInput,
    GetOneNoteSectionsInput,
    GetSharedWithMeInput,
    MoveItemInput,
    RenameItemInput,
    SearchFilesInput,
    SearchSharedWithMeInput,
    ShareItemInput,
    _DriveSearchResult,
    _SharedItemFetchResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client():
    """Return a mocked MSGraphClient."""
    return MagicMock()


def _mock_response(success=True, data=None, error=None, message=None):
    """Return a mock response object matching OneDriveResponse shape."""
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    resp.message = message
    return resp


def _make_onedrive(**overrides):
    """Create an OneDrive instance with a mocked client."""
    client = _mock_client()
    od = OneDrive.__new__(OneDrive)
    od.client = AsyncMock()
    od.state = overrides.get("state", {})
    return od


# ============================================================================
# _serialize_graph_obj
# ============================================================================

class TestSerializeGraphObj:
    def test_kiota_json_writer_success(self):
        class ParsableObj:
            def get_field_deserializers(self):
                return {}

        obj = ParsableObj()
        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(
            return_value=b'{"id":"x1","displayName":"Item"}'
        )
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = _serialize_graph_obj(obj)
        assert out == {"id": "x1", "displayName": "Item"}

    def test_kiota_writer_empty_dict_falls_through(self):
        class ParsableObj:
            def get_field_deserializers(self):
                return {}

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=b"{}")
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = _serialize_graph_obj(ParsableObj())
        assert isinstance(out, str)

    def test_kiota_backing_store_enumerate(self):
        class BS:
            def enumerate_(self):
                yield ("displayName", "Bob")
                yield ("_hidden", "skip")

        class ParsableObj:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = BS()

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=None)
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = _serialize_graph_obj(ParsableObj())
        assert out["displayName"] == "Bob"

    def test_vars_typeerror_fallback(self):
        class NoVars:
            __slots__ = ()

        assert isinstance(_serialize_graph_obj(NoVars()), str)

    def test_none(self):
        assert _serialize_graph_obj(None) is None

    def test_primitives(self):
        assert _serialize_graph_obj("hello") == "hello"
        assert _serialize_graph_obj(42) == 42
        assert _serialize_graph_obj(3.14) == 3.14
        assert _serialize_graph_obj(True) is True

    def test_list(self):
        assert _serialize_graph_obj([1, "two", None]) == [1, "two", None]

    def test_dict(self):
        result = _serialize_graph_obj({"a": 1, "b": "c"})
        assert result == {"a": 1, "b": "c"}

    def test_nested_dict(self):
        result = _serialize_graph_obj({"a": {"b": [1, 2]}})
        assert result == {"a": {"b": [1, 2]}}

    def test_object_with_vars(self):
        class Simple:
            def __init__(self):
                self.name = "test"
                self.value = 42
        obj = Simple()
        result = _serialize_graph_obj(obj)
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_object_with_private_attrs_skipped(self):
        class WithPrivate:
            def __init__(self):
                self._secret = "hidden"
                self.public = "visible"
        result = _serialize_graph_obj(WithPrivate())
        assert "public" in result
        assert "_secret" not in result

    def test_object_with_additional_data(self):
        class WithAdditional:
            def __init__(self):
                self.name = "test"
                self.additional_data = {"extra": "value"}
        result = _serialize_graph_obj(WithAdditional())
        assert result["name"] == "test"
        assert result["extra"] == "value"

    def test_kiota_json_loads_raises_triggers_except(self):
        class ParsableObj:
            def get_field_deserializers(self):
                return {}

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=b'{"a":1}')
        with (
            patch(
                "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
                return_value=mock_writer,
            ),
            patch(
                "app.agents.actions.microsoft.one_drive.one_drive.json.loads",
                side_effect=ValueError("bad json"),
            ),
        ):
            out = _serialize_graph_obj(ParsableObj())
        assert isinstance(out, str)

    def test_backing_store_value_serialize_failure_uses_str(self):
        class BS:
            def enumerate_(self):
                circ = []
                circ.append(circ)
                yield ("bad", circ)

        class ParsableObj:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = BS()

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=None)
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = _serialize_graph_obj(ParsableObj())
        assert "bad" in out
        assert isinstance(out["bad"], str)

    def test_backing_store_enumerate_raises_outer_except(self):
        """enumerate_() raises → inner except (96–97); code falls through to vars()."""
        class BS:
            def enumerate_(self):
                raise RuntimeError("enumerate boom")

        class ParsableObj:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = BS()

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=None)
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = _serialize_graph_obj(ParsableObj())
        # Generic fallback still serializes instance attributes (e.g. backing_store), not str(obj).
        assert isinstance(out, dict)
        assert "backing_store" in out

    def test_vars_child_serialize_failure_uses_str(self):
        class Parent:
            def __init__(self):
                circ = []
                circ.append(circ)
                self.child = circ

        out = _serialize_graph_obj(Parent())
        assert "child" in out
        assert isinstance(out["child"], str)

    def test_additional_data_merge_failure_uses_str(self):
        class Host:
            def __init__(self):
                self.name = "n"
                circ = []
                circ.append(circ)
                self.additional_data = {"extra": circ}

        out = _serialize_graph_obj(Host())
        assert out["name"] == "n"
        assert isinstance(out["extra"], str)


# ============================================================================
# _normalize_odata
# ============================================================================

class TestNormalizeOdata:
    def test_adds_results_alias(self):
        data = {"value": [{"id": 1}, {"id": 2}]}
        result = _normalize_odata(data)
        assert result["results"] == result["value"]

    def test_no_value_key(self):
        data = {"items": [1, 2]}
        result = _normalize_odata(data)
        assert "results" not in result

    def test_value_not_list(self):
        data = {"value": "string"}
        result = _normalize_odata(data)
        assert "results" not in result

    def test_results_already_present(self):
        data = {"value": [1], "results": [99]}
        result = _normalize_odata(data)
        assert result["results"] == [99]  # unchanged

    def test_non_dict_passthrough(self):
        assert _normalize_odata("string") == "string"
        assert _normalize_odata(42) == 42
        assert _normalize_odata(None) is None


# ============================================================================
# _response_json
# ============================================================================

class TestResponseJson:
    def test_success_with_data(self):
        resp = _mock_response(success=True, data={"id": "abc"})
        result = json.loads(_response_json(resp))
        assert result["success"] is True
        assert result["data"]["id"] == "abc"

    def test_failure_with_error(self):
        resp = _mock_response(success=False, error="Not found")
        result = json.loads(_response_json(resp))
        assert result["success"] is False
        assert result["error"] == "Not found"

    def test_message_included(self):
        resp = _mock_response(success=True, message="All good")
        result = json.loads(_response_json(resp))
        assert result["message"] == "All good"

    def test_no_data_no_error(self):
        resp = _mock_response(success=True, data=None, error=None, message=None)
        result = json.loads(_response_json(resp))
        assert result["success"] is True
        assert "data" not in result
        assert "error" not in result


# ============================================================================
# _generate_word_docx_bytes
# ============================================================================

class TestGenerateWordDocxBytes:
    def test_returns_bytes(self):
        result = _generate_word_docx_bytes("Hello world")
        assert isinstance(result, bytes)
        # DOCX is a ZIP file
        assert result[:4] == b"PK\x03\x04"

    def test_none_content(self):
        result = _generate_word_docx_bytes(None)
        assert isinstance(result, bytes)
        assert result[:4] == b"PK\x03\x04"

    def test_empty_string(self):
        result = _generate_word_docx_bytes("")
        assert isinstance(result, bytes)


# ============================================================================
# Pydantic Input Schemas
# ============================================================================

class TestPydanticSchemas:
    def test_get_drives_defaults(self):
        inp = GetDrivesInput()
        assert inp.search is None
        assert inp.top is None

    def test_get_drive_required_fields(self):
        inp = GetDriveInput(drive_id="d-1")
        assert inp.drive_id == "d-1"
        assert inp.select is None

    def test_get_files_input(self):
        inp = GetFilesInput(drive_id="d-1", folder_id="f-1", top=10)
        assert inp.drive_id == "d-1"
        assert inp.folder_id == "f-1"
        assert inp.top == 10

    def test_get_file_input(self):
        inp = GetFileInput(drive_id="d-1", item_id="i-1")
        assert inp.drive_id == "d-1"
        assert inp.item_id == "i-1"

    def test_search_files_input(self):
        inp = SearchFilesInput(drive_id="d-1", query="budget")
        assert inp.query == "budget"

    def test_create_folder_input(self):
        inp = CreateFolderInput(drive_id="d-1", folder_name="New Folder")
        assert inp.folder_name == "New Folder"
        assert inp.parent_folder_id is None

    def test_move_item_input(self):
        inp = MoveItemInput(drive_id="d-1", item_id="i-1", new_parent_id="p-2")
        assert inp.new_parent_id == "p-2"
        assert inp.new_name is None

    def test_rename_item_input(self):
        inp = RenameItemInput(drive_id="d-1", item_id="i-1", new_name="report.docx")
        assert inp.new_name == "report.docx"

    def test_copy_item_input(self):
        inp = CopyItemInput(drive_id="d-1", item_id="i-1", destination_folder_id="f-2")
        assert inp.destination_drive_id is None

    def test_share_item_input(self):
        inp = ShareItemInput(
            drive_id="d-1", item_id="i-1", emails=["a@b.com"], role="write"
        )
        assert inp.role == "write"
        assert inp.require_sign_in is True
        assert inp.send_invitation is True

    def test_get_shared_with_me_defaults(self):
        inp = GetSharedWithMeInput()
        assert inp.top is None

    def test_search_shared_with_me_defaults(self):
        inp = SearchSharedWithMeInput(query="report")
        assert inp.top == 10
        assert inp.per_drive_top is None

    def test_create_office_file_input(self):
        inp = CreateOfficeFileInput(
            drive_id="d-1", file_name="doc.docx", file_type="word"
        )
        assert inp.parent_folder_id is None
        assert inp.content is None

    def test_onenote_notebook_input(self):
        inp = CreateOneNoteNotebookInput(notebook_name="My Notes")
        assert inp.notebook_name == "My Notes"

    def test_onenote_section_input(self):
        inp = CreateOneNoteSectionInput(web_url="https://example.com/nb", section_name="S1")
        assert inp.section_name == "S1"

    def test_onenote_page_input(self):
        inp = CreateOneNotePageInput(section_id="s-1", page_title="Agenda")
        assert inp.page_body_html is None

    def test_get_onenote_sections_input(self):
        inp = GetOneNoteSectionsInput(web_url="https://example.com/nb")
        assert inp.web_url == "https://example.com/nb"

    def test_get_onenote_pages_input(self):
        inp = GetOneNotePagesInput(section_id="s-1")
        assert inp.section_id == "s-1"

    def test_get_onenote_page_content_input(self):
        inp = GetOneNotePageContentInput(page_id="p-1")
        assert inp.page_id == "p-1"

    def test_get_download_url_input(self):
        inp = GetDownloadUrlInput(drive_id="d-1", item_id="i-1")
        assert inp.drive_id == "d-1"

    def test_get_file_content_input(self):
        inp = GetFileContentInput(drive_id="d-1", item_id="i-1")
        assert inp.item_id == "i-1"


# ============================================================================
# OneDrive._resolve_item_type
# ============================================================================

class TestResolveItemType:
    def test_folder(self):
        assert OneDrive._resolve_item_type({"folder": {}}) == "Folder"

    def test_mime_type(self):
        data = {"file": {"mimeType": "application/pdf"}}
        assert OneDrive._resolve_item_type(data) == "PDF"

    def test_unknown(self):
        assert OneDrive._resolve_item_type({}) == "Unknown"

    def test_empty_mime(self):
        data = {"file": {"mimeType": ""}}
        assert OneDrive._resolve_item_type(data) == "Unknown"


# ============================================================================
# OneDrive._collect_enriched_and_drive_ids
# ============================================================================

class TestCollectEnrichedAndDriveIds:
    def test_empty_results(self):
        enriched, drive_ids = OneDrive._collect_enriched_and_drive_ids([])
        assert enriched == []
        assert drive_ids == []

    def test_none_results_skipped(self):
        results = [None, None]
        enriched, drive_ids = OneDrive._collect_enriched_and_drive_ids(results)
        assert enriched == []
        assert drive_ids == []

    def test_collects_unique_drive_ids(self):
        results = [
            _SharedItemFetchResult(enriched={"name": "f1"}, drive_id="d-1"),
            _SharedItemFetchResult(enriched={"name": "f2"}, drive_id="d-1"),
            _SharedItemFetchResult(enriched={"name": "f3"}, drive_id="d-2"),
        ]
        enriched, drive_ids = OneDrive._collect_enriched_and_drive_ids(results)
        assert len(enriched) == 3
        assert drive_ids == ["d-1", "d-2"]

    def test_none_drive_id_excluded(self):
        results = [
            _SharedItemFetchResult(enriched={"name": "f1"}, drive_id=None),
        ]
        enriched, drive_ids = OneDrive._collect_enriched_and_drive_ids(results)
        assert len(enriched) == 1
        assert drive_ids == []


# ============================================================================
# OneDrive tool methods
# ============================================================================

class TestGetDrives:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.me_list_drives = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        success, result = await od.get_drives()
        assert success is True
        parsed = json.loads(result)
        assert parsed["success"] is True

    @pytest.mark.asyncio
    async def test_failure(self):
        od = _make_onedrive()
        od.client.me_list_drives = AsyncMock(
            return_value=_mock_response(success=False, error="Unauthorized")
        )
        success, result = await od.get_drives()
        assert success is False

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.me_list_drives = AsyncMock(side_effect=RuntimeError("network"))
        success, result = await od.get_drives()
        assert success is False
        assert "network" in json.loads(result)["error"]


class TestGetDrive:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_drive_get_drive = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "d-1"})
        )
        success, result = await od.get_drive(drive_id="d-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        od = _make_onedrive()
        od.client.drives_drive_get_drive = AsyncMock(
            return_value=_mock_response(success=False, error="Not found")
        )
        success, _ = await od.get_drive(drive_id="d-bad")
        assert success is False

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_drive_get_drive = AsyncMock(side_effect=Exception("boom"))
        success, result = await od.get_drive(drive_id="d-1")
        assert success is False
        assert "boom" in json.loads(result)["error"]


class TestGetFiles:
    @pytest.mark.asyncio
    async def test_success_root(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        success, _ = await od.get_files(drive_id="d-1")
        assert success is True
        # Verify "root" used as default parent
        call_kwargs = od.client.drives_items_list_children.call_args[1]
        assert call_kwargs["driveItem_id"] == "root"

    @pytest.mark.asyncio
    async def test_with_folder_id(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        await od.get_files(drive_id="d-1", folder_id="f-1")
        call_kwargs = od.client.drives_items_list_children.call_args[1]
        assert call_kwargs["driveItem_id"] == "f-1"

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(side_effect=Exception("err"))
        success, result = await od.get_files(drive_id="d-1")
        assert success is False


class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_drive_search = AsyncMock(
            return_value=_mock_response(success=True, data={"value": [{"name": "report.pdf"}]})
        )
        success, result = await od.search_files(drive_id="d-1", query="report")
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_drive_search = AsyncMock(side_effect=RuntimeError("timeout"))
        success, result = await od.search_files(drive_id="d-1", query="q")
        assert success is False


class TestGetFile:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "i-1", "name": "file.txt"})
        )
        success, _ = await od.get_file(drive_id="d-1", item_id="i-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=False, error="404")
        )
        success, _ = await od.get_file(drive_id="d-1", item_id="bad")
        assert success is False


class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_items_create_children = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "new-f"})
        )
        success, result = await od.create_folder(
            drive_id="d-1", folder_name="NewFolder"
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_with_parent(self):
        od = _make_onedrive()
        od.client.drives_items_create_children = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        await od.create_folder(
            drive_id="d-1", folder_name="Sub", parent_folder_id="parent-1"
        )
        call_kwargs = od.client.drives_items_create_children.call_args[1]
        assert call_kwargs["driveItem_id"] == "parent-1"

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_items_create_children = AsyncMock(side_effect=Exception("e"))
        success, _ = await od.create_folder(drive_id="d-1", folder_name="F")
        assert success is False


class TestMoveItem:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, _ = await od.move_item(
            drive_id="d-1", item_id="i-1", new_parent_id="p-2"
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_with_new_name(self):
        od = _make_onedrive()
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        await od.move_item(
            drive_id="d-1", item_id="i-1", new_parent_id="p-2", new_name="renamed.txt"
        )
        od.client.drives_update_items.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_update_items = AsyncMock(side_effect=Exception("err"))
        success, _ = await od.move_item(drive_id="d-1", item_id="i-1", new_parent_id="p")
        assert success is False


class TestShareItem:
    @pytest.mark.asyncio
    async def test_invalid_role(self):
        od = _make_onedrive()
        success, result = await od.share_item(
            drive_id="d-1", item_id="i-1", emails=["a@b.com"], role="admin"
        )
        assert success is False
        assert "Invalid role" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_empty_emails(self):
        od = _make_onedrive()
        success, result = await od.share_item(
            drive_id="d-1", item_id="i-1", emails=[], role="read"
        )
        assert success is False
        assert "email" in json.loads(result)["error"].lower()

    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_items_invite = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        success, _ = await od.share_item(
            drive_id="d-1", item_id="i-1", emails=["x@y.com"], role="read"
        )
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_items_invite = AsyncMock(side_effect=Exception("fail"))
        success, result = await od.share_item(
            drive_id="d-1", item_id="i-1", emails=["x@y.com"], role="write"
        )
        assert success is False


class TestCopyItem:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_drive_items_drive_item_copy = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, result = await od.copy_item(
            drive_id="d-1", item_id="i-1", destination_folder_id="f-2"
        )
        assert success is True
        assert "copy operation started" in json.loads(result)["message"]

    @pytest.mark.asyncio
    async def test_with_dest_drive_and_name(self):
        od = _make_onedrive()
        od.client.drives_drive_items_drive_item_copy = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        await od.copy_item(
            drive_id="d-1",
            item_id="i-1",
            destination_folder_id="f-2",
            destination_drive_id="d-2",
            new_name="copy.txt",
        )
        call_kwargs = od.client.drives_drive_items_drive_item_copy.call_args[1]
        body = call_kwargs["request_body"]
        assert body["parentReference"]["driveId"] == "d-2"
        assert body["name"] == "copy.txt"


class TestGetRootFolder:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_get_root = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "root"})
        )
        success, _ = await od.get_root_folder(drive_id="d-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_get_root = AsyncMock(side_effect=Exception("err"))
        success, _ = await od.get_root_folder(drive_id="d-1")
        assert success is False


class TestGetFolderChildren:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        success, _ = await od.get_folder_children(drive_id="d-1", folder_id="f-1")
        assert success is True


class TestGetDownloadUrl:
    @pytest.mark.asyncio
    async def test_success_with_url(self):
        od = _make_onedrive()
        mock_data = MagicMock()
        mock_data.additional_data = {"@microsoft.graph.downloadUrl": "https://dl.example.com/file"}
        resp = _mock_response(success=True, data=mock_data)
        od.client.drives_get_items = AsyncMock(return_value=resp)
        success, result = await od.get_download_url(drive_id="d-1", item_id="i-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(side_effect=Exception("err"))
        success, _ = await od.get_download_url(drive_id="d-1", item_id="i-1")
        assert success is False


class TestGetSharedWithMe:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, '{"value": []}', []))
        success, result = await od.get_shared_with_me()
        assert success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(False, "error msg", []))
        success, result = await od.get_shared_with_me()
        assert success is False

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(side_effect=Exception("oops"))
        success, result = await od.get_shared_with_me()
        assert success is False


class TestSearchSharedWithMe:
    @pytest.mark.asyncio
    async def test_no_drives(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", []))
        success, result = await od.search_shared_with_me(query="budget")
        assert success is True
        parsed = json.loads(result)
        assert parsed["drives"] == []

    @pytest.mark.asyncio
    async def test_with_drives(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", ["d-1"]))
        od._search_drive = AsyncMock(return_value=_DriveSearchResult(
            drive_id="d-1",
            success=True,
            results={"value": [{"name": "report.pdf"}]},
        ))
        success, result = await od.search_shared_with_me(query="report")
        assert success is True
        parsed = json.loads(result)
        assert len(parsed["value"]) == 1
        assert parsed["value"][0]["_drive_id"] == "d-1"

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(side_effect=Exception("fail"))
        success, result = await od.search_shared_with_me(query="q")
        assert success is False


class TestCreateOfficeFile:
    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        od = _make_onedrive()
        success, result = await od.create_office_file(
            drive_id="d-1", file_name="file.txt", file_type="text"
        )
        assert success is False
        assert "Unsupported" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_success_word(self):
        od = _make_onedrive()
        mock_data = MagicMock()
        mock_data.additional_data = {}
        resp = _mock_response(success=True, data=mock_data)
        od.client.drives_items_upload_content = AsyncMock(return_value=resp)

        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value={"id": "new-id", "name": "doc.docx", "webUrl": "https://x"},
        ):
            success, result = await od.create_office_file(
                drive_id="d-1", file_name="doc", file_type="word"
            )
            assert success is True
            parsed = json.loads(result)
            assert parsed["file_type"] == "word"

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.drives_items_upload_content = AsyncMock(side_effect=Exception("err"))
        success, _ = await od.create_office_file(
            drive_id="d-1", file_name="f.docx", file_type="word"
        )
        assert success is False


class TestCreateOneNoteNotebook:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.me_onenote_create_notebooks = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "nb-1"})
        )
        success, _ = await od.create_onenote_notebook(notebook_name="Notes")
        assert success is True

    @pytest.mark.asyncio
    async def test_failure(self):
        od = _make_onedrive()
        od.client.me_onenote_create_notebooks = AsyncMock(
            return_value=_mock_response(success=False, error="err")
        )
        success, _ = await od.create_onenote_notebook(notebook_name="Notes")
        assert success is False


class TestGetOneNotePages:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.me_onenote_get_pages = AsyncMock(
            return_value=_mock_response(success=True, data={"value": []})
        )
        success, _ = await od.get_onenote_pages(section_id="s-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_exception(self):
        od = _make_onedrive()
        od.client.me_onenote_get_pages = AsyncMock(side_effect=Exception("err"))
        success, _ = await od.get_onenote_pages(section_id="s-1")
        assert success is False


class TestGetOneNotePageContent:
    @pytest.mark.asyncio
    async def test_success(self):
        od = _make_onedrive()
        od.client.me_onenote_get_page_content = AsyncMock(
            return_value=_mock_response(success=True, data="<html>content</html>")
        )
        success, _ = await od.get_onenote_page_content(page_id="p-1")
        assert success is True


# ============================================================================
# OneDrive.__init__ and _serialize_response (duplicate of module helper)
# ============================================================================


class TestOneDriveInit:
    def test_wraps_client_with_data_source(self):
        raw_client = MagicMock()
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive.OneDriveDataSource"
        ) as ds:
            od = OneDrive(raw_client)
        ds.assert_called_once_with(raw_client)
        assert od.client is ds.return_value


class TestOneDriveSerializeResponse:
    def test_dict_roundtrip(self):
        assert OneDrive._serialize_response({"a": [1, 2]}) == {"a": [1, 2]}

    def test_none_primitive_list_dict_mirror_module_helper(self):
        assert OneDrive._serialize_response(None) is None
        assert OneDrive._serialize_response("x") == "x"
        assert OneDrive._serialize_response([1, None]) == [1, None]

    def test_kiota_json_loads_raises(self):
        class ParsableObj:
            def get_field_deserializers(self):
                return {}

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=b'{"a":1}')
        with (
            patch(
                "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
                return_value=mock_writer,
            ),
            patch("json.loads", side_effect=ValueError("bad")),
        ):
            out = OneDrive._serialize_response(ParsableObj())
        assert isinstance(out, str)

    def test_backing_store_and_circular_additional_data(self):
        class BS:
            def enumerate_(self):
                yield ("id", "from-bs")

        class ParsableObj:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = BS()
                circ = []
                circ.append(circ)
                self.additional_data = {"extra": circ}

        mock_writer = MagicMock()
        mock_writer.get_serialized_content = MagicMock(return_value=None)
        with patch(
            "kiota_serialization_json.json_serialization_writer.JsonSerializationWriter",
            return_value=mock_writer,
        ):
            out = OneDrive._serialize_response(ParsableObj())
        assert out["id"] == "from-bs"
        assert isinstance(out["extra"], str)


# ============================================================================
# shared_with_data, helpers, and extra tool branches
# ============================================================================


class TestSharedWithData:
    @pytest.mark.asyncio
    async def test_success_builds_enriched(self):
        od = _make_onedrive()

        shared_item = MagicMock()
        shared_item.id = "SPO@123"
        shared_item.last_shared = MagicMock()
        shared_item.last_shared.shared_by = MagicMock()
        shared_item.last_shared.shared_by.address = "peer@example.com"
        shared_item.last_shared.shared_by.display_name = "Peer"
        shared_item.last_shared.shared_date_time = "2024-01-01"
        shared_item.last_shared.sharing_type = "direct"

        insights = _mock_response(
            success=True,
            data=MagicMock(value=[shared_item]),
        )
        od.client.me_insights_shared = AsyncMock(return_value=insights)

        me_resp = _mock_response(
            success=True,
            data={"mail": "me@example.com"},
        )
        od.client.me = AsyncMock(return_value=me_resp)

        resource = _mock_response(
            success=True,
            data={
                "name": "Doc.pdf",
                "webUrl": "https://x",
                "file": {"mimeType": "application/pdf"},
                "parentReference": {"driveId": "drv1", "path": "/drive/root:/Folder", "name": "Folder"},
                "size": 10,
                "lastModifiedDateTime": "t",
            },
        )
        od.client.me_insights_shared_resource = AsyncMock(return_value=resource)

        ok, payload, drives = await od.shared_with_data(top=5)
        assert ok is True
        data = json.loads(payload)
        assert len(data["value"]) == 1
        assert data["value"][0]["name"] == "Doc.pdf"
        assert "drv1" in drives

    @pytest.mark.asyncio
    async def test_insights_failure(self):
        od = _make_onedrive()
        od.client.me_insights_shared = AsyncMock(
            return_value=_mock_response(success=False, error="nope")
        )
        ok, raw, drives = await od.shared_with_data()
        assert ok is False
        assert "nope" in raw or "success" in raw
        assert drives == []

    @pytest.mark.asyncio
    async def test_fetch_shared_item_skips_self_share(self):
        od = _make_onedrive()
        item = MagicMock()
        item.id = "SPO@1"
        item.last_shared = MagicMock()
        item.last_shared.shared_by = MagicMock()
        item.last_shared.shared_by.address = "me@example.com"
        out = await od._fetch_shared_item(item, "me@example.com")
        assert out is None
        od.client.me_insights_shared_resource.assert_not_called()


class TestOneDriveHelpers:
    def test_build_enriched_item_no_last_shared(self):
        item = MagicMock(last_shared=None)
        resource = {
            "name": "n",
            "webUrl": "u",
            "folder": {},
            "parentReference": {"path": "/drive/root:/A/B", "name": "fallback"},
        }
        enriched = OneDrive._build_enriched_item(item, resource)
        assert enriched["type"] == "Folder"
        assert enriched["sharedBy"]["displayName"] is None
        assert enriched["location"] in ("A/B", "fallback")

    @pytest.mark.asyncio
    async def test_get_current_user_email(self):
        od = _make_onedrive()
        od.client.me = AsyncMock(
            return_value=_mock_response(success=True, data={"mail": "a@b.com"})
        )
        email = await od._get_current_user_email()
        assert email == "a@b.com"

    @pytest.mark.asyncio
    async def test_search_drive_exception(self):
        od = _make_onedrive()
        od.client.drives_drive_search = AsyncMock(side_effect=RuntimeError("down"))
        out = await od._search_drive("d1", "q")
        assert out.success is False
        assert "down" in out.error


class TestGetFilesExtraBranches:
    @pytest.mark.asyncio
    async def test_failure_response_branch(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            return_value=_mock_response(success=False, error="denied")
        )
        success, raw = await od.get_files(drive_id="d-1")
        assert success is False
        assert "denied" in raw


class TestGetFolderChildrenExtra:
    @pytest.mark.asyncio
    async def test_failure_branch(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            return_value=_mock_response(success=False, error="x")
        )
        success, _ = await od.get_folder_children(drive_id="d-1", folder_id="f-1")
        assert success is False


class TestGetFileExtra:
    @pytest.mark.asyncio
    async def test_failure_branch(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=False, error="missing")
        )
        success, _ = await od.get_file(drive_id="d-1", item_id="i-1")
        assert success is False


class TestSearchFilesExtra:
    @pytest.mark.asyncio
    async def test_api_failure_branch(self):
        od = _make_onedrive()
        od.client.drives_drive_search = AsyncMock(
            return_value=_mock_response(success=False, error="bad")
        )
        success, raw = await od.search_files(drive_id="d-1", query="q")
        assert success is False


class TestGetSharedWithMeDirect:
    @pytest.mark.asyncio
    async def test_success_path_without_mocking_shared_with_data(self):
        od = _make_onedrive()
        od.client.me_insights_shared = AsyncMock(
            return_value=_mock_response(success=True, data=MagicMock(value=[]))
        )
        od.client.me = AsyncMock(
            return_value=_mock_response(success=True, data={"mail": "m@x.com"})
        )
        success, raw = await od.get_shared_with_me()
        assert success is True
        assert "value" in json.loads(raw)


class TestSearchSharedWithMeExtra:
    @pytest.mark.asyncio
    async def test_results_list_payload(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", ["d-1"]))
        od._search_drive = AsyncMock(
            return_value=_DriveSearchResult(
                drive_id="d-1",
                success=True,
                results=[{"name": "a"}],
            )
        )
        success, raw = await od.search_shared_with_me(query="x", top=10)
        assert success is True
        parsed = json.loads(raw)
        assert any(h.get("name") == "a" for h in parsed["value"])

    @pytest.mark.asyncio
    async def test_non_dict_hit_wrapped(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", ["d-1"]))
        od._search_drive = AsyncMock(
            return_value=_DriveSearchResult(
                drive_id="d-1",
                success=True,
                results={"value": [42]},
            )
        )
        success, raw = await od.search_shared_with_me(query="x", top=10)
        assert success is True
        parsed = json.loads(raw)
        assert {"_drive_id": "d-1", "value": 42} in parsed["value"]


class TestRenameItemExtension:
    @pytest.mark.asyncio
    async def test_appends_extension_from_current_name(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(
                success=True,
                data={"name": "report.pdf", "id": "i"},
            )
        )
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, _ = await od.rename_item(
            drive_id="d-1", item_id="i-1", new_name="Final"
        )
        assert success is True
        body = od.client.drives_update_items.call_args[1]["request_body"]
        assert body.name == "Final.pdf"


class TestMoveItemSuccessJson:
    @pytest.mark.asyncio
    async def test_success_returns_serialized_response(self):
        od = _make_onedrive()
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "i"})
        )
        success, raw = await od.move_item(
            drive_id="d-1", item_id="i-1", new_parent_id="p-2"
        )
        assert success is True
        assert "success" in json.loads(raw)


class TestGetRootFolderExtra:
    @pytest.mark.asyncio
    async def test_failure_branch(self):
        od = _make_onedrive()
        od.client.drives_get_root = AsyncMock(
            return_value=_mock_response(success=False, error="no root")
        )
        success, _ = await od.get_root_folder(drive_id="d-1")
        assert success is False


class TestCopyItemFailure:
    @pytest.mark.asyncio
    async def test_api_failure_branch(self):
        od = _make_onedrive()
        od.client.drives_drive_items_drive_item_copy = AsyncMock(
            return_value=_mock_response(success=False, error="fail")
        )
        success, _ = await od.copy_item(
            drive_id="d-1", item_id="i-1", destination_folder_id="f-2"
        )
        assert success is False


class TestShareItemFailure:
    @pytest.mark.asyncio
    async def test_api_returns_failure(self):
        od = _make_onedrive()
        od.client.drives_items_invite = AsyncMock(
            return_value=_mock_response(success=False, error="invite failed")
        )
        success, raw = await od.share_item(
            drive_id="d-1", item_id="i-1", emails=["a@b.com"], role="read"
        )
        assert success is False


class TestGetDownloadUrlBranches:
    @pytest.mark.asyncio
    async def test_non_dict_data_uses_response_json(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=True, data="plain-id")
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value="not-a-dict",
        ):
            success, raw = await od.get_download_url(drive_id="d-1", item_id="i-1")
        assert success is True
        parsed = json.loads(raw)
        assert parsed.get("success") is True
        assert "data" in parsed

    @pytest.mark.asyncio
    async def test_dict_with_additional_data_download_url(self):
        od = _make_onedrive()
        mock_data = MagicMock()
        mock_data.additional_data = {"@microsoft.graph.downloadUrl": "https://dl"}
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=True, data=mock_data)
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value={
                "id": "1",
                "name": "f",
                "size": 3,
                "additionalData": {"@microsoft.graph.downloadUrl": "https://dl2"},
            },
        ):
            success, raw = await od.get_download_url(drive_id="d-1", item_id="i-1")
        assert success is True
        parsed = json.loads(raw)
        assert parsed["download_url"] in ("https://dl", "https://dl2")


class TestGetFileContentTool:
    @pytest.mark.asyncio
    async def test_parse_success_path(self):
        od = _make_onedrive()
        od.state = {
            "model_name": "m",
            "model_key": "k",
            "config_service": MagicMock(),
        }
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(
                success=True,
                data={
                    "name": "a.txt",
                    "size": 4,
                    "file": {"mimeType": "text/plain", "fileExtension": "txt"},
                },
            )
        )
        od.client.drives_items_get_content = AsyncMock(
            return_value=_mock_response(success=True, data=b"hello")
        )
        mock_parser = MagicMock()
        mock_parser.parse = AsyncMock(return_value=(True, [{"content": "ok"}]))
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive.FileContentParser",
            return_value=mock_parser,
        ):
            success, raw = await od.get_file_content(drive_id="d-1", item_id="i-1")
        assert success is True
        assert "ok" in raw


class TestCreateOfficeFileNonDict:
    @pytest.mark.asyncio
    async def test_success_when_serialize_returns_non_dict(self):
        od = _make_onedrive()
        od.client.drives_items_upload_content = AsyncMock(
            return_value=_mock_response(success=True, data=MagicMock())
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value="created-id-string",
        ):
            success, raw = await od.create_office_file(
                drive_id="d-1", file_name="doc.docx", file_type="word"
            )
        assert success is True
        assert "created" in json.loads(raw).get("message", "").lower()


class TestOneNoteFailures:
    @pytest.mark.asyncio
    async def test_create_notebook_api_failure(self):
        od = _make_onedrive()
        od.client.me_onenote_create_notebooks = AsyncMock(
            return_value=_mock_response(success=False, error="err")
        )
        success, _ = await od.create_onenote_notebook(notebook_name="N")
        assert success is False

    @pytest.mark.asyncio
    async def test_create_section_notebook_lookup_fails(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            return_value=_mock_response(success=False, error="nf")
        )
        success, _ = await od.create_onenote_section(
            web_url="https://n", section_name="S"
        )
        assert success is False

    @pytest.mark.asyncio
    async def test_create_section_create_section_fails(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            return_value=_mock_response(success=True, data=MagicMock())
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value={"id": "nb"},
        ):
            od.client.me_onenote_create_section = AsyncMock(
                return_value=_mock_response(success=False, error="bad")
            )
            success, _ = await od.create_onenote_section(
                web_url="https://n", section_name="S"
            )
        assert success is False

    @pytest.mark.asyncio
    async def test_create_page_failure(self):
        od = _make_onedrive()
        od.client.me_onenote_create_page = AsyncMock(
            return_value=_mock_response(success=False, error="e")
        )
        success, _ = await od.create_onenote_page(section_id="s", page_title="P")
        assert success is False

    @pytest.mark.asyncio
    async def test_get_sections_notebook_resolve_fails(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            return_value=_mock_response(success=False, error="x")
        )
        success, _ = await od.get_onenote_sections(web_url="https://n")
        assert success is False

    @pytest.mark.asyncio
    async def test_get_sections_missing_notebook_id(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            return_value=_mock_response(success=True, data=MagicMock())
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value={},
        ):
            success, raw = await od.get_onenote_sections(web_url="https://n")
        assert success is False
        assert "notebook" in json.loads(raw)["error"].lower()

    @pytest.mark.asyncio
    async def test_get_sections_list_fails(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            return_value=_mock_response(success=True, data=MagicMock())
        )
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._serialize_graph_obj",
            return_value={"id": "nb"},
        ):
            od.client.me_onenote_get_sections = AsyncMock(
                return_value=_mock_response(success=False, error="bad")
            )
            success, _ = await od.get_onenote_sections(web_url="https://n")
        assert success is False

    @pytest.mark.asyncio
    async def test_get_pages_failure(self):
        od = _make_onedrive()
        od.client.me_onenote_get_pages = AsyncMock(
            return_value=_mock_response(success=False, error="e")
        )
        success, _ = await od.get_onenote_pages(section_id="s-1")
        assert success is False

    @pytest.mark.asyncio
    async def test_get_page_content_failure(self):
        od = _make_onedrive()
        od.client.me_onenote_get_page_content = AsyncMock(
            return_value=_mock_response(success=False, error="e")
        )
        success, _ = await od.get_onenote_page_content(page_id="p-1")
        assert success is False

    @pytest.mark.asyncio
    async def test_create_notebook_exception(self):
        od = _make_onedrive()
        od.client.me_onenote_create_notebooks = AsyncMock(side_effect=RuntimeError("net"))
        success, raw = await od.create_onenote_notebook(notebook_name="N")
        assert success is False
        assert "net" in json.loads(raw)["error"]

    @pytest.mark.asyncio
    async def test_create_section_exception(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        success, raw = await od.create_onenote_section(
            web_url="https://n", section_name="S"
        )
        assert success is False
        assert "boom" in json.loads(raw)["error"]

    @pytest.mark.asyncio
    async def test_create_page_exception(self):
        od = _make_onedrive()
        od.client.me_onenote_create_page = AsyncMock(side_effect=RuntimeError("x"))
        success, raw = await od.create_onenote_page(section_id="s", page_title="P")
        assert success is False
        assert "x" in json.loads(raw)["error"]

    @pytest.mark.asyncio
    async def test_get_sections_exception(self):
        od = _make_onedrive()
        od.client.me_onenote_get_notebook_from_web_url = AsyncMock(
            side_effect=RuntimeError("y")
        )
        success, raw = await od.get_onenote_sections(web_url="https://n")
        assert success is False
        assert "y" in json.loads(raw)["error"]

    @pytest.mark.asyncio
    async def test_get_page_content_success_path_response_json_raises(self):
        od = _make_onedrive()
        ok_resp = _mock_response(success=True, data="<html/>")
        od.client.me_onenote_get_page_content = AsyncMock(return_value=ok_resp)
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive._response_json",
            side_effect=[
                RuntimeError("serialize fail"),
                '{"success":false,"data":null}',
            ],
        ):
            success, raw = await od.get_onenote_page_content(page_id="p-1")
        assert success is False
        assert "success" in json.loads(raw)


# ============================================================================
# Remaining branch coverage (fetch helpers, exceptions, tool paths)
# ============================================================================


class TestFetchSharedItemResourceFailure:
    @pytest.mark.asyncio
    async def test_returns_none_when_resource_call_fails(self):
        od = _make_onedrive()
        item = MagicMock()
        item.id = "SPO@1"
        item.last_shared = None
        od.client.me_insights_shared_resource = AsyncMock(
            return_value=_mock_response(success=False, error="nope")
        )
        out = await od._fetch_shared_item(item, "me@example.com")
        assert out is None


class TestFetchDriveObject:
    @pytest.mark.asyncio
    async def test_returns_none_on_api_failure(self):
        od = _make_onedrive()
        od.client.drives_drive_get_drive = AsyncMock(
            return_value=_mock_response(success=False, error="bad")
        )
        assert await od._fetch_drive_object("d-1") is None

    @pytest.mark.asyncio
    async def test_returns_data_on_success(self):
        od = _make_onedrive()
        od.client.drives_drive_get_drive = AsyncMock(
            return_value=_mock_response(success=True, data={"id": "d-1", "name": "My"})
        )
        out = await od._fetch_drive_object("d-1")
        assert out["id"] == "d-1"


class TestSharedWithDataException:
    @pytest.mark.asyncio
    async def test_insights_raises(self):
        od = _make_onedrive()
        od.client.me_insights_shared = AsyncMock(side_effect=RuntimeError("up"))
        ok, raw, drives = await od.shared_with_data()
        assert ok is False
        assert "up" in json.loads(raw)["error"]
        assert drives == []


class TestSearchDriveSuccessPayload:
    @pytest.mark.asyncio
    async def test_success_returns_results_payload(self):
        od = _make_onedrive()
        od.client.drives_drive_search = AsyncMock(
            return_value=_mock_response(
                success=True, data={"value": [{"name": "a"}], "extra": 1}
            )
        )
        out = await od._search_drive("d1", "q", per_drive_top=5, select="id")
        assert out.success is True
        assert out.results["value"][0]["name"] == "a"


class TestGetFolderChildrenException:
    @pytest.mark.asyncio
    async def test_client_raises(self):
        od = _make_onedrive()
        od.client.drives_items_list_children = AsyncMock(
            side_effect=RuntimeError("timeout")
        )
        success, raw = await od.get_folder_children(drive_id="d-1", folder_id="f-1")
        assert success is False
        assert "timeout" in json.loads(raw)["error"]


class TestGetFileException:
    @pytest.mark.asyncio
    async def test_client_raises(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(side_effect=RuntimeError("conn"))
        success, raw = await od.get_file(drive_id="d-1", item_id="i-1")
        assert success is False
        assert "conn" in json.loads(raw)["error"]


class TestSearchSharedWithMeMoreBranches:
    @pytest.mark.asyncio
    async def test_skips_failed_drive_search(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", ["d-1"]))
        od._search_drive = AsyncMock(
            return_value=_DriveSearchResult(drive_id="d-1", success=False, error="x"),
        )
        success, raw = await od.search_shared_with_me(query="q")
        assert success is True
        parsed = json.loads(raw)
        assert parsed["value"] == []

    @pytest.mark.asyncio
    async def test_results_payload_neither_dict_nor_list(self):
        od = _make_onedrive()
        od.shared_with_data = AsyncMock(return_value=(True, "{}", ["d-1"]))
        od._search_drive = AsyncMock(
            return_value=_DriveSearchResult(
                drive_id="d-1",
                success=True,
                results=42,
            )
        )
        success, raw = await od.search_shared_with_me(query="q")
        assert success is True
        assert json.loads(raw)["value"] == []


class TestCreateFolderFailureBranch:
    @pytest.mark.asyncio
    async def test_api_returns_not_success(self):
        od = _make_onedrive()
        od.client.drives_items_create_children = AsyncMock(
            return_value=_mock_response(success=False, error="denied")
        )
        success, raw = await od.create_folder(drive_id="d-1", folder_name="F")
        assert success is False
        assert "denied" in raw


class TestRenameItemMoreBranches:
    @pytest.mark.asyncio
    async def test_skips_extension_when_prefetch_fails(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=False, error="nf")
        )
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, _ = await od.rename_item(
            drive_id="d-1", item_id="i-1", new_name="BareName"
        )
        assert success is True
        body = od.client.drives_update_items.call_args[1]["request_body"]
        assert body.name == "BareName"

    @pytest.mark.asyncio
    async def test_no_append_when_new_name_has_extension(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(
                success=True,
                data={"name": "old.pdf", "id": "i"},
            )
        )
        od.client.drives_update_items = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, _ = await od.rename_item(
            drive_id="d-1", item_id="i-1", new_name="Final.docx"
        )
        assert success is True
        body = od.client.drives_update_items.call_args[1]["request_body"]
        assert body.name == "Final.docx"

    @pytest.mark.asyncio
    async def test_exception_in_try(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(side_effect=RuntimeError("boom"))
        success, raw = await od.rename_item(
            drive_id="d-1", item_id="i-1", new_name="x"
        )
        assert success is False
        assert "boom" in json.loads(raw)["error"]


class TestMoveItemException:
    @pytest.mark.asyncio
    async def test_update_raises(self):
        od = _make_onedrive()
        od.client.drives_update_items = AsyncMock(side_effect=RuntimeError("move err"))
        success, raw = await od.move_item(
            drive_id="d-1", item_id="i-1", new_parent_id="p-2"
        )
        assert success is False
        assert "move err" in json.loads(raw)["error"]


class TestCopyItemException:
    @pytest.mark.asyncio
    async def test_copy_raises(self):
        od = _make_onedrive()
        od.client.drives_drive_items_drive_item_copy = AsyncMock(
            side_effect=RuntimeError("copy bad")
        )
        success, raw = await od.copy_item(
            drive_id="d-1", item_id="i-1", destination_folder_id="f-2"
        )
        assert success is False
        assert "copy bad" in json.loads(raw)["error"]


class TestShareItemWithMessage:
    @pytest.mark.asyncio
    async def test_passes_message_to_request_body(self):
        od = _make_onedrive()
        od.client.drives_items_invite = AsyncMock(
            return_value=_mock_response(success=True, data={})
        )
        success, _ = await od.share_item(
            drive_id="d-1",
            item_id="i-1",
            emails=["a@b.com"],
            role="read",
            message="Please review",
        )
        assert success is True
        body = od.client.drives_items_invite.call_args[1]["request_body"]
        assert body.message == "Please review"


class TestGetDownloadUrlException:
    @pytest.mark.asyncio
    async def test_client_raises(self):
        od = _make_onedrive()
        od.client.drives_get_items = AsyncMock(side_effect=RuntimeError("dl err"))
        success, raw = await od.get_download_url(drive_id="d-1", item_id="i-1")
        assert success is False
        assert "dl err" in json.loads(raw)["error"]


class TestGetFileContentMoreBranches:
    @pytest.mark.asyncio
    async def test_file_info_not_success(self):
        od = _make_onedrive()
        od.state = {}
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(success=False, error="meta")
        )
        success, raw = await od.get_file_content(drive_id="d-1", item_id="i-1")
        assert success is False
        assert "meta" in raw

    @pytest.mark.asyncio
    async def test_non_bytes_content_returns_string_content(self):
        od = _make_onedrive()
        od.state = {"config_service": MagicMock()}
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(
                success=True,
                data={
                    "name": "a.txt",
                    "size": 1,
                    "file": {"mimeType": "text/plain", "fileExtension": "txt"},
                },
            )
        )
        od.client.drives_items_get_content = AsyncMock(
            return_value=_mock_response(success=True, data="not-bytes")
        )
        success, raw = await od.get_file_content(drive_id="d-1", item_id="i-1")
        assert success is True
        assert json.loads(raw)["content"] == "not-bytes"

    @pytest.mark.asyncio
    async def test_parse_returns_false(self):
        od = _make_onedrive()
        od.state = {"config_service": MagicMock()}
        od.client.drives_get_items = AsyncMock(
            return_value=_mock_response(
                success=True,
                data={
                    "name": "a.txt",
                    "size": 1,
                    "file": {"mimeType": "text/plain", "fileExtension": "txt"},
                },
            )
        )
        od.client.drives_items_get_content = AsyncMock(
            return_value=_mock_response(success=True, data=b"hello")
        )
        mock_parser = MagicMock()
        mock_parser.parse = AsyncMock(return_value=(False, [{"error": "parse err"}]))
        with patch(
            "app.agents.actions.microsoft.one_drive.one_drive.FileContentParser",
            return_value=mock_parser,
        ):
            success, raw = await od.get_file_content(drive_id="d-1", item_id="i-1")
        assert success is False
        assert "parse err" in raw

    @pytest.mark.asyncio
    async def test_outer_exception(self):
        od = _make_onedrive()
        od.state = {"config_service": MagicMock()}
        od.client.drives_get_items = AsyncMock(side_effect=RuntimeError("outer"))
        success, raw = await od.get_file_content(drive_id="d-1", item_id="i-1")
        assert success is False
        assert "outer" in json.loads(raw)["error"]


class TestCreateOfficeFileFailureResponse:
    @pytest.mark.asyncio
    async def test_upload_returns_not_success(self):
        od = _make_onedrive()
        od.client.drives_items_upload_content = AsyncMock(
            return_value=_mock_response(success=False, error="quota")
        )
        success, raw = await od.create_office_file(
            drive_id="d-1", file_name="d.docx", file_type="word"
        )
        assert success is False
        assert "quota" in raw

