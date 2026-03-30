"""Full coverage tests for app.connectors.sources.s3.base_connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    FilterOptionsResponse,
    IndexingFilterKey,
)
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
from app.models.entities import FileRecord, Record, RecordType


@pytest.fixture()
def mock_logger():
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
    u = MagicMock()
    u.email = "user@test.com"
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
def connector(mock_logger, mock_dep, mock_dsp, mock_cs):
    with patch("app.connectors.sources.s3.connector.S3App"):
        c = S3Connector(
            logger=mock_logger,
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


class TestCreateS3Permissions:
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
        mock_tx.get_user_by_id = AsyncMock(side_effect=Exception("DB error"))
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @pytest.mark.asyncio
    async def test_personal_scope_user_no_email(self, connector, mock_dsp):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        mock_tx = mock_dsp.transaction.return_value
        mock_tx.get_user_by_id = AsyncMock(return_value={"email": ""})
        perms = await connector._create_s3_permissions("bucket", "key")
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


class TestTestConnectionAndAccess:
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


class TestGetSignedUrl:
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


class TestStreamRecord:
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


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_data_source(self, connector):
        connector.data_source = MagicMock()
        await connector.cleanup()
        assert connector.data_source is None


class TestGetFilterOptions:
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


class TestHandleWebhookNotification:
    def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})


class TestReindexRecords:
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


class TestCheckAndFetchUpdatedRecord:
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


class TestRunIncrementalSync:
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


class TestSyncBucket:
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
    def test_default_parent_url_generator(self, mock_logger, mock_dsp, mock_cs):
        proc = S3CompatibleDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_dsp, config_service=mock_cs,
        )
        url = proc.parent_url_generator("mybucket/folder")
        assert "s3.console.aws.amazon.com" in url
