"""Full coverage tests for app.connectors.sources.atlassian.confluence_cloud.connector."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection, FilterOperator, SyncFilterKey
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    PSEUDO_USER_GROUP_PREFIX,
    ConfluenceConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_mock_deps():
    logger = logging.getLogger("test.confluence.full")
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.migrate_group_to_user_by_external_id = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
    mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    dsp.transaction.return_value = mock_tx

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs, mock_tx


def _c():
    logger, dep, dsp, cs, _ = _make_mock_deps()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-1")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


class TestParseConfluenceDatetime:
    def test_valid(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(result, int)
        assert result > 0

    def test_invalid(self):
        c = _c()
        assert c._parse_confluence_datetime("not-a-date") is None

    def test_with_timezone(self):
        c = _c()
        result = c._parse_confluence_datetime("2025-01-01T00:00:00+00:00")
        assert isinstance(result, int)


class TestConstructWebUrl:
    def test_with_base_url(self):
        c = _c()
        links = {"webui": "/spaces/ENG/pages/123"}
        assert c._construct_web_url(links, "https://company.atlassian.net/wiki") == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_without_base_url_v1_fallback(self):
        c = _c()
        links = {"webui": "/pages/123", "self": "https://company.atlassian.net/wiki/rest/api/content/123"}
        result = c._construct_web_url(links)
        assert result == "https://company.atlassian.net/wiki/pages/123"

    def test_no_webui(self):
        c = _c()
        assert c._construct_web_url({}) is None

    def test_no_base_no_self(self):
        c = _c()
        links = {"webui": "/page"}
        assert c._construct_web_url(links) is None


class TestMapConfluencePermission:
    def test_administer(self):
        c = _c()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _c()
        assert c._map_confluence_permission("read", "space") == PermissionType.READ

    def test_delete_space(self):
        c = _c()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_page(self):
        c = _c()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_archive_blogpost(self):
        c = _c()
        assert c._map_confluence_permission("archive", "blogpost") == PermissionType.WRITE

    def test_delete_attachment(self):
        c = _c()
        assert c._map_confluence_permission("delete", "attachment") == PermissionType.WRITE

    def test_export_fallback(self):
        c = _c()
        assert c._map_confluence_permission("export", "space") == PermissionType.OTHER


class TestMapPagePermission:
    def test_read(self):
        c = _c()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _c()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _c()
        assert c._map_page_permission("magic") == PermissionType.OTHER


class TestTransformToAppUser:
    def test_valid(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@test.com", "displayName": "User"})
        assert user is not None
        assert user.email == "u@test.com"

    def test_no_account_id(self):
        c = _c()
        assert c._transform_to_app_user({"email": "u@test.com"}) is None

    def test_no_email(self):
        c = _c()
        assert c._transform_to_app_user({"accountId": "u1"}) is None

    def test_with_last_modified(self):
        c = _c()
        user = c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "displayName": "User One", "lastModified": "2025-01-01T00:00:00Z"})
        assert user is not None
        assert user.source_updated_at is not None

    def test_exception_returns_none(self):
        c = _c()
        with patch.object(c, "_parse_confluence_datetime", side_effect=Exception("fail")):
            assert c._transform_to_app_user({"accountId": "u1", "email": "u@t.com", "lastModified": "bad"}) is None


class TestTransformToUserGroup:
    def test_valid(self):
        c = _c()
        g = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert g is not None
        assert g.name == "devs"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_no_name(self):
        c = _c()
        assert c._transform_to_user_group({"id": "g1"}) is None


class TestTransformToSpaceRecordGroup:
    def test_valid_with_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG", "_links": {"webui": "/spaces/ENG"}, "createdAt": "2025-01-01T00:00:00Z"}
        rg = c._transform_to_space_record_group(data, "https://company.atlassian.net/wiki")
        assert rg is not None
        assert rg.name == "Engineering"
        assert "ENG" in rg.web_url

    def test_without_base_url(self):
        c = _c()
        data = {"id": "s1", "name": "Engineering", "key": "ENG"}
        rg = c._transform_to_space_record_group(data)
        assert rg is not None
        assert rg.web_url is None

    def test_missing_id(self):
        c = _c()
        assert c._transform_to_space_record_group({"name": "Eng"}) is None

    def test_missing_name(self):
        c = _c()
        assert c._transform_to_space_record_group({"id": "s1"}) is None


class TestTransformToWebpageRecord:
    def test_v2_format(self):
        c = _c()
        data = {
            "id": "p1", "title": "Test Page", "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-01-02T00:00:00Z", "number": 3},
            "spaceId": 100, "parentId": "p0",
            "_links": {"webui": "/page/p1", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert rec is not None
        assert rec.record_name == "Test Page"
        assert rec.external_record_group_id == "100"
        assert rec.parent_external_record_id == "p0"

    def test_v1_format(self):
        c = _c()
        data = {
            "id": "p2", "title": "Blog",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 5}},
            "space": {"id": 200}, "ancestors": [{"id": "root"}, {"id": "parent1"}],
            "_links": {"webui": "/blog", "self": "https://c.atlassian.net/wiki/rest/api/content/p2"},
        }
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert rec is not None
        assert rec.parent_external_record_id == "parent1"

    def test_no_space(self):
        c = _c()
        data = {"id": "p3", "title": "Orphan"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "3"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 3

    def test_existing_record_same_version(self):
        c = _c()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = {"id": "p1", "title": "Page", "version": {"number": 4}, "spaceId": 1, "_links": {}}
        rec = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE, existing)
        assert rec.version == 2


class TestTransformToAttachmentFileRecord:
    def test_v1_format(self):
        c = _c()
        data = {
            "id": "att-1", "title": "doc.pdf",
            "history": {"createdDate": "2025-01-01T00:00:00Z", "lastUpdated": {"when": "2025-02-01T00:00:00Z", "number": 2}},
            "extensions": {"fileSize": 1024, "mediaType": "application/pdf"},
            "_links": {"webui": "/att", "self": "https://c.atlassian.net/wiki/rest/api/content/att-1"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-1", "space-1")
        assert rec is not None
        assert rec.record_name == "doc.pdf"
        assert rec.extension == "pdf"
        assert rec.size_in_bytes == 1024

    def test_v2_format(self):
        c = _c()
        data = {
            "id": "att-2", "title": "img.png",
            "createdAt": "2025-01-01T00:00:00Z",
            "version": {"createdAt": "2025-03-01T00:00:00Z", "number": 1},
            "fileSize": 2048, "mediaType": "image/png",
            "_links": {"webui": "/att2", "base": "https://c.atlassian.net/wiki"},
        }
        rec = c._transform_to_attachment_file_record(data, "page-2", "space-2")
        assert rec is not None
        assert rec.extension == "png"

    def test_no_id(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"title": "f.txt"}, "p", "s") is None

    def test_no_title(self):
        c = _c()
        assert c._transform_to_attachment_file_record({"id": "att-3"}, "p", "s") is None

    def test_query_params_in_title(self):
        c = _c()
        data = {"id": "att-4", "title": "image.jpg?version=1", "mediaType": "image/jpeg", "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec.record_name == "image.jpg"

    def test_unknown_media_type(self):
        c = _c()
        data = {"id": "att-5", "title": "f.xyz", "extensions": {"mediaType": "application/x-custom"}, "_links": {}}
        rec = c._transform_to_attachment_file_record(data, "p", "s")
        assert rec is not None


class TestProcessWebpageWithUpdate:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _c()
        data = {"id": "p1", "title": "Page", "version": {"number": 1}, "spaceId": 1, "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.is_new is True
        assert result.record is not None

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _c()
        existing = MagicMock()
        existing.id = "e1"
        existing.version = 1
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {"id": "p1", "title": "Page", "version": {"number": 2}, "spaceId": 1, "parentId": "new-parent", "_links": {}}
        result = await c._process_webpage_with_update(data, RecordType.CONFLUENCE_PAGE, existing, [])
        assert result.content_changed is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _c()
        result = await c._process_webpage_with_update({}, RecordType.CONFLUENCE_PAGE, None, [])
        assert result.record is None


class TestCreatePermissionFromPrincipal:
    @pytest.mark.asyncio
    async def test_user_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_by_source_id = AsyncMock(return_value=MagicMock(email="u@t.com"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "u@t.com"

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _c()
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_with_pseudo_group(self):
        c = _c()
        c._create_pseudo_group = AsyncMock(return_value=MagicMock(source_user_group_id="u1"))
        perm = await c._create_permission_from_principal("user", "u1", PermissionType.READ, create_pseudo_group_if_missing=True)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        _, _, dsp, _, mock_tx = _make_mock_deps()
        c = _c()
        c.data_store_provider = dsp
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(source_user_group_id="g1"))
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _c()
        perm = await c._create_permission_from_principal("group", "g1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        c = _c()
        perm = await c._create_permission_from_principal("robot", "r1", PermissionType.READ)
        assert perm is None


class TestCreatePseudoGroup:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        result = await c._create_pseudo_group("account-123")
        assert result is not None
        assert PSEUDO_USER_GROUP_PREFIX in result.name
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        c = _c()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("db fail"))
        result = await c._create_pseudo_group("account-123")
        assert result is None


class TestTransformPageRestrictionToPermissions:
    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _c()
        result = await c._transform_page_restriction_to_permissions({})
        assert result == []

    @pytest.mark.asyncio
    async def test_users_and_groups(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "u1"}]},
                "group": {"results": [{"id": "g1"}]},
            },
        }
        result = await c._transform_page_restriction_to_permissions(data)
        assert len(result) == 2


class TestTransformSpacePermission:
    @pytest.mark.asyncio
    async def test_valid(self):
        c = _c()
        c._create_permission_from_principal = AsyncMock(return_value=Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com"))
        data = {"principal": {"type": "user", "id": "u1"}, "operation": {"key": "read", "targetType": "space"}}
        result = await c._transform_space_permission(data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_fields(self):
        c = _c()
        assert await c._transform_space_permission({}) is None


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "u@t.com", "displayName": "U"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "devs")
        assert "u@t.com" in emails

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == []

    @pytest.mark.asyncio
    async def test_skips_no_email(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [{"email": "", "displayName": "NoEmail"}], "size": 1,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await c._fetch_group_members("g1", "devs") == []


class TestGetAppUsersByEmails:
    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _c()
        u1 = MagicMock(email="a@t.com")
        u2 = MagicMock(email="b@t.com")
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_list(self):
        c = _c()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_error(self):
        c = _c()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        assert await c._get_app_users_by_emails(["a@t.com"]) == []


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _c()
        assert await c.get_signed_url(MagicMock()) == ""


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_page_stream(self):
        c = _c()
        c._fetch_page_data_with_adf = AsyncMock(return_value={"id": "p1", "body": {"atlas_doc_format": {"value": "{}"}}})
        c._process_page_attachments_for_children = AsyncMock(return_value={})
        c._parse_confluence_page_to_blocks = AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}")))
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.record_name = "Test"
        record.external_record_id = "p1"
        record.weburl = "http://example.com"
        record.id = "rec-1"
        record.external_record_group_id = "space-1"
        resp = await c.stream_record(record)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock()
        record.record_type = RecordType.OTHERS
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c.stream_record(record)


class TestFetchPageContent:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Content</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "<p>Content</p>" in result

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert "<p>Blog</p>" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404, {}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _c()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_with_updated_and_non_updated(self):
        c = _c()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        rec1 = MagicMock(id="r1", record_type=RecordType.CONFLUENCE_PAGE)
        rec2 = MagicMock(id="r2", record_type=RecordType.CONFLUENCE_PAGE)

        updated = (MagicMock(), [Permission(type=PermissionType.READ, entity_type=EntityType.USER, email="u@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[updated, None])

        await c.reindex_records([rec1, rec2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_dispatches_page(self):
        c = _c()
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_PAGE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_blogpost(self):
        c = _c()
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.CONFLUENCE_BLOGPOST)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_file(self):
        c = _c()
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        record = MagicMock(record_type=RecordType.FILE)
        await c._check_and_fetch_updated_record("org-1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _c()
        record = MagicMock(record_type="UNKNOWN")
        result = await c._check_and_fetch_updated_record("org-1", record)
        assert result is None


class TestCheckAndFetchUpdatedPage:
    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 5},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        record.id = "e1"
        record.version = 1
        record.parent_external_record_id = None

        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "id": "p1", "title": "Page", "version": {"number": 3},
            "spaceId": 1, "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "3"
        result = await c._check_and_fetch_updated_page("org-1", record)
        assert result is None


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _c()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _c()
        await c.cleanup()


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_does_nothing(self):
        c = _c()
        await c.handle_webhook_notification({"type": "test"})


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _c()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _c()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _c()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _c()
        with pytest.raises(ValueError):
            await c.get_filter_options("unknown_key")


class TestGetSpaceOptions:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "ENG", "name": "Engineering"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "Eng", None)
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {"next": "/wiki/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert result.has_more is True


class TestGetPageOptions:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "My Page", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "My", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert result.success is True


class TestGetBlogpostOptions:
    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Blog", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Blog", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_without_search(self):
        c = _c()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Post 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert result.success is True


class TestSyncSpacesIncludeFilter:
    @pytest.mark.asyncio
    async def test_in_filter(self):
        c = _c()
        c.indexing_filters = FilterCollection()
        space_filter = MagicMock()
        space_filter.get_operator.return_value = FilterOperator.IN
        space_filter.get_value.return_value = ["ENG"]
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(side_effect=lambda k: space_filter if k == SyncFilterKey.SPACE_KEYS else None)

        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "1", "key": "ENG", "name": "Engineering"}],
            "_links": {"base": "https://c.atlassian.net/wiki"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_space_permissions = AsyncMock(return_value=[])
        c._transform_to_space_record_group = MagicMock(return_value=RecordGroup(
            external_group_id="1", name="Engineering", short_name="ENG",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            connector_name=Connectors.CONFLUENCE, connector_id="conn-1",
        ))

        spaces = await c._sync_spaces()
        assert len(spaces) == 1
        mock_ds.get_spaces.assert_awaited_once()
        call_kwargs = mock_ds.get_spaces.call_args
        assert call_kwargs[1].get("keys") == ["ENG"]
