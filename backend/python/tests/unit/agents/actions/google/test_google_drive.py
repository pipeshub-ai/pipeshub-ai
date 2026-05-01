"""
Unit tests for app.agents.actions.google.drive.drive

Tests the GoogleDrive agent toolset. All external dependencies
(GoogleClient, GoogleDriveDataSource) are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.google.drive.drive import (
    GoogleDrive,
    # Pydantic schemas
    CopyFileInput,
    CreateFolderInput,
    DeleteFileInput,
    DownloadFileInput,
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
