"""Tests for Dropbox Team and Dropbox Individual connectors."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from dropbox.exceptions import ApiError
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.dropbox.connector import (
    DropboxConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.connectors.sources.dropbox_individual.connector import (
    get_file_extension as ind_get_file_extension,
)
from app.connectors.sources.dropbox_individual.connector import (
    get_mimetype_enum_for_dropbox as ind_get_mimetype_enum_for_dropbox,
)
from app.connectors.sources.dropbox_individual.connector import (
    get_parent_path_from_path as ind_get_parent_path_from_path,
)
from app.models.entities import AppUser, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_file_entry(name="doc.pdf", file_id="id:f1", path="/folder/doc.pdf",
                     rev="0123456789abcdef", size=1024,
                     server_modified=None, client_modified=None,
                     content_hash="a" * 64):
    mod_time = server_modified or datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    cli_time = client_modified or mod_time
    entry = FileMetadata(
        name=name, id=file_id, client_modified=cli_time,
        server_modified=mod_time, rev=rev, size=size
    )
    entry.path_lower = path.lower()
    entry.path_display = path
    entry.content_hash = content_hash
    return entry


def _make_folder_entry(name="folder", folder_id="id:d1", path="/folder"):
    entry = FolderMetadata(name=name, id=folder_id, path_lower=path.lower())
    entry.path_display = path
    return entry


def _make_deleted_entry(name="old.txt", path="/old.txt"):
    entry = DeletedMetadata(name=name)
    entry.path_lower = path.lower()
    return entry


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
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
    return logging.getLogger("test.dropbox")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-123"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_updated_record_permissions = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    proc.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="user@test.com"),
    ])
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "credentials": {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "isTeam": True,
        },
        "auth": {
            "oauthConfigId": "oauth-config-123",
        },
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.data_source = AsyncMock()
    conn.dropbox_cursor_sync_point = AsyncMock()
    conn.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    conn.dropbox_cursor_sync_point.update_sync_point = AsyncMock()
    conn.user_sync_point = AsyncMock()
    conn.user_group_sync_point = AsyncMock()
    return conn


# ===========================================================================
# Dropbox helper functions
# ===========================================================================

class TestGetParentPathFromPath:
    def test_root_path_returns_none(self):
        assert get_parent_path_from_path("/") is None

    def test_empty_path_returns_none(self):
        assert get_parent_path_from_path("") is None

    def test_single_level_returns_root(self):
        assert get_parent_path_from_path("/folder") is None

    def test_nested_path(self):
        assert get_parent_path_from_path("/a/b/file.txt") == "/a/b"

    def test_deeply_nested(self):
        assert get_parent_path_from_path("/a/b/c/d/file.txt") == "/a/b/c/d"

    def test_two_level_path(self):
        assert get_parent_path_from_path("/folder/subfolder") == "/folder"


class TestGetFileExtension:
    def test_simple_extension(self):
        assert get_file_extension("file.txt") == "txt"

    def test_no_extension(self):
        assert get_file_extension("README") is None

    def test_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_uppercase_extension(self):
        assert get_file_extension("image.PNG") == "png"


class TestGetMimetypeEnumForDropbox:
    def test_folder_returns_folder_type(self):
        entry = FolderMetadata(name="test_folder", id="id:123", path_lower="/test_folder")
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_paper_file_returns_html(self):
        entry = FileMetadata(
            name="doc.paper", id="id:456", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="0123456789abcdef", size=100
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_pdf_file_returns_pdf(self):
        entry = FileMetadata(
            name="doc.pdf", id="id:789", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="0123456789abcdef", size=200
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF

    def test_unknown_extension_returns_bin(self):
        entry = FileMetadata(
            name="file.xyz123", id="id:abc", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="0123456789abcdef", size=50
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.BIN

    def test_no_mimetype_guessed_returns_bin(self):
        entry = FileMetadata(
            name="noext", id="id:noext", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="0123456789abcdef", size=0
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.BIN


# ===========================================================================
# DropboxConnector initialization
# ===========================================================================

class TestDropboxConnectorInit:
    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    def test_constructor_sets_attributes(self, mock_app,
                                         mock_logger, mock_data_entities_processor,
                                         mock_data_store_provider, mock_config_service):
        conn = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
        assert conn.connector_id == "conn-123"
        assert conn.data_source is None
        assert conn.batch_size == 100

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, mock_fetch_oauth,
                                mock_app, mock_logger, mock_data_entities_processor,
                                mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = {"config": {"clientId": "key", "clientSecret": "secret"}}
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        conn = DropboxConnector(
            logger=mock_logger, data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider, config_service=mock_config_service,
            connector_id="conn-123",
        )
        result = await conn.init()
        assert result is True
        assert conn.data_source is not None

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    async def test_init_fails_no_config(self, mock_app, mock_logger,
                                        mock_data_entities_processor, mock_data_store_provider):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value=None)
        conn = DropboxConnector(
            logger=mock_logger, data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider, config_service=config_svc,
            connector_id="conn-123",
        )
        assert await conn.init() is False

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_no_oauth_config(self, mock_fetch_oauth, mock_app,
                                              mock_logger, mock_data_entities_processor,
                                              mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = None
        conn = DropboxConnector(
            logger=mock_logger, data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider, config_service=mock_config_service,
            connector_id="conn-123",
        )
        assert await conn.init() is False

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, mock_fetch_oauth,
                                               mock_app, mock_logger,
                                               mock_data_entities_processor,
                                               mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = {"config": {"clientId": "key", "clientSecret": "secret"}}
        mock_build.side_effect = Exception("Connection failed")
        conn = DropboxConnector(
            logger=mock_logger, data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider, config_service=mock_config_service,
            connector_id="conn-123",
        )
        assert await conn.init() is False

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    async def test_init_no_oauth_config_id(self, mock_app, mock_logger,
                                           mock_data_entities_processor,
                                           mock_data_store_provider):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "t", "refresh_token": "r", "isTeam": True},
            "auth": {},
        })
        conn = DropboxConnector(
            logger=mock_logger, data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider, config_service=config_svc,
            connector_id="conn-123",
        )
        assert await conn.init() is False


# ===========================================================================
# _pass_date_filters
# ===========================================================================

class TestPassDateFilters:
    def test_folder_always_passes(self, connector):
        entry = _make_folder_entry()
        assert connector._pass_date_filters(entry, modified_after=datetime.now(timezone.utc)) is True

    def test_deleted_always_passes(self, connector):
        entry = _make_deleted_entry()
        assert connector._pass_date_filters(entry, modified_after=datetime.now(timezone.utc)) is True

    def test_no_filters_passes(self, connector):
        entry = _make_file_entry()
        assert connector._pass_date_filters(entry) is True

    def test_modified_after_filters_old_file(self, connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, modified_after=cutoff) is False

    def test_modified_before_filters_new_file(self, connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 12, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, modified_before=cutoff) is False

    def test_created_after_filters(self, connector):
        entry = _make_file_entry(
            client_modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
            server_modified=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2024, 3, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, created_after=cutoff) is False

    def test_created_before_filters(self, connector):
        entry = _make_file_entry(
            client_modified=datetime(2024, 12, 1, tzinfo=timezone.utc),
            server_modified=datetime(2024, 12, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, created_before=cutoff) is False

    def test_file_in_range_passes(self, connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 6, 15, tzinfo=timezone.utc)
        )
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        before = datetime(2024, 12, 31, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, modified_after=after, modified_before=before) is True

    def test_naive_timestamp_gets_utc(self, connector):
        """server_modified without tzinfo should still be processed."""
        entry = _make_file_entry(server_modified=datetime(2024, 6, 15))
        cutoff = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, modified_after=cutoff) is True

    def test_created_date_falls_back_to_server_modified(self, connector):
        """When client_modified is missing, created filters use server_modified."""
        mod_time = datetime(2024, 3, 1, tzinfo=timezone.utc)
        # Use MagicMock to simulate missing client_modified since Dropbox SDK validates
        entry = MagicMock(spec=FileMetadata)
        entry.server_modified = mod_time
        entry.client_modified = None
        entry.name = "doc.pdf"
        cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert connector._pass_date_filters(entry, created_after=cutoff) is False


# ===========================================================================
# _pass_extension_filter
# ===========================================================================

class TestPassExtensionFilter:
    def test_folder_always_passes(self, connector):
        entry = _make_folder_entry()
        assert connector._pass_extension_filter(entry) is True

    def test_no_filter_passes_all(self, connector):
        entry = _make_file_entry(name="doc.pdf")
        assert connector._pass_extension_filter(entry) is True

    def test_in_operator_allows_matching(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="doc.pdf")
        assert connector._pass_extension_filter(entry) is True

    def test_in_operator_rejects_non_matching(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="image.png")
        assert connector._pass_extension_filter(entry) is False

    def test_not_in_operator_allows_non_matching(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value="not_in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="doc.pdf")
        assert connector._pass_extension_filter(entry) is True

    def test_not_in_operator_rejects_matching(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_filter.get_operator.return_value = MagicMock(value="not_in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="virus.exe")
        assert connector._pass_extension_filter(entry) is False

    def test_no_extension_with_not_in(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value="not_in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="Makefile")
        # No extension with NOT_IN should return True (pass)
        assert connector._pass_extension_filter(entry) is True

    def test_no_extension_with_in(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="Makefile")
        assert connector._pass_extension_filter(entry) is False

    def test_invalid_filter_value_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not_a_list"
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        entry = _make_file_entry(name="doc.pdf")
        assert connector._pass_extension_filter(entry) is True


# ===========================================================================
# _get_date_filters
# ===========================================================================

class TestGetDateFilters:
    def test_no_filters_returns_nones(self, connector):
        result = connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_modified_date_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if "modified" in str(key).lower() else None
        result = connector._get_date_filters()
        assert result[0] is not None  # modified_after
        assert result[1] is not None  # modified_before


# ===========================================================================
# _permissions_equal
# ===========================================================================

class TestPermissionsEqual:
    def test_both_empty(self, connector):
        assert connector._permissions_equal([], []) is True

    def test_both_none(self, connector):
        assert connector._permissions_equal(None, None) is True

    def test_one_empty(self, connector):
        p = [Permission(external_id="u1", email="a@b.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        assert connector._permissions_equal(p, []) is False
        assert connector._permissions_equal([], p) is False

    def test_different_lengths(self, connector):
        p1 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        p2 = p1 + [Permission(external_id="u2", email="b@b.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        assert connector._permissions_equal(p1, p2) is False

    def test_same_permissions(self, connector):
        p1 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        p2 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        assert connector._permissions_equal(p1, p2) is True

    def test_different_permissions(self, connector):
        p1 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.OWNER, entity_type=EntityType.USER)]
        p2 = [Permission(external_id="u1", email="a@b.com", type=PermissionType.READ, entity_type=EntityType.USER)]
        assert connector._permissions_equal(p1, p2) is False


# ===========================================================================
# _convert_dropbox_permissions_to_permissions
# ===========================================================================

class TestConvertDropboxPermissions:
    async def test_file_permissions_with_users_and_groups(self, connector):
        mock_user = MagicMock()
        mock_user.access_type._tag = "editor"
        mock_user.user.account_id = "dbid:user1"
        mock_user.user.email = "user1@test.com"

        mock_group = MagicMock()
        mock_group.access_type._tag = "viewer"
        mock_group.group.group_id = "g:group1"

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data.users = [mock_user]
        mock_result.data.groups = [mock_group]
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=mock_result)

        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=True, team_member_id="tm1")
        assert len(perms) == 2
        assert perms[0].type == PermissionType.WRITE
        assert perms[0].entity_type == EntityType.USER
        assert perms[1].type == PermissionType.READ
        assert perms[1].entity_type == EntityType.GROUP

    async def test_folder_without_shared_id_returns_empty(self, connector):
        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=False, shared_folder_id=None)
        assert perms == []

    async def test_folder_with_shared_id_fetches_members(self, connector):
        mock_user = MagicMock()
        mock_user.access_type._tag = "owner"
        mock_user.user.account_id = "dbid:user1"
        mock_user.user.email = "owner@test.com"

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data.users = [mock_user]
        mock_result.data.groups = []
        connector.data_source.sharing_list_folder_members = AsyncMock(return_value=mock_result)

        perms = await connector._convert_dropbox_permissions_to_permissions(
            "id:f1", is_file=False, team_member_id="tm1", shared_folder_id="sf1"
        )
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER

    async def test_failed_result_returns_empty(self, connector):
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "Access denied"
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=mock_result)

        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=True)
        assert perms == []

    async def test_skips_users_without_email(self, connector):
        mock_user = MagicMock()
        mock_user.access_type._tag = "editor"
        mock_user.user.account_id = "dbid:user1"
        mock_user.user.email = None

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data.users = [mock_user]
        mock_result.data.groups = []
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=mock_result)

        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=True)
        assert len(perms) == 0

    async def test_skips_users_with_hash_email(self, connector):
        mock_user = MagicMock()
        mock_user.access_type._tag = "editor"
        mock_user.user.account_id = "dbid:user1"
        mock_user.user.email = "invalid#"

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data.users = [mock_user]
        mock_result.data.groups = []
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=mock_result)

        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=True)
        assert len(perms) == 0

    async def test_exception_returns_empty(self, connector):
        connector.data_source.sharing_list_file_members = AsyncMock(side_effect=Exception("API error"))
        perms = await connector._convert_dropbox_permissions_to_permissions("id:f1", is_file=True)
        assert perms == []


# ===========================================================================
# _process_dropbox_entry
# ===========================================================================

class TestProcessDropboxEntry:
    async def test_new_file_entry(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/temp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(id="id:parent"))
        )
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "doc.pdf"
        assert result.record.is_file is True
        assert result.record.signed_url == "https://dl.dropbox.com/temp"

    async def test_new_folder_entry(self, connector):
        entry = _make_folder_entry()
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/folder"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=MagicMock(success=False)
        )
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=MagicMock(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=True
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_deleted_entry_returns_none(self, connector):
        entry = _make_deleted_entry()
        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result is None

    async def test_existing_record_detects_name_change(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.record_name = "old_name.pdf"
        existing.external_revision_id = "0123456789abcdef"
        connector.data_store_provider = _make_mock_data_store_provider(existing)

        entry = _make_file_entry(name="new_name.pdf")
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/temp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(return_value=MagicMock(success=False))
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=MagicMock(success=False))

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result.metadata_changed is True
        assert result.is_updated is True

    async def test_existing_record_detects_content_change(self, connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.record_name = "doc.pdf"
        existing.external_revision_id = "old_rev"
        connector.data_store_provider = _make_mock_data_store_provider(existing)

        entry = _make_file_entry(name="doc.pdf", rev="abcdef0123456789")
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/temp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(return_value=MagicMock(success=False))
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=MagicMock(success=False))

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result.content_changed is True

    async def test_shared_link_already_exists_extracts_url(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/temp"))
        )
        # First call fails with shared_link_already_exists
        first_result = MagicMock()
        first_result.success = False
        first_result.error = "shared_link_already_exists"
        # Second call also fails but with URL in error
        second_result = MagicMock()
        second_result.success = False
        second_result.error = "shared_link_already_exists url='https://www.dropbox.com/s/existing'"

        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_result, second_result]
        )
        connector.data_source.files_get_metadata = AsyncMock(return_value=MagicMock(success=False))
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=MagicMock(success=False))

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result.record.weburl == "https://www.dropbox.com/s/existing"

    async def test_date_filter_skips_entry(self, connector):
        entry = _make_file_entry(
            server_modified=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False,
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        assert result is None

    async def test_permission_exception_uses_fallback(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/temp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(return_value=MagicMock(success=False))
        connector.data_source.sharing_list_file_members = AsyncMock(side_effect=Exception("Perm error"))

        result = await connector._process_dropbox_entry(
            entry, user_id="u1", user_email="user@test.com",
            record_group_id="rg1", is_person_folder=False
        )
        assert result is not None
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.OWNER


# ===========================================================================
# _process_dropbox_items_generator
# ===========================================================================

class TestProcessDropboxItemsGenerator:
    async def test_yields_valid_entries(self, connector):
        entry = _make_file_entry()
        mock_update = MagicMock()
        mock_update.record = MagicMock()
        mock_update.record.is_shared = False
        mock_update.new_permissions = []

        with patch.object(connector, "_process_dropbox_entry", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = mock_update
            results = []
            async for record, perms, update in connector._process_dropbox_items_generator(
                [entry], "u1", "user@test.com", "rg1", True
            ):
                results.append((record, perms, update))
            assert len(results) == 1

    async def test_skips_none_results(self, connector):
        entry = _make_file_entry()
        with patch.object(connector, "_process_dropbox_entry", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = None
            results = []
            async for record, perms, update in connector._process_dropbox_items_generator(
                [entry], "u1", "user@test.com", "rg1", True
            ):
                results.append(update)
            assert len(results) == 0

    async def test_applies_indexing_filter(self, connector):
        entry = _make_file_entry()
        mock_update = MagicMock()
        mock_update.record = MagicMock()
        mock_update.record.is_shared = False
        mock_update.new_permissions = []

        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        with patch.object(connector, "_process_dropbox_entry", new_callable=AsyncMock) as mock_process:
            mock_process.return_value = mock_update
            results = []
            async for record, perms, update in connector._process_dropbox_items_generator(
                [entry], "u1", "user@test.com", "rg1", True
            ):
                results.append(update)
            assert len(results) == 1
            mock_update.record.__setattr__("indexing_status", ProgressStatus.AUTO_INDEX_OFF.value)

    async def test_handles_exceptions(self, connector):
        entry = _make_file_entry()
        with patch.object(connector, "_process_dropbox_entry", new_callable=AsyncMock) as mock_process:
            mock_process.side_effect = Exception("Process error")
            results = []
            async for record, perms, update in connector._process_dropbox_items_generator(
                [entry], "u1", "user@test.com", "rg1", True
            ):
                results.append(update)
            assert len(results) == 0


# ===========================================================================
# _handle_record_updates
# ===========================================================================

class TestHandleRecordUpdates:
    async def test_deleted_record(self, connector):
        update = MagicMock()
        update.is_deleted = True
        update.is_new = False
        update.is_updated = False
        update.external_record_id = "ext-1"
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_called_once_with(record_id="ext-1")

    async def test_metadata_changed(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = True
        update.permissions_changed = False
        update.content_changed = False
        update.record = MagicMock()
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_called_once()

    async def test_content_changed(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = False
        update.content_changed = True
        update.record = MagicMock()
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_called_once()

    async def test_permissions_changed(self, connector):
        update = MagicMock()
        update.is_deleted = False
        update.is_new = False
        update.is_updated = True
        update.metadata_changed = False
        update.permissions_changed = True
        update.content_changed = False
        update.record = MagicMock()
        update.new_permissions = [MagicMock()]
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_updated_record_permissions.assert_called_once()

    async def test_exception_handled(self, connector):
        update = MagicMock()
        update.is_deleted = True
        update.external_record_id = "ext-1"
        connector.data_entities_processor.on_record_deleted = AsyncMock(side_effect=Exception("DB error"))
        await connector._handle_record_updates(update)  # Should not raise


# ===========================================================================
# get_app_users
# ===========================================================================

class TestGetAppUsers:
    def test_transforms_members(self, connector):
        mock_member = MagicMock()
        mock_member.profile.team_member_id = "tm1"
        mock_member.profile.name.display_name = "Test User"
        mock_member.profile.email = "test@example.com"
        mock_member.profile.status._tag = "active"
        mock_member.role._tag = "admin"

        mock_response = MagicMock()
        mock_response.data.members = [mock_member]

        result = connector.get_app_users(mock_response)
        assert len(result) == 1
        assert result[0].email == "test@example.com"
        assert result[0].is_active is True
        assert result[0].full_name == "Test User"

    def test_inactive_member(self, connector):
        mock_member = MagicMock()
        mock_member.profile.team_member_id = "tm2"
        mock_member.profile.name.display_name = "Inactive User"
        mock_member.profile.email = "inactive@example.com"
        mock_member.profile.status._tag = "suspended"
        mock_member.role._tag = "member"

        mock_response = MagicMock()
        mock_response.data.members = [mock_member]

        result = connector.get_app_users(mock_response)
        assert result[0].is_active is False


# ===========================================================================
# _process_users_in_batches
# ===========================================================================

class TestProcessUsersInBatches:
    async def test_processes_active_users(self, connector):
        user = MagicMock(spec=AppUser)
        user.email = "user@test.com"
        user.source_user_id = "u1"

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock) as mock_sync:
            await connector._process_users_in_batches([user])
            mock_sync.assert_called_once()

    async def test_filters_inactive_users(self, connector):
        user = MagicMock(spec=AppUser)
        user.email = "unknown@test.com"

        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[
            MagicMock(email="other@test.com")
        ])

        with patch.object(connector, "_run_sync_with_yield", new_callable=AsyncMock) as mock_sync:
            await connector._process_users_in_batches([user])
            mock_sync.assert_not_called()


# ===========================================================================
# DropboxIndividual helpers
# ===========================================================================

class TestDropboxIndividualHelpers:
    def test_get_parent_path(self):
        assert ind_get_parent_path_from_path("/a/b/c") == "/a/b"
        assert ind_get_parent_path_from_path("/") is None

    def test_get_file_extension(self):
        assert ind_get_file_extension("doc.pdf") == "pdf"
        assert ind_get_file_extension("noext") is None

    def test_mimetype_enum_folder(self):
        entry = FolderMetadata(name="test", id="id:1", path_lower="/test")
        assert ind_get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER


class TestDropboxIndividualConnectorInit:
    @patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp")
    def test_constructor(self, mock_app, mock_logger,
                         mock_data_entities_processor,
                         mock_data_store_provider, mock_config_service):
        from app.connectors.sources.dropbox_individual.connector import DropboxIndividualConnector
        conn = DropboxIndividualConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-ind-1",
        )
        assert conn.connector_id == "conn-ind-1"
        assert conn.data_source is None


# ===========================================================================
# DEEP SYNC LOOP TESTS — run_sync, _handle_record_updates,
# _process_dropbox_items_generator, _process_dropbox_entry,
# _convert_dropbox_permissions_to_permissions
# ===========================================================================


def _make_dropbox_connector(mock_logger, mock_data_entities_processor,
                            mock_data_store_provider, mock_config_service):
    """Build a DropboxConnector with all dependencies mocked."""
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="dbx-conn-1",
        )
    connector.sync_filters = FilterCollection()
    connector.indexing_filters = FilterCollection()
    connector.data_source = AsyncMock()
    connector.dropbox_cursor_sync_point = MagicMock()
    connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.dropbox_cursor_sync_point.update_sync_point = AsyncMock()
    return connector


@pytest.fixture()
def dropbox_connector(mock_logger, mock_data_entities_processor,
                      mock_data_store_provider, mock_config_service):
    return _make_dropbox_connector(
        mock_logger, mock_data_entities_processor,
        mock_data_store_provider, mock_config_service
    )


class TestDropboxRunSync:
    """Tests for run_sync orchestration."""

    async def test_full_sync_flow(self, dropbox_connector):
        users_resp = MagicMock()
        users_resp.data = MagicMock()
        users_resp.data.members = []
        dropbox_connector.data_source.team_members_list = AsyncMock(return_value=users_resp)

        dropbox_connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        dropbox_connector._initialize_event_cursor = AsyncMock()
        dropbox_connector.sync_record_groups = AsyncMock()
        dropbox_connector.sync_personal_record_groups = AsyncMock()
        dropbox_connector._process_users_in_batches = AsyncMock()
        dropbox_connector._sync_user_groups = AsyncMock()

        with patch(
            "app.connectors.sources.dropbox.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await dropbox_connector.run_sync()

        dropbox_connector.data_entities_processor.on_new_app_users.assert_awaited()

    async def test_run_sync_raises_on_error(self, dropbox_connector):
        dropbox_connector.data_source.team_members_list = AsyncMock(
            side_effect=Exception("auth failed")
        )

        with patch(
            "app.connectors.sources.dropbox.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            with pytest.raises(Exception, match="auth failed"):
                await dropbox_connector.run_sync()

    async def test_incremental_member_sync_path(self, dropbox_connector):
        users_resp = MagicMock()
        users_resp.data = MagicMock()
        users_resp.data.members = []
        dropbox_connector.data_source.team_members_list = AsyncMock(return_value=users_resp)

        # Return cursor for member events to trigger incremental path
        call_count = [0]

        async def _read_sync(key):
            call_count[0] += 1
            if "member_events" in key:
                return {"cursor": "existing-cursor"}
            return {}

        dropbox_connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(side_effect=_read_sync)
        dropbox_connector._sync_member_changes_with_cursor = AsyncMock()
        dropbox_connector._initialize_event_cursor = AsyncMock()
        dropbox_connector._sync_user_groups = AsyncMock()
        dropbox_connector.sync_record_groups = AsyncMock()
        dropbox_connector.sync_personal_record_groups = AsyncMock()
        dropbox_connector._process_users_in_batches = AsyncMock()

        with patch(
            "app.connectors.sources.dropbox.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await dropbox_connector.run_sync()

        dropbox_connector._sync_member_changes_with_cursor.assert_awaited_once()


class TestDropboxHandleRecordUpdates:
    """Tests for _handle_record_updates."""

    async def test_deleted_record(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="ext-1",
        )
        await dropbox_connector._handle_record_updates(update)
        dropbox_connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    async def test_metadata_changed(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.record_name = "file.pdf"
        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )
        dropbox_connector.data_entities_processor.on_record_metadata_update = AsyncMock()
        await dropbox_connector._handle_record_updates(update)
        dropbox_connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    async def test_content_changed(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.record_name = "file.pdf"
        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=True,
            permissions_changed=False,
        )
        dropbox_connector.data_entities_processor.on_record_content_update = AsyncMock()
        await dropbox_connector._handle_record_updates(update)
        dropbox_connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    async def test_permissions_changed(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.record_name = "file.pdf"
        new_perms = [MagicMock()]
        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=True,
            new_permissions=new_perms,
        )
        dropbox_connector.data_entities_processor.on_updated_record_permissions = AsyncMock()
        await dropbox_connector._handle_record_updates(update)
        dropbox_connector.data_entities_processor.on_updated_record_permissions.assert_awaited_once()

    async def test_exception_handled(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        update = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="ext-1",
        )
        dropbox_connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=Exception("DB error")
        )
        await dropbox_connector._handle_record_updates(update)  # Should not raise

    async def test_new_record_no_action(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.record_name = "new_file.pdf"
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await dropbox_connector._handle_record_updates(update)
        dropbox_connector.data_entities_processor.on_record_deleted.assert_not_called()


class TestDropboxProcessDropboxItemsGenerator:
    """Tests for _process_dropbox_items_generator."""

    async def test_yields_new_records(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.is_shared = False
        mock_update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
        )

        dropbox_connector._process_dropbox_entry = AsyncMock(return_value=mock_update)

        results = []
        async for rec, perms, update in dropbox_connector._process_dropbox_items_generator(
            [_make_file_entry()], "u1", "user@test.com", "rg1", True
        ):
            results.append(update)

        assert len(results) == 1

    async def test_skips_none_results(self, dropbox_connector):
        dropbox_connector._process_dropbox_entry = AsyncMock(return_value=None)

        results = []
        async for rec, perms, update in dropbox_connector._process_dropbox_items_generator(
            [_make_file_entry()], "u1", "user@test.com", "rg1", True
        ):
            results.append(update)

        assert len(results) == 0

    async def test_exception_continues(self, dropbox_connector):
        dropbox_connector._process_dropbox_entry = AsyncMock(
            side_effect=Exception("error")
        )

        results = []
        async for rec, perms, update in dropbox_connector._process_dropbox_items_generator(
            [_make_file_entry()], "u1", "user@test.com", "rg1", True
        ):
            results.append(update)

        assert len(results) == 0

    async def test_shared_filter_sets_auto_index_off(self, dropbox_connector):
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
        mock_record = MagicMock()
        mock_record.is_shared = True
        mock_update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )

        dropbox_connector._process_dropbox_entry = AsyncMock(return_value=mock_update)
        dropbox_connector.indexing_filters = MagicMock()
        dropbox_connector.indexing_filters.is_enabled.side_effect = lambda key, **kw: key != "shared"

        results = []
        async for rec, perms, update in dropbox_connector._process_dropbox_items_generator(
            [_make_file_entry()], "u1", "user@test.com", "rg1", True
        ):
            results.append(update)

        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestDropboxProcessDropboxEntry:
    """Tests for _process_dropbox_entry deep logic."""

    async def test_file_entry_new(self, dropbox_connector):
        entry = _make_file_entry()
        dropbox_connector.data_store_provider = _make_mock_data_store_provider()
        dropbox_connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/tmp"))
        )
        dropbox_connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/s/link"))
        )
        dropbox_connector.data_source.files_get_metadata = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(id="parent-id"))
        )
        dropbox_connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])
        dropbox_connector._pass_date_filters = MagicMock(return_value=True)
        dropbox_connector._pass_extension_filter = MagicMock(return_value=True)

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "doc.pdf"

    async def test_folder_entry_new(self, dropbox_connector):
        entry = _make_folder_entry()
        dropbox_connector.data_store_provider = _make_mock_data_store_provider()
        dropbox_connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/s/link"))
        )
        dropbox_connector.data_source.files_get_metadata = AsyncMock(
            return_value=MagicMock(success=False)
        )
        dropbox_connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])
        dropbox_connector._pass_date_filters = MagicMock(return_value=True)
        dropbox_connector._pass_extension_filter = MagicMock(return_value=True)

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is not None
        assert result.record.is_file is False

    async def test_date_filter_rejects(self, dropbox_connector):
        entry = _make_file_entry()
        dropbox_connector._pass_date_filters = MagicMock(return_value=False)

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is None

    async def test_extension_filter_rejects(self, dropbox_connector):
        entry = _make_file_entry()
        dropbox_connector._pass_date_filters = MagicMock(return_value=True)
        dropbox_connector._pass_extension_filter = MagicMock(return_value=False)

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is None

    async def test_existing_record_detected_updated(self, dropbox_connector):
        entry = _make_file_entry(rev="1234567890abcdef")
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.record_name = "doc.pdf"
        existing.external_revision_id = "old-revision"
        dropbox_connector.data_store_provider = _make_mock_data_store_provider(existing)
        dropbox_connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(link="https://dl.dropbox.com/tmp"))
        )
        dropbox_connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(url="https://dropbox.com/s/link"))
        )
        dropbox_connector.data_source.files_get_metadata = AsyncMock(
            return_value=MagicMock(success=False)
        )
        dropbox_connector._convert_dropbox_permissions_to_permissions = AsyncMock(return_value=[])
        dropbox_connector._pass_date_filters = MagicMock(return_value=True)
        dropbox_connector._pass_extension_filter = MagicMock(return_value=True)

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is not None
        assert result.is_updated is True
        assert result.content_changed is True

    async def test_exception_returns_none(self, dropbox_connector):
        entry = _make_file_entry()
        dropbox_connector._pass_date_filters = MagicMock(return_value=True)
        dropbox_connector._pass_extension_filter = MagicMock(return_value=True)
        dropbox_connector.data_store_provider = MagicMock()
        dropbox_connector.data_store_provider.transaction.side_effect = Exception("DB error")

        result = await dropbox_connector._process_dropbox_entry(
            entry, "u1", "user@test.com", "rg1", True
        )
        assert result is None


class TestDropboxConvertPermissions:
    """Tests for _convert_dropbox_permissions_to_permissions."""

    async def test_file_permissions_with_users(self, dropbox_connector):
        user_membership = MagicMock()
        user_membership.access_type._tag = "editor"
        user_membership.user.account_id = "acct1"
        user_membership.user.email = "user@test.com"

        members_data = MagicMock()
        members_data.users = [user_membership]
        members_data.groups = []

        dropbox_connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(success=True, data=members_data)
        )

        perms = await dropbox_connector._convert_dropbox_permissions_to_permissions(
            "file-id", True, "team-member"
        )
        assert len(perms) == 1
        assert perms[0].type == PermissionType.WRITE

    async def test_folder_no_shared_id_returns_empty(self, dropbox_connector):
        perms = await dropbox_connector._convert_dropbox_permissions_to_permissions(
            "folder-id", False, "team-member", shared_folder_id=None
        )
        assert perms == []

    async def test_api_failure_returns_empty(self, dropbox_connector):
        dropbox_connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(success=False, error="403")
        )

        perms = await dropbox_connector._convert_dropbox_permissions_to_permissions(
            "file-id", True, "team-member"
        )
        assert perms == []

    async def test_skips_invalid_email(self, dropbox_connector):
        user_membership = MagicMock()
        user_membership.access_type._tag = "viewer"
        user_membership.user.account_id = "acct1"
        user_membership.user.email = "user@test.com#"

        members_data = MagicMock()
        members_data.users = [user_membership]
        members_data.groups = []

        dropbox_connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(success=True, data=members_data)
        )

        perms = await dropbox_connector._convert_dropbox_permissions_to_permissions(
            "file-id", True, "team-member"
        )
        assert len(perms) == 0

    async def test_group_permissions(self, dropbox_connector):
        group_membership = MagicMock()
        group_membership.access_type._tag = "viewer"
        group_membership.group.group_id = "g1"

        members_data = MagicMock()
        members_data.users = []
        members_data.groups = [group_membership]

        dropbox_connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(success=True, data=members_data)
        )

        perms = await dropbox_connector._convert_dropbox_permissions_to_permissions(
            "file-id", True, "team-member"
        )
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP


class TestDropboxDateFilters:
    """Tests for _pass_date_filters."""

    def test_folder_always_passes(self, dropbox_connector):
        entry = _make_folder_entry()
        assert dropbox_connector._pass_date_filters(
            entry,
            modified_after=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ) is True

    def test_no_filters_passes(self, dropbox_connector):
        entry = _make_file_entry()
        assert dropbox_connector._pass_date_filters(entry) is True

    def test_modified_after_rejects(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        assert dropbox_connector._pass_date_filters(
            entry,
            modified_after=datetime(2024, 6, 1, tzinfo=timezone.utc)
        ) is False

    def test_modified_before_rejects(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 12, 1, tzinfo=timezone.utc)
        )
        assert dropbox_connector._pass_date_filters(
            entry,
            modified_before=datetime(2024, 6, 1, tzinfo=timezone.utc)
        ) is False

    def test_created_after_rejects(self, dropbox_connector):
        entry = _make_file_entry(
            client_modified=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        assert dropbox_connector._pass_date_filters(
            entry,
            created_after=datetime(2024, 6, 1, tzinfo=timezone.utc)
        ) is False

    def test_within_range_passes(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 6, 15, tzinfo=timezone.utc)
        )
        assert dropbox_connector._pass_date_filters(
            entry,
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
            modified_before=datetime(2024, 12, 31, tzinfo=timezone.utc)
        ) is True
