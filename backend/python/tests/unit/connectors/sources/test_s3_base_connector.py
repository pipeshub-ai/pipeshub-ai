"""Extended coverage tests for S3 base connector and S3CompatibleBaseConnector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.s3.base_connector import (
    S3CompatibleBaseConnector,
    S3CompatibleDataSourceEntitiesProcessor,
    get_file_extension,
    get_folder_path_segments_from_key,
    get_mimetype_for_s3,
    get_parent_path_for_s3,
    get_parent_path_from_key,
    get_parent_weburl_for_s3,
    make_s3_composite_revision,
    parse_parent_external_id,
)
from app.connectors.sources.s3.connector import S3Connector
from app.models.entities import RecordType, User
from fastapi import HTTPException
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    FilterOptionsResponse,
    IndexingFilterKey,
)
from app.models.entities import FileRecord, Record, RecordType


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
    tx.get_user_by_user_id = AsyncMock(return_value=user or {"email": "user@test.com"})
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
def mock_logger():
    return logging.getLogger("test.s3.cov")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-s3-cov"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    u = User(
        email="user@test.com",
        org_id="org-s3-cov",
        source_user_id="test-user-id",
        full_name="Test User",
    )
    proc.get_user_by_user_id = AsyncMock(return_value=u)
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "accessKey": "AKIAIOSFODNN7EXAMPLE",
            "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def s3_connector(mock_logger, mock_data_entities_processor,
                 mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.s3.connector.S3App"):
        connector = S3Connector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="s3-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


# ===========================================================================
# Helper functions - extended edge cases
# ===========================================================================
class TestHelperFunctionsExtended:
    def test_get_file_extension_double_dot(self):
        assert get_file_extension("file..pdf") == "pdf"

    def test_get_file_extension_only_dot(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_get_parent_path_leading_slash(self):
        assert get_parent_path_from_key("/a/b/c.txt") == "a/b"

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_key("a/b/c/") == "a/b"

    def test_get_parent_path_single_slash(self):
        assert get_parent_path_from_key("/") is None

    def test_folder_segments_deeply_nested(self):
        result = get_folder_path_segments_from_key("a/b/c/d/e/file.txt")
        assert result == ["a", "a/b", "a/b/c", "a/b/c/d", "a/b/c/d/e"]

    def test_folder_segments_trailing_slash(self):
        result = get_folder_path_segments_from_key("a/b/c/")
        assert result == ["a", "a/b"]

    def test_get_parent_weburl_for_s3_no_trailing_slash(self):
        url = get_parent_weburl_for_s3("mybucket/folder")
        assert "prefix=folder/" in url

    def test_get_parent_weburl_for_s3_with_trailing_slash(self):
        url = get_parent_weburl_for_s3("mybucket/folder/")
        assert "prefix=folder/" in url

    def test_parse_parent_external_id_with_trailing_slash(self):
        bucket, path = parse_parent_external_id("mybucket/folder/")
        assert bucket == "mybucket"
        assert path == "folder/"

    def test_parse_parent_external_id_deep_path(self):
        bucket, path = parse_parent_external_id("mybucket/a/b/c")
        assert bucket == "mybucket"
        assert path == "a/b/c/"

    def test_parse_parent_external_id_leading_slash_in_path(self):
        bucket, path = parse_parent_external_id("mybucket//path")
        assert bucket == "mybucket"
        assert path == "path/"

    def test_get_parent_path_for_s3_with_trailing_slash(self):
        assert get_parent_path_for_s3("bucket/folder/") == "folder/"

    def test_mimetype_for_docx(self):
        result = get_mimetype_for_s3("report.docx")
        assert result != MimeTypes.FOLDER.value

    def test_mimetype_for_no_extension(self):
        result = get_mimetype_for_s3("Makefile")
        assert result == MimeTypes.BIN.value

    def test_composite_revision_empty_etag(self):
        result = make_s3_composite_revision("bucket", "key.txt", "")
        # Empty etag is falsy, so falls back
        assert result == "bucket/key.txt|"


# ===========================================================================
# S3CompatibleDataSourceEntitiesProcessor
# ===========================================================================
class TestS3CompatibleDataSourceEntitiesProcessor:
    def test_default_url_generator(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc.base_console_url == "https://s3.console.aws.amazon.com"
        # Test the default URL generator
        url = proc.parent_url_generator("mybucket/folder")
        assert "s3.console.aws.amazon.com" in url

    def test_custom_base_url(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            base_console_url="http://minio:9000",
        )
        url = proc.parent_url_generator("mybucket/folder")
        assert "minio:9000" in url

    def test_custom_url_generator(self, mock_logger, mock_data_store_provider, mock_config_service):
        custom_gen = lambda pid: f"https://custom.com/{pid}"
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            parent_url_generator=custom_gen,
        )
        url = proc.parent_url_generator("mybucket/folder")
        assert url == "https://custom.com/mybucket/folder"


# ===========================================================================
# get_app_users
# ===========================================================================
class TestGetAppUsers:
    def test_filters_empty_email(self, s3_connector):
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        result = s3_connector.get_app_users(users)
        assert len(result) == 1
        assert result[0].email == "a@test.com"

    def test_none_active_defaults_to_true(self, s3_connector):
        users = [User(email="a@test.com", full_name="A", is_active=None)]
        result = s3_connector.get_app_users(users)
        assert result[0].is_active is True

    def test_uses_source_user_id(self, s3_connector):
        users = [User(email="a@test.com", full_name="A", source_user_id="src-1")]
        result = s3_connector.get_app_users(users)
        assert result[0].source_user_id == "src-1"

    def test_fallback_full_name_to_email(self, s3_connector):
        users = [User(email="a@test.com", full_name=None)]
        result = s3_connector.get_app_users(users)
        assert result[0].full_name == "a@test.com"


# ===========================================================================
# _create_record_groups_for_buckets
# ===========================================================================
class TestCreateRecordGroupsForBuckets:
    @pytest.mark.asyncio
    async def test_team_scope(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = None
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_bucket_names(self, s3_connector):
        await s3_connector._create_record_groups_for_buckets([])
        s3_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_bucket_names_skipped(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        await s3_connector._create_record_groups_for_buckets([None, "bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_user_lookup_fails(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        # Make user lookup fail
        provider = _make_mock_data_store_provider(user=None)
        s3_connector.data_store_provider = provider
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        # Should still create record groups with fallback permissions
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ===========================================================================
# _get_date_filters / _pass_date_filters extended
# ===========================================================================
class TestDateFiltersExtended:
    def test_pass_date_filters_passes_when_within_range(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        past_ms = int((now.timestamp() - 3600) * 1000)
        future_ms = int((now.timestamp() + 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, past_ms, future_ms, None, None) is True

    def test_pass_date_filters_all_filters_pass(self, s3_connector):
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now}
        past_ms = int((now.timestamp() - 3600) * 1000)
        future_ms = int((now.timestamp() + 3600) * 1000)
        assert s3_connector._pass_date_filters(obj, past_ms, future_ms, past_ms, future_ms) is True


# ===========================================================================
# _get_bucket_region
# ===========================================================================
class TestGetBucketRegion:
    @pytest.mark.asyncio
    async def test_cache_hit(self, s3_connector):
        s3_connector.bucket_regions = {"mybucket": "eu-west-1"}
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "eu-west-1"

    @pytest.mark.asyncio
    async def test_no_data_source(self, s3_connector):
        s3_connector.data_source = None
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    @pytest.mark.asyncio
    async def test_successful_region_fetch(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(True, {"LocationConstraint": "ap-southeast-1"})
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "ap-southeast-1"
        assert s3_connector.bucket_regions["mybucket"] == "ap-southeast-1"

    @pytest.mark.asyncio
    async def test_null_location_constraint(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(True, {"LocationConstraint": None})
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    @pytest.mark.asyncio
    async def test_empty_location_constraint(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(True, {"LocationConstraint": ""})
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    @pytest.mark.asyncio
    async def test_failed_region_fetch(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            return_value=_make_response(False, error="Access denied")
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"

    @pytest.mark.asyncio
    async def test_exception_during_fetch(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.get_bucket_location = AsyncMock(
            side_effect=Exception("Network error")
        )
        result = await s3_connector._get_bucket_region("mybucket")
        assert result == "us-east-1"


# ===========================================================================
# _process_s3_object
# ===========================================================================
class TestProcessS3Object:
    @pytest.mark.asyncio
    async def test_empty_key(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        record, perms = await s3_connector._process_s3_object({"Key": ""}, "mybucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_missing_key(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        record, perms = await s3_connector._process_s3_object({}, "mybucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_new_file(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc123"',
            "Size": 1024,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.record_name == "file.txt"
        assert record.is_file is True
        assert record.external_record_id == "mybucket/path/file.txt"

    @pytest.mark.asyncio
    async def test_folder_object(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "folder/",
            "LastModified": datetime.now(timezone.utc),
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.is_file is False
        assert record.mime_type == MimeTypes.FOLDER.value

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self, s3_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "mybucket/old_etag"
        existing.external_record_id = "mybucket/path/file.txt"
        existing.version = 1
        existing.source_created_at = 1700000000000
        s3_connector.data_store_provider = _make_mock_data_store_provider(existing)
        s3_connector.scope = ConnectorScope.TEAM.value

        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"new_etag"',
            "Size": 2048,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.id == "existing-id"
        assert record.version == 2

    @pytest.mark.asyncio
    async def test_move_detected(self, s3_connector):
        existing = MagicMock()
        existing.id = "moved-id"
        existing.external_record_id = "mybucket/old/path/file.txt"
        existing.external_revision_id = "mybucket/same_etag"
        existing.version = 0
        existing.source_created_at = 1700000000000
        s3_connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None, existing_revision_record=existing
        )
        s3_connector.scope = ConnectorScope.TEAM.value

        obj = {
            "Key": "new/path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"same_etag"',
            "Size": 1024,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.id == "moved-id"

    @pytest.mark.asyncio
    async def test_root_level_file(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc"',
            "Size": 100,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_no_etag(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "Size": 100,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {"Key": "path/file.txt", "ETag": '"abc"', "Size": 50}
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None


# ===========================================================================
# _create_s3_permissions
# ===========================================================================
class TestCreateS3Permissions:
    @pytest.mark.asyncio
    async def test_team_scope(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = None
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_not_initialized(self, s3_connector):
        s3_connector.data_source = None
        result = await s3_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True)
        )
        result = await s3_connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(False, error="Access denied")
        )
        result = await s3_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(side_effect=Exception("err"))
        result = await s3_connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_not_initialized(self, s3_connector):
        s3_connector.data_source = None
        record = MagicMock()
        result = await s3_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_bucket_name(self, s3_connector):
        s3_connector.data_source = MagicMock()
        record = MagicMock()
        record.external_record_group_id = None
        record.id = "rec-1"
        result = await s3_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_external_record_id(self, s3_connector):
        s3_connector.data_source = MagicMock()
        record = MagicMock()
        record.external_record_group_id = "mybucket"
        record.external_record_id = None
        record.id = "rec-1"
        result = await s3_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_success(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_make_response(True, "https://s3.example.com/signed")
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        record = MagicMock()
        record.external_record_group_id = "mybucket"
        record.external_record_id = "mybucket/path/file.txt"
        record.id = "rec-1"
        record.record_name = "file.txt"
        result = await s3_connector.get_signed_url(record)
        assert result == "https://s3.example.com/signed"

    @pytest.mark.asyncio
    async def test_access_denied(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_make_response(False, error="AccessDenied")
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        record = MagicMock()
        record.external_record_group_id = "mybucket"
        record.external_record_id = "mybucket/path/file.txt"
        record.id = "rec-1"
        record.record_name = "file.txt"
        result = await s3_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_such_key(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_make_response(False, error="NoSuchKey")
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        record = MagicMock()
        record.external_record_group_id = "mybucket"
        record.external_record_id = "mybucket/path/file.txt"
        record.id = "rec-1"
        record.record_name = "file.txt"
        result = await s3_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_bucket_prefix(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_make_response(True, "https://s3.example.com/signed")
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        record = MagicMock()
        record.external_record_group_id = "mybucket"
        record.external_record_id = "/path/file.txt"
        record.id = "rec-1"
        record.record_name = "file.txt"
        result = await s3_connector.get_signed_url(record)
        assert result is not None


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_folder_raises(self, s3_connector):
        from app.models.entities import FileRecord
        from fastapi import HTTPException
        record = MagicMock(spec=FileRecord)
        record.is_file = False
        with pytest.raises(HTTPException):
            await s3_connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_no_signed_url(self, s3_connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.is_file = True
        s3_connector.get_signed_url = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await s3_connector.stream_record(record)


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self, s3_connector):
        s3_connector.data_source = MagicMock()
        await s3_connector.cleanup()
        assert s3_connector.data_source is None


# ===========================================================================
# run_sync extended
# ===========================================================================
class TestRunSyncExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_fails(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(False, error="err")
        )
        await s3_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_buckets_found(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": []})
        )
        # Should just return without error
        await s3_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_bucket_sync_error_continues(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "mybucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._create_record_groups_for_buckets = AsyncMock()
        s3_connector._sync_bucket = AsyncMock(side_effect=Exception("sync error"))
        # Should not raise
        await s3_connector.run_sync()


# ===========================================================================
# _remove_old_parent_relationship
# ===========================================================================
class TestRemoveOldParentRelationship:
    @pytest.mark.asyncio
    async def test_successful_removal(self, s3_connector):
        tx_store = AsyncMock()
        tx_store.delete_parent_child_edge_to_record = AsyncMock(return_value=2)
        await s3_connector._remove_old_parent_relationship("rec-1", tx_store)

    @pytest.mark.asyncio
    async def test_exception_handled(self, s3_connector):
        tx_store = AsyncMock()
        tx_store.delete_parent_child_edge_to_record = AsyncMock(side_effect=Exception("err"))
        await s3_connector._remove_old_parent_relationship("rec-1", tx_store)


# ===========================================================================
# _ensure_parent_folders_exist
# ===========================================================================
class TestEnsureParentFoldersExist:
    @pytest.mark.asyncio
    async def test_empty_segments(self, s3_connector):
        await s3_connector._ensure_parent_folders_exist("mybucket", [])
        s3_connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_segment(self, s3_connector):
        s3_connector._create_s3_permissions = AsyncMock(return_value=[])
        await s3_connector._ensure_parent_folders_exist("mybucket", ["folder"])
        s3_connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_segments(self, s3_connector):
        s3_connector._create_s3_permissions = AsyncMock(return_value=[])
        await s3_connector._ensure_parent_folders_exist("mybucket", ["a", "a/b", "a/b/c"])
        assert s3_connector.data_entities_processor.on_new_records.await_count == 3


# ===========================================================================
# _sync_bucket
# ===========================================================================
class TestSyncBucket:
    @pytest.mark.asyncio
    async def test_not_initialized(self, s3_connector):
        s3_connector.data_source = None
        with pytest.raises(ConnectionError):
            await s3_connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_access_denied(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_make_response(False, error="AccessDenied: not authorized")
        )
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        await s3_connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_no_contents(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_make_response(True, {})
        )
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        s3_connector.record_sync_point.update_sync_point = AsyncMock()
        await s3_connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_with_objects(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_make_response(True, {
                "Contents": [
                    {"Key": "file.txt", "LastModified": datetime.now(timezone.utc), "ETag": '"abc"', "Size": 100}
                ],
                "IsTruncated": False,
            })
        )
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        s3_connector.record_sync_point.update_sync_point = AsyncMock()
        await s3_connector._sync_bucket("mybucket")
        s3_connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_exception_during_loop(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_objects_v2 = AsyncMock(
            side_effect=Exception("network error")
        )
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        s3_connector.record_sync_point.update_sync_point = AsyncMock()
        await s3_connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_with_continuation_token(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        s3_connector.data_source = MagicMock()
        # First call: truncated with token
        first_response = _make_response(True, {
            "Contents": [
                {"Key": "f1.txt", "LastModified": datetime.now(timezone.utc), "ETag": '"a"', "Size": 10}
            ],
            "IsTruncated": True,
            "NextContinuationToken": "token-2",
        })
        # Second call: final page
        second_response = _make_response(True, {
            "Contents": [
                {"Key": "f2.txt", "LastModified": datetime.now(timezone.utc), "ETag": '"b"', "Size": 20}
            ],
            "IsTruncated": False,
        })
        s3_connector.data_source.list_objects_v2 = AsyncMock(side_effect=[first_response, second_response])
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        s3_connector.record_sync_point.update_sync_point = AsyncMock()
        await s3_connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_extension_filter(self, s3_connector):
        from app.connectors.core.registry.filters import Filter, FilterCollection, FilterType
        s3_connector.scope = ConnectorScope.TEAM.value
        ext_filter = Filter(key="file_extensions", value=["txt", "pdf"], type=FilterType.LIST, operator="in")
        filter_coll = FilterCollection(filters=[ext_filter])
        s3_connector.sync_filters = filter_coll

        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_make_response(True, {
                "Contents": [
                    {"Key": "file.txt", "LastModified": datetime.now(timezone.utc), "ETag": '"a"', "Size": 10},
                    {"Key": "file.jpg", "LastModified": datetime.now(timezone.utc), "ETag": '"b"', "Size": 20},
                ],
                "IsTruncated": False,
            })
        )
        s3_connector.record_sync_point = MagicMock()
        s3_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        s3_connector.record_sync_point.update_sync_point = AsyncMock()
        await s3_connector._sync_bucket("mybucket")


# ===========================================================================
# get_filter_options / _get_bucket_options
# ===========================================================================
class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_buckets_filter(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"Name": "b1"}, {"Name": "b2"}]})
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await s3_connector.get_filter_options("buckets")
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_unsupported_filter(self, s3_connector):
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await s3_connector.get_filter_options("invalid_key")

    @pytest.mark.asyncio
    async def test_not_initialized(self, s3_connector):
        s3_connector.data_source = None
        result = await s3_connector.get_filter_options("buckets")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_buckets_fails(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(False, error="access denied")
        )
        result = await s3_connector.get_filter_options("buckets")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_buckets(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {})
        )
        result = await s3_connector.get_filter_options("buckets")
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_search_filter(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": [{"Name": "prod-data"}, {"Name": "dev-logs"}]})
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await s3_connector.get_filter_options("buckets", search="prod")
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].id == "prod-data"

    @pytest.mark.asyncio
    async def test_pagination(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {
                "Buckets": [{"Name": f"b{i}"} for i in range(5)]
            })
        )
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await s3_connector.get_filter_options("buckets", page=1, limit=2)
        assert len(result.options) == 2
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_exception(self, s3_connector):
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(side_effect=Exception("err"))
        result = await s3_connector.get_filter_options("buckets")
        assert result.success is False


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    def test_raises(self, s3_connector):
        with pytest.raises(NotImplementedError):
            s3_connector.handle_webhook_notification({})


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, s3_connector):
        await s3_connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, s3_connector):
        s3_connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await s3_connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self, s3_connector):
        s3_connector.data_source = MagicMock()
        updated_record = MagicMock(id="r1")
        non_updated_record = MagicMock(id="r2")
        s3_connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(MagicMock(), []), None]
        )
        s3_connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await s3_connector.reindex_records([updated_record, non_updated_record])
        s3_connector.data_entities_processor.on_new_records.assert_awaited_once()
        s3_connector.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_exception_skips(self, s3_connector):
        s3_connector.data_source = MagicMock()
        record = MagicMock(id="r1")
        s3_connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("err"))
        s3_connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await s3_connector.reindex_records([record])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_bucket(self, s3_connector):
        record = MagicMock(id="r1", external_record_group_id=None, external_record_id="key")
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_external_record_id(self, s3_connector):
        record = MagicMock(id="r1", external_record_group_id="bucket", external_record_id=None)
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_key(self, s3_connector):
        record = MagicMock(id="r1", external_record_group_id="bucket", external_record_id="bucket/")
        s3_connector.data_source = MagicMock()
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_object_not_found(self, s3_connector):
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
        )
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.head_object = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_change(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="bucket/abc123",
            version=1, source_created_at=1700000000000,
        )
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.head_object = AsyncMock(
            return_value=_make_response(True, {
                "ETag": '"abc123"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 100,
            })
        )
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_changed(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
            external_revision_id="bucket/old_etag",
            version=1, source_created_at=1700000000000,
        )
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.head_object = AsyncMock(
            return_value=_make_response(True, {
                "ETag": '"new_etag"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 200,
            })
        )
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated_record, perms = result
        assert updated_record.version == 2

    @pytest.mark.asyncio
    async def test_exception(self, s3_connector):
        record = MagicMock(
            id="r1", external_record_group_id="bucket",
            external_record_id="bucket/file.txt",
        )
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.head_object = AsyncMock(side_effect=Exception("err"))
        result = await s3_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# run_sync with configured bucket
# ===========================================================================
class TestRunSyncConfiguredBucket:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_bucket(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "configured-bucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._create_record_groups_for_buckets = AsyncMock()
        s3_connector._sync_bucket = AsyncMock()
        await s3_connector.run_sync()
        s3_connector._sync_bucket.assert_awaited_once_with("configured-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized_raises(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = None
        with pytest.raises(ConnectionError):
            await s3_connector.run_sync()


# ===========================================================================
# run_incremental_sync
# ===========================================================================
class TestRunIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = None
        with pytest.raises(ConnectionError):
            await s3_connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_bucket(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "mybucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._sync_bucket = AsyncMock()
        await s3_connector.run_incremental_sync()
        s3_connector._sync_bucket.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_buckets(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.data_source.list_buckets = AsyncMock(
            return_value=_make_response(True, {"Buckets": []})
        )
        await s3_connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_bucket_sync_error_continues(self, mock_filters, s3_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        s3_connector.data_source = MagicMock()
        s3_connector.bucket_name = "mybucket"
        s3_connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        s3_connector._sync_bucket = AsyncMock(side_effect=Exception("sync error"))
        await s3_connector.run_incremental_sync()


# ===========================================================================
# _process_s3_object indexing_filters
# ===========================================================================
class TestProcessS3ObjectIndexingFilters:
    @pytest.mark.asyncio
    async def test_indexing_off_sets_status(self, s3_connector):
        from app.connectors.core.registry.filters import FilterCollection
        s3_connector.scope = ConnectorScope.TEAM.value
        idx_filters = MagicMock(spec=FilterCollection)
        idx_filters.is_enabled = MagicMock(return_value=False)
        s3_connector.indexing_filters = idx_filters
        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc"',
            "Size": 100,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "path/file.txt",
            "LastModified": "not-a-datetime",
            "ETag": '"abc"',
            "Size": 100,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_leading_slash_key(self, s3_connector):
        s3_connector.scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "/leading/slash/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc"',
            "Size": 50,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert not record.external_record_id.startswith("mybucket//")


# ===========================================================================
# _create_s3_permissions edge cases
# ===========================================================================
class TestCreateS3PermissionsEdgeCases:
    @pytest.mark.asyncio
    async def test_personal_creator_lookup_fails(self, s3_connector):
        s3_connector.scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        # Mock tx_store to return None for user lookup so no email is found
        tx = AsyncMock()
        tx.get_user_by_id = AsyncMock(return_value=None)
        provider = MagicMock()
        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        s3_connector.data_store_provider = provider
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_exception_fallback(self, s3_connector):
        s3_connector.scope = "INVALID"
        # Force an exception in permissions logic
        s3_connector.data_entities_processor.org_id = "org-1"
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) >= 1

# =============================================================================
# Merged from test_s3_base_connector_full_coverage.py
# =============================================================================

@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.s3.full")


@pytest.fixture()
def mock_dep():
    proc = MagicMock()
    proc.org_id = "org-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.reindex_existing_records = AsyncMock()
    u = User(
        email="user@test.com",
        org_id="org-1",
        source_user_id="test-user-id",
        full_name="Test User",
    )
    proc.get_user_by_user_id = AsyncMock(return_value=u)
    return proc


@pytest.fixture()
def mock_dsp():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_cs():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "accessKey": "AKIAIOSFODNN7EXAMPLE",
            "secretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def connector(mock_logger_fullcov, mock_dep, mock_dsp, mock_cs):
    with patch("app.connectors.sources.s3.connector.S3App"):
        c = S3Connector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_dep,
            data_store_provider=mock_dsp,
            config_service=mock_cs,
            connector_id="s3-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


def _resp(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _record(**overrides):
    defaults = {
        "id": "rec-1",
        "record_name": "file.txt",
        "external_record_group_id": "mybucket",
        "external_record_id": "mybucket/path/file.txt",
        "external_revision_id": "mybucket/abc123",
        "version": 0,
        "source_created_at": 1000,
        "source_updated_at": 2000,
        "mime_type": "text/plain",
    }
    defaults.update(overrides)
    rec = MagicMock(spec=Record)
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _file_record(**overrides):
    defaults = {
        "id": "rec-1",
        "record_name": "file.txt",
        "external_record_group_id": "mybucket",
        "external_record_id": "mybucket/path/file.txt",
        "external_revision_id": "mybucket/abc123",
        "version": 0,
        "source_created_at": 1000,
        "source_updated_at": 2000,
        "mime_type": "text/plain",
        "is_file": True,
    }
    defaults.update(overrides)
    rec = MagicMock(spec=FileRecord)
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


class TestHelperFunctions:
    def test_get_file_extension_dotless(self):
        assert get_file_extension("no-ext") is None

    def test_get_file_extension_single_dot(self):
        assert get_file_extension(".hidden") == "hidden"

    def test_get_file_extension_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path_leading_slash(self):
        assert get_parent_path_from_key("/a/b/file.txt") == "a/b"

    def test_get_parent_path_only_slashes(self):
        assert get_parent_path_from_key("///") is None

    def test_get_parent_path_single_component_with_trailing(self):
        assert get_parent_path_from_key("single/") is None

    def test_folder_segments_trailing_slash(self):
        assert get_folder_path_segments_from_key("a/b/c/") == ["a", "a/b"]

    def test_folder_segments_leading_trailing_slashes(self):
        assert get_folder_path_segments_from_key("/a/b/") == ["a"]

    def test_get_parent_weburl_path_without_trailing_slash(self):
        url = get_parent_weburl_for_s3("bucket/folder")
        assert "prefix=folder/" in url

    def test_get_parent_weburl_path_with_trailing_slash(self):
        url = get_parent_weburl_for_s3("bucket/folder/")
        assert "prefix=folder/" in url

    def test_get_parent_weburl_leading_slash_in_path(self):
        url = get_parent_weburl_for_s3("bucket//subfolder")
        assert "prefix=subfolder/" in url

    def test_get_parent_path_for_s3_with_trailing_slash(self):
        assert get_parent_path_for_s3("bucket/dir/") == "dir/"

    def test_get_parent_path_for_s3_no_trailing_slash(self):
        assert get_parent_path_for_s3("bucket/dir") == "dir/"

    def test_parse_parent_external_id_empty_path(self):
        bucket, path = parse_parent_external_id("bucket/")
        assert bucket == "bucket"
        assert path == ""

    def test_composite_revision_empty_etag_string(self):
        assert make_s3_composite_revision("b", "k", "") == "b/k|"

    def test_get_mimetype_known_extension(self):
        assert get_mimetype_for_s3("report.pdf") == MimeTypes.PDF.value

    def test_get_mimetype_unknown_extension(self):
        assert get_mimetype_for_s3("file.xyzabc") == MimeTypes.BIN.value

    def test_get_mimetype_no_extension(self):
        assert get_mimetype_for_s3("Makefile") == MimeTypes.BIN.value

    def test_get_mimetype_html(self):
        result = get_mimetype_for_s3("index.html")
        assert result != MimeTypes.FOLDER.value


class TestCreateS3PermissionsFullCoverage:
    @pytest.mark.asyncio
    async def test_team_scope(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, connector, mock_dsp):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = None
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_creator_lookup_fails(self, connector, mock_dsp):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        mock_tx = mock_dsp.transaction.return_value
        mock_tx.get_user_by_user_id = AsyncMock(side_effect=Exception("DB error"))
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_user_no_email(self, connector, mock_dsp):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        mock_tx = mock_dsp.transaction.return_value
        mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": ""})
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


class TestTestConnectionAndAccessFullCoverage:
    @pytest.mark.asyncio
    async def test_no_data_source(self, connector):
        connector.data_source = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(True, {"Buckets": []}))
        assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(False, error="denied"))
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(side_effect=Exception("timeout"))
        assert await connector.test_connection_and_access() is False


class TestGetSignedUrlFullCoverage:
    @pytest.mark.asyncio
    async def test_no_data_source(self, connector):
        connector.data_source = None
        rec = _record()
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_no_bucket_name(self, connector):
        connector.data_source = MagicMock()
        rec = _record(external_record_group_id=None)
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_no_external_record_id(self, connector):
        connector.data_source = MagicMock()
        rec = _record(external_record_id=None)
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_resp(True, "https://presigned.url/file")
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record()
        result = await connector.get_signed_url(rec)
        assert result == "https://presigned.url/file"

    @pytest.mark.asyncio
    async def test_key_without_bucket_prefix(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_resp(True, "https://url")
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record(external_record_id="/path/file.txt")
        result = await connector.get_signed_url(rec)
        assert result == "https://url"

    @pytest.mark.asyncio
    async def test_access_denied(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_resp(False, error="AccessDenied: not authorized")
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record()
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_no_such_key(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_resp(False, error="NoSuchKey")
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record()
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_generic_error(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(
            return_value=_resp(False, error="InternalError")
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record()
        assert await connector.get_signed_url(rec) is None

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.generate_presigned_url = AsyncMock(side_effect=Exception("boom"))
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        rec = _record()
        assert await connector.get_signed_url(rec) is None


class TestStreamRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_folder_raises(self, connector):
        rec = _file_record(is_file=False)
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(rec)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_signed_url_raises(self, connector):
        connector.get_signed_url = AsyncMock(return_value=None)
        rec = _file_record(is_file=True)
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(rec)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.create_stream_record_response")
    @patch("app.connectors.sources.s3.base_connector.stream_content")
    async def test_success(self, mock_stream, mock_create, connector):
        connector.get_signed_url = AsyncMock(return_value="https://signed.url")
        mock_create.return_value = MagicMock()
        rec = _file_record(is_file=True, mime_type="text/plain")
        result = await connector.stream_record(rec)
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.create_stream_record_response")
    @patch("app.connectors.sources.s3.base_connector.stream_content")
    async def test_no_mime_type(self, mock_stream, mock_create, connector):
        connector.get_signed_url = AsyncMock(return_value="https://signed.url")
        mock_create.return_value = MagicMock()
        rec = _file_record(is_file=True, mime_type=None)
        await connector.stream_record(rec)
        call_kwargs = mock_create.call_args
        assert "application/octet-stream" in str(call_kwargs)


class TestCleanupFullCoverage:
    @pytest.mark.asyncio
    async def test_cleanup_clears_data_source(self, connector):
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.data_source is None


class TestGetFilterOptionsFullCoverage:
    @pytest.mark.asyncio
    async def test_buckets_key(self, connector):
        connector._get_bucket_options = AsyncMock(
            return_value=FilterOptionsResponse(success=True, options=[], page=1, limit=20, has_more=False)
        )
        result = await connector.get_filter_options("buckets")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unsupported_key(self, connector):
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await connector.get_filter_options("unsupported")


class TestGetBucketOptions:
    @pytest.mark.asyncio
    async def test_no_data_source(self, connector):
        connector.data_source = None
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_failure(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(False, error="denied"))
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_empty_buckets(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(True, {}))
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is True
        assert result.options == []

    @pytest.mark.asyncio
    async def test_with_buckets(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": [{"Name": "b1"}, {"Name": "b2"}, {"Name": "b3"}]})
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is True
        assert len(result.options) == 3

    @pytest.mark.asyncio
    async def test_search_filter(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": [{"Name": "prod-data"}, {"Name": "staging-data"}, {"Name": "dev-logs"}]})
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await connector._get_bucket_options(1, 20, "data")
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": [{"Name": f"b{i}"} for i in range(5)]})
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        result = await connector._get_bucket_options(1, 2, None)
        assert len(result.options) == 2
        assert result.has_more is True

        result2 = await connector._get_bucket_options(2, 2, None)
        assert len(result2.options) == 2
        assert result2.has_more is True

        result3 = await connector._get_bucket_options(3, 2, None)
        assert len(result3.options) == 1
        assert result3.has_more is False

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(side_effect=Exception("oops"))
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_buckets_key_in_data(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(True, {"Other": []}))
        result = await connector._get_bucket_options(1, 20, None)
        assert result.success is True
        assert result.options == []


class TestHandleWebhookNotificationFullCoverage:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


class TestReindexRecordsFullCoverage:
    @pytest.mark.asyncio
    async def test_empty_records(self, connector, mock_dep):
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_with_updated_records(self, connector, mock_dep):
        connector.data_source = MagicMock()
        rec = _record()
        connector._check_and_fetch_updated_record = AsyncMock(
            return_value=(_file_record(), [])
        )
        await connector.reindex_records([rec])
        mock_dep.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_with_non_updated_records(self, connector, mock_dep):
        connector.data_source = MagicMock()
        rec = _record()
        connector._check_and_fetch_updated_record = AsyncMock(return_value=None)
        await connector.reindex_records([rec])
        mock_dep.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_records(self, connector, mock_dep):
        connector.data_source = MagicMock()
        rec_updated = _record(id="u1")
        rec_not_updated = _record(id="u2")

        async def side_effect(org_id, record):
            if record.id == "u1":
                return (_file_record(), [])
            return None

        connector._check_and_fetch_updated_record = AsyncMock(side_effect=side_effect)
        await connector.reindex_records([rec_updated, rec_not_updated])
        mock_dep.on_new_records.assert_awaited_once()
        mock_dep.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        rec = _record()
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([rec])

    @pytest.mark.asyncio
    async def test_record_check_exception(self, connector, mock_dep):
        connector.data_source = MagicMock()
        rec = _record()
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))
        await connector.reindex_records([rec])


class TestCheckAndFetchUpdatedRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_missing_bucket(self, connector):
        connector.data_source = MagicMock()
        rec = _record(external_record_group_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_external_record_id(self, connector):
        connector.data_source = MagicMock()
        rec = _record(external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_normalized_key(self, connector):
        connector.data_source = MagicMock()
        rec = _record(external_record_id="mybucket/")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_head_object_failure(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(return_value=_resp(False, error="Not found"))
        rec = _record()
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_head_object_no_metadata(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(return_value=_resp(True, None))
        rec = _record()
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_revision_unchanged(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {"ETag": '"abc123"', "LastModified": datetime.now(timezone.utc)})
        )
        rec = _record(external_revision_id="mybucket/abc123")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_revision_changed(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {
                "ETag": '"newtag"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 2048,
            })
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        rec = _record(external_revision_id="mybucket/oldtag")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None
        updated_rec, perms = result
        assert updated_rec.version == 1

    @pytest.mark.asyncio
    async def test_key_without_bucket_prefix(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {
                "ETag": '"newtag"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 512,
            })
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        rec = _record(external_record_id="/path/file.txt", external_revision_id="mybucket/old")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {
                "ETag": '"newtag"',
                "LastModified": "not-a-datetime",
                "ContentLength": 512,
            })
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        rec = _record(external_revision_id="mybucket/old")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {"ETag": '"newtag"'})
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        rec = _record(external_revision_id="mybucket/old")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(side_effect=Exception("boom"))
        rec = _record()
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is None

    @pytest.mark.asyncio
    async def test_root_level_file(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {
                "ETag": '"newtag"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 100,
            })
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        rec = _record(
            external_record_id="mybucket/file.txt",
            external_revision_id="mybucket/old",
        )
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None
        updated_rec, _ = result
        assert updated_rec.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, connector):
        connector.data_source = MagicMock()
        connector.data_source.head_object = AsyncMock(
            return_value=_resp(True, {
                "ETag": '"newtag"',
                "LastModified": datetime.now(timezone.utc),
                "ContentLength": 100,
            })
        )
        connector._create_s3_permissions = AsyncMock(return_value=[])
        mock_idx = MagicMock()
        mock_idx.is_enabled = MagicMock(return_value=False)
        mock_idx.__bool__ = MagicMock(return_value=True)
        connector.indexing_filters = mock_idx
        rec = _record(external_revision_id="mybucket/old")
        result = await connector._check_and_fetch_updated_record("org-1", rec)
        assert result is not None
        updated_rec, _ = result
        assert updated_rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestRunIncrementalSyncFullCoverage:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_with_configured_bucket(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = "mybucket"
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_bucket.assert_awaited_once_with("mybucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": [{"Name": "a"}, {"Name": "b"}]})
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        assert connector._sync_bucket.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_buckets_found(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": []})
        )
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_bucket_sync_error_continues(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.bucket_name = "mybucket"
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._sync_bucket = AsyncMock(side_effect=Exception("sync failed"))
        await connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_with_bucket_filter(self, mock_filters, connector):
        mock_filter = MagicMock()
        mock_filter.value = ["filtered-bucket"]
        fc = MagicMock(spec=FilterCollection)
        fc.get = MagicMock(return_value=mock_filter)
        mock_filters.return_value = (fc, FilterCollection())
        connector.data_source = MagicMock()
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._sync_bucket = AsyncMock()
        await connector.run_incremental_sync()
        connector._sync_bucket.assert_awaited_once_with("filtered-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_failure(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(False, error="denied"))
        await connector.run_incremental_sync()


class TestSyncBucketFullCoverage:
    @pytest.mark.asyncio
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_access_denied(self, connector):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(False, error="AccessDenied: not authorized")
        )
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_generic_list_error(self, connector):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(False, error="InternalServerError")
        )
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_no_contents(self, connector):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {})
        )
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_processes_objects(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [
                    {"Key": "file.txt", "LastModified": now, "ETag": '"e1"', "Size": 100},
                ],
                "IsTruncated": False,
            })
        )
        connector._process_s3_object = AsyncMock(return_value=(_file_record(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("mybucket")
        mock_dep.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_file_extension_filter_list(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [
                    {"Key": "doc.pdf", "LastModified": now, "ETag": '"e1"', "Size": 100},
                    {"Key": "img.jpg", "LastModified": now, "ETag": '"e2"', "Size": 200},
                ],
                "IsTruncated": False,
            })
        )
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["pdf"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_ext_filter if k == "file_extensions" else None)
        connector.sync_filters.__bool__ = MagicMock(return_value=True)
        connector._process_s3_object = AsyncMock(return_value=(_file_record(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_file_extension_filter_string(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [{"Key": "doc.pdf", "LastModified": now, "ETag": '"e1"', "Size": 100}],
                "IsTruncated": False,
            })
        )
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ".pdf"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_ext_filter if k == "file_extensions" else None)
        connector.sync_filters.__bool__ = MagicMock(return_value=True)
        connector._process_s3_object = AsyncMock(return_value=(_file_record(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_pagination_with_continuation(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        page1 = _resp(True, {
            "Contents": [{"Key": "file1.txt", "LastModified": now, "ETag": '"e1"', "Size": 10}],
            "IsTruncated": True,
            "NextContinuationToken": "token123",
        })
        page2 = _resp(True, {
            "Contents": [{"Key": "file2.txt", "LastModified": now, "ETag": '"e2"', "Size": 20}],
            "IsTruncated": False,
        })
        connector.data_source.list_objects_v2 = AsyncMock(side_effect=[page1, page2])
        connector._process_s3_object = AsyncMock(return_value=(_file_record(), []))
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("mybucket")
        assert connector.data_source.list_objects_v2.await_count == 2

    @pytest.mark.asyncio
    async def test_with_sync_point(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"continuation_token": "prev-token", "last_sync_time": 1000}
        )
        connector.record_sync_point.update_sync_point = AsyncMock()
        connector.data_source.list_objects_v2 = AsyncMock(return_value=_resp(True, {}))
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_process_object_exception_continues(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [
                    {"Key": "ok.txt", "LastModified": now, "ETag": '"e1"', "Size": 10},
                    {"Key": "fail.txt", "LastModified": now, "ETag": '"e2"', "Size": 20},
                ],
                "IsTruncated": False,
            })
        )
        call_count = 0

        async def _process_side_effect(obj, bucket):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("process error")
            return (_file_record(), [])

        connector._process_s3_object = AsyncMock(side_effect=_process_side_effect)
        connector._ensure_parent_folders_exist = AsyncMock()
        await connector._sync_bucket("mybucket")

    @pytest.mark.asyncio
    async def test_folder_objects_not_extension_filtered(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [{"Key": "folder/", "LastModified": now, "ETag": "", "Size": 0}],
                "IsTruncated": False,
            })
        )
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["pdf"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_ext_filter if k == "file_extensions" else None)
        connector.sync_filters.__bool__ = MagicMock(return_value=True)
        connector._process_s3_object = AsyncMock(return_value=(_file_record(is_file=False), []))
        await connector._sync_bucket("mybucket")
        connector._process_s3_object.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_no_extension_file(self, connector, mock_dep):
        connector.data_source = MagicMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.record_sync_point.update_sync_point = AsyncMock()
        now = datetime.now(timezone.utc)
        connector.data_source.list_objects_v2 = AsyncMock(
            return_value=_resp(True, {
                "Contents": [{"Key": "Makefile", "LastModified": now, "ETag": '"e1"', "Size": 10}],
                "IsTruncated": False,
            })
        )
        mock_ext_filter = MagicMock()
        mock_ext_filter.is_empty.return_value = False
        mock_ext_filter.value = ["pdf"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_ext_filter if k == "file_extensions" else None)
        connector.sync_filters.__bool__ = MagicMock(return_value=True)
        connector._process_s3_object = AsyncMock()
        await connector._sync_bucket("mybucket")
        connector._process_s3_object.assert_not_awaited()


class TestProcessS3ObjectAdvanced:
    @pytest.mark.asyncio
    async def test_slash_only_key(self, connector):
        record, perms = await connector._process_s3_object({"Key": "/"}, "bucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_existing_record_same_revision(self, connector, mock_dsp):
        mock_tx = mock_dsp.transaction.return_value
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "mybucket/abc123"
        existing.external_record_id = "mybucket/path/file.txt"
        existing.version = 2
        existing.source_created_at = 1000
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._create_s3_permissions = AsyncMock(return_value=[])
        now = datetime.now(timezone.utc)
        obj = {"Key": "path/file.txt", "LastModified": now, "ETag": '"abc123"', "Size": 100}
        record, perms = await connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.id == "existing-id"
        assert record.version == 3

    @pytest.mark.asyncio
    async def test_move_rename_detection(self, connector, mock_dsp):
        mock_tx = mock_dsp.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        existing = MagicMock()
        existing.id = "moved-id"
        existing.external_revision_id = "mybucket/abc123"
        existing.external_record_id = "mybucket/old/path/file.txt"
        existing.version = 1
        existing.source_created_at = 500
        mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=existing)
        connector._create_s3_permissions = AsyncMock(return_value=[])
        now = datetime.now(timezone.utc)
        obj = {"Key": "new/path/file.txt", "LastModified": now, "ETag": '"abc123"', "Size": 100}
        record, perms = await connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.id == "moved-id"
        mock_tx.delete_parent_child_edge_to_record.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_last_modified(self, connector):
        connector._create_s3_permissions = AsyncMock(return_value=[])
        obj = {"Key": "file.txt", "ETag": '"e1"', "Size": 50}
        record, _ = await connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, connector):
        connector._create_s3_permissions = AsyncMock(return_value=[])
        obj = {"Key": "file.txt", "LastModified": "2025-01-01", "ETag": '"e1"', "Size": 50}
        record, _ = await connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_root_level_file_no_parent(self, connector):
        connector._create_s3_permissions = AsyncMock(return_value=[])
        now = datetime.now(timezone.utc)
        obj = {"Key": "root-file.txt", "LastModified": now, "ETag": '"e1"', "Size": 50}
        record, _ = await connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_existing_record_missing_etag(self, connector, mock_dsp):
        mock_tx = mock_dsp.transaction.return_value
        existing = MagicMock()
        existing.id = "eid"
        existing.external_revision_id = ""
        existing.external_record_id = "mybucket/file.txt"
        existing.version = 0
        existing.source_created_at = 1000
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._create_s3_permissions = AsyncMock(return_value=[])
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now, "ETag": "", "Size": 10}
        record, _ = await connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_exception_handling(self, connector, mock_dsp):
        mock_tx = mock_dsp.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB error"))
        obj = {"Key": "file.txt", "LastModified": datetime.now(timezone.utc), "ETag": '"e1"', "Size": 10}
        record, perms = await connector._process_s3_object(obj, "mybucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_indexing_filter_auto_off(self, connector):
        connector._create_s3_permissions = AsyncMock(return_value=[])
        mock_idx = MagicMock()
        mock_idx.is_enabled = MagicMock(return_value=False)
        mock_idx.__bool__ = MagicMock(return_value=True)
        connector.indexing_filters = mock_idx
        now = datetime.now(timezone.utc)
        obj = {"Key": "file.txt", "LastModified": now, "ETag": '"e1"', "Size": 10}
        record, _ = await connector._process_s3_object(obj, "mybucket")
        assert record is not None
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestGetDateFiltersWithValues:
    def test_modified_and_created_filters(self, connector):
        mock_modified = MagicMock()
        mock_modified.is_empty.return_value = False
        mock_modified.get_datetime_iso.return_value = ("2025-01-01T00:00:00", "2025-06-01T00:00:00")
        mock_created = MagicMock()
        mock_created.is_empty.return_value = False
        mock_created.get_datetime_iso.return_value = ("2025-02-01T00:00:00", "2025-05-01T00:00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: {
            "modified": mock_modified,
            "created": mock_created,
        }.get(k))
        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is not None
        assert mb is not None
        assert ca is not None
        assert cb is not None

    def test_partial_modified_after_only(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2025-03-01T00:00:00", None)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: mock_filter if k == "modified" else None)
        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is not None
        assert mb is None

    def test_filter_is_empty(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = True
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        ma, mb, ca, cb = connector._get_date_filters()
        assert all(x is None for x in (ma, mb, ca, cb))


class TestRunSyncAdvanced:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_with_bucket_filter(self, mock_filters, connector, mock_dep):
        mock_filter = MagicMock()
        mock_filter.value = ["selected-bucket"]
        fc = MagicMock(spec=FilterCollection)
        fc.get = MagicMock(return_value=mock_filter)
        mock_filters.return_value = (fc, FilterCollection())
        connector.data_source = MagicMock()
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock()
        await connector.run_sync()
        connector._sync_bucket.assert_awaited_once_with("selected-bucket")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_buckets_failure(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(False, error="denied"))
        await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_buckets_found(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(return_value=_resp(True, {}))
        await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.s3.base_connector.load_connector_filters", new_callable=AsyncMock)
    async def test_none_bucket_skipped(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_buckets = AsyncMock(
            return_value=_resp(True, {"Buckets": [{"Name": None}, {"Name": "valid"}]})
        )
        connector._get_bucket_region = AsyncMock(return_value="us-east-1")
        connector._create_record_groups_for_buckets = AsyncMock()
        connector._sync_bucket = AsyncMock()
        await connector.run_sync()
        connector._sync_bucket.assert_awaited_once_with("valid")


class TestEntitiesProcessorEdge:
    def test_default_parent_url_generator(self, mock_logger_fullcov, mock_dsp, mock_cs):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger_fullcov, data_store_provider=mock_dsp, config_service=mock_cs,
        )
        url = proc.parent_url_generator("mybucket/folder")
        assert "s3.console.aws.amazon.com" in url
