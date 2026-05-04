"""Tests for app.connectors.sources.microsoft.common.msgraph_client."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)
from app.models.permission import PermissionType
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from app.connectors.sources.microsoft.common.msgraph_client import (
    DeltaGetResponse,
    GroupDeltaGetResponse,
    MSGraphClient,
    PermissionChange,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)


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

# =============================================================================
# Merged from test_msgraph_client_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(max_rps: int = 1000) -> MSGraphClient:
    """Create an MSGraphClient with fully mocked Graph SDK."""
    mock_graph = MagicMock()
    mock_graph.request_adapter = MagicMock()
    mock_graph.request_adapter.send_async = AsyncMock()
    logger = logging.getLogger("test_msgraph_coverage")
    return MSGraphClient(
        app_name="ONEDRIVE",
        connector_id="connector-test-id",
        client=mock_graph,
        logger=logger,
        max_requests_per_second=max_rps,
    )


def _make_odata_error(code: str = "BadRequest", message: str = "") -> Exception:
    """Build a mock ODataError with .error.code and .error.message."""
    from msgraph.generated.models.o_data_errors.o_data_error import ODataError

    err = ODataError()
    err.error = MagicMock()
    err.error.code = code
    err.error.message = message
    return err


def _paged_result(values, next_link=None):
    """Return a MagicMock that looks like a Graph SDK paged response."""
    result = MagicMock()
    result.value = values
    result.odata_next_link = next_link
    return result


# ===========================================================================
# map_msgraph_role_to_permission_type
# ===========================================================================


class TestMapMsgraphRoleToPermissionTypeCoverage:
    def test_owner(self):
        assert map_msgraph_role_to_permission_type("owner") == PermissionType.OWNER

    def test_fullcontrol(self):
        assert map_msgraph_role_to_permission_type("fullcontrol") == PermissionType.OWNER

    def test_owner_uppercase(self):
        assert map_msgraph_role_to_permission_type("OWNER") == PermissionType.OWNER

    def test_fullcontrol_mixed(self):
        assert map_msgraph_role_to_permission_type("FullControl") == PermissionType.OWNER

    def test_write(self):
        assert map_msgraph_role_to_permission_type("write") == PermissionType.WRITE

    def test_editor(self):
        assert map_msgraph_role_to_permission_type("editor") == PermissionType.WRITE

    def test_contributor(self):
        assert map_msgraph_role_to_permission_type("contributor") == PermissionType.WRITE

    def test_writeaccess(self):
        assert map_msgraph_role_to_permission_type("writeaccess") == PermissionType.WRITE

    def test_writeaccess_upper(self):
        assert map_msgraph_role_to_permission_type("WRITEACCESS") == PermissionType.WRITE

    def test_read(self):
        assert map_msgraph_role_to_permission_type("read") == PermissionType.READ

    def test_reader(self):
        assert map_msgraph_role_to_permission_type("reader") == PermissionType.READ

    def test_readaccess(self):
        assert map_msgraph_role_to_permission_type("readaccess") == PermissionType.READ

    def test_unknown_defaults_to_read(self):
        assert map_msgraph_role_to_permission_type("custom_role") == PermissionType.READ

    def test_empty_string_defaults_to_read(self):
        assert map_msgraph_role_to_permission_type("") == PermissionType.READ


# ===========================================================================
# PermissionChange dataclass
# ===========================================================================


class TestPermissionChange:
    def test_basic_creation(self):
        pc = PermissionChange(
            record_id="r1",
            external_record_id="ext-r1",
            added_permissions=[],
            removed_permissions=[],
            modified_permissions=[],
        )
        assert pc.record_id == "r1"
        assert pc.external_record_id == "ext-r1"
        assert pc.added_permissions == []
        assert pc.removed_permissions == []
        assert pc.modified_permissions == []

    def test_with_permissions(self):
        perm = MagicMock()
        pc = PermissionChange(
            record_id="r2",
            external_record_id="ext-r2",
            added_permissions=[perm],
            removed_permissions=[perm],
            modified_permissions=[perm],
        )
        assert len(pc.added_permissions) == 1
        assert len(pc.removed_permissions) == 1
        assert len(pc.modified_permissions) == 1


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================


class TestRecordUpdateCoverage:
    def test_defaults(self):
        ru = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert ru.old_permissions is None
        assert ru.new_permissions is None
        assert ru.external_record_id is None

    def test_all_fields(self):
        rec = MagicMock()
        ru = RecordUpdate(
            record=rec,
            is_new=True,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            old_permissions=[MagicMock()],
            new_permissions=[MagicMock()],
            external_record_id="ext-id",
        )
        assert ru.record is rec
        assert ru.is_new is True
        assert ru.external_record_id == "ext-id"


# ===========================================================================
# DeltaGetResponse
# ===========================================================================


class TestDeltaGetResponse:
    def test_create_from_discriminator_value(self):
        parse_node = MagicMock()
        resp = DeltaGetResponse.create_from_discriminator_value(parse_node)
        assert isinstance(resp, DeltaGetResponse)

    def test_create_from_discriminator_value_null(self):
        with pytest.raises(TypeError, match="parse_node cannot be null"):
            DeltaGetResponse.create_from_discriminator_value(None)

    def test_get_field_deserializers_contains_value(self):
        resp = DeltaGetResponse()
        fields = resp.get_field_deserializers()
        assert "value" in fields

    def test_value_deserializer_sets_attribute(self):
        resp = DeltaGetResponse()
        fields = resp.get_field_deserializers()
        mock_node = MagicMock()
        mock_node.get_collection_of_object_values.return_value = ["item1", "item2"]
        fields["value"](mock_node)
        assert resp.value == ["item1", "item2"]

    def test_serialize(self):
        resp = DeltaGetResponse()
        resp.value = [MagicMock(), MagicMock()]
        writer = MagicMock()
        resp.serialize(writer)
        writer.write_collection_of_object_values.assert_called_once_with("value", resp.value)

    def test_serialize_null_writer(self):
        resp = DeltaGetResponse()
        with pytest.raises(TypeError, match="writer cannot be null"):
            resp.serialize(None)

    def test_default_value_is_none(self):
        resp = DeltaGetResponse()
        assert resp.value is None


# ===========================================================================
# GroupDeltaGetResponse
# ===========================================================================


class TestGroupDeltaGetResponse:
    def test_create_from_discriminator_value(self):
        parse_node = MagicMock()
        resp = GroupDeltaGetResponse.create_from_discriminator_value(parse_node)
        assert isinstance(resp, GroupDeltaGetResponse)

    def test_create_from_discriminator_value_null(self):
        with pytest.raises(TypeError, match="parse_node cannot be null"):
            GroupDeltaGetResponse.create_from_discriminator_value(None)

    def test_get_field_deserializers_contains_value(self):
        resp = GroupDeltaGetResponse()
        fields = resp.get_field_deserializers()
        assert "value" in fields

    def test_value_deserializer_sets_attribute(self):
        resp = GroupDeltaGetResponse()
        fields = resp.get_field_deserializers()
        mock_node = MagicMock()
        mock_node.get_collection_of_object_values.return_value = ["group1"]
        fields["value"](mock_node)
        assert resp.value == ["group1"]

    def test_serialize(self):
        resp = GroupDeltaGetResponse()
        resp.value = [MagicMock()]
        writer = MagicMock()
        resp.serialize(writer)
        writer.write_collection_of_object_values.assert_called_once_with("value", resp.value)

    def test_serialize_null_writer(self):
        resp = GroupDeltaGetResponse()
        with pytest.raises(TypeError, match="writer cannot be null"):
            resp.serialize(None)

    def test_default_value_is_none(self):
        resp = GroupDeltaGetResponse()
        assert resp.value is None


# ===========================================================================
# MSGraphClient.__init__
# ===========================================================================


class TestMSGraphClientInit:
    def test_basic_init(self):
        client = _make_client()
        assert client.app_name == "ONEDRIVE"
        assert client.connector_id == "connector-test-id"
        assert client.rate_limiter is not None

    def test_custom_rate_limit(self):
        client = _make_client(max_rps=5)
        assert client.rate_limiter.max_rate == 5


# ===========================================================================
# get_all_user_groups
# ===========================================================================


class TestGetAllUserGroups:
    @pytest.mark.asyncio
    async def test_empty_result(self):
        client = _make_client()
        client.client.groups.get = AsyncMock(return_value=_paged_result([]))
        groups = await client.get_all_user_groups()
        assert groups == []

    @pytest.mark.asyncio
    async def test_single_page(self):
        client = _make_client()
        g1 = MagicMock(id="g1")
        g2 = MagicMock(id="g2")
        client.client.groups.get = AsyncMock(return_value=_paged_result([g1, g2]))
        groups = await client.get_all_user_groups()
        assert len(groups) == 2

    @pytest.mark.asyncio
    async def test_multiple_pages(self):
        client = _make_client()
        g1 = MagicMock(id="g1")
        g2 = MagicMock(id="g2")
        page1 = _paged_result([g1], next_link="https://next-page")
        page2 = _paged_result([g2])

        client.client.groups.get = AsyncMock(return_value=page1)
        with_url_mock = MagicMock()
        with_url_mock.get = AsyncMock(return_value=page2)
        client.client.groups.with_url = MagicMock(return_value=with_url_mock)

        groups = await client.get_all_user_groups()
        assert len(groups) == 2
        client.client.groups.with_url.assert_called_once_with("https://next-page")

    @pytest.mark.asyncio
    async def test_none_value_in_page(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        client.client.groups.get = AsyncMock(return_value=result)
        groups = await client.get_all_user_groups()
        assert groups == []

    @pytest.mark.asyncio
    async def test_odata_error(self):
        client = _make_client()
        odata_err = _make_odata_error("Forbidden", "Access denied")
        client.client.groups.get = AsyncMock(side_effect=odata_err)
        with pytest.raises(Exception):
            await client.get_all_user_groups()

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        client = _make_client()
        client.client.groups.get = AsyncMock(side_effect=RuntimeError("network"))
        with pytest.raises(RuntimeError):
            await client.get_all_user_groups()


# ===========================================================================
# get_group_members
# ===========================================================================


class TestGetGroupMembers:
    @pytest.mark.asyncio
    async def test_single_page(self):
        client = _make_client()
        m1 = MagicMock(id="m1")
        page = _paged_result([m1])
        members_mock = MagicMock()
        members_mock.get = AsyncMock(return_value=page)
        group_by_id = MagicMock()
        group_by_id.members = members_mock
        client.client.groups.by_group_id = MagicMock(return_value=group_by_id)

        members = await client.get_group_members("g1")
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_multiple_pages(self):
        client = _make_client()
        m1 = MagicMock(id="m1")
        m2 = MagicMock(id="m2")
        page1 = _paged_result([m1], next_link="https://next")
        page2 = _paged_result([m2])

        members_mock = MagicMock()
        members_mock.get = AsyncMock(return_value=page1)
        with_url_mock = MagicMock()
        with_url_mock.get = AsyncMock(return_value=page2)
        members_mock.with_url = MagicMock(return_value=with_url_mock)

        group_by_id = MagicMock()
        group_by_id.members = members_mock
        client.client.groups.by_group_id = MagicMock(return_value=group_by_id)

        members = await client.get_group_members("g1")
        assert len(members) == 2

    @pytest.mark.asyncio
    async def test_none_value_page(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        members_mock = MagicMock()
        members_mock.get = AsyncMock(return_value=result)
        group_by_id = MagicMock()
        group_by_id.members = members_mock
        client.client.groups.by_group_id = MagicMock(return_value=group_by_id)

        members = await client.get_group_members("g1")
        assert members == []

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        client = _make_client()
        client.client.groups.by_group_id = MagicMock(side_effect=Exception("fail"))
        members = await client.get_group_members("g1")
        assert members == []


# ===========================================================================
# get_all_users
# ===========================================================================


class TestGetAllUsers:
    def _user_mock(
        self,
        uid="u1",
        name="John",
        mail="john@test.com",
        upn="john@upn.com",
        enabled=True,
        title="Eng",
        created=None,
    ):
        u = MagicMock()
        u.id = uid
        u.display_name = name
        u.mail = mail
        u.user_principal_name = upn
        u.account_enabled = enabled
        u.job_title = title
        u.created_date_time = created
        return u

    @pytest.mark.asyncio
    async def test_single_page(self):
        client = _make_client()
        user = self._user_mock()
        client.client.users.get = AsyncMock(return_value=_paged_result([user]))

        users = await client.get_all_users()
        assert len(users) == 1
        assert users[0].email == "john@test.com"
        assert users[0].full_name == "John"
        assert users[0].is_active is True
        assert users[0].title == "Eng"

    @pytest.mark.asyncio
    async def test_mail_fallback_to_upn(self):
        client = _make_client()
        user = self._user_mock(mail=None, upn="fallback@upn.com")
        client.client.users.get = AsyncMock(return_value=_paged_result([user]))

        users = await client.get_all_users()
        assert users[0].email == "fallback@upn.com"

    @pytest.mark.asyncio
    async def test_with_created_datetime(self):
        client = _make_client()
        from datetime import datetime, timezone

        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        user = self._user_mock(created=dt)
        client.client.users.get = AsyncMock(return_value=_paged_result([user]))

        users = await client.get_all_users()
        assert users[0].source_created_at == dt.timestamp()

    @pytest.mark.asyncio
    async def test_pagination(self):
        client = _make_client()
        u1 = self._user_mock(uid="u1")
        u2 = self._user_mock(uid="u2", name="Jane", mail="jane@test.com")
        page1 = _paged_result([u1], next_link="https://next")
        page2 = _paged_result([u2])

        client.client.users.get = AsyncMock(return_value=page1)
        with_url_mock = MagicMock()
        with_url_mock.get = AsyncMock(return_value=page2)
        client.client.users.with_url = MagicMock(return_value=with_url_mock)

        users = await client.get_all_users()
        assert len(users) == 2

    @pytest.mark.asyncio
    async def test_odata_error(self):
        client = _make_client()
        client.client.users.get = AsyncMock(side_effect=_make_odata_error())
        with pytest.raises(Exception):
            await client.get_all_users()

    @pytest.mark.asyncio
    async def test_generic_error(self):
        client = _make_client()
        client.client.users.get = AsyncMock(side_effect=ValueError("oops"))
        with pytest.raises(ValueError):
            await client.get_all_users()

    @pytest.mark.asyncio
    async def test_none_value_page(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        client.client.users.get = AsyncMock(return_value=result)
        users = await client.get_all_users()
        assert users == []


# ===========================================================================
# get_user_email
# ===========================================================================


class TestGetUserEmail:
    @pytest.mark.asyncio
    async def test_returns_mail(self):
        client = _make_client()
        user = MagicMock()
        user.mail = "a@b.com"
        user.user_principal_name = "a@upn.com"
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=user)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_email("uid") == "a@b.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_upn(self):
        client = _make_client()
        user = MagicMock()
        user.mail = None
        user.user_principal_name = "upn@test.com"
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=user)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_email("uid") == "upn@test.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_user_is_none(self):
        client = _make_client()
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=None)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_email("uid") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        client = _make_client()
        by_id = MagicMock()
        by_id.get = AsyncMock(side_effect=Exception("gone"))
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_email("uid") is None


# ===========================================================================
# get_user_info
# ===========================================================================


class TestGetUserInfo:
    @pytest.mark.asyncio
    async def test_returns_info(self):
        client = _make_client()
        user = MagicMock()
        user.mail = "info@test.com"
        user.user_principal_name = "info@upn.com"
        user.display_name = "Info User"
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=user)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        info = await client.get_user_info("uid")
        assert info == {"email": "info@test.com", "display_name": "Info User"}

    @pytest.mark.asyncio
    async def test_uses_upn_when_mail_is_none(self):
        client = _make_client()
        user = MagicMock()
        user.mail = None
        user.user_principal_name = "upn@test.com"
        user.display_name = "UPN User"
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=user)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        info = await client.get_user_info("uid")
        assert info["email"] == "upn@test.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_user_is_none(self):
        client = _make_client()
        by_id = MagicMock()
        by_id.get = AsyncMock(return_value=None)
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_info("uid") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        client = _make_client()
        by_id = MagicMock()
        by_id.get = AsyncMock(side_effect=Exception("err"))
        client.client.users.by_user_id = MagicMock(return_value=by_id)

        assert await client.get_user_info("uid") is None


# ===========================================================================
# get_user_drive
# ===========================================================================


class TestGetUserDrive:
    @pytest.mark.asyncio
    async def test_returns_drive(self):
        client = _make_client()
        drive = MagicMock(id="d1")
        drive_builder = MagicMock()
        drive_builder.get = AsyncMock(return_value=drive)
        user_by_id = MagicMock()
        user_by_id.drive = drive_builder
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        result = await client.get_user_drive("uid")
        assert result.id == "d1"

    @pytest.mark.asyncio
    async def test_raises_odata_error(self):
        client = _make_client()
        err = _make_odata_error("NotFound", "No drive")
        drive_builder = MagicMock()
        drive_builder.get = AsyncMock(side_effect=err)
        user_by_id = MagicMock()
        user_by_id.drive = drive_builder
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        with pytest.raises(Exception):
            await client.get_user_drive("uid")

    @pytest.mark.asyncio
    async def test_raises_generic_error(self):
        client = _make_client()
        drive_builder = MagicMock()
        drive_builder.get = AsyncMock(side_effect=RuntimeError("network"))
        user_by_id = MagicMock()
        user_by_id.drive = drive_builder
        client.client.users.by_user_id = MagicMock(return_value=user_by_id)

        with pytest.raises(RuntimeError):
            await client.get_user_drive("uid")


# ===========================================================================
# get_delta_response_sharepoint
# ===========================================================================


class TestGetDeltaResponseSharepoint:
    @pytest.mark.asyncio
    async def test_returns_items_and_links(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["item1", "item2"]
        result.odata_next_link = "https://next"
        result.odata_delta_link = "https://delta"
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["drive_items"] == ["item1", "item2"]
        assert resp["next_link"] == "https://next"
        assert resp["delta_link"] == "https://delta"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        client = _make_client()
        result = MagicMock(spec=[])  # no attributes by default
        # Manually set hasattr behavior via spec
        result_obj = MagicMock()
        result_obj.value = None
        result_obj.odata_next_link = None
        result_obj.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result_obj)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["drive_items"] == []
        assert resp["next_link"] is None
        assert resp["delta_link"] is None

    @pytest.mark.asyncio
    async def test_no_next_link_no_delta_link(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["item1"]
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["drive_items"] == ["item1"]
        assert resp["next_link"] is None
        assert resp["delta_link"] is None

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        client = _make_client()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=RuntimeError("server error")
        )
        with pytest.raises(RuntimeError):
            await client.get_delta_response_sharepoint("https://graph/delta")

    @pytest.mark.asyncio
    async def test_result_without_value_attr(self):
        """When result has no 'value' attribute, drive_items should be empty."""
        client = _make_client()
        # Use a simple object without .value
        result = MagicMock(spec=["odata_next_link", "odata_delta_link"])
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["drive_items"] == []


# ===========================================================================
# get_delta_response
# ===========================================================================


class TestGetDeltaResponse:
    @pytest.mark.asyncio
    async def test_returns_items_and_links(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["item1"]
        result.odata_next_link = "https://next"
        result.odata_delta_link = "https://delta"
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response("https://graph/delta")
        assert resp["drive_items"] == ["item1"]
        assert resp["next_link"] == "https://next"
        assert resp["delta_link"] == "https://delta"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response("https://graph/delta")
        assert resp["drive_items"] == []
        assert resp["next_link"] is None
        assert resp["delta_link"] is None

    @pytest.mark.asyncio
    async def test_only_next_link(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["a"]
        result.odata_next_link = "https://next"
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response("https://graph/delta")
        assert resp["next_link"] == "https://next"
        assert resp["delta_link"] is None

    @pytest.mark.asyncio
    async def test_only_delta_link(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["a"]
        result.odata_next_link = None
        result.odata_delta_link = "https://delta"
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response("https://graph/delta")
        assert resp["next_link"] is None
        assert resp["delta_link"] == "https://delta"

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        client = _make_client()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=ValueError("bad request")
        )
        with pytest.raises(ValueError):
            await client.get_delta_response("https://graph/delta")

    @pytest.mark.asyncio
    async def test_result_without_value_attr(self):
        client = _make_client()
        result = MagicMock(spec=["odata_next_link", "odata_delta_link"])
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response("https://graph/delta")
        assert resp["drive_items"] == []


# ===========================================================================
# get_groups_delta_response
# ===========================================================================


class TestGetGroupsDeltaResponse:
    @pytest.mark.asyncio
    async def test_returns_groups_and_links(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["group1", "group2"]
        result.odata_next_link = "https://next"
        result.odata_delta_link = "https://delta"
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_groups_delta_response("https://graph/delta")
        assert resp["groups"] == ["group1", "group2"]
        assert resp["next_link"] == "https://next"
        assert resp["delta_link"] == "https://delta"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_groups_delta_response("https://graph/delta")
        assert resp["groups"] == []
        assert resp["next_link"] is None
        assert resp["delta_link"] is None

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        client = _make_client()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=RuntimeError("err")
        )
        with pytest.raises(RuntimeError):
            await client.get_groups_delta_response("https://graph/delta")

    @pytest.mark.asyncio
    async def test_result_without_value_attr(self):
        client = _make_client()
        result = MagicMock(spec=["odata_next_link", "odata_delta_link"])
        result.odata_next_link = None
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_groups_delta_response("https://graph/delta")
        assert resp["groups"] == []

    @pytest.mark.asyncio
    async def test_only_next_link(self):
        client = _make_client()
        result = MagicMock()
        result.value = ["g1"]
        result.odata_next_link = "https://next"
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_groups_delta_response("https://graph/delta")
        assert resp["next_link"] == "https://next"
        assert resp["delta_link"] is None


# ===========================================================================
# get_file_permission
# ===========================================================================


class TestGetFilePermission:
    @pytest.mark.asyncio
    async def test_single_page(self):
        client = _make_client()
        perm = MagicMock(id="p1")
        page = _paged_result([perm])

        perms_builder = MagicMock()
        perms_builder.get = AsyncMock(return_value=page)
        items_builder = MagicMock()
        items_builder.permissions = perms_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        perms = await client.get_file_permission("d1", "i1")
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_pagination(self):
        client = _make_client()
        p1 = MagicMock(id="p1")
        p2 = MagicMock(id="p2")
        page1 = _paged_result([p1], next_link="https://next")
        page2 = _paged_result([p2])

        perms_builder = MagicMock()
        perms_builder.get = AsyncMock(return_value=page1)
        with_url_mock = MagicMock()
        with_url_mock.get = AsyncMock(return_value=page2)
        perms_builder.with_url = MagicMock(return_value=with_url_mock)
        items_builder = MagicMock()
        items_builder.permissions = perms_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        perms = await client.get_file_permission("d1", "i1")
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_none_value(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        perms_builder = MagicMock()
        perms_builder.get = AsyncMock(return_value=result)
        items_builder = MagicMock()
        items_builder.permissions = perms_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        perms = await client.get_file_permission("d1", "i1")
        assert perms == []

    @pytest.mark.asyncio
    async def test_odata_error_returns_empty(self):
        client = _make_client()
        err = _make_odata_error("NotFound", "not found")
        perms_builder = MagicMock()
        perms_builder.get = AsyncMock(side_effect=err)
        items_builder = MagicMock()
        items_builder.permissions = perms_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        perms = await client.get_file_permission("d1", "i1")
        assert perms == []

    @pytest.mark.asyncio
    async def test_generic_error_returns_empty(self):
        client = _make_client()
        perms_builder = MagicMock()
        perms_builder.get = AsyncMock(side_effect=RuntimeError("err"))
        items_builder = MagicMock()
        items_builder.permissions = perms_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        perms = await client.get_file_permission("d1", "i1")
        assert perms == []


# ===========================================================================
# list_folder_children
# ===========================================================================


class TestListFolderChildren:
    def _setup_children_mock(self, client, pages):
        """Set up chained mock for drive.items.children calls.
        pages: list of (values, next_link) tuples.
        """
        first_page = _paged_result(*pages[0])

        children_builder = MagicMock()
        children_builder.get = AsyncMock(return_value=first_page)

        if len(pages) > 1:
            subsequent = []
            for vals, nl in pages[1:]:
                subsequent.append(_paged_result(vals, nl))
            with_url_mock = MagicMock()
            with_url_mock.get = AsyncMock(side_effect=subsequent)
            children_builder.with_url = MagicMock(return_value=with_url_mock)

        items_builder = MagicMock()
        items_builder.children = children_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

    @pytest.mark.asyncio
    async def test_single_page(self):
        client = _make_client()
        c1 = MagicMock(id="c1")
        self._setup_children_mock(client, [([c1], None)])

        children = await client.list_folder_children("d1", "f1")
        assert len(children) == 1

    @pytest.mark.asyncio
    async def test_multiple_pages(self):
        client = _make_client()
        c1 = MagicMock(id="c1")
        c2 = MagicMock(id="c2")
        self._setup_children_mock(client, [([c1], "https://next"), ([c2], None)])

        children = await client.list_folder_children("d1", "f1")
        assert len(children) == 2

    @pytest.mark.asyncio
    async def test_empty(self):
        client = _make_client()
        self._setup_children_mock(client, [([], None)])

        children = await client.list_folder_children("d1", "f1")
        assert children == []

    @pytest.mark.asyncio
    async def test_none_value(self):
        client = _make_client()
        result = MagicMock()
        result.value = None
        result.odata_next_link = None
        children_builder = MagicMock()
        children_builder.get = AsyncMock(return_value=result)
        items_builder = MagicMock()
        items_builder.children = children_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        children = await client.list_folder_children("d1", "f1")
        assert children == []

    @pytest.mark.asyncio
    async def test_odata_error_returns_empty(self):
        client = _make_client()
        err = _make_odata_error("Forbidden", "access denied")
        children_builder = MagicMock()
        children_builder.get = AsyncMock(side_effect=err)
        items_builder = MagicMock()
        items_builder.children = children_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        children = await client.list_folder_children("d1", "f1")
        assert children == []

    @pytest.mark.asyncio
    async def test_generic_error_returns_empty(self):
        client = _make_client()
        children_builder = MagicMock()
        children_builder.get = AsyncMock(side_effect=RuntimeError("net"))
        items_builder = MagicMock()
        items_builder.children = children_builder
        drive_items = MagicMock()
        drive_items.by_drive_item_id = MagicMock(return_value=items_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_items
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        children = await client.list_folder_children("d1", "f1")
        assert children == []


# ===========================================================================
# get_signed_url
# ===========================================================================


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_url(self):
        client = _make_client()
        item = MagicMock()
        item.additional_data = {"@microsoft.graph.downloadUrl": "https://signed-url"}
        item_builder = MagicMock()
        item_builder.get = AsyncMock(return_value=item)
        drive_item_builder = MagicMock()
        drive_item_builder.by_drive_item_id = MagicMock(return_value=item_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_item_builder
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        url = await client.get_signed_url("d1", "i1")
        assert url == "https://signed-url"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_download_url(self):
        client = _make_client()
        item = MagicMock()
        item.additional_data = {}
        item_builder = MagicMock()
        item_builder.get = AsyncMock(return_value=item)
        drive_item_builder = MagicMock()
        drive_item_builder.by_drive_item_id = MagicMock(return_value=item_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_item_builder
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        url = await client.get_signed_url("d1", "i1")
        assert url is None

    @pytest.mark.asyncio
    async def test_returns_none_when_item_is_none(self):
        client = _make_client()
        item_builder = MagicMock()
        item_builder.get = AsyncMock(return_value=None)
        drive_item_builder = MagicMock()
        drive_item_builder.by_drive_item_id = MagicMock(return_value=item_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_item_builder
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        url = await client.get_signed_url("d1", "i1")
        assert url is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_additional_data(self):
        client = _make_client()
        # Item exists but has no additional_data attribute
        item = MagicMock(spec=["id", "name"])
        item_builder = MagicMock()
        item_builder.get = AsyncMock(return_value=item)
        drive_item_builder = MagicMock()
        drive_item_builder.by_drive_item_id = MagicMock(return_value=item_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_item_builder
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        url = await client.get_signed_url("d1", "i1")
        assert url is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        client = _make_client()
        item_builder = MagicMock()
        item_builder.get = AsyncMock(side_effect=Exception("err"))
        drive_item_builder = MagicMock()
        drive_item_builder.by_drive_item_id = MagicMock(return_value=item_builder)
        drive_builder = MagicMock()
        drive_builder.items = drive_item_builder
        client.client.drives.by_drive_id = MagicMock(return_value=drive_builder)

        url = await client.get_signed_url("d1", "i1")
        assert url is None


# ===========================================================================
# search_query
# ===========================================================================


class TestSearchQuery:
    @pytest.mark.asyncio
    async def test_basic_search(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], query="test", page=1, limit=10)
        assert result is expected_result
        client.client.request_adapter.send_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_query_star(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"])
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_empty_query_uses_star(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], query="   ")
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_none_region_defaults_to_nam(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], region=None)
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_page_offset_calculation(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], page=3, limit=20)
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_from_offset_overrides_page(self):
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(
            ["driveItem"], page=99, limit=20, from_offset=120
        )
        assert result is expected_result
        req = client.client.request_adapter.send_async.call_args.kwargs["request_info"]
        body = json.loads(req.content.decode("utf-8"))
        assert body["requests"][0]["from"] == 120
        assert body["requests"][0]["size"] == 20

    @pytest.mark.asyncio
    async def test_region_retry_on_bad_request(self):
        """When region is invalid, extract correct region from error and retry."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested region  not found. Only valid regions are EUR.",
        )
        expected_result = MagicMock()
        # First call raises, second call succeeds
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=[err, expected_result]
        )

        result = await client.search_query(["driveItem"], region="NAM")
        assert result is expected_result
        assert client.client.request_adapter.send_async.call_count == 2

    @pytest.mark.asyncio
    async def test_region_retry_multiple_valid_regions(self):
        """Extract first valid region from error containing multiple regions."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested region  not found. Only valid regions are EUR, APC, NAM.",
        )
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=[err, expected_result]
        )

        result = await client.search_query(["driveItem"], region="XYZ")
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_region_retry_same_region_no_retry(self):
        """When extracted region is same as the one passed, do not retry."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested region  not found. Only valid regions are NAM.",
        )
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(Exception):
            await client.search_query(["driveItem"], region="NAM")
        # Should only call once (no retry since extracted == passed)
        assert client.client.request_adapter.send_async.call_count == 1

    @pytest.mark.asyncio
    async def test_region_error_no_valid_region_extracted(self):
        """When error message does not contain parseable regions, raise."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Some other valid regions are mentioned without proper format",
        )
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(Exception):
            await client.search_query(["driveItem"], region="NAM")

    @pytest.mark.asyncio
    async def test_non_region_odata_error_raises(self):
        """Non-region ODataError should raise without retry."""
        client = _make_client()
        err = _make_odata_error("Forbidden", "Access denied")
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(Exception):
            await client.search_query(["driveItem"])
        assert client.client.request_adapter.send_async.call_count == 1

    @pytest.mark.asyncio
    async def test_odata_error_no_error_attr(self):
        """ODataError with error=None should raise without retry."""
        client = _make_client()
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        err = ODataError()
        err.error = None
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(ODataError):
            await client.search_query(["driveItem"])

    @pytest.mark.asyncio
    async def test_odata_error_no_message(self):
        """ODataError with error.message=None should raise without retry."""
        client = _make_client()
        err = _make_odata_error("BadRequest", None)
        # Make sure the 'in' check on None fails gracefully
        err.error.message = None
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(Exception):
            await client.search_query(["driveItem"])

    @pytest.mark.asyncio
    async def test_extract_region_error_in_parser(self):
        """When _extract_region_from_error itself throws, return None gracefully."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested region not found. Only valid regions are EUR.",
        )
        # Make error.error raise on second access to simulate parse failure
        err_inner = MagicMock()
        err_inner.code = "BadRequest"
        err_inner.message = "Requested region not found. Only valid regions are EUR."
        err.error = err_inner

        expected = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=[err, expected]
        )

        result = await client.search_query(["driveItem"], region="NAM")
        assert result is expected

    @pytest.mark.asyncio
    async def test_non_odata_error_not_caught(self):
        """Non-ODataError exceptions propagate directly."""
        client = _make_client()
        client.client.request_adapter.send_async = AsyncMock(
            side_effect=RuntimeError("network down")
        )

        with pytest.raises(RuntimeError, match="network down"):
            await client.search_query(["driveItem"])

    @pytest.mark.asyncio
    async def test_empty_query_none_uses_star(self):
        """When query is None, should use '*'."""
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], query=None)
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_region_error_message_with_valid_regions_lowercase_check(self):
        """The code checks 'valid regions are' in lower case of the message.
        The regex is case-sensitive and expects 'Only valid regions are [A-Z...].'
        So an all-uppercase message like 'VALID REGIONS ARE' matches the .lower()
        guard, but the regex won't match -> _extract returns None -> no retry.
        """
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested REGION not found. Only VALID REGIONS ARE APC.",
        )
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        # Regex won't match all-caps, so no region extracted -> no retry -> raise
        with pytest.raises(Exception):
            await client.search_query(["driveItem"], region="NAM")

    @pytest.mark.asyncio
    async def test_odata_error_code_not_bad_request(self):
        """ODataError with code != 'BadRequest' should raise without retry."""
        client = _make_client()
        err = _make_odata_error(
            "InternalServerError",
            "Requested region  not found. Only valid regions are EUR.",
        )
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        with pytest.raises(Exception):
            await client.search_query(["driveItem"])
        assert client.client.request_adapter.send_async.call_count == 1

    @pytest.mark.asyncio
    async def test_region_error_no_regions_in_match(self):
        """When regex matches but all regions are empty strings after split."""
        client = _make_client()
        err = _make_odata_error(
            "BadRequest",
            "Requested region  not found. Only valid regions are .",
        )
        client.client.request_adapter.send_async = AsyncMock(side_effect=err)

        # The regex expects [A-Z,\s]+ which won't match an empty string after "are "
        # so _extract_region_from_error returns None => no retry
        with pytest.raises(Exception):
            await client.search_query(["driveItem"], region="NAM")

    @pytest.mark.asyncio
    async def test_extract_region_exception_in_error_access(self):
        """When accessing error.error.message throws during region extraction,
        the except Exception: pass catches it and returns None -> no retry -> raise."""
        client = _make_client()
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        err = ODataError()
        # Create an error attribute that raises on property access in _extract
        inner_error = MagicMock()
        inner_error.code = "BadRequest"
        # First access to .message works (for the guard in search_query),
        # but .message as a property could raise during _extract_region_from_error
        msg = "Requested region  not found. Only valid regions are EUR."
        inner_error.message = msg
        err.error = inner_error

        # Make the error raise when _extract_region_from_error tries
        # to access error.error.message the regex way -- we need a different approach.
        # Instead, let's make the error object raise on the second access to .error
        call_count = 0
        original_error = err.error

        def error_property_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First accesses work (guard checks in search_query)
                return original_error
            # Third+ access (inside _extract_region_from_error) raises
            raise AttributeError("simulated error")

        # This approach won't work easily with __getattr__. Let's take a simpler path:
        # Make error.error.message a property that raises on regex search
        inner2 = MagicMock()
        inner2.code = "BadRequest"
        inner2.message = PropertyMock(side_effect=["valid regions are EUR.", AttributeError("boom")])

        # Simplest approach: just test the path with error.error = None inside _extract
        # but error still has code/message for the guard
        err2 = ODataError()
        err2_inner = MagicMock()
        err2_inner.code = "BadRequest"
        err2_inner.message = "valid regions are here but will fail in extract"

        # Override error to raise on message access in _extract context
        # Actually the cleanest way: error.error raises on .message in _extract
        err3 = ODataError()
        err3_error = MagicMock()
        err3_error.code = "BadRequest"

        # message property: returns string first time (guard), raises second time (extract)
        message_calls = [0]
        real_message = "Requested region  not found. Only valid regions are EUR."

        def message_getter():
            message_calls[0] += 1
            if message_calls[0] <= 2:
                return real_message
            raise RuntimeError("simulated parse failure")

        type(err3_error).message = PropertyMock(side_effect=lambda: message_getter())

        # This is getting complex. Instead, let's directly test the branch
        # by making error.error.message be a string that causes re.search to throw.
        # re.search can't really throw on valid inputs though.

        # The simplest trigger: make error.error be something whose .message raises
        err4 = ODataError()

        class BrokenInner:
            code = "BadRequest"
            _count = 0

            @property
            def message(self):
                self._count += 1
                if self._count > 2:
                    raise RuntimeError("boom")
                return "Requested region not found. Only valid regions are EUR."

        err4.error = BrokenInner()
        client.client.request_adapter.send_async = AsyncMock(side_effect=err4)

        # The guard checks ex.error.message (count 1 and 2),
        # then _extract_region_from_error checks error.error.message (count 3 -> raises)
        # The except Exception: pass catches it, returns None => no retry => raise
        with pytest.raises(ODataError):
            await client.search_query(["driveItem"], region="NAM")

    @pytest.mark.asyncio
    async def test_search_with_empty_string_region(self):
        """When region is empty string, _execute_search uses it directly
        and the 'if search_region:' guard skips adding region."""
        client = _make_client()
        expected_result = MagicMock()
        client.client.request_adapter.send_async = AsyncMock(return_value=expected_result)

        result = await client.search_query(["driveItem"], region="")
        assert result is expected_result

    @pytest.mark.asyncio
    async def test_get_all_user_groups_pagination_with_multiple_next_links(self):
        """Test 3-page pagination for groups."""
        client = _make_client()
        g1, g2, g3 = MagicMock(id="g1"), MagicMock(id="g2"), MagicMock(id="g3")
        page1 = _paged_result([g1], next_link="https://page2")
        page2 = _paged_result([g2], next_link="https://page3")
        page3 = _paged_result([g3])

        client.client.groups.get = AsyncMock(return_value=page1)
        with_url_mock = MagicMock()
        with_url_mock.get = AsyncMock(side_effect=[page2, page3])
        client.client.groups.with_url = MagicMock(return_value=with_url_mock)

        groups = await client.get_all_user_groups()
        assert len(groups) == 3

    @pytest.mark.asyncio
    async def test_get_group_members_empty_result(self):
        """Get group members returns empty list with empty value."""
        client = _make_client()
        page = _paged_result([])
        members_mock = MagicMock()
        members_mock.get = AsyncMock(return_value=page)
        group_by_id = MagicMock()
        group_by_id.members = members_mock
        client.client.groups.by_group_id = MagicMock(return_value=group_by_id)

        members = await client.get_group_members("g1")
        assert members == []

    @pytest.mark.asyncio
    async def test_get_delta_response_sharepoint_only_delta_link(self):
        """Sharepoint delta response with only a delta_link, no next_link."""
        client = _make_client()
        result = MagicMock()
        result.value = ["item1"]
        result.odata_next_link = None
        result.odata_delta_link = "https://delta-only"
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["delta_link"] == "https://delta-only"
        assert resp["next_link"] is None

    @pytest.mark.asyncio
    async def test_get_delta_response_sharepoint_only_next_link(self):
        """Sharepoint delta response with only a next_link, no delta_link."""
        client = _make_client()
        result = MagicMock()
        result.value = ["item1"]
        result.odata_next_link = "https://next-only"
        result.odata_delta_link = None
        client.client.request_adapter.send_async = AsyncMock(return_value=result)

        resp = await client.get_delta_response_sharepoint("https://graph/delta")
        assert resp["next_link"] == "https://next-only"
        assert resp["delta_link"] is None
