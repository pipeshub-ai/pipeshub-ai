"""
Unit tests for app.agents.actions.google.drive.drive

Tests the GoogleDrive agent toolset. All external dependencies
(GoogleClient, GoogleDriveDataSource) are mocked.
"""

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.agents.actions.google.drive.drive import (
    GoogleDrive,
    _is_structured_query,
    _is_size_query,
    _raw_drive_service,
    _execute_media_download,
    # Pydantic schemas
    CopyFileInput,
    CreateFolderInput,
    DeleteFileInput,
    DownloadFileInput,
    GetFileContentInput,
    GetFileDetailsInput,
    GetFilePermissionsInput,
    GetFilesListInput,
    GetSharedDrivesInput,
    SearchFilesInput,
    UploadFileInput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_drive():
    """Create a GoogleDrive instance with a fully mocked DataSource client."""
    gd = GoogleDrive.__new__(GoogleDrive)
    gd.client = AsyncMock()
    return gd


# ============================================================================
# Pydantic Input Schemas
# ============================================================================

class TestPydanticSchemas:
    def test_get_files_list_defaults(self):
        inp = GetFilesListInput()
        assert inp.corpora is None
        assert inp.drive_id is None
        assert inp.order_by is None
        assert inp.page_size is None
        assert inp.page_token is None
        assert inp.query is None
        assert inp.spaces is None

    def test_get_file_details_defaults(self):
        inp = GetFileDetailsInput()
        assert inp.fileId is None
        assert inp.acknowledge_abuse is None
        assert inp.supports_all_drives is None

    def test_create_folder_defaults(self):
        inp = CreateFolderInput()
        assert inp.folderName is None
        assert inp.parent_folder_id is None

    def test_delete_file_required(self):
        inp = DeleteFileInput(file_id="abc")
        assert inp.file_id == "abc"
        assert inp.supports_all_drives is None

    def test_copy_file_required(self):
        inp = CopyFileInput(file_id="f1")
        assert inp.file_id == "f1"
        assert inp.new_name is None
        assert inp.parent_folder_id is None

    def test_search_files_required(self):
        inp = SearchFilesInput(query="report")
        assert inp.query == "report"
        assert inp.page_size is None
        assert inp.order_by is None

    def test_download_file_defaults(self):
        inp = DownloadFileInput()
        assert inp.fileId is None
        assert inp.mimeType is None

    def test_upload_file_defaults(self):
        inp = UploadFileInput()
        assert inp.file_name is None
        assert inp.content is None
        assert inp.mime_type is None
        assert inp.parent_folder_id is None

    def test_get_file_permissions_required(self):
        inp = GetFilePermissionsInput(file_id="fid")
        assert inp.file_id == "fid"
        assert inp.page_size is None

    def test_get_shared_drives_defaults(self):
        inp = GetSharedDrivesInput()
        assert inp.page_size is None
        assert inp.query is None


# ============================================================================
# GoogleDrive.__init__
# ============================================================================

class TestGoogleDriveInit:
    def test_wraps_client_with_data_source(self):
        raw_client = MagicMock()
        with patch(
            "app.agents.actions.google.drive.drive.GoogleDriveDataSource"
        ) as ds:
            gd = GoogleDrive(raw_client)
        ds.assert_called_once_with(raw_client)
        assert gd.client is ds.return_value


# ============================================================================
# get_files_list
# ============================================================================

class TestGetFilesList:
    @pytest.mark.asyncio
    async def test_success_no_query(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(
            return_value={"files": [{"id": "1", "name": "doc.pdf"}], "nextPageToken": None}
        )
        success, result = await gd.get_files_list()
        assert success is True
        data = json.loads(result)
        assert data["totalResults"] == 1
        assert data["files"][0]["id"] == "1"

    @pytest.mark.asyncio
    async def test_simple_text_query_wrapped_in_name_contains(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(query="report")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["q"] == 'name contains "report"'

    @pytest.mark.asyncio
    async def test_structured_query_passed_through(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(query='name contains "budget"')
        call_kwargs = gd.client.files_list.call_args[1]
        assert "budget" in call_kwargs["q"]

    @pytest.mark.asyncio
    async def test_invalid_size_operator_query_ignored(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": [{"size": "100"}]})
        success, result = await gd.get_files_list(query="size=100")
        assert success is True
        data = json.loads(result)
        # query was detected as invalid; formatted_query should be None
        assert data["formatted_query"] is None
        assert "warning" in data

    @pytest.mark.asyncio
    async def test_drive_id_root_normalized_to_none(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(drive_id="root")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["driveId"] is None

    @pytest.mark.asyncio
    async def test_drive_id_forces_corpora_drive(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(drive_id="shared-drive-id", corpora="user")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["corpora"] == "drive"

    @pytest.mark.asyncio
    async def test_corpora_drive_without_drive_id_falls_back_to_allDrives(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(corpora="drive")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["corpora"] == "allDrives"

    @pytest.mark.asyncio
    async def test_shared_drive_sets_include_items_and_supports_all_drives(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.get_files_list(drive_id="shared-id")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["includeItemsFromAllDrives"] is True
        assert call_kwargs["supportsAllDrives"] is True

    @pytest.mark.asyncio
    async def test_next_page_token_included_in_response(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(
            return_value={"files": [], "nextPageToken": "tok123"}
        )
        success, result = await gd.get_files_list()
        assert json.loads(result)["nextPageToken"] == "tok123"

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(side_effect=RuntimeError("network error"))
        success, result = await gd.get_files_list()
        assert success is False
        assert "network error" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_fulltext_contains_query_not_wrapped(self):
        # Regression: 'fullText contains' has a capital T but the operator list
        # is checked against query.lower(), so the literal must be lowercase too.
        # Before the fix this query was misidentified as a plain-text query and
        # wrapped as: name contains "fullText contains \"budget\""
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        q = 'fullText contains "budget"'
        await gd.get_files_list(query=q)
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["q"] == q, (
            f"fullText contains query must be passed through unchanged; got: {call_kwargs['q']!r}"
        )

    @pytest.mark.asyncio
    async def test_size_gt_client_side_filtering_applied(self):
        gd = _make_drive()
        files = [{"size": "500"}, {"size": "1500"}, {"size": "2000"}]
        gd.client.files_list = AsyncMock(return_value={"files": files})
        success, result = await gd.get_files_list(query="size>1000")
        assert success is True
        data = json.loads(result)
        assert data["totalResults"] == 2
        assert all(int(f["size"]) > 1000 for f in data["files"])


# ============================================================================
# _filter_files_by_size
# ============================================================================

class TestFilterFilesBySize:
    def _files(self):
        return [
            {"size": "0"},
            {"size": "500"},
            {"size": "1000"},
            {"size": "2000"},
        ]

    def test_equal(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size=1000")
        assert len(result) == 1
        assert result[0]["size"] == "1000"

    def test_greater_than(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size>500")
        assert len(result) == 2
        assert all(int(f["size"]) > 500 for f in result)

    def test_greater_than_or_equal(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size>=500")
        assert len(result) == 3

    def test_less_than(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size<500")
        assert len(result) == 1
        assert result[0]["size"] == "0"

    def test_less_than_or_equal(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size<=500")
        assert len(result) == 2

    def test_no_match_returns_empty(self):
        gd = _make_drive()
        result = gd._filter_files_by_size(self._files(), "size=9999")
        assert result == []

    def test_invalid_condition_returns_original(self):
        gd = _make_drive()
        files = [{"size": "100"}]
        result = gd._filter_files_by_size(files, "badcondition")
        assert result == files

    def test_missing_size_field_defaults_to_zero(self):
        gd = _make_drive()
        files = [{"name": "no-size.txt"}]
        result = gd._filter_files_by_size(files, "size=0")
        assert len(result) == 1

    def test_exception_returns_original(self):
        gd = _make_drive()
        files = [{"size": "not-a-number"}]
        # int() on "not-a-number" raises; should fall back to original list
        result = gd._filter_files_by_size(files, "size>0")
        assert result == files


# ============================================================================
# get_file_details
# ============================================================================

class TestGetFileDetails:
    @pytest.mark.asyncio
    async def test_missing_file_id_returns_error(self):
        gd = _make_drive()
        success, result = await gd.get_file_details(fileId=None)
        assert success is False
        assert "fileId is required" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_success(self):
        gd = _make_drive()
        gd.client.files_get = AsyncMock(
            return_value={"id": "abc", "name": "report.pdf", "mimeType": "application/pdf"}
        )
        success, result = await gd.get_file_details(fileId="abc")
        assert success is True
        data = json.loads(result)
        assert data["id"] == "abc"
        assert data["name"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_passes_optional_params_to_client(self):
        gd = _make_drive()
        gd.client.files_get = AsyncMock(return_value={"id": "f1"})
        await gd.get_file_details(
            fileId="f1", acknowledge_abuse=True, supports_all_drives=True
        )
        gd.client.files_get.assert_called_once_with(
            fileId="f1", acknowledgeAbuse=True, supportsAllDrives=True
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.files_get = AsyncMock(side_effect=RuntimeError("not found"))
        success, result = await gd.get_file_details(fileId="bad")
        assert success is False
        assert "not found" in json.loads(result)["error"]


# ============================================================================
# create_folder
# ============================================================================

class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_missing_folder_name_returns_error(self):
        gd = _make_drive()
        success, result = await gd.create_folder(folderName=None)
        assert success is False
        assert "folderName is required" in json.loads(result)["error"]

    @pytest.mark.asyncio
    async def test_success_no_parent(self):
        gd = _make_drive()
        gd.client.files_create = AsyncMock(
            return_value={
                "id": "folder-id",
                "name": "MyFolder",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [],
                "createdTime": "2024-01-01T00:00:00.000Z",
                "webViewLink": "https://drive.google.com/drive/folders/folder-id",
            }
        )
        success, result = await gd.create_folder(folderName="MyFolder")
        assert success is True
        data = json.loads(result)
        assert data["folder_id"] == "folder-id"
        assert data["folder_name"] == "MyFolder"

    @pytest.mark.asyncio
    async def test_success_with_parent(self):
        gd = _make_drive()
        gd.client.files_create = AsyncMock(
            return_value={"id": "child-id", "name": "Sub", "parents": ["parent-123"]}
        )
        success, result = await gd.create_folder(
            folderName="Sub", parent_folder_id="parent-123"
        )
        assert success is True
        # Verify the body includes the parent
        call_kwargs = gd.client.files_create.call_args[1]
        assert "body" in call_kwargs
        assert call_kwargs["body"]["parents"] == ["parent-123"]

    @pytest.mark.asyncio
    async def test_metadata_contains_correct_mime_type(self):
        gd = _make_drive()
        gd.client.files_create = AsyncMock(return_value={"id": "x"})
        await gd.create_folder(folderName="Folder")
        call_kwargs = gd.client.files_create.call_args[1]
        assert call_kwargs["body"]["mimeType"] == "application/vnd.google-apps.folder"

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.files_create = AsyncMock(side_effect=RuntimeError("quota exceeded"))
        success, result = await gd.create_folder(folderName="Fail")
        assert success is False
        assert "quota exceeded" in json.loads(result)["error"]


# ============================================================================
# search_files
# ============================================================================

class TestSearchFiles:
    @pytest.mark.asyncio
    async def test_simple_text_query_wrapped(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(
            return_value={"files": [{"id": "1", "name": "q1_report.pdf"}]}
        )
        success, result = await gd.search_files(query="report")
        assert success is True
        data = json.loads(result)
        assert data["formatted_query"] == 'name contains "report"'
        assert data["query"] == "report"

    @pytest.mark.asyncio
    async def test_structured_query_not_wrapped(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        raw_query = 'mimeType="application/pdf"'
        await gd.search_files(query=raw_query)
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["q"] == raw_query

    @pytest.mark.asyncio
    async def test_name_contains_query_not_re_wrapped(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        q = 'name contains "budget"'
        await gd.search_files(query=q)
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["q"] == q

    @pytest.mark.asyncio
    async def test_returns_total_results(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(
            return_value={"files": [{"id": "1"}, {"id": "2"}]}
        )
        success, result = await gd.search_files(query="doc")
        assert json.loads(result)["totalResults"] == 2

    @pytest.mark.asyncio
    async def test_page_size_and_order_by_forwarded(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        await gd.search_files(query="test", page_size=20, order_by="modifiedTime desc")
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["pageSize"] == 20
        assert call_kwargs["orderBy"] == "modifiedTime desc"

    @pytest.mark.asyncio
    async def test_fulltext_contains_query_not_wrapped(self):
        # Regression: same fix as TestGetFilesList — fullText contains must be
        # recognised as a structured operator and passed through unmodified.
        gd = _make_drive()
        gd.client.files_list = AsyncMock(return_value={"files": []})
        q = 'fullText contains "budget"'
        await gd.search_files(query=q)
        call_kwargs = gd.client.files_list.call_args[1]
        assert call_kwargs["q"] == q, (
            f"fullText contains query must be passed through unchanged; got: {call_kwargs['q']!r}"
        )

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.files_list = AsyncMock(side_effect=RuntimeError("api error"))
        success, result = await gd.search_files(query="fail")
        assert success is False
        assert "api error" in json.loads(result)["error"]


# ============================================================================
# get_drive_info
# ============================================================================

class TestGetDriveInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        gd = _make_drive()
        gd.client.about_get = AsyncMock(
            return_value={
                "user": {"displayName": "Test User"},
                "storageQuota": {"limit": "16106127360", "usage": "1234"},
                "maxUploadSize": "5368709120",
                "appInstalled": True,
                "exportFormats": {},
                "importFormats": {},
            }
        )
        success, result = await gd.get_drive_info()
        assert success is True
        data = json.loads(result)
        assert data["user"]["displayName"] == "Test User"
        assert data["storageQuota"]["limit"] == "16106127360"
        assert data["maxUploadSize"] == "5368709120"
        assert data["appInstalled"] is True

    @pytest.mark.asyncio
    async def test_missing_fields_default_to_empty(self):
        gd = _make_drive()
        gd.client.about_get = AsyncMock(return_value={})
        success, result = await gd.get_drive_info()
        assert success is True
        data = json.loads(result)
        assert data["user"] == {}
        assert data["storageQuota"] == {}
        assert data["maxUploadSize"] == ""
        assert data["appInstalled"] is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.about_get = AsyncMock(side_effect=RuntimeError("auth error"))
        success, result = await gd.get_drive_info()
        assert success is False
        assert "auth error" in json.loads(result)["error"]


# ============================================================================
# get_shared_drives
# ============================================================================

class TestGetSharedDrives:
    @pytest.mark.asyncio
    async def test_success(self):
        gd = _make_drive()
        gd.client.drives_list = AsyncMock(
            return_value={
                "drives": [{"id": "d1", "name": "Team Drive"}],
                "nextPageToken": None,
            }
        )
        success, result = await gd.get_shared_drives()
        assert success is True
        data = json.loads(result)
        assert data["totalResults"] == 1
        assert data["drives"][0]["id"] == "d1"

    @pytest.mark.asyncio
    async def test_page_size_and_query_forwarded(self):
        gd = _make_drive()
        gd.client.drives_list = AsyncMock(return_value={"drives": []})
        await gd.get_shared_drives(page_size=5, query="Engineering")
        call_kwargs = gd.client.drives_list.call_args[1]
        assert call_kwargs["pageSize"] == 5
        assert call_kwargs["q"] == "Engineering"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        gd = _make_drive()
        gd.client.drives_list = AsyncMock(return_value={"drives": []})
        success, result = await gd.get_shared_drives()
        assert success is True
        assert json.loads(result)["totalResults"] == 0

    @pytest.mark.asyncio
    async def test_next_page_token_included(self):
        gd = _make_drive()
        gd.client.drives_list = AsyncMock(
            return_value={"drives": [], "nextPageToken": "pageX"}
        )
        success, result = await gd.get_shared_drives()
        assert json.loads(result)["nextPageToken"] == "pageX"

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.drives_list = AsyncMock(side_effect=RuntimeError("timeout"))
        success, result = await gd.get_shared_drives()
        assert success is False
        assert "timeout" in json.loads(result)["error"]


# ============================================================================
# get_file_permissions
# ============================================================================

class TestGetFilePermissions:
    @pytest.mark.asyncio
    async def test_success(self):
        gd = _make_drive()
        gd.client.permissions_list = AsyncMock(
            return_value={
                "permissions": [
                    {"id": "p1", "role": "owner", "type": "user"}
                ],
                "nextPageToken": None,
            }
        )
        success, result = await gd.get_file_permissions(file_id="file123")
        assert success is True
        data = json.loads(result)
        assert data["file_id"] == "file123"
        assert len(data["permissions"]) == 1
        assert data["permissions"][0]["role"] == "owner"

    @pytest.mark.asyncio
    async def test_page_size_forwarded(self):
        gd = _make_drive()
        gd.client.permissions_list = AsyncMock(return_value={"permissions": []})
        await gd.get_file_permissions(file_id="f1", page_size=10)
        call_kwargs = gd.client.permissions_list.call_args[1]
        assert call_kwargs["fileId"] == "f1"
        assert call_kwargs["pageSize"] == 10

    @pytest.mark.asyncio
    async def test_empty_permissions(self):
        gd = _make_drive()
        gd.client.permissions_list = AsyncMock(return_value={"permissions": []})
        success, result = await gd.get_file_permissions(file_id="f2")
        assert success is True
        assert json.loads(result)["permissions"] == []

    @pytest.mark.asyncio
    async def test_next_page_token_included(self):
        gd = _make_drive()
        gd.client.permissions_list = AsyncMock(
            return_value={"permissions": [], "nextPageToken": "nextTok"}
        )
        success, result = await gd.get_file_permissions(file_id="f3")
        assert json.loads(result)["nextPageToken"] == "nextTok"

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = _make_drive()
        gd.client.permissions_list = AsyncMock(side_effect=RuntimeError("forbidden"))
        success, result = await gd.get_file_permissions(file_id="f4")
        assert success is False
        assert "forbidden" in json.loads(result)["error"]


# ============================================================================
# GetFileContentInput schema
# ============================================================================

class TestGetFileContentInput:
    def test_required_file_id(self):
        inp = GetFileContentInput(file_id="abc123")
        assert inp.file_id == "abc123"

    def test_missing_file_id_raises(self):
        with pytest.raises(Exception):
            GetFileContentInput()


# ============================================================================
# Module-level helpers: _is_structured_query / _is_size_query
# ============================================================================

class TestIsStructuredQuery:
    def test_name_contains_is_structured(self):
        assert _is_structured_query('name contains "report"') is True

    def test_fulltext_contains_is_structured(self):
        assert _is_structured_query('fullText contains "budget"') is True

    def test_mimetype_is_structured(self):
        assert _is_structured_query('mimeType="application/pdf"') is True

    def test_modifiedtime_is_structured(self):
        assert _is_structured_query("modifiedTime > '2024-01-01'") is True

    def test_trashed_is_structured(self):
        assert _is_structured_query("trashed = false") is True

    def test_plain_text_is_not_structured(self):
        assert _is_structured_query("quarterly report") is False

    def test_empty_string_is_not_structured(self):
        assert _is_structured_query("") is False

    def test_case_insensitive_detection(self):
        # operator list is lowercase; query is lowercased before check
        assert _is_structured_query('MimeType="image/png"') is True


class TestIsSizeQuery:
    def test_size_equal(self):
        assert _is_size_query("size=0") is True

    def test_size_gt(self):
        assert _is_size_query("size>1000") is True

    def test_size_gte(self):
        assert _is_size_query("size>=500") is True

    def test_size_lt(self):
        assert _is_size_query("size<500") is True

    def test_size_lte(self):
        assert _is_size_query("size<=500") is True

    def test_size_with_spaces(self):
        assert _is_size_query("size = 0") is True

    def test_non_size_query(self):
        assert _is_size_query('name contains "budget"') is False

    def test_empty_string(self):
        assert _is_size_query("") is False


# ============================================================================
# Module-level helper: _raw_drive_service
# ============================================================================

class TestRawDriveService:
    def test_returns_inner_when_has_files_attr(self):
        """If the inner client already exposes .files(), return it directly."""
        ds = MagicMock()
        ds.client = MagicMock(spec=["files"])
        ds.client.files = MagicMock()
        result = _raw_drive_service(ds)
        assert result is ds.client

    def test_calls_get_client_when_no_files_attr(self):
        """If the inner client has no .files attr, unwrap via get_client()."""
        raw_api = MagicMock(spec=["files"])
        wrapper = MagicMock(spec=[])          # no 'files' attr
        wrapper.get_client = MagicMock(return_value=raw_api)
        ds = MagicMock()
        ds.client = wrapper
        result = _raw_drive_service(ds)
        wrapper.get_client.assert_called_once()
        assert result is raw_api


# ============================================================================
# Module-level helper: _execute_media_download
# ============================================================================

class TestExecuteMediaDownload:
    def test_returns_bytes_from_chunked_download(self):
        payload = b"hello world"
        # Build a fake request whose MediaIoBaseDownload will yield the payload
        fake_request = MagicMock()

        with patch(
            "app.agents.actions.google.drive.drive.MediaIoBaseDownload"
        ) as MockDownloader:
            instance = MockDownloader.return_value
            # Simulate two chunks: first not done, second done
            instance.next_chunk.side_effect = [(MagicMock(), False), (MagicMock(), True)]
            # On the second call buffer should already contain bytes; we simulate
            # by writing into the BytesIO that is passed to the constructor.
            def capture_buffer(*args, **kwargs):
                buf = args[0]
                buf.write(payload)
                return instance
            MockDownloader.side_effect = capture_buffer

            result = _execute_media_download(fake_request)

        assert result == payload

    def test_calls_next_chunk_until_done(self):
        fake_request = MagicMock()
        with patch(
            "app.agents.actions.google.drive.drive.MediaIoBaseDownload"
        ) as MockDownloader:
            instance = MockDownloader.return_value
            instance.next_chunk.side_effect = [
                (None, False),
                (None, False),
                (None, True),
            ]
            MockDownloader.side_effect = lambda buf, req: instance
            _execute_media_download(fake_request)
        assert instance.next_chunk.call_count == 3


# ============================================================================
# get_file_content
# ============================================================================

class TestGetFileContent:
    # ------------------------------------------------------------------
    # helpers to build a minimal GoogleDrive mock
    # ------------------------------------------------------------------

    def _make_drive_with_state(self, state=None):
        gd = GoogleDrive.__new__(GoogleDrive)
        gd.client = AsyncMock()
        if state is None:
            state = {
                "config_service": MagicMock(),
                "org_id": "test-org",
                "tool_to_toolset_map": {"drive.get_file_content": "test-connector-id"},
            }
        gd.state = state
        return gd

    def _file_info(self, mime_type="text/plain", name="notes.txt", size="100", ext="txt"):
        return {
            "id": "file-id",
            "name": name,
            "mimeType": mime_type,
            "size": size,
            "fileExtension": ext,
        }

    # ------------------------------------------------------------------
    # folder guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_folder_returns_error(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="application/vnd.google-apps.folder")
        )
        success, result = await gd.get_file_content("folder-id")
        assert success is False
        assert "folder" in json.loads(result)["error"].lower()

    # ------------------------------------------------------------------
    # size guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_oversized_file_returns_error(self):
        gd = self._make_drive_with_state()
        big_size = str(51 * 1024 * 1024)  # 51 MB > 50 MB limit
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(size=big_size)
        )
        success, result = await gd.get_file_content("big-file-id")
        assert success is False
        data = json.loads(result)
        assert "too large" in data["error"].lower()

    # ------------------------------------------------------------------
    # Google Workspace document → export path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_google_doc_uses_export_path(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(
                mime_type="application/vnd.google-apps.document",
                name="doc.gdoc",
                ext="",
            )
        )
        raw_bytes = b"document content"
        gd._files_export_media_bytes = AsyncMock(return_value=raw_bytes)
        gd._files_get_media_bytes = AsyncMock()

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"text": "document content"}
            MockParser.return_value.parse = AsyncMock(return_value=(True, [mock_item]))
            success, result = await gd.get_file_content("doc-id")

        gd._files_export_media_bytes.assert_called_once_with("doc-id", "text/plain")
        gd._files_get_media_bytes.assert_not_called()
        assert success is True

    @pytest.mark.asyncio
    async def test_google_spreadsheet_exported_as_csv(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(
                mime_type="application/vnd.google-apps.spreadsheet",
                name="sheet.gsheet",
                ext="",
            )
        )
        gd._files_export_media_bytes = AsyncMock(return_value=b"a,b,c\n1,2,3")
        gd._files_get_media_bytes = AsyncMock()

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"text": "a,b,c"}
            MockParser.return_value.parse = AsyncMock(return_value=(True, [mock_item]))
            await gd.get_file_content("sheet-id")

        gd._files_export_media_bytes.assert_called_once_with("sheet-id", "text/csv")

    # ------------------------------------------------------------------
    # Regular binary file → get_media path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_regular_file_uses_get_media_path(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="application/pdf", name="doc.pdf", ext="pdf")
        )
        raw_bytes = b"%PDF-content"
        gd._files_get_media_bytes = AsyncMock(return_value=raw_bytes)
        gd._files_export_media_bytes = AsyncMock()

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"text": "pdf text"}
            MockParser.return_value.parse = AsyncMock(return_value=(True, [mock_item]))
            success, result = await gd.get_file_content("pdf-id")

        gd._files_get_media_bytes.assert_called_once_with("pdf-id", supports_all_drives=True)
        gd._files_export_media_bytes.assert_not_called()
        assert success is True

    # ------------------------------------------------------------------
    # Parser failure → False result
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parser_failure_returns_false(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="text/plain", ext="txt")
        )
        gd._files_get_media_bytes = AsyncMock(return_value=b"some text")

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"error": "parse failed"}
            MockParser.return_value.parse = AsyncMock(return_value=(False, [mock_item]))
            success, result = await gd.get_file_content("txt-id")

        assert success is False

    # ------------------------------------------------------------------
    # Non-bytes download response guard
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_bytes_response_returns_error(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="text/plain", ext="txt")
        )
        gd._files_get_media_bytes = AsyncMock(return_value="not bytes")  # string, not bytes

        success, result = await gd.get_file_content("txt-id")
        assert success is False
        assert "bytes" in json.loads(result)["error"].lower()

    # ------------------------------------------------------------------
    # Exception path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(side_effect=RuntimeError("network error"))
        success, result = await gd.get_file_content("any-id")
        assert success is False
        assert "network error" in json.loads(result)["error"]

    # ------------------------------------------------------------------
    # State fields forwarded to parser
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_state_fields_forwarded_to_parser(self):
        state = {
            "model_name": "gpt-4o",
            "model_key": "sk-test",
            "config_service": MagicMock(),
            "org_id": "test-org",
            "tool_to_toolset_map": {"drive.get_file_content": "test-connector-id"},
        }
        gd = self._make_drive_with_state(state=state)
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="text/plain", ext="txt")
        )
        gd._files_get_media_bytes = AsyncMock(return_value=b"text content")

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            mock_item = MagicMock()
            mock_item.model_dump.return_value = {"text": "text content"}
            instance = MockParser.return_value
            instance.parse = AsyncMock(return_value=(True, [mock_item]))
            await gd.get_file_content("txt-id")
            _, parse_args, _ = instance.parse.mock_calls[0]

        # parse(file_record, raw, model_name, model_key, config_service)
        assert parse_args[2] == "gpt-4o"
        assert parse_args[3] == "sk-test"
        assert parse_args[4] is state["config_service"]

    # ------------------------------------------------------------------
    # Fallback name when file name is empty
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_file_name_falls_back_to_document_ext(self):
        gd = self._make_drive_with_state()
        info = self._file_info(mime_type="text/plain", name="", ext="txt")
        gd.client.files_get = AsyncMock(return_value=info)
        gd._files_get_media_bytes = AsyncMock(return_value=b"data")

        with patch(
            "app.agents.actions.google.drive.drive.FileContentParser"
        ) as MockParser:
            with patch(
                "app.agents.actions.google.drive.drive.FileRecord"
            ) as MockFileRecord:
                mock_item = MagicMock()
                mock_item.model_dump.return_value = {}
                MockParser.return_value.parse = AsyncMock(return_value=(True, [mock_item]))
                MockFileRecord.return_value = MagicMock()
                await gd.get_file_content("txt-id")

            _, fr_kwargs = MockFileRecord.call_args
            assert fr_kwargs.get("record_name", "").startswith("document.")

    # ------------------------------------------------------------------
    # Missing config_service in state (line 808)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_missing_config_service_returns_error(self):
        state = {
            "org_id": "org",
            "tool_to_toolset_map": {"drive.get_file_content": "cid"},
            # config_service intentionally absent
        }
        gd = self._make_drive_with_state(state=state)
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="text/plain", ext="txt")
        )
        success, result = await gd.get_file_content("txt-id")
        assert success is False
        assert "config_service" in json.loads(result)["error"].lower()

    # ------------------------------------------------------------------
    # Unsupported Google Workspace MIME type (lines 816-817)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unsupported_workspace_mime_returns_error(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(
                mime_type="application/vnd.google-apps.form",
                name="survey.gform",
                size=None,
                ext="",
            )
        )
        success, result = await gd.get_file_content("form-id")
        assert success is False
        data = json.loads(result)
        assert "form" in data["error"].lower()
        assert "does not support text export" in data["error"]

    # ------------------------------------------------------------------
    # Workspace export exceeds 50 MB cap (line 830)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_workspace_export_too_large_returns_error(self):
        gd = self._make_drive_with_state()
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(
                mime_type="application/vnd.google-apps.spreadsheet",
                name="huge.gsheet",
                size=None,   # Google omits size for Workspace files
                ext="",
            )
        )
        oversized_bytes = b"x" * (51 * 1024 * 1024)  # 51 MB
        gd._files_export_media_bytes = AsyncMock(return_value=oversized_bytes)

        success, result = await gd.get_file_content("sheet-id")
        assert success is False
        assert "too large" in json.loads(result)["error"].lower()

    # ------------------------------------------------------------------
    # Missing connector_id in tool_to_toolset_map (line 851)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_missing_connector_id_returns_error(self):
        state = {
            "config_service": MagicMock(),
            "org_id": "org",
            "tool_to_toolset_map": {},  # key absent
        }
        gd = self._make_drive_with_state(state=state)
        gd.client.files_get = AsyncMock(
            return_value=self._file_info(mime_type="text/plain", ext="txt")
        )
        gd._files_get_media_bytes = AsyncMock(return_value=b"data")

        success, result = await gd.get_file_content("txt-id")
        assert success is False
        assert "drive.get_file_content" in json.loads(result)["error"]


# ============================================================================
# _files_get_media_bytes / _files_export_media_bytes
# ============================================================================

class TestMediaBytesHelpers:
    def _make_api(self):
        """Return a mock api object mimicking googleapiclient Resource."""
        api = MagicMock()
        return api

    @pytest.mark.asyncio
    async def test_files_get_media_bytes_basic(self):
        gd = _make_drive()
        api = self._make_api()
        fake_bytes = b"raw content"

        with patch(
            "app.agents.actions.google.drive.drive._raw_drive_service",
            return_value=api,
        ):
            with patch(
                "app.agents.actions.google.drive.drive._execute_media_download",
                return_value=fake_bytes,
            ) as mock_dl:
                with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_bytes)) as mock_to_thread:
                    result = await gd._files_get_media_bytes("fid")

        api.files().get_media.assert_called_once_with(fileId="fid")
        mock_to_thread.assert_called_once()
        assert result == fake_bytes

    @pytest.mark.asyncio
    async def test_files_get_media_bytes_with_optional_params(self):
        gd = _make_drive()
        api = self._make_api()

        with patch(
            "app.agents.actions.google.drive.drive._raw_drive_service",
            return_value=api,
        ):
            with patch("asyncio.to_thread", new=AsyncMock(return_value=b"")):
                await gd._files_get_media_bytes(
                    "fid", acknowledge_abuse=True, supports_all_drives=True
                )

        api.files().get_media.assert_called_once_with(
            fileId="fid", acknowledgeAbuse=True, supportsAllDrives=True
        )

    @pytest.mark.asyncio
    async def test_files_export_media_bytes(self):
        gd = _make_drive()
        api = self._make_api()
        fake_bytes = b"csv content"

        with patch(
            "app.agents.actions.google.drive.drive._raw_drive_service",
            return_value=api,
        ):
            with patch("asyncio.to_thread", new=AsyncMock(return_value=fake_bytes)) as mock_to_thread:
                result = await gd._files_export_media_bytes("fid", "text/csv")

        api.files().export_media.assert_called_once_with(fileId="fid", mimeType="text/csv")
        mock_to_thread.assert_called_once()
        assert result == fake_bytes
