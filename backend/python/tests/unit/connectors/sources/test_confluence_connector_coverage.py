"""Extended coverage tests for app.connectors.sources.atlassian.confluence_cloud.connector.

Targets uncovered lines: transform methods, permission mapping, streaming,
reindex, filter options, and edge cases in sync flows.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    CONTENT_EXPAND_PARAMS,
    PSEUDO_USER_GROUP_PREFIX,
    TIME_OFFSET_HOURS,
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


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.confluence.coverage")
    dep = MagicMock()
    dep.org_id = "org-cov-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.migrate_group_to_user_by_external_id = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()

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
    return logger, dep, dsp, cs


def _conn():
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-cov-1")


def _resp(status=200, data=None):
    r = MagicMock()
    r.status = status
    r.json = MagicMock(return_value=data or {})
    r.text = MagicMock(return_value="")
    return r


# ===========================================================================
# _parse_confluence_datetime
# ===========================================================================


class TestParseConfluenceDatetime:
    def test_valid_z_suffix(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526Z")
        assert isinstance(ts, int)
        assert ts > 0

    def test_valid_offset(self):
        c = _conn()
        ts = c._parse_confluence_datetime("2025-11-13T07:51:50.526+00:00")
        assert isinstance(ts, int)
        assert ts > 0

    def test_invalid_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("not-a-date")
        assert ts is None

    def test_empty_string(self):
        c = _conn()
        ts = c._parse_confluence_datetime("")
        assert ts is None


# ===========================================================================
# _transform_to_app_user
# ===========================================================================


class TestTransformToAppUser:
    def test_creates_app_user(self):
        c = _conn()
        user_data = {
            "accountId": "acc-1",
            "email": "alice@example.com",
            "displayName": "Alice Smith",
            "lastModified": "2025-01-01T00:00:00.000Z",
        }
        result = c._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "alice@example.com"
        assert result.full_name == "Alice Smith"
        assert result.source_user_id == "acc-1"

    def test_missing_account_id(self):
        c = _conn()
        result = c._transform_to_app_user({"email": "bob@example.com"})
        assert result is None

    def test_missing_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-2"})
        assert result is None

    def test_whitespace_email(self):
        c = _conn()
        result = c._transform_to_app_user({"accountId": "acc-3", "email": "  "})
        assert result is None

    def test_no_last_modified(self):
        c = _conn()
        result = c._transform_to_app_user({
            "accountId": "acc-4", "email": "test@test.com",
            "displayName": "Test User"
        })
        assert result is not None
        assert result.source_updated_at is None


# ===========================================================================
# _transform_to_user_group
# ===========================================================================


class TestTransformToUserGroup:
    def test_creates_group(self):
        c = _conn()
        result = c._transform_to_user_group({"id": "g1", "name": "devs"})
        assert result is not None
        assert result.name == "devs"
        assert result.source_user_group_id == "g1"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_user_group({"name": "devs"}) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_user_group({"id": "g1"}) is None

    def test_both_missing(self):
        c = _conn()
        assert c._transform_to_user_group({}) is None


# ===========================================================================
# _transform_to_space_record_group
# ===========================================================================


class TestTransformToSpaceRecordGroup:
    def test_full_space(self):
        c = _conn()
        data = {
            "id": "100",
            "name": "Engineering",
            "key": "ENG",
            "description": "Eng space",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "_links": {"webui": "/spaces/ENG"},
        }
        result = c._transform_to_space_record_group(data, "https://wiki.example.com")
        assert result is not None
        assert result.name == "Engineering"
        assert result.short_name == "ENG"
        assert result.web_url == "https://wiki.example.com/spaces/ENG"

    def test_no_base_url(self):
        c = _conn()
        data = {"id": "101", "name": "Test", "key": "TST"}
        result = c._transform_to_space_record_group(data, None)
        assert result is not None
        assert result.web_url is None

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_space_record_group({"name": "T"}, None) is None

    def test_missing_name(self):
        c = _conn()
        assert c._transform_to_space_record_group({"id": "1"}, None) is None


# ===========================================================================
# _map_confluence_permission
# ===========================================================================


class TestMapConfluencePermission:
    def test_administer(self):
        c = _conn()
        assert c._map_confluence_permission("administer", "space") == PermissionType.OWNER

    def test_read(self):
        c = _conn()
        assert c._map_confluence_permission("read", "page") == PermissionType.READ

    def test_delete_space(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "space") == PermissionType.OWNER

    def test_create_page(self):
        c = _conn()
        assert c._map_confluence_permission("create", "page") == PermissionType.WRITE

    def test_delete_blogpost(self):
        c = _conn()
        assert c._map_confluence_permission("delete", "blogpost") == PermissionType.WRITE

    def test_archive_attachment(self):
        c = _conn()
        assert c._map_confluence_permission("archive", "attachment") == PermissionType.WRITE

    def test_restrict_content_uses_default_read(self):
        c = _conn()
        assert c._map_confluence_permission("restrict_content", "space") == PermissionType.READ

    def test_export_uses_default_read(self):
        c = _conn()
        assert c._map_confluence_permission("export", "space") == PermissionType.READ


# ===========================================================================
# _map_page_permission
# ===========================================================================


class TestMapPagePermission:
    def test_read(self):
        c = _conn()
        assert c._map_page_permission("read") == PermissionType.READ

    def test_update(self):
        c = _conn()
        assert c._map_page_permission("update") == PermissionType.WRITE

    def test_unknown(self):
        c = _conn()
        assert c._map_page_permission("delete") == PermissionType.READ


# ===========================================================================
# _construct_web_url
# ===========================================================================


class TestConstructWebUrl:
    def test_v2_with_base_url(self):
        c = _conn()
        links = {"webui": "/spaces/ENG/pages/123"}
        url = c._construct_web_url(links, "https://wiki.example.com")
        assert url == "https://wiki.example.com/spaces/ENG/pages/123"

    def test_v1_with_self_link(self):
        c = _conn()
        links = {
            "webui": "/spaces/ENG/pages/123",
            "self": "https://company.atlassian.net/wiki/rest/api/content/123",
        }
        url = c._construct_web_url(links, None)
        assert url == "https://company.atlassian.net/wiki/spaces/ENG/pages/123"

    def test_no_webui(self):
        c = _conn()
        url = c._construct_web_url({}, "https://base.com")
        assert url is None

    def test_no_base_no_self(self):
        c = _conn()
        links = {"webui": "/some/path"}
        url = c._construct_web_url(links, None)
        assert url is None

    def test_self_link_without_wiki(self):
        c = _conn()
        links = {"webui": "/path", "self": "https://other.com/api/content/1"}
        url = c._construct_web_url(links, None)
        assert url is None


# ===========================================================================
# _transform_to_webpage_record
# ===========================================================================


class TestTransformToWebpageRecord:
    def _page_data(self, **overrides):
        data = {
            "id": "page-1",
            "title": "Test Page",
            "space": {"id": 100},
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 5},
            },
            "ancestors": [{"id": "parent-1"}],
            "_links": {
                "webui": "/spaces/ENG/pages/1",
                "self": "https://company.atlassian.net/wiki/rest/api/content/1",
            },
        }
        data.update(overrides)
        return data

    def test_v1_format(self):
        c = _conn()
        result = c._transform_to_webpage_record(
            self._page_data(), RecordType.CONFLUENCE_PAGE
        )
        assert result is not None
        assert result.record_name == "Test Page"
        assert result.external_record_id == "page-1"
        assert result.parent_external_record_id == "parent-1"
        assert result.external_revision_id == "5"
        assert result.version == 0

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "page-v2",
            "title": "V2 Page",
            "spaceId": 200,
            "parentId": "parent-v2",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 3},
            "_links": {"webui": "/p", "base": "https://wiki.com"},
        }
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_BLOGPOST)
        assert result is not None
        assert result.external_record_group_id == "200"
        assert result.parent_external_record_id == "parent-v2"
        assert result.record_type == RecordType.CONFLUENCE_BLOGPOST

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_webpage_record({"title": "t"}, RecordType.CONFLUENCE_PAGE) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_webpage_record({"id": "1"}, RecordType.CONFLUENCE_PAGE) is None

    def test_no_space(self):
        c = _conn()
        data = {"id": "1", "title": "T"}
        assert c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE) is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "4"
        data = self._page_data()  # version=5
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result is not None
        assert result.id == "existing-id"
        assert result.version == 3  # bumped from 2

    def test_existing_record_no_version_change(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.external_revision_id = "5"  # same as data
        data = self._page_data()
        result = c._transform_to_webpage_record(
            data, RecordType.CONFLUENCE_PAGE, existing_record=existing
        )
        assert result.version == 2  # unchanged

    def test_no_ancestors_no_parent(self):
        c = _conn()
        data = self._page_data()
        data.pop("ancestors", None)
        result = c._transform_to_webpage_record(data, RecordType.CONFLUENCE_PAGE)
        assert result is not None
        assert result.parent_external_record_id is None


# ===========================================================================
# _transform_to_attachment_file_record
# ===========================================================================


class TestTransformToAttachmentFileRecord:
    def _attachment_data(self, **overrides):
        data = {
            "id": "att-1",
            "title": "report.pdf",
            "history": {
                "createdDate": "2025-01-01T00:00:00.000Z",
                "lastUpdated": {"when": "2025-02-01T00:00:00.000Z", "number": 2},
            },
            "extensions": {"fileSize": 12345, "mediaType": "application/pdf"},
            "_links": {
                "webui": "/att/report.pdf",
                "self": "https://company.atlassian.net/wiki/rest/api/content/att-1",
            },
        }
        data.update(overrides)
        return data

    def test_basic_attachment(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "parent-1", "space-1"
        )
        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.extension == "pdf"
        assert result.size_in_bytes == 12345
        assert result.is_file is True
        assert result.is_dependent_node is True

    def test_v2_format(self):
        c = _conn()
        data = {
            "id": "att-v2",
            "title": "image.png",
            "createdAt": "2025-01-01T00:00:00.000Z",
            "version": {"createdAt": "2025-02-01T00:00:00.000Z", "number": 1},
            "fileSize": 5000,
            "mediaType": "image/png",
            "_links": {"webui": "/att", "base": "https://wiki.com"},
        }
        result = c._transform_to_attachment_file_record(data, "p1", "s1")
        assert result is not None
        assert result.extension == "png"

    def test_missing_id(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"title": "file.txt"}, "p", "s"
        ) is None

    def test_missing_title(self):
        c = _conn()
        assert c._transform_to_attachment_file_record(
            {"id": "att-1"}, "p", "s"
        ) is None

    def test_filename_with_query_params(self):
        c = _conn()
        data = self._attachment_data(title="file.pdf?version=1")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.record_name == "file.pdf"

    def test_no_extension(self):
        c = _conn()
        data = self._attachment_data(title="README")
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result.extension is None

    def test_existing_record_version_bump(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "ex-att"
        existing.version = 1
        existing.external_revision_id = "1"  # old version
        data = self._attachment_data()  # version=2
        result = c._transform_to_attachment_file_record(
            data, "p", "s", existing_record=existing
        )
        assert result.id == "ex-att"
        assert result.version == 2

    def test_parent_node_id(self):
        c = _conn()
        result = c._transform_to_attachment_file_record(
            self._attachment_data(), "p1", "s1", parent_node_id="node-123"
        )
        assert result.parent_node_id == "node-123"

    def test_mime_type_from_metadata(self):
        c = _conn()
        data = {
            "id": "att-m",
            "title": "file.bin",
            "metadata": {"mediaType": "application/octet-stream"},
            "_links": {},
        }
        result = c._transform_to_attachment_file_record(data, "p", "s")
        assert result is not None


# ===========================================================================
# _create_permission_from_principal
# ===========================================================================


class TestCreatePermissionFromPrincipal:
    @pytest.mark.asyncio
    async def test_user_found(self):
        c = _conn()
        mock_user = MagicMock()
        mock_user.email = "alice@test.com"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=mock_user)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is not None
        assert perm.email == "alice@test.com"
        assert perm.entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=False
        )
        assert perm is None

    @pytest.mark.asyncio
    async def test_user_not_found_create_pseudo(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        pseudo_group = MagicMock()
        pseudo_group.source_user_group_id = "acc-1"
        c._create_pseudo_group = AsyncMock(return_value=pseudo_group)

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_user_not_found_existing_pseudo(self):
        c = _conn()
        pseudo = MagicMock()
        pseudo.source_user_group_id = "acc-1"
        mock_tx = MagicMock()
        mock_tx.get_user_by_source_id = AsyncMock(return_value=None)
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=pseudo)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal(
            "user", "acc-1", PermissionType.READ, create_pseudo_group_if_missing=True
        )
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_found(self):
        c = _conn()
        mock_group = MagicMock()
        mock_group.source_user_group_id = "grp-1"
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=mock_group)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-1", PermissionType.READ)
        assert perm is not None
        assert perm.entity_type == EntityType.GROUP
        assert perm.external_id == "grp-1"

    @pytest.mark.asyncio
    async def test_group_not_found(self):
        c = _conn()
        mock_tx = MagicMock()
        mock_tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction.return_value = mock_tx

        perm = await c._create_permission_from_principal("group", "grp-x", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_unknown_principal_type(self):
        c = _conn()
        perm = await c._create_permission_from_principal("role", "r1", PermissionType.READ)
        assert perm is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_store_provider.transaction.side_effect = Exception("DB error")
        perm = await c._create_permission_from_principal("user", "acc-1", PermissionType.READ)
        assert perm is None


# ===========================================================================
# _create_pseudo_group
# ===========================================================================


class TestCreatePseudoGroup:
    @pytest.mark.asyncio
    async def test_creates_and_saves(self):
        c = _conn()
        result = await c._create_pseudo_group("acc-no-email")
        assert result is not None
        assert result.name == f"{PSEUDO_USER_GROUP_PREFIX} acc-no-email"
        c.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        c.data_entities_processor.on_new_user_groups = AsyncMock(side_effect=Exception("fail"))
        result = await c._create_pseudo_group("acc-fail")
        assert result is None


# ===========================================================================
# _transform_space_permission
# ===========================================================================


class TestTransformSpacePermission:
    @pytest.mark.asyncio
    async def test_valid_permission(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        perm_data = {
            "principal": {"type": "user", "id": "acc-1"},
            "operation": {"key": "read", "targetType": "space"},
        }
        result = await c._transform_space_permission(perm_data)
        assert result is not None

    @pytest.mark.asyncio
    async def test_missing_principal(self):
        c = _conn()
        result = await c._transform_space_permission({"operation": {"key": "read"}})
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_operation(self):
        c = _conn()
        result = await c._transform_space_permission(
            {"principal": {"type": "user", "id": "1"}}
        )
        assert result is None


# ===========================================================================
# _transform_page_restriction_to_permissions
# ===========================================================================


class TestTransformPageRestrictionToPermissions:
    @pytest.mark.asyncio
    async def test_processes_users_and_groups(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(
            return_value=Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="u@t.com")
        )
        restriction = {
            "operation": "read",
            "restrictions": {
                "user": {"results": [{"accountId": "acc-1"}]},
                "group": {"results": [{"id": "grp-1"}]},
            },
        }
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_no_operation(self):
        c = _conn()
        perms = await c._transform_page_restriction_to_permissions({})
        assert perms == []

    @pytest.mark.asyncio
    async def test_empty_restrictions(self):
        c = _conn()
        c._create_permission_from_principal = AsyncMock(return_value=None)
        restriction = {"operation": "read", "restrictions": {}}
        perms = await c._transform_page_restriction_to_permissions(restriction)
        assert perms == []


# ===========================================================================
# _process_webpage_with_update
# ===========================================================================


class TestProcessWebpageWithUpdate:
    @pytest.mark.asyncio
    async def test_new_record(self):
        c = _conn()
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.is_new is True
        assert result.record is mock_record

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = None
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 2},  # changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.is_new is False
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_transform_fails(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result.record is None

    @pytest.mark.asyncio
    async def test_metadata_changed(self):
        c = _conn()
        existing = MagicMock()
        existing.external_revision_id = "1"
        existing.parent_external_record_id = "old-parent"
        data = {
            "id": "p1", "title": "T", "space": {"id": 1},
            "version": {"number": 1},
            "ancestors": [{"id": "new-parent"}],  # parent changed
            "_links": {},
        }
        mock_record = MagicMock()
        mock_record.external_record_id = "p1"
        c._transform_to_webpage_record = MagicMock(return_value=mock_record)

        result = await c._process_webpage_with_update(
            data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result.metadata_changed is True


# ===========================================================================
# _fetch_group_members
# ===========================================================================


class TestFetchGroupMembers:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(200, {
            "results": [
                {"email": "a@t.com", "displayName": "A"},
                {"email": "", "displayName": "NoEmail"},
            ],
            "size": 2,
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "Group 1")
        assert emails == ["a@t.com"]

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_group_members = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        emails = await c._fetch_group_members("g1", "G")
        assert emails == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c._get_fresh_datasource = AsyncMock(side_effect=Exception("fail"))
        emails = await c._fetch_group_members("g1", "G")
        assert emails == []


# ===========================================================================
# _get_app_users_by_emails
# ===========================================================================


class TestGetAppUsersByEmails:
    @pytest.mark.asyncio
    async def test_empty_emails(self):
        c = _conn()
        assert await c._get_app_users_by_emails([]) == []

    @pytest.mark.asyncio
    async def test_filters_by_email(self):
        c = _conn()
        u1 = MagicMock()
        u1.email = "a@t.com"
        u2 = MagicMock()
        u2.email = "b@t.com"
        c.data_entities_processor.get_all_app_users = AsyncMock(return_value=[u1, u2])
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert len(result) == 1
        assert result[0].email == "a@t.com"

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _conn()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=Exception("fail"))
        result = await c._get_app_users_by_emails(["a@t.com"])
        assert result == []


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_empty(self):
        c = _conn()
        result = await c.get_signed_url(MagicMock())
        assert result == ""


# ===========================================================================
# cleanup
# ===========================================================================


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _conn()
        await c.cleanup()  # should not raise


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_delegates_to_run_sync(self):
        c = _conn()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


# ===========================================================================
# handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_no_op(self):
        c = _conn()
        await c.handle_webhook_notification({})


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_space_keys(self):
        c = _conn()
        c._get_space_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("space_keys")
        c._get_space_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_page_ids(self):
        c = _conn()
        c._get_page_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("page_ids")
        c._get_page_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost_ids(self):
        c = _conn()
        c._get_blogpost_options = AsyncMock(return_value=MagicMock())
        await c.get_filter_options("blogpost_ids")
        c._get_blogpost_options.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        with pytest.raises(ValueError, match="Unsupported"):
            await c.get_filter_options("unknown_key")


# ===========================================================================
# _get_space_options
# ===========================================================================


class TestGetSpaceOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "ENG", "name": "Engineering"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].id == "ENG"

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_spaces_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"space": {"key": "HR", "name": "Human Resources"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, "HR", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_next_cursor(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"key": "A", "name": "A"}],
            "_links": {"next": "/api/v2/spaces?cursor=abc"},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_space_options(1, 20, None, None)
        assert result.has_more is True
        assert result.cursor == "abc"

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_spaces = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_space_options(1, 20, None, None)


# ===========================================================================
# _get_page_options / _get_blogpost_options
# ===========================================================================


class TestGetPageOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "p1", "title": "Page 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_pages_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "p1", "title": "Match", "type": "page"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_page_options(1, 20, "Match", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_pages = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_page_options(1, 20, None, None)


class TestGetBlogpostOptions:
    @pytest.mark.asyncio
    async def test_no_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "b1", "title": "Blog 1"}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, None, None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_with_search(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.search_blogposts_cql = AsyncMock(return_value=_resp(200, {
            "results": [{"content": {"id": "b1", "title": "Hit", "type": "blogpost"}}],
            "_links": {},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._get_blogpost_options(1, 20, "Hit", None)
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blog_posts = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(RuntimeError):
            await c._get_blogpost_options(1, 20, None, None)


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "Test"
        record.weburl = "http://example.com"
        record.id = "rec-1"
        record.external_record_group_id = "space-1"
        c._fetch_page_data_with_adf = AsyncMock(return_value={"id": "p1", "body": {"atlas_doc_format": {"value": "{}"}}})
        c._process_page_attachments_for_children = AsyncMock(return_value={})
        c._parse_confluence_page_to_blocks = AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}")))
        result = await c.stream_record(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stream_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        async def mock_fetch(r):
            yield b"data"

        c._fetch_attachment_content = mock_fetch
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.create_stream_record_response") as mock_create:
            mock_create.return_value = MagicMock()
            result = await c.stream_record(record)
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_exception(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.external_record_id = "p1"
        record.record_name = "T"
        c._fetch_page_content = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# _fetch_page_content
# ===========================================================================


class TestFetchPageContent:
    @pytest.mark.asyncio
    async def test_page_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<h1>Content</h1>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert result == "<h1>Content</h1>"

    @pytest.mark.asyncio
    async def test_blogpost_success(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_blogpost_content_v2 = AsyncMock(return_value=_resp(200, {
            "body": {"export_view": {"value": "<p>Blog</p>"}},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("b1", RecordType.CONFLUENCE_BLOGPOST)
        assert result == "<p>Blog</p>"

    @pytest.mark.asyncio
    async def test_empty_body(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {"body": {}}))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        result = await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert "No content" in result

    @pytest.mark.asyncio
    async def test_api_failure(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.CONFLUENCE_PAGE)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_type(self):
        c = _conn()
        mock_ds = MagicMock()
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await c._fetch_page_content("p1", RecordType.TICKET)
        assert exc_info.value.status_code == 400


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        c = _conn()
        await c.reindex_records([])
        c.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_initialized(self):
        c = _conn()
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()

        r1 = MagicMock()
        r1.id = "r1"
        r2 = MagicMock()
        r2.id = "r2"

        mock_updated = (MagicMock(), [Permission(entity_type=EntityType.USER, type=PermissionType.READ, email="x@t.com")])
        c._check_and_fetch_updated_record = AsyncMock(side_effect=[mock_updated, None])

        await c.reindex_records([r1, r2])
        c.data_entities_processor.on_record_content_update.assert_awaited_once()
        c.data_entities_processor.on_updated_record_permissions.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_exception_skips(self):
        c = _conn()
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("fail"))
        await c.reindex_records([MagicMock()])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_page(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        c._check_and_fetch_updated_page = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogpost(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_BLOGPOST
        c._check_and_fetch_updated_blogpost = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_blogpost.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_file(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.FILE
        c._check_and_fetch_updated_attachment = AsyncMock(return_value=None)
        await c._check_and_fetch_updated_record("org1", record)
        c._check_and_fetch_updated_attachment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.TICKET
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _conn()
        record = MagicMock()
        record.record_type = RecordType.CONFLUENCE_PAGE
        record.id = "r1"
        c._check_and_fetch_updated_page = AsyncMock(side_effect=Exception("fail"))
        result = await c._check_and_fetch_updated_record("org1", record)
        assert result is None


# ===========================================================================
# _check_and_fetch_updated_page
# ===========================================================================


class TestCheckAndFetchUpdatedPage:
    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_version_change(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 5},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_changed(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_page_content_v2 = AsyncMock(return_value=_resp(200, {
            "version": {"number": 6}, "space": {"id": 1}, "id": "p1", "title": "T",
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c._fetch_page_permissions = AsyncMock(return_value=[])
        mock_wr = MagicMock()
        mock_wr.inherit_permissions = True
        c._transform_to_webpage_record = MagicMock(return_value=mock_wr)

        record = MagicMock()
        record.external_record_id = "p1"
        record.external_revision_id = "5"
        result = await c._check_and_fetch_updated_page("org1", record)
        assert result is not None


# ===========================================================================
# _check_and_fetch_updated_attachment
# ===========================================================================


class TestCheckAndFetchUpdatedAttachment:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = None
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(404))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_version_unchanged(self):
        c = _conn()
        mock_ds = MagicMock()
        mock_ds.get_attachment_by_id = AsyncMock(return_value=_resp(200, {
            "version": {"number": 2},
        }))
        c._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        record = MagicMock()
        record.external_record_id = "att-1"
        record.parent_external_record_id = "p1"
        record.external_revision_id = "2"
        result = await c._check_and_fetch_updated_attachment("org1", record)
        assert result is None


# ===========================================================================
# create_connector (class method)
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_factory(self):
        with patch("app.connectors.sources.atlassian.confluence_cloud.connector.DataSourceEntitiesProcessor") as mock_dep:
            mock_instance = MagicMock()
            mock_instance.initialize = AsyncMock()
            mock_instance.org_id = "org-1"
            mock_dep.return_value = mock_instance

            logger = logging.getLogger("test")
            dsp = MagicMock()
            mock_tx = MagicMock()
            mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
            mock_tx.__aexit__ = AsyncMock(return_value=None)
            dsp.transaction.return_value = mock_tx
            cs = MagicMock()

            connector = await ConfluenceConnector.create_connector(logger, dsp, cs, "c1")
            assert isinstance(connector, ConfluenceConnector)
            mock_instance.initialize.assert_awaited_once()


# ===========================================================================
# _extract_cursor_from_next_link
# ===========================================================================
class TestExtractCursorFromNextLink:
    def test_valid_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20&cursor=eyJpZCI6Ijk5In0=")
        assert result == "eyJpZCI6Ijk5In0="

    def test_no_cursor(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("/wiki/api/v2/spaces?limit=20")
        assert result is None

    def test_empty_string(self):
        c = _conn()
        result = c._extract_cursor_from_next_link("")
        assert result is None

    def test_none(self):
        c = _conn()
        result = c._extract_cursor_from_next_link(None)
        assert result is None


# ===========================================================================
# _extract_content_title_from_audit_record
# ===========================================================================
class TestExtractContentTitleFromAuditRecord:
    def test_non_permission_category(self):
        c = _conn()
        result = c._extract_content_title_from_audit_record({"category": "Security"})
        assert result is None

    def test_no_space(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Page", "name": "My Page"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_no_content(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [{"objectType": "Space", "name": "My Space"}]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result is None

    def test_valid_page_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Page", "name": "My Page"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Page"

    def test_blog_permission(self):
        c = _conn()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "My Space"},
                {"objectType": "Blog", "name": "My Blog"}
            ]
        }
        result = c._extract_content_title_from_audit_record(record)
        assert result == "My Blog"


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = None
        c.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await c.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_successful_sync(self, mock_filters):
        c = _conn()
        mock_filters.return_value = (MagicMock(), MagicMock())
        c.external_client = MagicMock()
        c.data_source = MagicMock()
        c._sync_users = AsyncMock()
        c._sync_user_groups = AsyncMock()

        space = MagicMock()
        space.short_name = "TEST"
        space.name = "Test Space"
        c._sync_spaces = AsyncMock(return_value=[space])
        c._sync_content = AsyncMock()
        c._sync_permission_changes_from_audit_log = AsyncMock()

        await c.run_sync()
        c._sync_users.assert_awaited_once()
        c._sync_user_groups.assert_awaited_once()
        assert c._sync_content.await_count == 2  # pages + blogposts


# ===========================================================================
# _sync_users
# ===========================================================================
class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_users()


# ===========================================================================
# _sync_user_groups
# ===========================================================================
class TestSyncUserGroups:
    @pytest.mark.asyncio
    async def test_no_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=None)
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(500))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()

    @pytest.mark.asyncio
    async def test_empty_results(self):
        c = _conn()
        ds = AsyncMock()
        ds.get_groups = AsyncMock(return_value=_resp(200, {"results": []}))
        c._get_fresh_datasource = AsyncMock(return_value=ds)
        await c._sync_user_groups()


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================
class TestFetchAttachmentContent:
    @pytest.mark.asyncio
    async def test_no_parent_page_id(self):
        c = _conn()
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = None
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        # should be an async generator even with error
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_client(self):
        c = _conn()
        c.data_source = None
        record = MagicMock(spec=FileRecord)
        record.parent_node_id = "page-1"
        record.record_name = "file.pdf"
        result = c._fetch_attachment_content(record)
        assert result is not None


# ===========================================================================
# _map_confluence_permission edge cases
# ===========================================================================
class TestMapConfluencePermissionEdgeCases:
    def test_create_blogpost(self):
        c = _conn()
        result = c._map_confluence_permission("create", "blogpost")
        assert result == PermissionType.WRITE

    def test_create_attachment(self):
        c = _conn()
        result = c._map_confluence_permission("create", "attachment")
        assert result == PermissionType.WRITE

    def test_delete_page(self):
        c = _conn()
        result = c._map_confluence_permission("delete", "page")
        assert result == PermissionType.WRITE

    def test_archive_page(self):
        c = _conn()
        result = c._map_confluence_permission("archive", "page")
        assert result == PermissionType.WRITE

    def test_unknown_returns_read(self):
        c = _conn()
        result = c._map_confluence_permission("unknown_op", "unknown_target")
        assert result == PermissionType.READ


# ===========================================================================
# _map_page_permission edge cases
# ===========================================================================
class TestMapPagePermissionEdgeCases:
    def test_delete_permission(self):
        c = _conn()
        result = c._map_page_permission("delete")
        assert result == PermissionType.READ

    def test_administer_permission(self):
        c = _conn()
        result = c._map_page_permission("administer")
        assert result == PermissionType.READ


# ===========================================================================
# _process_webpage_with_update edge cases
# ===========================================================================
class TestProcessWebpageWithUpdateEdgeCases:
    @pytest.mark.asyncio
    async def test_no_change_skips(self):
        c = _conn()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "v10"
        existing.version = 10
        existing.record_name = "Same Title"
        content_data = {
            "id": "page-1",
            "title": "Same Title",
            "version": {"number": 10, "when": "2025-01-01T00:00:00Z"},
            "_links": {"webui": "/wiki/page"},
            "status": "current",
        }
        c._transform_to_webpage_record = MagicMock(return_value=MagicMock(
            external_revision_id="v10", version=10, record_name="Same Title"
        ))
        result = await c._process_webpage_with_update(
            content_data, RecordType.CONFLUENCE_PAGE, existing, []
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_transform_returns_none(self):
        c = _conn()
        c._transform_to_webpage_record = MagicMock(return_value=None)
        result = await c._process_webpage_with_update(
            {"id": "p1"}, RecordType.CONFLUENCE_PAGE, None, []
        )
        assert result is not None
        assert result.record is None
