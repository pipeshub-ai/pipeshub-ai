"""Tests for app.connectors.sources.microsoft.common.msgraph_client."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)
from app.models.permission import PermissionType


# ===========================================================================
# map_msgraph_role_to_permission_type
# ===========================================================================


class TestMapMsgraphRoleToPermissionType:
    """Tests for the role-to-permission-type mapping function."""

    def test_owner_role(self):
        assert map_msgraph_role_to_permission_type("owner") == PermissionType.OWNER

    def test_fullcontrol_role(self):
        assert map_msgraph_role_to_permission_type("fullcontrol") == PermissionType.OWNER

    def test_write_role(self):
        assert map_msgraph_role_to_permission_type("write") == PermissionType.WRITE

    def test_editor_role(self):
        assert map_msgraph_role_to_permission_type("editor") == PermissionType.WRITE

    def test_contributor_role(self):
        assert map_msgraph_role_to_permission_type("contributor") == PermissionType.WRITE

    def test_read_role(self):
        assert map_msgraph_role_to_permission_type("read") == PermissionType.READ

    def test_reader_role(self):
        assert map_msgraph_role_to_permission_type("reader") == PermissionType.READ

    def test_unknown_role_defaults_to_read(self):
        assert map_msgraph_role_to_permission_type("some_custom_role") == PermissionType.READ

    def test_case_insensitive_owner(self):
        assert map_msgraph_role_to_permission_type("OWNER") == PermissionType.OWNER

    def test_case_insensitive_write(self):
        assert map_msgraph_role_to_permission_type("WRITE") == PermissionType.WRITE


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================


class TestRecordUpdate:
    """Tests for RecordUpdate dataclass."""

    def test_creation_with_defaults(self):
        update = RecordUpdate(
            record=None,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert update.record is None
        assert update.is_new is True
        assert update.old_permissions is None
        assert update.new_permissions is None
        assert update.external_record_id is None

    def test_creation_with_all_fields(self):
        mock_record = MagicMock()
        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            old_permissions=[],
            new_permissions=[],
            external_record_id="ext-123",
        )
        assert update.record is mock_record
        assert update.is_updated is True
        assert update.external_record_id == "ext-123"

    def test_deleted_record_update(self):
        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="deleted-item-id",
        )
        assert update.is_deleted is True
        assert update.external_record_id == "deleted-item-id"


# ===========================================================================
# MSGraphClient
# ===========================================================================


class TestMSGraphClient:
    """Tests for MSGraphClient methods."""

    def _make_client(self):
        mock_graph = MagicMock()
        logger = logging.getLogger("test")
        return MSGraphClient("ONEDRIVE", "connector-123", mock_graph, logger, max_requests_per_second=100)

    @pytest.mark.asyncio
    async def test_get_all_user_groups_empty(self):
        client = self._make_client()
        result_mock = MagicMock()
        result_mock.value = []
        result_mock.odata_next_link = None
        client.client.groups.get = AsyncMock(return_value=result_mock)

        groups = await client.get_all_user_groups()
        assert groups == []

    @pytest.mark.asyncio
    async def test_get_all_user_groups_single_page(self):
        client = self._make_client()
        group1 = MagicMock(id="g1", display_name="Group1")
        group2 = MagicMock(id="g2", display_name="Group2")
        result_mock = MagicMock()
        result_mock.value = [group1, group2]
        result_mock.odata_next_link = None
        client.client.groups.get = AsyncMock(return_value=result_mock)

        groups = await client.get_all_user_groups()
        assert len(groups) == 2
        assert groups[0].id == "g1"

    @pytest.mark.asyncio
    async def test_get_group_members_returns_empty_on_error(self):
        client = self._make_client()
        client.client.groups.by_group_id = MagicMock(
            side_effect=Exception("API error")
        )

        members = await client.get_group_members("group-id")
        assert members == []

    @pytest.mark.asyncio
    async def test_get_all_users_single_page(self):
        client = self._make_client()
        user_mock = MagicMock()
        user_mock.id = "u1"
        user_mock.display_name = "John Doe"
        user_mock.mail = "john@example.com"
        user_mock.user_principal_name = "john@example.com"
        user_mock.account_enabled = True
        user_mock.job_title = "Engineer"
        user_mock.created_date_time = None

        result_mock = MagicMock()
        result_mock.value = [user_mock]
        result_mock.odata_next_link = None
        client.client.users.get = AsyncMock(return_value=result_mock)

        users = await client.get_all_users()
        assert len(users) == 1
        assert users[0].email == "john@example.com"
        assert users[0].full_name == "John Doe"

    @pytest.mark.asyncio
    async def test_get_user_email_returns_mail(self):
        client = self._make_client()
        user_mock = MagicMock()
        user_mock.mail = "user@example.com"
        user_mock.user_principal_name = "user@upn.com"

        user_by_id = MagicMock()
        user_by_id.get = AsyncMock(return_value=user_mock)
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        email = await client.get_user_email("user-id-1")
        assert email == "user@example.com"

    @pytest.mark.asyncio
    async def test_get_user_email_falls_back_to_upn(self):
        client = self._make_client()
        user_mock = MagicMock()
        user_mock.mail = None
        user_mock.user_principal_name = "user@upn.com"

        user_by_id = MagicMock()
        user_by_id.get = AsyncMock(return_value=user_mock)
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        email = await client.get_user_email("user-id-2")
        assert email == "user@upn.com"

    @pytest.mark.asyncio
    async def test_get_user_email_returns_none_on_error(self):
        client = self._make_client()
        user_by_id = MagicMock()
        user_by_id.get = AsyncMock(side_effect=Exception("Not found"))
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        email = await client.get_user_email("nonexistent")
        assert email is None

    @pytest.mark.asyncio
    async def test_get_user_info_returns_dict(self):
        client = self._make_client()
        user_mock = MagicMock()
        user_mock.mail = "info@example.com"
        user_mock.user_principal_name = "info@upn.com"
        user_mock.display_name = "Info User"

        user_by_id = MagicMock()
        user_by_id.get = AsyncMock(return_value=user_mock)
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        info = await client.get_user_info("user-info-1")
        assert info["email"] == "info@example.com"
        assert info["display_name"] == "Info User"

    @pytest.mark.asyncio
    async def test_get_user_info_returns_none_on_error(self):
        client = self._make_client()
        user_by_id = MagicMock()
        user_by_id.get = AsyncMock(side_effect=Exception("Error"))
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        info = await client.get_user_info("bad-id")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_user_drive_returns_drive(self):
        client = self._make_client()
        drive_mock = MagicMock(id="drive-1")
        drive_obj = MagicMock()
        drive_obj.get = AsyncMock(return_value=drive_mock)
        user_by_id = MagicMock()
        user_by_id.drive = drive_obj
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        drive = await client.get_user_drive("user-1")
        assert drive.id == "drive-1"
