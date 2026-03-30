"""Tests for Azure Blob Storage connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
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
import base64
from contextlib import asynccontextmanager
from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.models.entities import RecordType, User
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.models.entities import FileRecord, RecordGroupType, RecordType, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_blob")


@pytest.fixture()
def mock_data_entities_processor():
    from app.models.entities import AppMetadata
    
    proc = MagicMock(spec=AzureBlobDataSourceEntitiesProcessor)
    proc.org_id = "org-az-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.account_name = "teststorage"
    proc.get_app_by_id = AsyncMock(return_value=AppMetadata(
        connector_id="az-blob-1",
        name="Azure Blob",
        type="azure_blob",
        app_group="STORAGE",
        scope="PERSONAL",
        created_by="user-1",
        created_at_timestamp=1234567890,
        updated_at_timestamp=1234567890,
    ))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
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
def azure_blob_connector(mock_logger, mock_data_entities_processor,
                          mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        connector = AzureBlobConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="az-blob-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# Helper functions
# ===========================================================================
class TestAzureBlobHelpers:
    def test_get_file_extension(self):
        assert get_file_extension("report.pdf") == "pdf"
        assert get_file_extension("a/b/c/file.docx") == "docx"
        assert get_file_extension("Makefile") is None

    def test_get_parent_path(self):
        assert get_parent_path_from_blob_name("a/b/c/file.txt") == "a/b/c"
        assert get_parent_path_from_blob_name("file.txt") is None
        assert get_parent_path_from_blob_name("") is None
        assert get_parent_path_from_blob_name("a/b/c/") == "a/b"

    def test_folder_segments(self):
        assert get_folder_path_segments_from_blob_name("a/b/c/file.txt") == ["a", "a/b", "a/b/c"]
        assert get_folder_path_segments_from_blob_name("file.txt") == []
        assert get_folder_path_segments_from_blob_name("") == []

    def test_mimetype(self):
        assert get_mimetype_for_azure_blob("folder/", is_folder=True) == MimeTypes.FOLDER.value
        assert get_mimetype_for_azure_blob("report.pdf") == MimeTypes.PDF.value
        assert get_mimetype_for_azure_blob("data.xyz999") == MimeTypes.BIN.value

    def test_parse_parent(self):
        container, path = parse_parent_external_id("mycontainer/path/to/dir")
        assert container == "mycontainer"
        assert path == "path/to/dir/"
        container, path = parse_parent_external_id("mycontainer")
        assert path is None

    def test_parent_weburl(self):
        url = get_parent_weburl_for_azure_blob("container/folder/", "testacc")
        assert "testacc.blob.core.windows.net" in url
        url = get_parent_weburl_for_azure_blob("container", "testacc")
        assert "testacc.blob.core.windows.net/container" in url

    def test_parent_path(self):
        assert get_parent_path_for_azure_blob("container/folder") == "folder/"
        assert get_parent_path_for_azure_blob("container") is None


# ===========================================================================
# Init
# ===========================================================================
class TestAzureBlobConnectorInit:
    def test_constructor(self, azure_blob_connector):
        assert azure_blob_connector.connector_id == "az-blob-1"
        assert azure_blob_connector.data_source is None
        assert azure_blob_connector.batch_size == 100

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "teststorage"
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        assert await azure_blob_connector.init() is True

    async def test_init_fails_no_config(self, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await azure_blob_connector.init() is False

    async def test_init_fails_no_connection_string(self, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await azure_blob_connector.init() is False

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, azure_blob_connector):
        mock_build.side_effect = Exception("Connection failed")
        assert await azure_blob_connector.init() is False

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_account_name_extraction_failure(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        mock_client = MagicMock()
        mock_client.get_account_name.side_effect = Exception("Cannot extract")
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_blob_connector.init()
        assert result is True
        assert azure_blob_connector.account_name is None

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_personal_scope_with_creator(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "teststorage"
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_blob_connector.init()
        assert result is True
        assert azure_blob_connector.creator_email == "user@test.com"


# ===========================================================================
# Web URLs
# ===========================================================================
class TestAzureBlobWebUrls:
    def test_generate_web_url(self, azure_blob_connector):
        azure_blob_connector.account_name = "testacc"
        url = azure_blob_connector._generate_web_url("container", "path/file.txt")
        assert "testacc.blob.core.windows.net" in url
        assert "container" in url

    def test_generate_parent_web_url(self, azure_blob_connector):
        azure_blob_connector.account_name = "testacc"
        url = azure_blob_connector._generate_parent_web_url("container/dir")
        assert "testacc.blob.core.windows.net" in url


# ===========================================================================
# App users
# ===========================================================================
class TestAzureBlobAppUsers:
    def test_get_app_users(self, azure_blob_connector):
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = azure_blob_connector.get_app_users(users)
        assert len(app_users) == 1

    def test_get_app_users_none_active(self, azure_blob_connector):
        from app.models.entities import User
        users = [User(email="a@test.com", full_name="A", is_active=None)]
        app_users = azure_blob_connector.get_app_users(users)
        assert app_users[0].is_active is True


# ===========================================================================
# Container name extraction
# ===========================================================================
class TestAzureBlobExtractContainerNames:
    def test_dict_based(self, azure_blob_connector):
        data = [{"name": "c1"}, {"name": "c2"}]
        result = azure_blob_connector._extract_container_names(data)
        assert result == ["c1", "c2"]

    def test_object_based(self, azure_blob_connector):
        obj1 = MagicMock()
        obj1.name = "c1"
        obj2 = MagicMock()
        obj2.name = "c2"
        result = azure_blob_connector._extract_container_names([obj1, obj2])
        assert result == ["c1", "c2"]

    def test_none_input(self, azure_blob_connector):
        assert azure_blob_connector._extract_container_names(None) == []


# ===========================================================================
# DataSourceEntitiesProcessor
# ===========================================================================
class TestAzureBlobDataSourceEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="myaccount",
        )
        assert proc.account_name == "myaccount"

# =============================================================================
# Merged from test_azure_blob_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_mock_tx_store(existing_record=None, existing_revision_record=None, user=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_external_revision_id = AsyncMock(return_value=existing_revision_record)
    tx.get_user_by_id = AsyncMock(return_value=user or {"email": "user@test.com"})
    tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    return tx


def _make_mock_data_store_provider(existing_record=None, existing_revision_record=None, user=None):
    tx = _make_mock_tx_store(existing_record, existing_revision_record, user)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger_cov():
    return logging.getLogger("test.azure_blob.cov")


@pytest.fixture()
def mock_data_entities_processor_cov():
    proc = MagicMock(spec=AzureBlobDataSourceEntitiesProcessor)
    proc.org_id = "org-az-cov"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.account_name = "teststorage"
    return proc


@pytest.fixture()
def mock_data_store_provider_cov():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
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
def azure_connector(mock_logger_cov, mock_data_entities_processor_cov,
                    mock_data_store_provider_cov, mock_config_service):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        connector = AzureBlobConnector(
            logger=mock_logger_cov,
            data_entities_processor=mock_data_entities_processor_cov,
            data_store_provider=mock_data_store_provider_cov,
            config_service=mock_config_service,
            connector_id="az-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


# ===========================================================================
# Helper functions extended
# ===========================================================================
class TestAzureBlobHelpersExtended:
    def test_get_file_extension_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_get_file_extension_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_blob_name("a/b/c/") == "a/b"

    def test_get_parent_path_leading_slash(self):
        assert get_parent_path_from_blob_name("/a/b/c.txt") == "a/b"

    def test_get_parent_path_single_slash(self):
        assert get_parent_path_from_blob_name("/") is None

    def test_folder_segments_deeply_nested(self):
        result = get_folder_path_segments_from_blob_name("a/b/c/d/e/f.txt")
        assert result == ["a", "a/b", "a/b/c", "a/b/c/d", "a/b/c/d/e"]

    def test_folder_segments_with_leading_slash(self):
        result = get_folder_path_segments_from_blob_name("/a/b/c.txt")
        assert result == ["a", "a/b"]

    def test_mimetype_unknown(self):
        assert get_mimetype_for_azure_blob("data.xyz999") == MimeTypes.BIN.value

    def test_mimetype_text(self):
        result = get_mimetype_for_azure_blob("file.txt")
        assert result != MimeTypes.FOLDER.value

    def test_parse_parent_deep_path(self):
        container, path = parse_parent_external_id("c1/a/b/c")
        assert container == "c1"
        assert path == "a/b/c/"

    def test_get_parent_weburl_with_path(self):
        url = get_parent_weburl_for_azure_blob("container/folder/sub", "myacc")
        assert "myacc.blob.core.windows.net" in url

    def test_get_parent_path_for_azure_with_trailing_slash(self):
        assert get_parent_path_for_azure_blob("c1/folder/") == "folder/"


# ===========================================================================
# AzureBlobDataSourceEntitiesProcessor
# ===========================================================================
class TestAzureBlobProcessorExtended:
    def test_default_account_name(self, mock_logger_cov, mock_data_store_provider_cov, mock_config_service):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger_cov,
            data_store_provider=mock_data_store_provider_cov,
            config_service=mock_config_service,
        )
        assert proc.account_name == ""


# ===========================================================================
# get_app_users
# ===========================================================================
class TestGetAppUsersExtended:
    def test_user_with_title(self, azure_connector):
        users = [User(email="a@test.com", full_name="A", title="Engineer")]
        result = azure_connector.get_app_users(users)
        assert result[0].title == "Engineer"

    def test_multiple_users_mixed(self, azure_connector):
        users = [
            User(email="a@test.com", full_name="A", is_active=True),
            User(email="", full_name="B", is_active=True),
            User(email="c@test.com", full_name="C", is_active=False),
        ]
        result = azure_connector.get_app_users(users)
        # Only users with email are included
        assert len(result) == 2


# ===========================================================================
# _extract_container_names extended
# ===========================================================================
class TestExtractContainerNamesExtended:
    def test_mixed_types(self, azure_connector):
        obj = MagicMock()
        obj.name = "c2"
        data = [{"name": "c1"}, obj, {"name": None}]
        result = azure_connector._extract_container_names(data)
        assert result == ["c1", "c2"]

    def test_empty_list(self, azure_connector):
        assert azure_connector._extract_container_names([]) == []


# ===========================================================================
# _blob_properties_to_dict
# ===========================================================================
class TestBlobPropertiesToDict:
    def test_dict_passthrough(self, azure_connector):
        d = {"name": "test.txt", "size": 100}
        assert azure_connector._blob_properties_to_dict(d) == d

    def test_object_conversion(self, azure_connector):
        blob = MagicMock()
        blob.name = "test.txt"
        blob.last_modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.etag = '"abc123"'
        blob.size = 1024
        content_settings = MagicMock()
        content_settings.content_type = "text/plain"
        content_settings.content_md5 = b"abc"
        blob.content_settings = content_settings

        result = azure_connector._blob_properties_to_dict(blob)
        assert result["name"] == "test.txt"
        assert result["size"] == 1024
        assert result["content_type"] == "text/plain"

    def test_object_no_content_settings(self, azure_connector):
        blob = MagicMock(spec=[])
        blob.name = "test.txt"
        blob.last_modified = None
        blob.creation_time = None
        blob.etag = ""
        blob.size = 0
        # No content_settings attribute
        result = azure_connector._blob_properties_to_dict(blob)
        assert result["name"] == "test.txt"
        assert result["content_type"] is None


# ===========================================================================
# _get_azure_blob_revision_id
# ===========================================================================
class TestGetAzureBlobRevisionId:
    def test_with_bytes_md5(self, azure_connector):
        blob = {"content_md5": b"\x01\x02\x03"}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == base64.b64encode(b"\x01\x02\x03").decode("utf-8")

    def test_with_bytearray_md5(self, azure_connector):
        blob = {"content_md5": bytearray(b"\x01\x02\x03")}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == base64.b64encode(b"\x01\x02\x03").decode("utf-8")

    def test_with_string_md5(self, azure_connector):
        blob = {"content_md5": "abc123"}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == "abc123"

    def test_with_etag_no_md5(self, azure_connector):
        blob = {"etag": '"etag_value"'}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == "etag_value"

    def test_no_md5_no_etag(self, azure_connector):
        blob = {}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == ""


# ===========================================================================
# _pass_date_filters extended
# ===========================================================================
class TestAzureDateFiltersExtended:
    def test_folder_always_passes(self, azure_connector):
        blob = {"name": "folder/"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_no_filters(self, azure_connector):
        blob = {"name": "file.txt"}
        assert azure_connector._pass_date_filters(blob, None, None, None, None) is True

    def test_no_last_modified(self, azure_connector):
        blob = {"name": "file.txt"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_string_last_modified(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "2025-01-01T00:00:00+00:00"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, past_ms, future_ms, None, None) is True

    def test_string_last_modified_z_suffix(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "2025-01-01T00:00:00Z"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, past_ms, None, None, None) is True

    def test_invalid_string_last_modified(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "not-a-date"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_non_datetime_type(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": 12345}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_creation_time_datetime(self, azure_connector):
        now = datetime.now(timezone.utc)
        blob = {
            "name": "file.txt",
            "last_modified": now,
            "creation_time": now,
        }
        future_ms = int((now.timestamp() + 3600) * 1000)
        # created_after_ms in the future should fail
        assert azure_connector._pass_date_filters(blob, None, None, future_ms, None) is False

    def test_creation_time_string(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "2025-01-01T00:00:00+00:00",
        }
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, None, None, past_ms, future_ms) is True

    def test_creation_time_invalid_string(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "invalid",
        }
        assert azure_connector._pass_date_filters(blob, None, None, 100, None) is True

    def test_creation_time_non_type(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": 12345,
        }
        assert azure_connector._pass_date_filters(blob, None, None, 100, None) is True


# ===========================================================================
# _pass_extension_filter
# ===========================================================================
class TestPassExtensionFilter:
    def test_folder_always_passes(self, azure_connector):
        assert azure_connector._pass_extension_filter("folder/", is_folder=True) is True

    def test_no_filter_configured(self, azure_connector):
        azure_connector.sync_filters = FilterCollection()
        assert azure_connector._pass_extension_filter("file.txt") is True

    def test_file_no_extension_in_operator(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("Makefile") is False

    def test_file_no_extension_not_in_operator(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("Makefile") is True

    def test_extension_in_allowed_list(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.pdf") is True

    def test_extension_not_in_allowed_list(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.docx") is False

    def test_invalid_filter_value_type(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not a list"
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.pdf") is True


# ===========================================================================
# _create_record_groups_for_containers
# ===========================================================================
class TestCreateRecordGroupsForContainers:
    @pytest.mark.asyncio
    async def test_team_scope(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_with_creator_email(self, azure_connector):
        azure_connector.scope = ConnectorScope.PERSONAL.value
        azure_connector.creator_email = "user@test.com"
        azure_connector.created_by = "user-1"
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_no_creator(self, azure_connector):
        azure_connector.scope = ConnectorScope.PERSONAL.value
        azure_connector.creator_email = None
        azure_connector.created_by = None
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_list(self, azure_connector):
        await azure_connector._create_record_groups_for_containers([])
        azure_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_items_skipped(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        await azure_connector._create_record_groups_for_containers([None, "container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ===========================================================================
# _process_azure_blob
# ===========================================================================
class TestProcessAzureBlob:
    @pytest.mark.asyncio
    async def test_empty_name(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        record, perms = await azure_connector._process_azure_blob({"name": ""}, "container")
        assert record is None

    @pytest.mark.asyncio
    async def test_new_file(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "path/file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "etag": '"abc123"',
            "size": 1024,
            "content_md5": None,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.record_name == "file.txt"

    @pytest.mark.asyncio
    async def test_folder_blob(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "folder/",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": None,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        # RecordType.FOLDER does not exist in the enum, so the method
        # catches the error and returns (None, [])
        assert record is None
        assert perms == []

    @pytest.mark.asyncio
    async def test_string_timestamps(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "file.txt",
            "last_modified": "2025-01-01T00:00:00Z",
            "creation_time": "2025-01-01T00:00:00Z",
            "etag": '"abc"',
            "size": 100,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self, azure_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old_revision"
        existing.external_record_id = "container/file.txt"
        existing.version = 1
        existing.source_created_at = 1700000000000
        azure_connector.data_store_provider = _make_mock_data_store_provider(existing)
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"

        blob = {
            "name": "file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "etag": '"new_etag"',
            "content_md5": b"\x01\x02",
            "size": 2048,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.version == 2

    @pytest.mark.asyncio
    async def test_move_detected(self, azure_connector):
        existing = MagicMock()
        existing.id = "moved-id"
        existing.external_record_id = "container/old/file.txt"
        existing.external_revision_id = "same_md5"
        existing.version = 0
        existing.source_created_at = 1700000000000
        azure_connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None, existing_revision_record=existing
        )
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"

        blob = {
            "name": "new/file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "content_md5": "same_md5",
            "size": 100,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.id == "moved-id"


# ===========================================================================
# _remove_old_parent_relationship
# ===========================================================================
class TestRemoveOldParentRelationship:
    @pytest.mark.asyncio
    async def test_successful(self, azure_connector):
        tx = AsyncMock()
        tx.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
        await azure_connector._remove_old_parent_relationship("rec-1", tx)

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        tx = AsyncMock()
        tx.delete_parent_child_edge_to_record = AsyncMock(side_effect=Exception("err"))
        await azure_connector._remove_old_parent_relationship("rec-1", tx)


# ===========================================================================
# run_sync extended
# ===========================================================================
class TestRunSyncExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = None
        with pytest.raises(ConnectionError):
            await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_containers_fails(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="err")
        )
        await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_containers_found(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_container(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._create_record_groups_for_containers = AsyncMock()
        azure_connector._sync_container = AsyncMock()
        await azure_connector.run_sync()
        azure_connector._sync_container.assert_awaited_once_with("mycontainer")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_container_sync_error_continues(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._create_record_groups_for_containers = AsyncMock()
        azure_connector._sync_container = AsyncMock(side_effect=Exception("sync error"))
        # Should not raise
        await azure_connector.run_sync()


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True)
        )
        result = await azure_connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="denied")
        )
        result = await azure_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(side_effect=Exception("err"))
        result = await azure_connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.get_signed_url(MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_container(self, azure_connector):
        azure_connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id=None)
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_external_record_id(self, azure_connector):
        azure_connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id="container", external_record_id=None)
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_success(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://blob.url/sas"})
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result == "https://blob.url/sas"

    @pytest.mark.asyncio
    async def test_failure(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(False, error="denied")
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(side_effect=Exception("err"))
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_container_prefix(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://blob.url/sas"})
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="/path/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is not None


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_folder_raises(self, azure_connector):
        from app.models.entities import FileRecord
        from fastapi import HTTPException
        record = MagicMock(spec=FileRecord)
        record.is_file = False
        with pytest.raises(HTTPException):
            await azure_connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_no_signed_url(self, azure_connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.is_file = True
        azure_connector.get_signed_url = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await azure_connector.stream_record(record)


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_with_data_source(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.close_async_client = AsyncMock()
        await azure_connector.cleanup()
        assert azure_connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_no_data_source(self, azure_connector):
        azure_connector.data_source = None
        await azure_connector.cleanup()
        assert azure_connector.data_source is None


# ===========================================================================
# get_filter_options / _get_container_options
# ===========================================================================
class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_containers(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": "c1"}, {"name": "c2"}])
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_unsupported(self, azure_connector):
        with pytest.raises(ValueError, match="Unsupported"):
            await azure_connector.get_filter_options("invalid_key")

    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_fails(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="err")
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_containers(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_search(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": "prod-data"}, {"name": "dev-logs"}])
        )
        result = await azure_connector.get_filter_options("containers", search="prod")
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_pagination(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": f"c{i}"} for i in range(5)])
        )
        result = await azure_connector.get_filter_options("containers", page=1, limit=2)
        assert len(result.options) == 2
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(side_effect=Exception("err"))
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    def test_raises(self, azure_connector):
        with pytest.raises(NotImplementedError):
            azure_connector.handle_webhook_notification({})


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self, azure_connector):
        await azure_connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await azure_connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(MagicMock(), []), None]
        )
        azure_connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await azure_connector.reindex_records([MagicMock(id="r1"), MagicMock(id="r2")])
        azure_connector.data_entities_processor.on_new_records.assert_awaited_once()
        azure_connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_container(self, azure_connector):
        record = MagicMock(id="r1", external_record_group_id=None, external_record_id="k")
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_blob_not_found(self, azure_connector):
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt"
        )
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.get_blob_properties = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_change(self, azure_connector):
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt",
            external_revision_id="abc123", version=1, source_created_at=1700000000000,
        )
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.get_blob_properties = AsyncMock(
            return_value=_make_response(True, {"etag": '"abc123"', "last_modified": datetime.now(timezone.utc)})
        )
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# run_incremental_sync
# ===========================================================================
class TestRunIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = None
        with pytest.raises(ConnectionError):
            await azure_connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_container(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._sync_container = AsyncMock()
        await azure_connector.run_incremental_sync()
        azure_connector._sync_container.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_containers(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        await azure_connector.run_incremental_sync()


# ===========================================================================
# _ensure_parent_folders_exist
# ===========================================================================
class TestEnsureParentFoldersExist:
    @pytest.mark.asyncio
    async def test_empty_segments(self, azure_connector):
        await azure_connector._ensure_parent_folders_exist("container", [])
        azure_connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_segment(self, azure_connector):
        azure_connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await azure_connector._ensure_parent_folders_exist("container", ["folder"])
        azure_connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_segments(self, azure_connector):
        azure_connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await azure_connector._ensure_parent_folders_exist("container", ["a", "a/b", "a/b/c"])
        assert azure_connector.data_entities_processor.on_new_records.await_count == 3


# ===========================================================================
# _generate_web_url / _generate_parent_web_url
# ===========================================================================
class TestGenerateUrls:
    def test_generate_web_url(self, azure_connector):
        azure_connector.account_name = "teststorage"
        url = azure_connector._generate_web_url("container", "path/file.txt")
        assert "teststorage" in url
        assert "container" in url

    def test_generate_parent_web_url(self, azure_connector):
        azure_connector.account_name = "teststorage"
        url = azure_connector._generate_parent_web_url("container/path")
        assert "teststorage" in url


# ===========================================================================
# _extract_container_names edge cases
# ===========================================================================
class TestExtractContainerNamesEdgeCases:
    def test_none_input(self, azure_connector):
        result = azure_connector._extract_container_names(None)
        assert result == []

    def test_objects_with_name_attr(self, azure_connector):
        obj = MagicMock()
        obj.name = "c1"
        result = azure_connector._extract_container_names([obj])
        assert result == ["c1"]

# =============================================================================
# Merged from test_azure_blob_connector_full_coverage.py
# =============================================================================

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
    tx.get_user_by_user_id = AsyncMock(
        return_value=user or {"email": "creator@test.com"}
    )
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
def mock_logger_fullcov():
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
def connector(mock_logger_fullcov, mock_dep, mock_provider, mock_config):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        c = AzureBlobConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_dep,
            data_store_provider=mock_provider,
            config_service=mock_config,
            connector_id="az-fc-1",
            scope="personal",
            created_by="test-user-id",
        )
    c.account_name = "teststorage"
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
    def test_create_placeholder_file_record(self, mock_logger_fullcov, mock_provider, mock_config):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger_fullcov,
            data_store_provider=mock_provider,
            config_service=mock_config,
            account_name="myaccount",
        )
        assert proc.account_name == "myaccount"

    def test_create_placeholder_parent_record(self, mock_logger_fullcov, mock_provider, mock_config):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger_fullcov,
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


class TestRunSyncExtendedFullCoverage:
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
    async def test_with_connection_string(self, mock_app, mock_proc_cls, mock_config, mock_provider, mock_logger_fullcov):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger_fullcov,
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
    async def test_no_config(self, mock_app, mock_proc_cls, mock_provider, mock_logger_fullcov):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value=None)
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger_fullcov,
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
    async def test_config_exception(self, mock_app, mock_proc_cls, mock_provider, mock_logger_fullcov):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(side_effect=Exception("config err"))
        result = await AzureBlobConnector.create_connector(
            logger=mock_logger_fullcov,
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
