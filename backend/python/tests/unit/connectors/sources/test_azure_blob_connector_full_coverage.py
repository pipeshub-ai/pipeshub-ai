import base64
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.connectors.sources.azure_blob.connector import (
    AzureBlobConnector,
    AzureBlobDataSourceEntitiesProcessor,
    get_file_extension,
    get_folder_path_segments_from_blob_name,
    get_mimetype_for_azure_blob,
    get_parent_path_for_azure_blob,
    get_parent_path_from_blob_name,
    get_parent_weburl_for_azure_blob,
    parse_parent_external_id,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType, User


def _resp(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_tx(existing_record=None, revision_record=None, user=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_external_revision_id = AsyncMock(return_value=revision_record)
    tx.get_user_by_id = AsyncMock(return_value=user or {"email": "creator@test.com"})
    tx.get_user_by_user_id = AsyncMock(return_value=user or {"email": "creator@test.com"})
    tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    return tx


def _make_provider(existing_record=None, revision_record=None, user=None):
    tx = _make_tx(existing_record, revision_record, user)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx = tx
    return provider


def _make_existing_record(**overrides):
    rec = MagicMock()
    rec.id = overrides.get("id", "existing-id")
    rec.external_revision_id = overrides.get("external_revision_id", "old-rev")
    rec.external_record_id = overrides.get("external_record_id", "container/file.txt")
    rec.version = overrides.get("version", 1)
    rec.source_created_at = overrides.get("source_created_at", 1700000000000)
    rec.external_record_group_id = overrides.get("external_record_group_id", "container")
    return rec


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_blob.fc")


@pytest.fixture()
def mock_dep():
    proc = MagicMock(spec=AzureBlobDataSourceEntitiesProcessor)
    proc.org_id = "org-fc"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.reindex_existing_records = AsyncMock()
    proc.account_name = "teststorage"
    return proc


@pytest.fixture()
def mock_provider():
    return _make_provider()


@pytest.fixture()
def mock_config():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=abc123;EndpointSuffix=core.windows.net"
        },
        "scope": "TEAM",
        "created_by": "user-1",
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_dep, mock_provider, mock_config):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        c = AzureBlobConnector(
            logger=mock_logger,
            data_entities_processor=mock_dep,
            data_store_provider=mock_provider,
            config_service=mock_config,
            connector_id="az-fc-1",
            scope="personal",
            created_by="test-user-id",
        )
    c.account_name = "teststorage"
    c.scope = ConnectorScope.TEAM.value
    return c


class TestHelperGetFileExtension:
    def test_no_dot(self):
        assert get_file_extension("README") is None

    def test_single_dot(self):
        assert get_file_extension("file.pdf") == "pdf"

    def test_uppercase(self):
        assert get_file_extension("IMG.PNG") == "png"

    def test_dot_only(self):
        assert get_file_extension(".") == ""

    def test_empty_after_dot(self):
        assert get_file_extension("file.") == ""


class TestHelperGetParentPath:
    def test_empty_string(self):
        assert get_parent_path_from_blob_name("") is None

    def test_just_filename(self):
        assert get_parent_path_from_blob_name("file.txt") is None

    def test_nested(self):
        assert get_parent_path_from_blob_name("a/b/file.txt") == "a/b"

    def test_trailing_and_leading_slash(self):
        assert get_parent_path_from_blob_name("/a/b/") == "a"


class TestHelperFolderPathSegments:
    def test_empty(self):
        assert get_folder_path_segments_from_blob_name("") == []

    def test_no_slash(self):
        assert get_folder_path_segments_from_blob_name("file.txt") == []

    def test_trailing_slash_folder(self):
        assert get_folder_path_segments_from_blob_name("a/b/") == ["a"]

    def test_normal(self):
        assert get_folder_path_segments_from_blob_name("a/b/c.txt") == ["a", "a/b"]


class TestHelperMimetype:
    def test_folder(self):
        assert get_mimetype_for_azure_blob("f/", is_folder=True) == MimeTypes.FOLDER.value

    def test_known_type(self):
        result = get_mimetype_for_azure_blob("doc.pdf")
        assert result != MimeTypes.FOLDER.value
        assert result != MimeTypes.BIN.value

    def test_unknown_type(self):
        assert get_mimetype_for_azure_blob("data.zzz999") == MimeTypes.BIN.value

    def test_no_extension(self):
        assert get_mimetype_for_azure_blob("Makefile") == MimeTypes.BIN.value


class TestHelperParseParentExternalId:
    def test_container_only(self):
        c, p = parse_parent_external_id("mycontainer")
        assert c == "mycontainer"
        assert p is None

    def test_with_path_no_trailing(self):
        c, p = parse_parent_external_id("c1/folder")
        assert c == "c1"
        assert p == "folder/"

    def test_with_path_trailing(self):
        c, p = parse_parent_external_id("c1/folder/")
        assert c == "c1"
        assert p == "folder/"

    def test_nested_path(self):
        c, p = parse_parent_external_id("c1/a/b/c")
        assert c == "c1"
        assert p == "a/b/c/"


class TestHelperGetParentWeburlForAzureBlob:
    def test_container_only(self):
        url = get_parent_weburl_for_azure_blob("container", "acc")
        assert url == "https://acc.blob.core.windows.net/container"

    def test_with_path(self):
        url = get_parent_weburl_for_azure_blob("c/dir/sub", "acc")
        assert "dir/sub/" in url


class TestHelperGetParentPathForAzureBlob:
    def test_no_slash(self):
        assert get_parent_path_for_azure_blob("container") is None

    def test_with_path(self):
        assert get_parent_path_for_azure_blob("c/dir") == "dir/"

    def test_empty_dir(self):
        result = get_parent_path_for_azure_blob("c/")
        assert result is not None


class TestEntitiesProcessor:
    def test_create_placeholder_file_record(self, mock_logger, mock_provider, mock_config):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_provider,
            config_service=mock_config,
            account_name="myaccount",
        )
        assert proc.account_name == "myaccount"

    def test_create_placeholder_parent_record(self, mock_logger, mock_provider, mock_config):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_provider,
            config_service=mock_config,
            account_name="myaccount",
        )
        parent = FileRecord(
            id="p1",
            record_name="placeholder",
            record_type=RecordType.FILE,
            external_record_id="c/folder",
            version=0,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.AZURE_BLOB,
            connector_id="conn-1",
            is_file=False,
        )
        with patch.object(
            type(proc).__bases__[0],
            "_create_placeholder_parent_record",
            return_value=parent,
        ):
            result = proc._create_placeholder_parent_record("c/folder", RecordType.FILE, MagicMock())
        assert result.weburl is not None
        assert result.is_internal is True
        assert result.hide_weburl is True


class TestGetAppUsers:
    def test_skips_no_email(self, connector):
        users = [User(email="", full_name="X")]
        assert connector.get_app_users(users) == []

    def test_uses_source_user_id(self, connector):
        users = [User(email="a@t.com", source_user_id="src-1")]
        result = connector.get_app_users(users)
        assert result[0].source_user_id == "src-1"

    def test_fallback_to_id(self, connector):
        users = [User(email="a@t.com", id="uid-1")]
        result = connector.get_app_users(users)
        assert result[0].source_user_id == "uid-1"

    def test_fallback_to_email(self, connector):
        users = [User(email="a@t.com", id="")]
        result = connector.get_app_users(users)
        assert result[0].source_user_id == "a@t.com"

    def test_inactive_user(self, connector):
        users = [User(email="a@t.com", is_active=False)]
        result = connector.get_app_users(users)
        assert result[0].is_active is False

    def test_none_is_active_defaults_true(self, connector):
        users = [User(email="a@t.com", is_active=None)]
        result = connector.get_app_users(users)
        assert result[0].is_active is True

    def test_full_name_fallback(self, connector):
        users = [User(email="a@t.com", full_name=None)]
        result = connector.get_app_users(users)
        assert result[0].full_name == "a@t.com"


class TestInit:
    @pytest.mark.asyncio
    async def test_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_no_connection_string(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_success(self, mock_filters, mock_client_cls, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "teststorage"
        mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=key;EndpointSuffix=core.windows.net"},
            "scope": "TEAM",
            "created_by": "user-1",
        })
        assert await connector.init() is True

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_personal_scope_fetches_creator(self, mock_filters, mock_client_cls, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "acc"
        mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)
        connector.scope = ConnectorScope.PERSONAL.value
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=acc;AccountKey=k;EndpointSuffix=core.windows.net"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        assert await connector.init() is True
        assert connector.creator_email == "creator@test.com"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_account_name_extraction_failure(self, mock_filters, mock_client_cls, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        mock_client = MagicMock()
        mock_client.get_account_name.side_effect = Exception("parse error")
        mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=acc;AccountKey=k;EndpointSuffix=core.windows.net"},
            "scope": "TEAM",
        })
        assert await connector.init() is True
        assert connector.account_name is None

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient")
    async def test_client_build_failure(self, mock_client_cls, connector):
        mock_client_cls.build_from_services = AsyncMock(side_effect=Exception("connection refused"))
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=acc;AccountKey=k;EndpointSuffix=core.windows.net"},
            "scope": "TEAM",
        })
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_creator_lookup_exception(self, mock_filters, mock_client_cls, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "acc"
        mock_client_cls.build_from_services = AsyncMock(return_value=mock_client)
        bad_provider = MagicMock()

        @asynccontextmanager
        async def _fail_tx():
            raise Exception("db error")
            yield  # noqa: unreachable

        bad_provider.transaction = _fail_tx
        connector.data_store_provider = bad_provider
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=acc;AccountKey=k;EndpointSuffix=core.windows.net"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        assert await connector.init() is True
        assert connector.creator_email is None


class TestGetDateFilters:
    def test_no_filters(self, connector):
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_with_modified_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00", "2025-12-31T23:59:59")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == "modified" else None)
        m_after, m_before, c_after, c_before = connector._get_date_filters()
        assert m_after is not None
        assert m_before is not None
        assert c_after is None

    def test_with_created_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2025-06-01T00:00:00", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == "created" else None)
        _, _, c_after, c_before = connector._get_date_filters()
        assert c_after is not None
        assert c_before is None


class TestPassDateFiltersExtended:
    def test_modified_before_fails(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
        }
        past_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, None, past_ms, None, None) is False

    def test_creation_before_fails(self, connector):
        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        blob = {
            "name": "file.txt",
            "last_modified": now,
            "creation_time": now,
        }
        past_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, None, None, None, past_ms) is False

    def test_creation_time_string_z(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "2025-03-01T00:00:00Z",
        }
        past = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        future = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, None, None, past, future) is True

    def test_creation_after_ms_fails_string(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "2025-01-01T00:00:00+00:00",
        }
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, None, None, future_ms, None) is False


class TestPassExtensionFilterExtended:
    def test_not_in_operator_match(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_extension_filter("file.exe") is False

    def test_not_in_operator_no_match(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_extension_filter("file.pdf") is True

    def test_unknown_operator(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value="UNKNOWN_OP")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_extension_filter("file.pdf") is True

    def test_operator_without_value_attr(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        op = MagicMock()
        op.value = FilterOperator.IN
        del op.spec
        mock_filter.get_operator.return_value = op
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_extension_filter("file.pdf") is True

    def test_extensions_with_dots(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = [".pdf", ".TXT"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        assert connector._pass_extension_filter("file.pdf") is True
        assert connector._pass_extension_filter("file.txt") is True


class TestExtractContainerNames:
    def test_none_name_in_dict(self, connector):
        result = connector._extract_container_names([{"name": None}])
        assert result == []

    def test_object_without_name(self, connector):
        obj = MagicMock(spec=[])
        result = connector._extract_container_names([obj])
        assert result == []


class TestBlobPropertiesToDictExtended:
    def test_object_no_content_settings_attr(self, connector):
        blob = MagicMock()
        blob.name = "test.bin"
        blob.last_modified = None
        blob.creation_time = None
        blob.etag = ""
        blob.size = 0
        del blob.content_settings
        result = connector._blob_properties_to_dict(blob)
        assert result["content_type"] is None
        assert result["content_md5"] is None


class TestGetAzureBlobRevisionIdExtended:
    def test_empty_etag(self, connector):
        assert connector._get_azure_blob_revision_id({"etag": ""}) == ""

    def test_none_md5_none_etag(self, connector):
        assert connector._get_azure_blob_revision_id({"content_md5": None, "etag": None}) == ""


class TestProcessAzureBlobExtended:
    @pytest.mark.asyncio
    async def test_leading_slash_only(self, connector):
        record, perms = await connector._process_azure_blob({"name": "/"}, "c")
        assert record is None

    @pytest.mark.asyncio
    async def test_no_timestamps(self, connector):
        blob = {"name": "file.txt", "size": 10, "etag": '"e1"'}
        record, perms = await connector._process_azure_blob(blob, "c")
        assert record is not None
        assert record.source_updated_at is not None

    @pytest.mark.asyncio
    async def test_invalid_string_last_modified(self, connector):
        blob = {"name": "file.txt", "last_modified": "bad-date", "size": 10}
        record, perms = await connector._process_azure_blob(blob, "c")
        assert record is not None

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, connector):
        blob = {"name": "file.txt", "last_modified": 12345, "size": 10}
        record, perms = await connector._process_azure_blob(blob, "c")
        assert record is not None

    @pytest.mark.asyncio
    async def test_string_creation_time_invalid(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "creation_time": "not-a-date",
            "size": 100,
            "etag": '"e"',
        }
        record, perms = await connector._process_azure_blob(blob, "c")
        assert record is not None

    @pytest.mark.asyncio
    async def test_non_datetime_creation_time(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "creation_time": 99999,
            "size": 100,
        }
        record, perms = await connector._process_azure_blob(blob, "c")
        assert record is not None

    @pytest.mark.asyncio
    async def test_content_md5_bytes(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_md5": b"\xab\xcd",
            "size": 100,
            "etag": '"e"',
        }
        record, _ = await connector._process_azure_blob(blob, "c")
        assert record is not None
        expected = base64.b64encode(b"\xab\xcd").decode("utf-8")
        assert record.md5_hash == expected

    @pytest.mark.asyncio
    async def test_content_md5_non_string_type(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_md5": 12345,
            "size": 100,
        }
        record, _ = await connector._process_azure_blob(blob, "c")
        assert record is not None
        assert record.md5_hash == "12345"

    @pytest.mark.asyncio
    async def test_indexing_filters_disabled(self, connector):
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        connector.indexing_filters.__bool__ = MagicMock(return_value=True)
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 100,
        }
        record, _ = await connector._process_azure_blob(blob, "c")
        assert record is not None
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_existing_no_revision(self, connector):
        existing = _make_existing_record(external_revision_id=None)
        connector.data_store_provider = _make_provider(existing_record=existing)
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 100,
        }
        record, _ = await connector._process_azure_blob(blob, "container")
        assert record is not None

    @pytest.mark.asyncio
    async def test_existing_no_current_revision(self, connector):
        existing = _make_existing_record(external_revision_id="old")
        connector.data_store_provider = _make_provider(existing_record=existing)
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 100,
        }
        record, _ = await connector._process_azure_blob(blob, "container")
        assert record is not None

    @pytest.mark.asyncio
    async def test_new_no_revision(self, connector):
        blob = {"name": "file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 10}
        record, _ = await connector._process_azure_blob(blob, "c")
        assert record is not None

    @pytest.mark.asyncio
    async def test_content_type_from_blob(self, connector):
        blob = {
            "name": "file.bin",
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_type": "application/octet-stream",
            "size": 100,
        }
        record, _ = await connector._process_azure_blob(blob, "c")
        assert record is not None
        assert record.mime_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = MagicMock(side_effect=Exception("db fail"))
        record, perms = await connector._process_azure_blob({"name": "f.txt"}, "c")
        assert record is None
        assert perms == []


class TestCreateAzureBlobPermissions:
    @pytest.mark.asyncio
    async def test_team_scope(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        perms = await connector._create_azure_blob_permissions("c", "file.txt")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_with_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = "user@test.com"
        connector.created_by = "uid-1"
        perms = await connector._create_azure_blob_permissions("c", "file.txt")
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_personal_no_creator_fallback(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = None
        perms = await connector._create_azure_blob_permissions("c", "file.txt")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_exception_fallback(self, connector):
        connector.scope = None
        connector.creator_email = "u@t.com"
        original_scope = connector.scope

        async def _bad_perms(c, b):
            raise Exception("perm err")

        connector._create_azure_blob_permissions = AsyncMock(side_effect=_bad_perms)
        connector._create_azure_blob_permissions.reset_mock()
        connector.scope = original_scope

        connector2 = connector
        connector2.scope = "INVALID"
        connector2.creator_email = None
        perms = await AzureBlobConnector._create_azure_blob_permissions(connector2, "c", "file.txt")
        assert len(perms) == 1


class TestSyncContainer:
    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_list_blobs_fails(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(False, error="denied"))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_no_blobs(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=None))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_processes_blobs(self, connector):
        blob1 = {"name": "file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 100, "etag": '"e"'}
        blob2 = {"name": "other.pdf", "last_modified": datetime(2025, 2, 1, tzinfo=timezone.utc), "size": 200, "etag": '"f"'}

        async def _mock_iter():
            for b in [blob1, blob2]:
                yield b

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_container("c")
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_with_sync_point(self, connector):
        async def _mock_iter():
            yield {"name": "f.txt", "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc), "size": 10, "etag": '"e"'}

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_blob_processing_error_continues(self, connector):
        call_count = 0

        async def _mock_iter():
            nonlocal call_count
            for i in range(3):
                call_count += 1
                yield {"name": f"file{i}.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 10}

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()

        original = connector._process_azure_blob
        call_idx = 0

        async def _process_with_error(blob, container):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 2:
                raise Exception("processing error")
            return await original(blob, container)

        connector._process_azure_blob = _process_with_error
        await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_string_last_modified_max_timestamp(self, connector):
        async def _mock_iter():
            yield {
                "name": "f.txt",
                "last_modified": "2025-06-01T00:00:00Z",
                "size": 10,
                "etag": '"e"',
            }

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_container("c")
        connector.record_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_batch_size_trigger(self, connector):
        connector.batch_size = 2

        async def _mock_iter():
            for i in range(5):
                yield {"name": f"f{i}.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 10}

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_container("c")
        assert connector.data_entities_processor.on_new_records.await_count >= 2

    @pytest.mark.asyncio
    async def test_extension_filter_skips_blobs(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        from app.connectors.core.registry.filters import SyncFilterKey
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == SyncFilterKey.FILE_EXTENSIONS else None)
        connector.sync_filters.__bool__ = MagicMock(return_value=True)

        async def _mock_iter():
            yield {"name": "file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 10}
            yield {"name": "doc.pdf", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 20}

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_container("c")

    @pytest.mark.asyncio
    async def test_folder_hierarchy_created(self, connector):
        async def _mock_iter():
            yield {"name": "a/b/file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 10}

        connector.data_source = MagicMock()
        connector.data_source.list_blobs = AsyncMock(return_value=_resp(True, data=_mock_iter()))
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await connector._sync_container("c")
        assert connector.data_entities_processor.on_new_records.await_count >= 2


class TestRunSyncExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_selected_containers_filter(self, mock_filters, connector):
        container_filter = MagicMock()
        container_filter.value = ["filtered-c1", "filtered-c2"]
        sync_filters = MagicMock(spec=FilterCollection)
        sync_filters.get.return_value = container_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector._create_record_groups_for_containers = AsyncMock()
        connector._sync_container = AsyncMock()
        await connector.run_sync()
        assert connector._sync_container.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_empty_container_names_skipped(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(
            return_value=_resp(True, [{"name": "c1"}, {"name": ""}, {"name": None}])
        )
        connector._create_record_groups_for_containers = AsyncMock()
        connector._sync_container = AsyncMock()
        await connector.run_sync()
        connector._sync_container.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_valid_containers(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(
            return_value=_resp(True, [{"name": None}])
        )
        connector._create_record_groups_for_containers = AsyncMock()
        await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_exception_propagates(self, mock_filters, connector):
        mock_filters.side_effect = Exception("filter error")
        connector.data_source = MagicMock()
        with pytest.raises(Exception, match="filter error"):
            await connector.run_sync()


class TestRunIncrementalSyncExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_selected_containers(self, mock_filters, connector):
        container_filter = MagicMock()
        container_filter.value = ["c1"]
        sync_filters = MagicMock(spec=FilterCollection)
        sync_filters.get.return_value = container_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector._sync_container = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_container.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_containers_no_data(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(return_value=_resp(False))
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_container_sync_error_continues(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.container_name = "c1"
        connector._sync_container = AsyncMock(side_effect=Exception("sync err"))
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_empty_container_skipped(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(
            return_value=_resp(True, [{"name": "c1"}, {"name": ""}])
        )
        connector._sync_container = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_container.assert_awaited_once_with("c1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_exception_propagates(self, mock_filters, connector):
        mock_filters.side_effect = Exception("filter err")
        connector.data_source = MagicMock()
        with pytest.raises(Exception, match="filter err"):
            await connector.run_incremental_sync()


class TestCheckAndFetchUpdatedRecordExtended:
    @pytest.mark.asyncio
    async def test_missing_external_record_id(self, connector):
        record = MagicMock(id="r1", external_record_group_id="c", external_record_id=None)
        connector.data_source = MagicMock()
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_blob_name(self, connector):
        record = MagicMock(id="r1", external_record_group_id="c", external_record_id="c/")
        connector.data_source = MagicMock()
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_blob_metadata(self, connector):
        record = _make_existing_record()
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, data=None))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_etag_changed(self, connector):
        record = _make_existing_record(external_revision_id="old_etag")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new_etag"',
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "content_type": "text/plain",
            "size": 500,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None
        updated_record, perms = result
        assert updated_record.version == 2

    @pytest.mark.asyncio
    async def test_string_last_modified(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": "2025-06-01T00:00:00Z",
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_invalid_string_last_modified(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": "not-a-date",
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": 99999,
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_content_md5_bytes(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_md5": b"\x01\x02",
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None
        updated_record, _ = result
        assert updated_record.md5_hash == base64.b64encode(b"\x01\x02").decode("utf-8")

    @pytest.mark.asyncio
    async def test_content_md5_non_string(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "content_md5": 42,
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None
        updated_record, _ = result
        assert updated_record.md5_hash == "42"

    @pytest.mark.asyncio
    async def test_indexing_filters_disabled(self, connector):
        record = _make_existing_record(external_revision_id="old")
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 100,
        }))
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False
        connector.indexing_filters.__bool__ = MagicMock(return_value=True)
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None
        updated_record, _ = result
        assert updated_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_folder_blob(self, connector):
        record = _make_existing_record(
            external_record_id="c/folder/",
            external_revision_id="old",
            external_record_group_id="c",
        )
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 0,
        }))
        connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, connector):
        record = _make_existing_record()
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(side_effect=Exception("err"))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_container_prefix(self, connector):
        record = _make_existing_record(
            external_record_id="/path/file.txt",
            external_record_group_id="container",
            external_revision_id="old",
        )
        connector.data_source = MagicMock()
        connector.data_source.get_blob_properties = AsyncMock(return_value=_resp(True, {
            "etag": '"new"',
            "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "size": 100,
        }))
        result = await connector._check_and_fetch_updated_record("org", record)
        assert result is not None


class TestReindexRecordsExtended:
    @pytest.mark.asyncio
    async def test_record_check_exception_continues(self, connector):
        connector.data_source = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("check err"))
        await connector.reindex_records([MagicMock(id="r1")])

    @pytest.mark.asyncio
    async def test_exception_propagates(self, connector):
        connector.data_source = MagicMock()
        connector.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("db err"))
        connector._check_and_fetch_updated_record = AsyncMock(return_value=(MagicMock(), []))
        with pytest.raises(Exception, match="db err"):
            await connector.reindex_records([MagicMock(id="r1")])


class TestGetSignedUrlExtended:
    @pytest.mark.asyncio
    async def test_url_encoded_blob_name(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_resp(True, {"sas_url": "https://sas.url"})
        )
        record = MagicMock(
            id="r1",
            external_record_group_id="container",
            external_record_id="container/path%20with%20spaces/file.txt",
            record_name="file.txt",
        )
        result = await connector.get_signed_url(record)
        assert result == "https://sas.url"


class TestStreamRecordExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.create_stream_record_response")
    @patch("app.connectors.sources.azure_blob.connector.stream_content")
    async def test_success(self, mock_stream, mock_create, connector):
        mock_create.return_value = MagicMock()
        connector.get_signed_url = AsyncMock(return_value="https://sas.url")
        record = MagicMock(spec=FileRecord)
        record.is_file = True
        record.id = "r1"
        record.record_name = "file.txt"
        record.mime_type = "text/plain"
        result = await connector.stream_record(record)
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_mime_type(self, connector):
        connector.get_signed_url = AsyncMock(return_value="https://sas.url")
        record = MagicMock(spec=FileRecord)
        record.is_file = True
        record.id = "r1"
        record.record_name = "file.bin"
        record.mime_type = None
        with patch("app.connectors.sources.azure_blob.connector.create_stream_record_response") as mock_create:
            with patch("app.connectors.sources.azure_blob.connector.stream_content"):
                mock_create.return_value = MagicMock()
                await connector.stream_record(record)
                call_kwargs = mock_create.call_args
                assert call_kwargs[1]["mime_type"] == "application/octet-stream"


class TestGetContainerOptionsExtended:
    @pytest.mark.asyncio
    async def test_last_page(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(
            return_value=_resp(True, [{"name": "c1"}, {"name": "c2"}])
        )
        result = await connector._get_container_options(page=2, limit=5, search=None)
        assert result.success is True
        assert len(result.options) == 0
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_containers = AsyncMock(
            return_value=_resp(True, [{"name": "Prod-Data"}, {"name": "dev-logs"}])
        )
        result = await connector._get_container_options(page=1, limit=20, search="PROD")
        assert len(result.options) == 1
        assert result.options[0].id == "Prod-Data"


class TestCreateConnector:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSourceEntitiesProcessor")
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobApp")
    async def test_with_connection_string(self, mock_app, mock_proc_cls, mock_config, mock_provider, mock_logger):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_provider,
            config_service=mock_config,
            connector_id="az-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureBlobConnector)

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSourceEntitiesProcessor")
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobApp")
    async def test_no_config(self, mock_app, mock_proc_cls, mock_provider, mock_logger):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value=None)
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_provider,
            config_service=config_svc,
            connector_id="az-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureBlobConnector)

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSourceEntitiesProcessor")
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobApp")
    async def test_config_exception(self, mock_app, mock_proc_cls, mock_provider, mock_logger):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=Exception("config err"))
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger,
            data_store_provider=mock_provider,
            config_service=config_svc,
            connector_id="az-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureBlobConnector)


class TestEnsureParentFoldersExtended:
    @pytest.mark.asyncio
    async def test_segments_with_parent_refs(self, connector):
        connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await connector._ensure_parent_folders_exist("c", ["a", "a/b"])
        assert connector.data_entities_processor.on_new_records.await_count == 2
        first_call = connector.data_entities_processor.on_new_records.call_args_list[0]
        first_record = first_call[0][0][0][0]
        assert first_record.parent_external_record_id is None
        second_call = connector.data_entities_processor.on_new_records.call_args_list[1]
        second_record = second_call[0][0][0][0]
        assert second_record.parent_external_record_id == "c/a"


class TestGenerateWebUrl:
    def test_url_encoding(self, connector):
        url = connector._generate_web_url("c", "path with spaces/file.txt")
        assert "path%20with%20spaces" in url

    def test_parent_url_no_account(self, connector):
        connector.account_name = None
        url = connector._generate_parent_web_url("c/dir")
        assert ".blob.core.windows.net" in url
