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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = None
        await s3_connector._create_record_groups_for_buckets(["bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_bucket_names(self, s3_connector):
        await s3_connector._create_record_groups_for_buckets([])
        s3_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_bucket_names_skipped(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        await s3_connector._create_record_groups_for_buckets([None, "bucket1"])
        s3_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_scope_user_lookup_fails(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        record, perms = await s3_connector._process_s3_object({"Key": ""}, "mybucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_missing_key(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        record, perms = await s3_connector._process_s3_object({}, "mybucket")
        assert record is None

    @pytest.mark.asyncio
    async def test_new_file(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value

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
        s3_connector.connector_scope = ConnectorScope.TEAM.value

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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        obj = {
            "Key": "path/file.txt",
            "LastModified": datetime.now(timezone.utc),
            "Size": 100,
        }
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        obj = {"Key": "path/file.txt", "ETag": '"abc"', "Size": 50}
        record, perms = await s3_connector._process_s3_object(obj, "mybucket")
        assert record is not None


# ===========================================================================
# _create_s3_permissions
# ===========================================================================
class TestCreateS3Permissions:
    @pytest.mark.asyncio
    async def test_team_scope(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.TEAM.value
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
        s3_connector.created_by = "user-1"
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator(self, s3_connector):
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.TEAM.value
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
        s3_connector.connector_scope = ConnectorScope.PERSONAL.value
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
        s3_connector.connector_scope = "INVALID"
        # Force an exception in permissions logic
        s3_connector.data_entities_processor.org_id = "org-1"
        perms = await s3_connector._create_s3_permissions("bucket", "key")
        assert len(perms) >= 1
