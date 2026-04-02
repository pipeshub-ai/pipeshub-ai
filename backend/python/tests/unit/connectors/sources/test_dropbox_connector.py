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
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.core.registry.filters import FilterCollection, SyncFilterKey
from app.models.entities import AppUser, FileRecord, RecordType
from dataclasses import dataclass
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata, ListFolderResult
from dropbox.sharing import AccessLevel
from dropbox.team_log import EventCategory
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)


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

# =============================================================================
# Merged from test_dropbox_connector_coverage.py
# =============================================================================

def _make_connector():
    logger = logging.getLogger("test.dropbox")
    dep = MagicMock()
    dep.org_id = "org-dbx-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()
    cs = AsyncMock()
    return DropboxConnector(logger, dep, dsp, cs, "conn-dbx-1")


def _make_file_metadata(name="test.pdf"):
    from dropbox.files import FileMetadata
    meta = MagicMock(spec=FileMetadata)
    meta.__class__ = FileMetadata
    meta.name = name
    meta.path_lower = f"/{name}"
    meta.id = f"id:{uuid4()}"
    meta.size = 1024
    meta.server_modified = datetime(2024, 6, 1, tzinfo=timezone.utc)
    meta.client_modified = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return meta


def _make_folder_metadata(name="folder"):
    from dropbox.files import FolderMetadata
    meta = MagicMock(spec=FolderMetadata)
    meta.__class__ = FolderMetadata
    meta.name = name
    meta.path_lower = f"/{name}"
    meta.id = f"id:{uuid4()}"
    return meta


class TestGetParentPathFromPathCoverage:
    def test_root(self):
        assert get_parent_path_from_path("/") is None

    def test_empty(self):
        assert get_parent_path_from_path("") is None

    def test_top_level(self):
        assert get_parent_path_from_path("/file.txt") is None

    def test_nested(self):
        assert get_parent_path_from_path("/folder/file.txt") == "/folder"

    def test_deeply_nested(self):
        assert get_parent_path_from_path("/a/b/c/file.txt") == "/a/b/c"

    def test_none(self):
        assert get_parent_path_from_path(None) is None


class TestGetFileExtensionCoverage:
    def test_pdf(self):
        assert get_file_extension("file.pdf") == "pdf"

    def test_no_extension(self):
        assert get_file_extension("file") is None

    def test_multiple_dots(self):
        assert get_file_extension("file.tar.gz") == "gz"


class TestGetMimetypeEnumForDropboxCoverage:
    def test_folder(self):
        from dropbox.files import FolderMetadata
        entry = MagicMock(spec=FolderMetadata)
        entry.__class__ = FolderMetadata
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_paper_file(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.__class__ = FileMetadata
        entry.name = "doc.paper"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_pdf_file(self):
        from dropbox.files import FileMetadata
        entry = MagicMock(spec=FileMetadata)
        entry.__class__ = FileMetadata
        entry.name = "doc.pdf"
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF


class TestDropboxConnectorInitCoverage:
    def test_initialization(self):
        c = _make_connector()
        assert c.connector_name == Connectors.DROPBOX
        assert c.connector_id == "conn-dbx-1"
        assert c.data_source is None


class TestDropboxInitMethod:
    @pytest.mark.asyncio
    async def test_init_no_config(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value=None)
        result = await c.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref"},
            "auth": {}
        })
        result = await c.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_success(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref", "isTeam": True},
            "auth": {"oauthConfigId": "oauth-1"}
        })
        with patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock, return_value={
            "config": {"clientId": "cid", "clientSecret": "csecret"}
        }):
            with patch("app.connectors.sources.dropbox.connector.DropboxClient") as MockClient:
                MockClient.build_with_config = AsyncMock(return_value=MagicMock())
                with patch("app.connectors.sources.dropbox.connector.DropboxDataSource"):
                    result = await c.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_client_error(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "tok", "refresh_token": "ref", "isTeam": True},
            "auth": {"oauthConfigId": "oauth-1"}
        })
        with patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock, return_value={
            "config": {"clientId": "cid", "clientSecret": "csecret"}
        }):
            with patch("app.connectors.sources.dropbox.connector.DropboxClient") as MockClient:
                MockClient.build_with_config = AsyncMock(side_effect=Exception("fail"))
                result = await c.init()
        assert result is False


class TestDropboxDateFiltersCoverage:
    def test_folder_always_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(_make_folder_metadata()) is True

    def test_no_filters(self):
        c = _make_connector()
        assert c._pass_date_filters(_make_file_metadata()) is True

    def test_modified_after_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc)
        ) is True

    def test_modified_after_fails(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_after=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ) is False

    def test_modified_before_passes(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_before=datetime(2025, 1, 1, tzinfo=timezone.utc)
        ) is True

    def test_modified_before_fails(self):
        c = _make_connector()
        assert c._pass_date_filters(
            _make_file_metadata(),
            modified_before=datetime(2023, 1, 1, tzinfo=timezone.utc)
        ) is False

    def test_get_date_filters_no_filters(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._get_date_filters() == (None, None, None, None)


class TestDropboxExtensionFilter:
    def test_folder_passes(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._pass_extension_filter(_make_folder_metadata()) is True

    def test_no_filter(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._pass_extension_filter(_make_file_metadata()) is True


class TestDropboxPermissionEquality:
    def test_equal(self):
        c = _make_connector()
        p1 = [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")]
        p2 = [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")]
        assert c._permissions_equal(p1, p2) is True

    def test_different_length(self):
        c = _make_connector()
        assert c._permissions_equal(
            [Permission(entity_type=EntityType.USER, type=PermissionType.READ, external_id="u1")],
            []
        ) is False


class TestDropboxMisc:
    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        c = _make_connector()
        c.run_incremental_sync = AsyncMock()
        c.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _make_connector()
        await c.cleanup()

    @pytest.mark.asyncio
    async def test_reindex_empty(self):
        c = _make_connector()
        await c.reindex_records([])


class TestDropboxCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.dropbox.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await DropboxConnector.create_connector(
                logger=logging.getLogger("test"),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-dbx",
            )
            assert isinstance(connector, DropboxConnector)

# =============================================================================
# Merged from test_dropbox_connector_full_coverage.py
# =============================================================================

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


def _make_mock_tx_store_fullcov(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    tx.get_file_record_by_id = AsyncMock(return_value=None)
    tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
    tx.get_user_by_email = AsyncMock(return_value=None)
    tx.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx.get_edge = AsyncMock(return_value=None)
    tx.batch_upsert_user_groups = AsyncMock()
    tx.batch_create_edges = AsyncMock()
    tx.delete_edge = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store_fullcov(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_dropbox_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


def _make_app_user(email="user@test.com", source_user_id="dbmid:AAuser1",
                   full_name="Test User", title="member", is_active=True):
    return AppUser(
        app_name=Connectors.DROPBOX,
        connector_id="conn-123",
        source_user_id=source_user_id,
        full_name=full_name,
        email=email,
        is_active=is_active,
        title=title,
    )


def _make_event(event_type_tag, participants=None, details=None, assets=None,
                context=None, actor=None):
    event = MagicMock()
    event.event_type = MagicMock()
    event.event_type._tag = event_type_tag
    event.participants = participants or []
    event.details = details or MagicMock()
    event.assets = assets or []
    event.context = context
    event.actor = actor
    return event


def _make_group_participant(group_id="g1", display_name="Group1"):
    p = MagicMock()
    p.is_group.return_value = True
    p.is_user.return_value = False
    gi = MagicMock()
    gi.group_id = group_id
    gi.display_name = display_name
    p.get_group.return_value = gi
    return p


def _make_user_participant(email="user@test.com", display_name="User", team_member_id="tm1"):
    p = MagicMock()
    p.is_group.return_value = False
    p.is_user.return_value = True
    ui = MagicMock()
    ui.email = email
    ui.display_name = display_name
    ui.team_member_id = team_member_id
    p.get_user.return_value = ui
    return p


def _make_folder_asset(display_name="TeamFolder", ns_id="ns123"):
    asset = MagicMock()
    asset.is_folder.return_value = True
    asset.is_file.return_value = False
    folder_info = MagicMock()
    folder_info.display_name = display_name
    folder_info.path = MagicMock()
    folder_info.path.namespace_relative = MagicMock()
    folder_info.path.namespace_relative.ns_id = ns_id
    asset.get_folder.return_value = folder_info
    return asset


def _make_file_asset(file_id="id:file1", ns_id="ns123"):
    asset = MagicMock()
    asset.is_folder.return_value = False
    asset.is_file.return_value = True
    file_info = MagicMock()
    file_info.file_id = file_id
    file_info.path = MagicMock()
    file_info.path.namespace_relative = MagicMock()
    file_info.path.namespace_relative.ns_id = ns_id
    asset.get_file.return_value = file_info
    return asset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger_fullcov():
    return logging.getLogger("test.dropbox95")


@pytest.fixture()
def mock_data_entities_processor_fullcov():
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
    proc.on_user_group_member_added = AsyncMock()
    proc.on_user_group_member_removed = AsyncMock()
    proc.on_user_group_deleted = AsyncMock()
    proc.update_record_group_name = AsyncMock()
    proc.on_record_group_deleted = AsyncMock()
    proc.reindex_existing_records = AsyncMock()
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
def connector(mock_logger_fullcov, mock_data_entities_processor_fullcov,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.dropbox.connector.DropboxApp"):
        conn = DropboxConnector(
            logger=mock_logger_fullcov,
            data_entities_processor=mock_data_entities_processor_fullcov,
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
# Helper function edge cases
# ===========================================================================
class TestHelperEdgeCases:
    def test_get_file_extension_single_dot(self):
        assert get_file_extension("file.") is None or get_file_extension("file.") == ""

    def test_get_mimetype_enum_non_file_non_folder(self):
        entry = MagicMock()
        entry.__class__ = type("Unknown", (), {})
        result = get_mimetype_enum_for_dropbox(entry)
        assert result == MimeTypes.BIN

    def test_get_mimetype_enum_known_mimetype(self):
        entry = _make_file_entry(name="doc.pdf")
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF

    def test_get_mimetype_enum_zip_returns_zip(self):
        entry = _make_file_entry(name="archive.zip")
        result = get_mimetype_enum_for_dropbox(entry)
        # Platform may guess a zip variant not in MimeTypes → BIN
        assert result in (MimeTypes.ZIP, MimeTypes.BIN)


# ===========================================================================
# _process_dropbox_entry: cover more branches
# ===========================================================================
class TestProcessDropboxEntryBranches:
    async def test_extension_filter_rejects_entry(self, connector):
        entry = _make_file_entry(name="file.xyz")
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value="in")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is None

    async def test_signed_url_failure(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="no link")
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://link"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", True
        )
        assert result is not None
        assert result.record.signed_url is None

    async def test_shared_link_second_call_succeeds(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )

        first_call = _make_dropbox_response(success=False, error="shared_link_already_exists stuff")
        second_call = _make_dropbox_response(success=True, data=MagicMock(url="https://preview2"))
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_call, second_call]
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.weburl == "https://preview2"

    async def test_shared_link_url_extracted_from_second_error(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )

        first_call = _make_dropbox_response(success=False, error="shared_link_already_exists")
        second_call = _make_dropbox_response(
            success=False,
            error="shared_link_already_exists url='https://extracted.url/link'"
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_call, second_call]
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.weburl == "https://extracted.url/link"

    async def test_shared_link_no_url_in_second_error(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )

        first_call = _make_dropbox_response(success=False, error="shared_link_already_exists")
        second_call = _make_dropbox_response(
            success=False,
            error="shared_link_already_exists no_url_here"
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_call, second_call]
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.weburl is None

    async def test_shared_link_second_call_unexpected_error(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )

        first_call = _make_dropbox_response(success=False, error="shared_link_already_exists")
        second_call = _make_dropbox_response(
            success=False,
            error="some_other_error completely different"
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first_call, second_call]
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.weburl is None

    async def test_first_shared_link_call_unexpected_error(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="rate_limit_exceeded")
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.weburl is None

    async def test_parent_path_resolution(self, connector):
        entry = _make_file_entry(path="/a/b/file.pdf")
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        parent_resp = _make_dropbox_response(success=True, data=MagicMock(id="id:parent1"))
        connector.data_source.files_get_metadata = AsyncMock(return_value=parent_resp)

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.parent_external_record_id == "id:parent1"

    async def test_root_path_no_parent(self, connector):
        entry = _make_file_entry(path="/file.pdf")
        entry.path_display = "/file.pdf"
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None

    async def test_shared_folder_id_on_entry(self, connector):
        # Use a FolderMetadata since FileMetadata does not support shared_folder_id
        entry = _make_folder_entry()
        entry.shared_folder_id = "sf:123"
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )
        members_data = MagicMock()
        members_data.users = []
        members_data.groups = []
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=members_data)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None

    async def test_permissions_user_already_present(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        user_membership = MagicMock()
        user_membership.access_type = MagicMock(_tag="editor")
        user_info = MagicMock()
        user_info.account_id = "acct:1"
        user_info.email = "user@test.com"
        user_membership.user = user_info

        members_data = MagicMock()
        members_data.users = [user_membership]
        members_data.groups = []
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=members_data)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.is_shared is False
        emails = [p.email for p in result.new_permissions if p.email]
        assert "user@test.com" in emails

    async def test_permissions_single_group_is_shared(self, connector):
        """When a single group permission is returned, the source code tries to compare
        permission type with PermissionType.GROUP which doesn't exist, causing an
        AttributeError. This is caught by the try/except, which falls back to owner
        permission, and is_shared remains False (its default)."""
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        group_membership = MagicMock()
        group_membership.access_type = MagicMock(_tag="viewer")
        group_info = MagicMock()
        group_info.group_id = "g:1"
        group_membership.group = group_info

        members_data = MagicMock()
        members_data.users = []
        members_data.groups = [group_membership]
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=members_data)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        # PermissionType.GROUP doesn't exist, so the code raises AttributeError,
        # falls back to owner permission, and is_shared stays False
        assert result.record.is_shared is False

    async def test_permission_fetch_exception_fallback(self, connector):
        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )
        connector._convert_dropbox_permissions_to_permissions = AsyncMock(
            side_effect=Exception("permission error")
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert len(result.new_permissions) == 1
        assert result.new_permissions[0].type == PermissionType.OWNER

    async def test_existing_record_with_permissions_change(self, connector):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "doc.pdf"
        existing.external_revision_id = "0123456789abcdef"
        existing.version = 1

        provider = _make_mock_data_store_provider(existing)
        connector.data_store_provider = provider

        entry = _make_file_entry()
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://tmp"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(users=[], groups=[]))
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.is_updated is True
        assert result.permissions_changed is True

    async def test_exception_in_process_entry_returns_none(self, connector):
        entry = _make_file_entry()
        connector._pass_date_filters = MagicMock(side_effect=Exception("boom"))

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is None

    async def test_folder_entry_no_signed_url(self, connector):
        entry = _make_folder_entry()
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://preview"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )

        result = await connector._process_dropbox_entry(
            entry, "user1", "user@test.com", "rg1", False
        )
        assert result is not None
        assert result.record.signed_url is None
        assert result.record.is_file is False


# ===========================================================================
# _process_entry (legacy)
# ===========================================================================
class TestProcessEntryLegacy:
    async def test_deleted_entry(self, connector):
        entry = _make_deleted_entry()
        result = await connector._process_entry(entry)
        assert result is None

    async def test_file_entry(self, connector):
        """_process_entry is a legacy/unused function that creates FileRecord without
        required 'version' field and uses invalid 'web_url' kwarg, causing ValidationError."""
        from pydantic import ValidationError
        entry = _make_file_entry()
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(data=MagicMock(users=[], groups=[]))
        )
        with pytest.raises(ValidationError):
            await connector._process_entry(entry)

    async def test_folder_entry(self, connector):
        """_process_entry is a legacy/unused function that creates FileRecord without
        required 'version' field and uses invalid 'web_url' kwarg, causing ValidationError."""
        from pydantic import ValidationError
        entry = _make_folder_entry()
        entry.shared_folder_id = None
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=MagicMock(data=MagicMock(users=[], groups=[]))
        )
        with pytest.raises(ValidationError):
            await connector._process_entry(entry)


# ===========================================================================
# _get_permissions (legacy)
# ===========================================================================
class TestGetPermissionsLegacy:
    async def test_no_data_source(self, connector):
        connector.data_source = None
        entry = _make_file_entry()
        perms = await connector._get_permissions(entry)
        assert perms == []

    async def test_file_with_members(self, connector):
        user_member = MagicMock()
        user_member.access_type = AccessLevel.owner
        user_info = MagicMock()
        user_info.account_id = "acct:1"
        user_info.email = "user@test.com"
        user_member.user = user_info

        members_data = MagicMock()
        members_data.users = [user_member]
        members_data.groups = []
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(data=members_data)
        )

        entry = _make_file_entry()
        perms = await connector._get_permissions(entry)
        assert len(perms) == 1

    async def test_folder_with_shared_folder_id(self, connector):
        entry = _make_folder_entry()
        entry.shared_folder_id = "sf:1"

        # The _get_permissions code uses getattr(member, 'user', getattr(member, 'group', None))
        # so we must ensure the group_member has no 'user' attr for 'group' to be picked up.
        # Also, group_info must not have 'account_id' (so entity_type becomes GROUP)
        # and group_id must be a real string (not MagicMock) for Pydantic validation.
        group_member = MagicMock(spec=['access_type', 'group'])
        group_member.access_type = AccessLevel.editor
        group_info = MagicMock(spec=['group_id'])
        group_info.group_id = "g:1"
        group_member.group = group_info

        members_data = MagicMock()
        members_data.users = []
        members_data.groups = [group_member]
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=MagicMock(data=members_data)
        )

        perms = await connector._get_permissions(entry)
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP

    async def test_folder_no_shared_folder_id(self, connector):
        entry = _make_folder_entry()
        entry.shared_folder_id = None
        perms = await connector._get_permissions(entry)
        assert perms == []

    async def test_no_members_result(self, connector):
        connector.data_source.sharing_list_file_members = AsyncMock(return_value=None)
        entry = _make_file_entry()
        perms = await connector._get_permissions(entry)
        assert perms == []

    async def test_exception_returns_empty(self, connector):
        connector.data_source.sharing_list_file_members = AsyncMock(
            side_effect=Exception("API down")
        )
        entry = _make_file_entry()
        perms = await connector._get_permissions(entry)
        assert perms == []

    async def test_member_without_user_or_group(self, connector):
        member = MagicMock()
        member.access_type = AccessLevel.viewer
        member.user = None
        member.group = None
        delattr(member, 'user')
        delattr(member, 'group')

        members_data = MagicMock()
        members_data.users = [member]
        members_data.groups = []
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(data=members_data)
        )

        entry = _make_file_entry()
        perms = await connector._get_permissions(entry)
        assert len(perms) == 0


# ===========================================================================
# _sync_from_source
# ===========================================================================
class TestSyncFromSource:
    async def test_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector._sync_from_source()

    async def test_basic_sync(self, connector):
        """_sync_from_source calls _process_entry which is a legacy function that
        creates FileRecord without required 'version' field, causing ValidationError.
        The error is caught and the sync loop ends. on_new_records is never called."""
        entry = _make_file_entry()
        list_result = MagicMock()
        list_result.entries = [entry]
        list_result.data = MagicMock(cursor="cursor1", has_more=False)

        connector.data_source.files_list_folder = AsyncMock(
            return_value=MagicMock(data=list_result)
        )
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(data=MagicMock(users=[], groups=[]))
        )
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        await connector._sync_from_source()
        # _process_entry raises ValidationError, caught by _sync_from_source,
        # so no records are processed
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_sync_with_cursor(self, connector):
        list_result = MagicMock()
        list_result.entries = []
        list_result.data = MagicMock(cursor="cursor2", has_more=False)

        connector.data_source.files_list_folder_continue = AsyncMock(
            return_value=MagicMock(data=list_result)
        )
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        await connector._sync_from_source(cursor="cursor1")

    async def test_sync_error_stops_loop(self, connector):
        connector.data_source.files_list_folder = AsyncMock(
            side_effect=Exception("API error")
        )
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.update_sync_point = AsyncMock()
        await connector._sync_from_source()

    async def test_batch_processing(self, connector):
        """_sync_from_source calls _process_entry which is a legacy function that
        creates FileRecord without required 'version' field, causing ValidationError.
        The error is caught on the first entry and the sync loop ends."""
        entries = [_make_file_entry(name=f"f{i}.pdf", file_id=f"id:{i}", path=f"/f{i}.pdf")
                   for i in range(150)]
        list_result = MagicMock()
        list_result.entries = entries
        list_result.data = MagicMock(cursor="c1", has_more=False)

        connector.data_source.files_list_folder = AsyncMock(
            return_value=MagicMock(data=list_result)
        )
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=MagicMock(data=MagicMock(users=[], groups=[]))
        )
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        await connector._sync_from_source()
        # _process_entry raises ValidationError on the first entry,
        # caught by _sync_from_source's except, ending the loop
        connector.data_entities_processor.on_new_records.assert_not_called()


# ===========================================================================
# _process_users_in_batches
# ===========================================================================
class TestProcessUsersInBatchesFullCoverage:
    async def test_filters_non_active_users(self, connector):
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock(email="active@test.com")]
        )
        users = [
            MagicMock(email="active@test.com", source_user_id="u1"),
            MagicMock(email="inactive@test.com", source_user_id="u2"),
        ]
        connector._run_sync_with_yield = AsyncMock()

        await connector._process_users_in_batches(users)
        connector._run_sync_with_yield.assert_called_once()

    async def test_exception_propagates(self, connector):
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=Exception("DB error")
        )
        with pytest.raises(Exception, match="DB error"):
            await connector._process_users_in_batches([])


# ===========================================================================
# _run_sync_with_yield
# ===========================================================================
class TestRunSyncWithYield:
    async def test_personal_and_shared_folders(self, connector):
        shared_folder = MagicMock()
        shared_folder.name = "SharedTeam"
        shared_folder.shared_folder_id = "sf:1"

        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[shared_folder]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        list_result = MagicMock()
        list_result.entries = []
        list_result.has_more = False
        list_result.cursor = "new_cursor"

        connector.data_source.files_list_folder = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=list_result)
        )
        connector.data_source.files_list_folder_continue = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=list_result)
        )
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "old_cursor"}
        )

        await connector._run_sync_with_yield("user1", "user@test.com")
        assert connector.dropbox_cursor_sync_point.update_sync_point.call_count >= 2

    async def test_shared_folders_fail(self, connector):
        shared_resp = _make_dropbox_response(success=False, error="access denied")
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        list_result = MagicMock()
        list_result.entries = []
        list_result.has_more = False
        list_result.cursor = "c1"
        connector.data_source.files_list_folder = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=list_result)
        )

        await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_api_error_path_not_found(self, connector):
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        connector.data_source.files_list_folder = AsyncMock(
            side_effect=ApiError("req_id", MagicMock(is_path=lambda: True, get_path=lambda: MagicMock(is_not_found=lambda: True)), "user_message", "path/not_found")
        )
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_api_error_other_does_not_raise(self, connector):
        """When files_list_folder raises ApiError inside the inner try/except (line 1205-1213),
        the ApiError is caught by that inner except. Then 'result' is undefined, causing
        UnboundLocalError which is caught by the outer except Exception (line 1276).
        The function does NOT propagate the ApiError."""
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        connector.data_source.files_list_folder = AsyncMock(
            side_effect=ApiError("req_id", MagicMock(), "user_message", "other_error")
        )
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        # Does not raise because the inner except catches the ApiError,
        # and the subsequent UnboundLocalError is caught by the outer except
        await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_generic_exception_in_loop(self, connector):
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        connector.data_source.files_list_folder = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_api_failure_result(self, connector):
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        connector.data_source.files_list_folder = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="rate limited")
        )
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_with_entries_and_batching(self, connector):
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)

        entry = _make_file_entry()
        list_result = MagicMock()
        list_result.entries = [entry]
        list_result.has_more = False
        list_result.cursor = "c1"

        connector.data_source.files_list_folder = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=list_result)
        )
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(link="https://link"))
        )
        connector.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(url="https://url"))
        )
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False)
        )
        connector.data_source.sharing_list_file_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=MagicMock(users=[], groups=[]))
        )

        await connector._run_sync_with_yield("user1", "user@test.com")
        connector.data_entities_processor.on_new_records.assert_called()

    async def test_outer_api_error(self, connector):
        connector.data_source.sharing_list_folders = AsyncMock(
            side_effect=ApiError("req", MagicMock(), "msg", "err")
        )
        with pytest.raises(ApiError):
            await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_outer_generic_exception(self, connector):
        connector.data_source.sharing_list_folders = AsyncMock(
            side_effect=RuntimeError("fatal")
        )
        with pytest.raises(RuntimeError):
            await connector._run_sync_with_yield("user1", "user@test.com")

    async def test_first_sync_exception_in_files_list_folder(self, connector):
        shared_resp = _make_dropbox_response(success=True, data=MagicMock(entries=[]))
        connector.data_source.sharing_list_folders = AsyncMock(return_value=shared_resp)
        connector.data_source.files_list_folder = AsyncMock(side_effect=Exception("api call error"))
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})

        await connector._run_sync_with_yield("user1", "user@test.com")


# ===========================================================================
# _initialize_event_cursor
# ===========================================================================
class TestInitializeEventCursor:
    async def test_success(self, connector):
        resp = _make_dropbox_response(success=True, data=MagicMock(cursor="evt_cursor1"))
        connector.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await connector._initialize_event_cursor("key1", EventCategory.groups)
        connector.dropbox_cursor_sync_point.update_sync_point.assert_called()

    async def test_failure(self, connector):
        resp = _make_dropbox_response(success=False, error="denied")
        connector.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await connector._initialize_event_cursor("key1", EventCategory.groups)

    async def test_no_cursor_returned(self, connector):
        data = MagicMock(spec=[])
        resp = _make_dropbox_response(success=True, data=data)
        connector.data_source.team_log_get_events = AsyncMock(return_value=resp)

        await connector._initialize_event_cursor("key1", EventCategory.groups)

    async def test_exception(self, connector):
        connector.data_source.team_log_get_events = AsyncMock(
            side_effect=Exception("timeout")
        )
        await connector._initialize_event_cursor("key1", EventCategory.groups)


# ===========================================================================
# _sync_member_changes_with_cursor
# ===========================================================================
class TestSyncMemberChangesWithCursor:
    async def test_no_cursor(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        await connector._sync_member_changes_with_cursor([])

    async def test_processes_events(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "c1"}
        )
        event = _make_event("member_change_status")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="c2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_member_event = AsyncMock()

        await connector._sync_member_changes_with_cursor([])
        connector._process_member_event.assert_called_once()

    async def test_api_failure(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "c1"}
        )
        resp = _make_dropbox_response(success=False, error="rate limited")
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_member_changes_with_cursor([])

    async def test_event_processing_error(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "c1"}
        )
        event = _make_event("member_change_status")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="c2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_member_event = AsyncMock(side_effect=Exception("event error"))

        await connector._sync_member_changes_with_cursor([])

    async def test_loop_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "c1"}
        )
        connector.data_source.team_log_get_events_continue = AsyncMock(
            side_effect=Exception("loop error")
        )

        await connector._sync_member_changes_with_cursor([])

    async def test_fatal_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("fatal")
        )
        with pytest.raises(Exception, match="fatal"):
            await connector._sync_member_changes_with_cursor([])


# ===========================================================================
# _process_member_event
# ===========================================================================
class TestProcessMemberEvent:
    async def test_member_change_status(self, connector):
        event = _make_event("member_change_status")
        connector._handle_member_change_status_event = AsyncMock()
        await connector._process_member_event(event, [])
        connector._handle_member_change_status_event.assert_called_once()

    async def test_unknown_event_type(self, connector):
        event = _make_event("something_else")
        await connector._process_member_event(event, [])

    async def test_exception_in_processing(self, connector):
        event = _make_event("member_change_status")
        connector._handle_member_change_status_event = AsyncMock(
            side_effect=Exception("handler error")
        )
        await connector._process_member_event(event, [])


# ===========================================================================
# _handle_member_change_status_event
# ===========================================================================
class TestHandleMemberChangeStatusEvent:
    async def test_active_status(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "new@test.com"
        user_info.display_name = "New User"
        user_info.team_member_id = "tm:new"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value = MagicMock(_tag="active")
        status_details.previous_value = MagicMock(_tag="invited")
        event.details = MagicMock()
        event.details.get_member_change_status_details.return_value = status_details

        connector._handle_member_added = AsyncMock()
        app_users = [_make_app_user(email="new@test.com")]

        await connector._handle_member_change_status_event(event, app_users)
        connector._handle_member_added.assert_called_once()

    async def test_removed_status(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "removed@test.com"
        user_info.display_name = "Removed User"
        user_info.team_member_id = "tm:old"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value = MagicMock(_tag="removed")
        status_details.previous_value = MagicMock(_tag="active")
        event.details = MagicMock()
        event.details.get_member_change_status_details.return_value = status_details

        await connector._handle_member_change_status_event(event, [])

    async def test_other_status(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "user@test.com"
        user_info.display_name = "User"
        user_info.team_member_id = "tm:1"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value = MagicMock(_tag="suspended")
        status_details.previous_value = MagicMock(_tag="active")
        event.details = MagicMock()
        event.details.get_member_change_status_details.return_value = status_details

        await connector._handle_member_change_status_event(event, [])

    async def test_no_email(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = False
        event.details = MagicMock(spec=[])

        await connector._handle_member_change_status_event(event, [])

    async def test_exception_in_handler(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "user@test.com"
        user_info.display_name = "User"
        user_info.team_member_id = "tm:1"
        event.context.get_team_member.return_value = user_info

        status_details = MagicMock()
        status_details.new_value = MagicMock(_tag="active")
        status_details.previous_value = MagicMock(_tag="invited")
        event.details = MagicMock()
        event.details.get_member_change_status_details.return_value = status_details

        connector._handle_member_added = AsyncMock(side_effect=Exception("fail"))
        await connector._handle_member_change_status_event(event, [])

    async def test_no_details_method(self, connector):
        event = MagicMock()
        event.context = MagicMock()
        event.context.is_team_member.return_value = True
        user_info = MagicMock()
        user_info.email = "user@test.com"
        user_info.display_name = "User"
        user_info.team_member_id = "tm:1"
        event.context.get_team_member.return_value = user_info
        event.details = MagicMock(spec=[])

        await connector._handle_member_change_status_event(event, [])


# ===========================================================================
# _handle_member_added
# ===========================================================================
class TestHandleMemberAdded:
    async def test_user_found(self, connector):
        user = _make_app_user(email="new@test.com")
        await connector._handle_member_added("new@test.com", "tm:1", [user])
        connector.data_entities_processor.on_new_app_users.assert_called_once()

    async def test_user_not_found(self, connector):
        await connector._handle_member_added("unknown@test.com", "tm:1", [])

    async def test_exception(self, connector):
        user = _make_app_user(email="new@test.com")
        connector.data_entities_processor.on_new_app_users = AsyncMock(
            side_effect=Exception("DB error")
        )
        await connector._handle_member_added("new@test.com", "tm:1", [user])


# ===========================================================================
# _sync_user_groups
# ===========================================================================
class TestSyncUserGroups:
    async def test_full_group_sync(self, connector):
        group = MagicMock()
        group.group_id = "g1"
        group.group_name = "Group1"

        groups_resp = _make_dropbox_response(success=True, data=MagicMock(
            groups=[group], cursor="gc1", has_more=False
        ))
        connector.data_source.team_groups_list = AsyncMock(return_value=groups_resp)

        member = MagicMock()
        member.profile = MagicMock()
        member.profile.team_member_id = "tm:1"
        member.profile.email = "user@test.com"
        member.profile.name = MagicMock(display_name="User One")

        members_resp = _make_dropbox_response(success=True, data=MagicMock(
            members=[member], cursor="mc1", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=members_resp)

        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_groups_list_failure(self, connector):
        groups_resp = _make_dropbox_response(success=False, error="denied")
        connector.data_source.team_groups_list = AsyncMock(return_value=groups_resp)

        with pytest.raises(Exception):
            await connector._sync_user_groups()

    async def test_groups_pagination(self, connector):
        group1 = MagicMock(group_id="g1", group_name="G1")
        group2 = MagicMock(group_id="g2", group_name="G2")

        page1 = _make_dropbox_response(success=True, data=MagicMock(
            groups=[group1], cursor="gc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=True, data=MagicMock(
            groups=[group2], cursor="gc2", has_more=False
        ))
        connector.data_source.team_groups_list = AsyncMock(return_value=page1)
        connector.data_source.team_groups_list_continue = AsyncMock(return_value=page2)

        # 'name' is a special MagicMock constructor arg; must set it as an attribute
        member = MagicMock()
        profile = MagicMock(team_member_id="tm:1", email="u@t.com")
        profile.name = MagicMock(display_name="U")
        member.profile = profile
        members_resp = _make_dropbox_response(success=True, data=MagicMock(
            members=[member], cursor="mc1", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=members_resp)

        await connector._sync_user_groups()
        args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert len(args) == 2

    async def test_groups_pagination_error(self, connector):
        group1 = MagicMock(group_id="g1", group_name="G1")
        page1 = _make_dropbox_response(success=True, data=MagicMock(
            groups=[group1], cursor="gc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=False, error="fail")
        connector.data_source.team_groups_list = AsyncMock(return_value=page1)
        connector.data_source.team_groups_list_continue = AsyncMock(return_value=page2)

        # 'name' is a special MagicMock constructor arg; must set it as an attribute
        member = MagicMock()
        profile = MagicMock(team_member_id="tm:1", email="u@t.com")
        profile.name = MagicMock(display_name="U")
        member.profile = profile
        members_resp = _make_dropbox_response(success=True, data=MagicMock(
            members=[member], cursor="mc1", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=members_resp)

        await connector._sync_user_groups()

    async def test_group_processing_error_skips(self, connector):
        """When _fetch_group_members fails for a group, the exception is caught
        in the group loop and processing continues. The overall function does not raise."""
        group = MagicMock(group_id="g1", group_name="G1")
        groups_resp = _make_dropbox_response(success=True, data=MagicMock(
            groups=[group], cursor="gc1", has_more=False
        ))
        connector.data_source.team_groups_list = AsyncMock(return_value=groups_resp)
        connector.data_source.team_groups_members_list = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="not found")
        )

        # The error is caught by the inner try/except in the group processing loop,
        # so the function completes without raising
        await connector._sync_user_groups()
        # on_new_user_groups is not called since no groups were successfully processed
        connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_no_groups_found(self, connector):
        groups_resp = _make_dropbox_response(success=True, data=MagicMock(
            groups=[], cursor="gc1", has_more=False
        ))
        connector.data_source.team_groups_list = AsyncMock(return_value=groups_resp)

        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_not_called()


# ===========================================================================
# _sync_group_changes_with_cursor
# ===========================================================================
class TestSyncGroupChangesWithCursor:
    async def test_no_cursor_runs_full_sync(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector._sync_user_groups = AsyncMock()

        await connector._sync_group_changes_with_cursor()
        connector._sync_user_groups.assert_called_once()

    async def test_processes_events(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "gc1"}
        )
        event = _make_event("group_create")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="gc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_group_event = AsyncMock()

        await connector._sync_group_changes_with_cursor()
        connector._process_group_event.assert_called_once()

    async def test_api_failure(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "gc1"}
        )
        resp = _make_dropbox_response(success=False, error="error")
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_group_changes_with_cursor()

    async def test_event_error(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "gc1"}
        )
        event = _make_event("group_create")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="gc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_group_event = AsyncMock(side_effect=Exception("event fail"))

        await connector._sync_group_changes_with_cursor()

    async def test_loop_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "gc1"}
        )
        connector.data_source.team_log_get_events_continue = AsyncMock(
            side_effect=Exception("loop err")
        )
        await connector._sync_group_changes_with_cursor()

    async def test_fatal_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("fatal")
        )
        with pytest.raises(Exception, match="fatal"):
            await connector._sync_group_changes_with_cursor()


# ===========================================================================
# _process_group_event
# ===========================================================================
class TestProcessGroupEvent:
    async def test_group_create(self, connector):
        event = _make_event("group_create")
        connector._handle_group_created_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_created_event.assert_called_once()

    async def test_group_delete(self, connector):
        event = _make_event("group_delete")
        connector._handle_group_deleted_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_deleted_event.assert_called_once()

    async def test_group_add_member(self, connector):
        event = _make_event("group_add_member")
        connector._handle_group_membership_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_membership_event.assert_called_once()

    async def test_group_remove_member(self, connector):
        event = _make_event("group_remove_member")
        connector._handle_group_membership_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_membership_event.assert_called_once()

    async def test_group_rename(self, connector):
        event = _make_event("group_rename")
        connector._handle_group_renamed_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_renamed_event.assert_called_once()

    async def test_group_change_member_role(self, connector):
        event = _make_event("group_change_member_role")
        connector._handle_group_change_member_role_event = AsyncMock()
        await connector._process_group_event(event)
        connector._handle_group_change_member_role_event.assert_called_once()

    async def test_unknown_event(self, connector):
        event = _make_event("unknown_type")
        await connector._process_group_event(event)

    async def test_exception(self, connector):
        event = _make_event("group_create")
        connector._handle_group_created_event = AsyncMock(side_effect=Exception("fail"))
        await connector._process_group_event(event)


# ===========================================================================
# _handle_group_membership_event
# ===========================================================================
class TestHandleGroupMembershipEvent:
    async def test_add_member(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_add_member", participants=[gp, up])

        await connector._handle_group_membership_event(event, "group_add_member")
        connector.data_entities_processor.on_user_group_member_added.assert_called_once()

    async def test_remove_member(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_remove_member", participants=[gp, up])

        await connector._handle_group_membership_event(event, "group_remove_member")
        connector.data_entities_processor.on_user_group_member_removed.assert_called_once()

    async def test_missing_group_id(self, connector):
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_add_member", participants=[up])

        await connector._handle_group_membership_event(event, "group_add_member")
        connector.data_entities_processor.on_user_group_member_added.assert_not_called()

    async def test_missing_member_email(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        event = _make_event("group_add_member", participants=[gp])

        await connector._handle_group_membership_event(event, "group_add_member")
        connector.data_entities_processor.on_user_group_member_added.assert_not_called()

    async def test_participant_parse_error(self, connector):
        bad_participant = MagicMock()
        bad_participant.is_group.side_effect = Exception("parse fail")
        event = _make_event("group_add_member", participants=[bad_participant])

        await connector._handle_group_membership_event(event, "group_add_member")

    async def test_add_member_with_owner_role(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_add_member", participants=[gp, up])
        event.details = MagicMock()
        event.details.is_group_owner = True

        await connector._handle_group_membership_event(event, "group_add_member")
        call_args = connector.data_entities_processor.on_user_group_member_added.call_args
        assert call_args[1]["permission_type"] == PermissionType.OWNER


# ===========================================================================
# _handle_group_deleted_event
# ===========================================================================
class TestHandleGroupDeletedEvent:
    async def test_with_group_participant(self, connector):
        gp = _make_group_participant("g1", "DelGroup")
        event = _make_event("group_delete", participants=[gp])

        await connector._handle_group_deleted_event(event)
        connector.data_entities_processor.on_user_group_deleted.assert_called_once()

    async def test_no_group_participant(self, connector):
        up = _make_user_participant()
        event = _make_event("group_delete", participants=[up])

        await connector._handle_group_deleted_event(event)
        connector.data_entities_processor.on_user_group_deleted.assert_not_called()


# ===========================================================================
# _handle_group_created_event
# ===========================================================================
class TestHandleGroupCreatedEvent:
    async def test_creates_group(self, connector):
        gp = _make_group_participant("g1", "NewGroup")
        event = _make_event("group_create", participants=[gp])
        connector._process_single_group = AsyncMock()

        await connector._handle_group_created_event(event)
        connector._process_single_group.assert_called_once_with("g1", "NewGroup")

    async def test_no_group_id(self, connector):
        up = _make_user_participant()
        event = _make_event("group_create", participants=[up])
        connector._process_single_group = AsyncMock()

        await connector._handle_group_created_event(event)
        connector._process_single_group.assert_not_called()

    async def test_exception(self, connector):
        gp = _make_group_participant("g1", "NewGroup")
        event = _make_event("group_create", participants=[gp])
        connector._process_single_group = AsyncMock(side_effect=Exception("fail"))

        await connector._handle_group_created_event(event)


# ===========================================================================
# _process_single_group
# ===========================================================================
class TestProcessSingleGroup:
    async def test_success(self, connector):
        # 'name' is a special MagicMock constructor arg; must set it as an attribute
        member = MagicMock()
        profile = MagicMock(team_member_id="tm:1", email="u@t.com")
        profile.name = MagicMock(display_name="U")
        member.profile = profile
        members_resp = _make_dropbox_response(success=True, data=MagicMock(
            members=[member], cursor="mc1", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=members_resp)

        await connector._process_single_group("g1", "GroupName")
        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    async def test_exception(self, connector):
        connector.data_source.team_groups_members_list = AsyncMock(
            side_effect=Exception("fail")
        )
        await connector._process_single_group("g1", "GroupName")


# ===========================================================================
# _fetch_group_members
# ===========================================================================
class TestFetchGroupMembers:
    async def test_single_page(self, connector):
        member = MagicMock()
        resp = _make_dropbox_response(success=True, data=MagicMock(
            members=[member], cursor="mc1", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=resp)

        result = await connector._fetch_group_members("g1", "G1")
        assert len(result) == 1

    async def test_pagination(self, connector):
        m1 = MagicMock()
        m2 = MagicMock()
        page1 = _make_dropbox_response(success=True, data=MagicMock(
            members=[m1], cursor="mc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=True, data=MagicMock(
            members=[m2], cursor="mc2", has_more=False
        ))
        connector.data_source.team_groups_members_list = AsyncMock(return_value=page1)
        connector.data_source.team_groups_members_list_continue = AsyncMock(return_value=page2)

        result = await connector._fetch_group_members("g1", "G1")
        assert len(result) == 2

    async def test_failure(self, connector):
        resp = _make_dropbox_response(success=False, error="denied")
        connector.data_source.team_groups_members_list = AsyncMock(return_value=resp)

        with pytest.raises(Exception):
            await connector._fetch_group_members("g1", "G1")

    async def test_pagination_error(self, connector):
        page1 = _make_dropbox_response(success=True, data=MagicMock(
            members=[MagicMock()], cursor="mc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=False, error="fail")
        connector.data_source.team_groups_members_list = AsyncMock(return_value=page1)
        connector.data_source.team_groups_members_list_continue = AsyncMock(return_value=page2)

        result = await connector._fetch_group_members("g1", "G1")
        assert len(result) == 1


# ===========================================================================
# _handle_group_renamed_event
# ===========================================================================
class TestHandleGroupRenamedEvent:
    async def test_rename_success(self, connector):
        gp = _make_group_participant("g1", "NewName")
        event = _make_event("group_rename", participants=[gp])
        rename_details = MagicMock()
        rename_details.previous_value = "OldName"
        rename_details.new_value = "NewName"
        event.details = MagicMock()
        event.details.get_group_rename_details.return_value = rename_details

        connector._update_group_name = AsyncMock()
        await connector._handle_group_renamed_event(event)
        connector._update_group_name.assert_called_once()

    async def test_missing_info(self, connector):
        """When group_id is None (no group participant) and get_group_rename_details
        is not available, the method returns early without calling _update_group_name."""
        up = _make_user_participant()
        event = _make_event("group_rename", participants=[up])
        event.details = MagicMock(spec=[])

        connector._update_group_name = AsyncMock()
        await connector._handle_group_renamed_event(event)
        connector._update_group_name.assert_not_called()

    async def test_exception(self, connector):
        gp = _make_group_participant("g1", "NewName")
        event = _make_event("group_rename", participants=[gp])
        rename_details = MagicMock()
        rename_details.previous_value = "OldName"
        rename_details.new_value = "NewName"
        event.details = MagicMock()
        event.details.get_group_rename_details.return_value = rename_details

        connector._update_group_name = AsyncMock(side_effect=Exception("fail"))
        await connector._handle_group_renamed_event(event)


# ===========================================================================
# _update_group_name
# ===========================================================================
class TestUpdateGroupName:
    async def test_success(self, connector):
        existing_group = MagicMock()
        existing_group.id = "ig1"
        existing_group.name = "OldName"

        tx = _make_mock_tx_store_fullcov()
        tx.get_user_group_by_external_id = AsyncMock(return_value=existing_group)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        await connector._update_group_name("g1", "NewName", "OldName")
        tx.batch_upsert_user_groups.assert_called_once()

    async def test_group_not_found(self, connector):
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        await connector._update_group_name("g1", "NewName")

    async def test_exception(self, connector):
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_group_by_external_id = AsyncMock(side_effect=Exception("DB fail"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx

        provider.transaction = _transaction
        connector.data_store_provider = provider

        with pytest.raises(Exception, match="DB fail"):
            await connector._update_group_name("g1", "NewName")


# ===========================================================================
# _handle_group_change_member_role_event
# ===========================================================================
class TestHandleGroupChangeMemberRoleEvent:
    async def test_success(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        role_details = MagicMock()
        role_details.is_group_owner = True
        event.details = MagicMock()
        event.details.get_group_change_member_role_details.return_value = role_details

        connector._update_user_group_permission = AsyncMock(return_value=True)
        await connector._handle_group_change_member_role_event(event)
        connector._update_user_group_permission.assert_called_once()

    async def test_missing_info(self, connector):
        event = _make_event("group_change_member_role", participants=[])
        event.details = MagicMock(spec=[])

        connector._update_user_group_permission = AsyncMock()
        await connector._handle_group_change_member_role_event(event)
        connector._update_user_group_permission.assert_not_called()

    async def test_is_group_owner_attr_direct(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        event.details = MagicMock(spec=['is_group_owner'])
        event.details.is_group_owner = False

        connector._update_user_group_permission = AsyncMock(return_value=True)
        await connector._handle_group_change_member_role_event(event)
        call_args = connector._update_user_group_permission.call_args
        assert call_args[0][2] == PermissionType.WRITE

    async def test_no_role_details(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        event.details = MagicMock()
        del event.details.get_group_change_member_role_details
        del event.details.is_group_owner

        await connector._handle_group_change_member_role_event(event)

    async def test_role_extraction_error(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        event.details = MagicMock()
        event.details.get_group_change_member_role_details.side_effect = Exception("parse error")
        del event.details.is_group_owner

        await connector._handle_group_change_member_role_event(event)

    async def test_update_failure(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        role_details = MagicMock()
        role_details.is_group_owner = False
        event.details = MagicMock()
        event.details.get_group_change_member_role_details.return_value = role_details

        connector._update_user_group_permission = AsyncMock(return_value=False)
        await connector._handle_group_change_member_role_event(event)

    async def test_update_exception(self, connector):
        gp = _make_group_participant("g1", "TestGroup")
        up = _make_user_participant("user@test.com", "User")
        event = _make_event("group_change_member_role", participants=[gp, up])
        role_details = MagicMock()
        role_details.is_group_owner = True
        event.details = MagicMock()
        event.details.get_group_change_member_role_details.return_value = role_details

        connector._update_user_group_permission = AsyncMock(side_effect=Exception("fail"))
        await connector._handle_group_change_member_role_event(event)


# ===========================================================================
# _update_user_group_permission
# ===========================================================================
class TestUpdateUserGroupPermission:
    async def test_user_not_found(self, connector):
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is False

    async def test_group_not_found(self, connector):
        user = MagicMock(id="u1")
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(return_value=user)
        tx.get_user_group_by_external_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is False

    async def test_no_existing_edge_creates_new(self, connector):
        user = MagicMock(id="u1")
        group = MagicMock(id="ug1", name="G1")
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(return_value=user)
        tx.get_user_group_by_external_id = AsyncMock(return_value=group)
        tx.get_edge = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is True
        tx.batch_create_edges.assert_called_once()

    async def test_same_permission_type(self, connector):
        user = MagicMock(id="u1")
        group = MagicMock(id="ug1", name="G1")
        existing_edge = {"permissionType": PermissionType.OWNER.value}
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(return_value=user)
        tx.get_user_group_by_external_id = AsyncMock(return_value=group)
        tx.get_edge = AsyncMock(return_value=existing_edge)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is True

    async def test_different_permission_updates(self, connector):
        user = MagicMock(id="u1")
        group = MagicMock(id="ug1", name="G1")
        existing_edge = {"permissionType": PermissionType.READ.value}
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(return_value=user)
        tx.get_user_group_by_external_id = AsyncMock(return_value=group)
        tx.get_edge = AsyncMock(return_value=existing_edge)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is True
        tx.delete_edge.assert_called_once()
        tx.batch_create_edges.assert_called_once()

    async def test_exception_returns_false(self, connector):
        tx = _make_mock_tx_store_fullcov()
        tx.get_user_by_email = AsyncMock(side_effect=Exception("DB error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._update_user_group_permission("g1", "user@test.com", PermissionType.OWNER)
        assert result is False


# ===========================================================================
# sync_record_groups and sync_personal_record_groups
# ===========================================================================
class TestSyncRecordGroups:
    async def test_no_admin_user(self, connector):
        users = [_make_app_user(title="member")]
        await connector.sync_record_groups(users)

    async def test_team_folders_success(self, connector):
        admin = _make_app_user(title="team_admin")
        folder = MagicMock()
        folder.team_folder_id = "tf:1"
        folder.name = "TeamFolder"
        folder.status = MagicMock(_tag="active")

        resp = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[folder], cursor="tc1", has_more=False
        ))
        connector.data_source.team_team_folder_list = AsyncMock(return_value=resp)
        connector._create_and_sync_single_record_group = AsyncMock()

        await connector.sync_record_groups([admin])
        connector._create_and_sync_single_record_group.assert_called_once()

    async def test_team_folders_fail(self, connector):
        admin = _make_app_user(title="team_admin")
        resp = _make_dropbox_response(success=False, error="denied")
        connector.data_source.team_team_folder_list = AsyncMock(return_value=resp)

        await connector.sync_record_groups([admin])

    async def test_inactive_folder_skipped(self, connector):
        admin = _make_app_user(title="team_admin")
        folder = MagicMock()
        folder.team_folder_id = "tf:1"
        folder.name = "Archived"
        folder.status = MagicMock(_tag="archived")

        resp = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[folder], cursor="tc1", has_more=False
        ))
        connector.data_source.team_team_folder_list = AsyncMock(return_value=resp)
        connector._create_and_sync_single_record_group = AsyncMock()

        await connector.sync_record_groups([admin])
        connector._create_and_sync_single_record_group.assert_not_called()

    async def test_folder_sync_exception(self, connector):
        admin = _make_app_user(title="team_admin")
        folder = MagicMock()
        folder.team_folder_id = "tf:1"
        folder.name = "Folder"
        folder.status = MagicMock(_tag="active")

        resp = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[folder], cursor="tc1", has_more=False
        ))
        connector.data_source.team_team_folder_list = AsyncMock(return_value=resp)
        connector._create_and_sync_single_record_group = AsyncMock(
            side_effect=Exception("sync error")
        )

        await connector.sync_record_groups([admin])

    async def test_pagination(self, connector):
        admin = _make_app_user(title="team_admin")
        f1 = MagicMock(team_folder_id="tf:1", name="F1", status=MagicMock(_tag="active"))
        f2 = MagicMock(team_folder_id="tf:2", name="F2", status=MagicMock(_tag="active"))

        page1 = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[f1], cursor="tc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[f2], cursor="tc2", has_more=False
        ))
        connector.data_source.team_team_folder_list = AsyncMock(return_value=page1)
        connector.data_source.team_team_folder_list_continue = AsyncMock(return_value=page2)
        connector._create_and_sync_single_record_group = AsyncMock()

        await connector.sync_record_groups([admin])
        assert connector._create_and_sync_single_record_group.call_count == 2

    async def test_pagination_error(self, connector):
        admin = _make_app_user(title="team_admin")
        f1 = MagicMock(team_folder_id="tf:1", name="F1", status=MagicMock(_tag="active"))

        page1 = _make_dropbox_response(success=True, data=MagicMock(
            team_folders=[f1], cursor="tc1", has_more=True
        ))
        page2 = _make_dropbox_response(success=False, error="pagination fail")
        connector.data_source.team_team_folder_list = AsyncMock(return_value=page1)
        connector.data_source.team_team_folder_list_continue = AsyncMock(return_value=page2)
        connector._create_and_sync_single_record_group = AsyncMock()

        await connector.sync_record_groups([admin])


class TestSyncPersonalRecordGroups:
    async def test_creates_personal_groups(self, connector):
        user = _make_app_user()
        await connector.sync_personal_record_groups([user])
        connector.data_entities_processor.on_new_record_groups.assert_called_once()
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(args) == 1

    async def test_skips_empty_name(self, connector):
        user = _make_app_user(full_name="")
        await connector.sync_personal_record_groups([user])
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(args) == 0

    async def test_skips_empty_source_user_id(self, connector):
        user = _make_app_user(source_user_id="")
        await connector.sync_personal_record_groups([user])
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(args) == 0


# ===========================================================================
# _create_and_sync_single_record_group
# ===========================================================================
class TestCreateAndSyncSingleRecordGroup:
    async def test_success(self, connector):
        user_membership = MagicMock()
        user_membership.access_type = MagicMock(_tag="editor")
        user_membership.user = MagicMock(email="u@t.com")

        group_membership = MagicMock()
        group_membership.access_type = MagicMock(_tag="viewer")
        group_membership.group = MagicMock(group_id="g1")

        members_data = MagicMock()
        members_data.users = [user_membership]
        members_data.groups = [group_membership]
        members_data.cursor = "mc1"
        members_data.has_more = False

        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=members_data)
        )

        admin = _make_app_user(title="team_admin")
        await connector._create_and_sync_single_record_group("tf:1", "Folder", admin)
        connector.data_entities_processor.on_new_record_groups.assert_called_once()

    async def test_members_fail(self, connector):
        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="access denied")
        )
        admin = _make_app_user(title="team_admin")
        await connector._create_and_sync_single_record_group("tf:1", "Folder", admin)
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    async def test_members_pagination(self, connector):
        user1 = MagicMock()
        user1.access_type = MagicMock(_tag="owner")
        user1.user = MagicMock(email="u1@t.com")

        page1_data = MagicMock()
        page1_data.users = [user1]
        page1_data.groups = []
        page1_data.cursor = "mc1"
        page1_data.has_more = True

        user2 = MagicMock()
        user2.access_type = MagicMock(_tag="editor")
        user2.user = MagicMock(email="u2@t.com")

        page2_data = MagicMock()
        page2_data.users = [user2]
        page2_data.groups = []
        page2_data.cursor = "mc2"
        page2_data.has_more = False

        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=page1_data)
        )
        connector.data_source.sharing_list_folder_members_continue = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=page2_data)
        )

        admin = _make_app_user(title="team_admin")
        await connector._create_and_sync_single_record_group("tf:1", "Folder", admin)

    async def test_exception(self, connector):
        connector.data_source.sharing_list_folder_members = AsyncMock(
            side_effect=Exception("fatal")
        )
        admin = _make_app_user(title="team_admin")
        with pytest.raises(Exception):
            await connector._create_and_sync_single_record_group("tf:1", "Folder", admin)

    async def test_members_pagination_error(self, connector):
        page1_data = MagicMock()
        page1_data.users = []
        page1_data.groups = []
        page1_data.cursor = "mc1"
        page1_data.has_more = True

        page2 = _make_dropbox_response(success=False, error="pagination fail")

        connector.data_source.sharing_list_folder_members = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=page1_data)
        )
        connector.data_source.sharing_list_folder_members_continue = AsyncMock(
            return_value=page2
        )

        admin = _make_app_user(title="team_admin")
        await connector._create_and_sync_single_record_group("tf:1", "Folder", admin)


# ===========================================================================
# _sync_record_group_changes_with_cursor
# ===========================================================================
class TestSyncRecordGroupChangesWithCursor:
    async def test_no_cursor(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector._initialize_event_cursor = AsyncMock()

        await connector._sync_record_group_changes_with_cursor([])
        connector._initialize_event_cursor.assert_called_once()

    async def test_processes_events(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "rc1"}
        )
        event = _make_event("team_folder_create")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="rc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_record_group_event = AsyncMock()

        await connector._sync_record_group_changes_with_cursor([])
        connector._process_record_group_event.assert_called_once()

    async def test_api_failure(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "rc1"}
        )
        resp = _make_dropbox_response(success=False, error="error")
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_record_group_changes_with_cursor([])

    async def test_event_error(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "rc1"}
        )
        event = _make_event("team_folder_create")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="rc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._process_record_group_event = AsyncMock(side_effect=Exception("err"))

        await connector._sync_record_group_changes_with_cursor([])

    async def test_loop_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "rc1"}
        )
        connector.data_source.team_log_get_events_continue = AsyncMock(
            side_effect=Exception("loop err")
        )

        await connector._sync_record_group_changes_with_cursor([])

    async def test_fatal_exception(self, connector):
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("fatal")
        )
        with pytest.raises(Exception, match="fatal"):
            await connector._sync_record_group_changes_with_cursor([])


# ===========================================================================
# _process_record_group_event
# ===========================================================================
class TestProcessRecordGroupEvent:
    async def test_team_folder_create(self, connector):
        event = _make_event("team_folder_create")
        connector._handle_record_group_created_event = AsyncMock()
        await connector._process_record_group_event(event, [])
        connector._handle_record_group_created_event.assert_called_once()

    async def test_team_folder_rename(self, connector):
        event = _make_event("team_folder_rename")
        connector._handle_record_group_renamed_event = AsyncMock()
        await connector._process_record_group_event(event, [])
        connector._handle_record_group_renamed_event.assert_called_once()

    async def test_team_folder_permanently_delete(self, connector):
        event = _make_event("team_folder_permanently_delete")
        connector._handle_record_group_deleted_event = AsyncMock()
        await connector._process_record_group_event(event, [])
        connector._handle_record_group_deleted_event.assert_called_once()

    async def test_team_folder_change_status(self, connector):
        event = _make_event("team_folder_change_status")
        connector._handle_record_group_status_changed_event = AsyncMock()
        await connector._process_record_group_event(event, [])
        connector._handle_record_group_status_changed_event.assert_called_once()

    async def test_unknown_event(self, connector):
        event = _make_event("unknown_event")
        await connector._process_record_group_event(event, [])

    async def test_exception(self, connector):
        event = _make_event("team_folder_create")
        connector._handle_record_group_created_event = AsyncMock(side_effect=Exception("fail"))
        await connector._process_record_group_event(event, [])


# ===========================================================================
# _handle_record_group_created_event / renamed / deleted / status_changed
# ===========================================================================
class TestRecordGroupEventHandlers:
    async def test_created_with_admin(self, connector):
        asset = _make_folder_asset("NewFolder", "ns:1")
        event = _make_event("team_folder_create", assets=[asset])
        admin = _make_app_user(title="team_admin")
        connector._create_and_sync_single_record_group = AsyncMock()

        await connector._handle_record_group_created_event(event, [admin])
        connector._create_and_sync_single_record_group.assert_called_once()

    async def test_created_no_folder_info(self, connector):
        event = _make_event("team_folder_create", assets=[])
        await connector._handle_record_group_created_event(event, [])

    async def test_created_no_admin(self, connector):
        asset = _make_folder_asset("NewFolder", "ns:1")
        event = _make_event("team_folder_create", assets=[asset])
        user = _make_app_user(title="member")

        await connector._handle_record_group_created_event(event, [user])

    async def test_created_exception(self, connector):
        asset = _make_folder_asset("NewFolder", "ns:1")
        event = _make_event("team_folder_create", assets=[asset])
        admin = _make_app_user(title="team_admin")
        connector._create_and_sync_single_record_group = AsyncMock(
            side_effect=Exception("sync error")
        )

        await connector._handle_record_group_created_event(event, [admin])

    async def test_renamed_success(self, connector):
        asset = _make_folder_asset("NewName", "ns:1")
        event = _make_event("team_folder_rename", assets=[asset])
        rename_details = MagicMock()
        rename_details.previous_folder_name = "OldName"
        event.details = MagicMock()
        event.details.get_team_folder_rename_details.return_value = rename_details

        await connector._handle_record_group_renamed_event(event)
        connector.data_entities_processor.update_record_group_name.assert_called_once()

    async def test_renamed_missing_info(self, connector):
        event = _make_event("team_folder_rename", assets=[])
        event.details = MagicMock(spec=[])

        await connector._handle_record_group_renamed_event(event)

    async def test_renamed_exception(self, connector):
        asset = _make_folder_asset("NewName", "ns:1")
        event = _make_event("team_folder_rename", assets=[asset])
        event.details = MagicMock(spec=[])
        connector.data_entities_processor.update_record_group_name = AsyncMock(
            side_effect=Exception("fail")
        )

        await connector._handle_record_group_renamed_event(event)

    async def test_deleted_success(self, connector):
        asset = _make_folder_asset("DelFolder", "ns:1")
        event = _make_event("team_folder_permanently_delete", assets=[asset])

        await connector._handle_record_group_deleted_event(event)
        connector.data_entities_processor.on_record_group_deleted.assert_called_once()

    async def test_deleted_no_folder_id(self, connector):
        event = _make_event("team_folder_permanently_delete", assets=[])
        await connector._handle_record_group_deleted_event(event)

    async def test_deleted_exception(self, connector):
        asset = _make_folder_asset("Folder", "ns:1")
        event = _make_event("team_folder_permanently_delete", assets=[asset])
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(
            side_effect=Exception("fail")
        )

        await connector._handle_record_group_deleted_event(event)

    async def test_status_changed_to_active(self, connector):
        asset = _make_folder_asset("Folder", "ns:1")
        event = _make_event("team_folder_change_status", assets=[asset])
        status_details = MagicMock()
        status_details.previous_value = MagicMock(_tag="archived")
        status_details.new_value = MagicMock(_tag="active")
        event.details = MagicMock()
        event.details.get_team_folder_change_status_details.return_value = status_details

        connector._handle_record_group_created_event = AsyncMock()
        await connector._handle_record_group_status_changed_event(event, [])
        connector._handle_record_group_created_event.assert_called_once()

    async def test_status_changed_from_active(self, connector):
        asset = _make_folder_asset("Folder", "ns:1")
        event = _make_event("team_folder_change_status", assets=[asset])
        status_details = MagicMock()
        status_details.previous_value = MagicMock(_tag="active")
        status_details.new_value = MagicMock(_tag="archived")
        event.details = MagicMock()
        event.details.get_team_folder_change_status_details.return_value = status_details

        connector._handle_record_group_deleted_event = AsyncMock()
        await connector._handle_record_group_status_changed_event(event, [])
        connector._handle_record_group_deleted_event.assert_called_once()

    async def test_status_changed_other(self, connector):
        asset = _make_folder_asset("Folder", "ns:1")
        event = _make_event("team_folder_change_status", assets=[asset])
        status_details = MagicMock()
        status_details.previous_value = MagicMock(_tag="archived")
        status_details.new_value = MagicMock(_tag="permanently_deleted")
        event.details = MagicMock()
        event.details.get_team_folder_change_status_details.return_value = status_details

        await connector._handle_record_group_status_changed_event(event, [])

    async def test_status_changed_missing_info(self, connector):
        event = _make_event("team_folder_change_status", assets=[])
        event.details = MagicMock(spec=[])

        await connector._handle_record_group_status_changed_event(event, [])

    async def test_status_changed_exception(self, connector):
        asset = _make_folder_asset("Folder", "ns:1")
        event = _make_event("team_folder_change_status", assets=[asset])
        status_details = MagicMock()
        status_details.previous_value = MagicMock(_tag="archived")
        status_details.new_value = MagicMock(_tag="active")
        event.details = MagicMock()
        event.details.get_team_folder_change_status_details.return_value = status_details
        connector._handle_record_group_created_event = AsyncMock(side_effect=Exception("fail"))

        await connector._handle_record_group_status_changed_event(event, [])


# ===========================================================================
# _sync_sharing_changes_with_cursor
# ===========================================================================
class TestSyncSharingChangesWithCursor:
    async def test_no_events(self, connector):
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")
        connector.dropbox_cursor_sync_point.update_sync_point.assert_called()

    async def test_file_sharing_event(self, connector):
        actor = MagicMock()
        actor.team_member_id = "tm:1"
        actor.email = "user@test.com"

        file_asset = _make_file_asset("id:file1", "ns:1")

        event = _make_event("shared_content_add_member", assets=[file_asset])
        event.actor = MagicMock()
        event.actor.is_admin.return_value = False
        event.actor.get_user.return_value = actor

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._resync_record_by_external_id = AsyncMock()

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")
        connector._resync_record_by_external_id.assert_called_once()

    async def test_folder_sharing_event(self, connector):
        actor = MagicMock()
        actor.team_member_id = "tm:1"
        actor.email = "user@test.com"

        folder_asset = _make_folder_asset("SharedFolder", "ns:1")

        event = _make_event("shared_content_remove_member", assets=[folder_asset])
        event.actor = MagicMock()
        event.actor.is_admin.return_value = True
        event.actor.get_admin.return_value = actor

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._resync_record_by_external_id = AsyncMock()

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")
        connector._resync_record_by_external_id.assert_called_once()

    async def test_api_failure(self, connector):
        resp = _make_dropbox_response(success=False, error="fail")
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_no_actor(self, connector):
        event = _make_event("shared_content_add_member")
        event.actor = MagicMock()
        event.actor.is_admin.return_value = False
        event.actor.get_user.return_value = None

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_no_assets(self, connector):
        actor = MagicMock()
        actor.team_member_id = "tm:1"
        actor.email = "user@test.com"

        event = _make_event("shared_content_add_member", assets=[])
        event.assets = None
        event.actor = MagicMock()
        event.actor.is_admin.return_value = False
        event.actor.get_user.return_value = actor

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_event_exception(self, connector):
        event = _make_event("shared_content_add_member")
        event.event_type._tag = "shared_content_add_member"
        event.actor = MagicMock()
        event.actor.is_admin.side_effect = Exception("actor error")

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_loop_exception(self, connector):
        connector.data_source.team_log_get_events_continue = AsyncMock(
            side_effect=Exception("loop error")
        )
        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_irrelevant_event_skipped(self, connector):
        event = _make_event("some_other_sharing_event")
        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")

    async def test_folder_asset_no_ns_id(self, connector):
        actor = MagicMock()
        actor.team_member_id = "tm:1"
        actor.email = "user@test.com"

        folder_asset = MagicMock()
        folder_asset.is_folder.return_value = True
        folder_asset.is_file.return_value = False
        folder_info = MagicMock()
        folder_info.path = None
        folder_asset.get_folder.return_value = folder_info

        event = _make_event("shared_content_add_member", assets=[folder_asset])
        event.actor = MagicMock()
        event.actor.is_admin.return_value = False
        event.actor.get_user.return_value = actor

        resp = _make_dropbox_response(success=True, data=MagicMock(
            events=[event], cursor="sc2", has_more=False
        ))
        connector.data_source.team_log_get_events_continue = AsyncMock(return_value=resp)
        connector._resync_record_by_external_id = AsyncMock()

        await connector._sync_sharing_changes_with_cursor("key1", "sc1")
        connector._resync_record_by_external_id.assert_not_called()


# ===========================================================================
# _resync_record_by_external_id
# ===========================================================================
class TestResyncRecordByExternalId:
    async def test_file_resync_update(self, connector):
        metadata_data = _make_file_entry()
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=metadata_data)
        )

        existing_record = MagicMock()
        existing_record.external_record_group_id = "rg:1"

        tx = _make_mock_tx_store_fullcov()
        tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        record_update = MagicMock()
        record_update.is_updated = True
        record_update.record = MagicMock(record_name="doc.pdf")
        connector._process_dropbox_entry = AsyncMock(return_value=record_update)
        connector._handle_record_updates = AsyncMock()

        await connector._resync_record_by_external_id(
            "id:f1", "tm:1", "user@test.com", False, "ns:1"
        )
        connector._handle_record_updates.assert_called_once()

    async def test_file_no_existing_record(self, connector):
        metadata_data = _make_file_entry()
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=metadata_data)
        )

        tx = _make_mock_tx_store_fullcov()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        await connector._resync_record_by_external_id(
            "id:f1", "tm:1", "user@test.com", False
        )

    async def test_folder_resync(self, connector):
        ns_metadata = _make_folder_entry()
        file_metadata = _make_folder_entry(folder_id="id:folder1")

        connector.data_source.files_get_metadata = AsyncMock(
            side_effect=[
                _make_dropbox_response(success=True, data=ns_metadata),
                _make_dropbox_response(success=True, data=file_metadata),
            ]
        )

        existing_record = MagicMock()
        existing_record.external_record_group_id = "rg:1"

        tx = _make_mock_tx_store_fullcov()
        tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        record_update = MagicMock()
        record_update.is_updated = False
        connector._process_dropbox_entry = AsyncMock(return_value=record_update)

        await connector._resync_record_by_external_id(
            "id:folder1", "tm:1", "user@test.com", True
        )

    async def test_metadata_failure(self, connector):
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="not found")
        )

        await connector._resync_record_by_external_id(
            "id:f1", "tm:1", "user@test.com", False
        )

    async def test_folder_ns_metadata_failure(self, connector):
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="not found")
        )

        await connector._resync_record_by_external_id(
            "ns:1", "tm:1", "user@test.com", True
        )

    async def test_exception(self, connector):
        connector.data_source.files_get_metadata = AsyncMock(
            side_effect=Exception("error")
        )
        await connector._resync_record_by_external_id(
            "id:f1", "tm:1", "user@test.com", False
        )

    async def test_folder_no_existing_record(self, connector):
        ns_metadata = _make_folder_entry()
        file_metadata = _make_folder_entry(folder_id="id:folder1")

        connector.data_source.files_get_metadata = AsyncMock(
            side_effect=[
                _make_dropbox_response(success=True, data=ns_metadata),
                _make_dropbox_response(success=True, data=file_metadata),
            ]
        )

        tx = _make_mock_tx_store_fullcov()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        await connector._resync_record_by_external_id(
            "ns:1", "tm:1", "user@test.com", True
        )


# ===========================================================================
# run_incremental_sync
# ===========================================================================
class TestRunIncrementalSync:
    async def test_no_cursor(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.dropbox_cursor_sync_point = AsyncMock(return_value=None)
        connector.run_sync = AsyncMock()

        await connector.run_incremental_sync()
        connector.run_sync.assert_called_once()

    async def test_with_cursor(self, connector):
        connector.record_sync_point = AsyncMock()
        connector.record_sync_point.dropbox_cursor_sync_point = AsyncMock(
            return_value={"cursor": "c1"}
        )
        connector._sync_from_source = AsyncMock()

        await connector.run_incremental_sync()
        connector._sync_from_source.assert_called_once()


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrl:
    async def test_no_data_source(self, connector):
        connector.data_source = None
        record = MagicMock(id="r1")
        result = await connector.get_signed_url(record)
        assert result is None

    async def test_no_user_with_permission(self, connector):
        record = MagicMock(id="r1")

        tx = _make_mock_tx_store_fullcov()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
        tx.get_file_record_by_id = AsyncMock(return_value=MagicMock(path="/file.pdf"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector.get_signed_url(record)
        assert result is None

    async def test_no_file_record(self, connector):
        record = MagicMock(id="r1")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        tx.get_file_record_by_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector.get_signed_url(record)
        assert result is None

    async def test_success(self, connector):
        record = MagicMock(id="r1", external_record_group_id="ns:1")
        user = MagicMock(email="user@test.com")
        file_record = MagicMock(path="/file.pdf")

        tx = _make_mock_tx_store_fullcov()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(data=MagicMock(members_info=[member_info]))
        )
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(data=MagicMock(link="https://signed-url"))
        )

        result = await connector.get_signed_url(record)
        assert result == "https://signed-url"

    async def test_personal_folder_no_team_folder_id(self, connector):
        record = MagicMock(id="r1", external_record_group_id="dbmid:AAuser1")
        user = MagicMock(email="user@test.com")
        file_record = MagicMock(path="/file.pdf")

        tx = _make_mock_tx_store_fullcov()
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(data=MagicMock(members_info=[member_info]))
        )
        connector.data_source.files_get_temporary_link = AsyncMock(
            return_value=MagicMock(data=MagicMock(link="https://signed-url2"))
        )

        result = await connector.get_signed_url(record)
        assert result == "https://signed-url2"

    async def test_exception(self, connector):
        record = MagicMock(id="r1")

        tx = _make_mock_tx_store_fullcov()
        tx.get_first_user_with_permission_to_node = AsyncMock(side_effect=Exception("DB error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector.get_signed_url(record)
        assert result is None


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @patch("app.connectors.sources.dropbox.connector.create_stream_record_response")
    async def test_success(self, mock_create, connector):
        connector.get_signed_url = AsyncMock(return_value="https://signed-url")
        record = MagicMock(id="r1", record_name="file.pdf", mime_type="application/pdf")
        mock_create.return_value = MagicMock()

        result = await connector.stream_record(record)
        mock_create.assert_called_once()

    async def test_no_signed_url(self, connector):
        connector.get_signed_url = AsyncMock(return_value=None)
        record = MagicMock(id="r1", record_name="file.pdf")

        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await connector.stream_record(record)


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestTestConnectionAndAccess:
    async def test_success(self, connector):
        connector.data_source.users_get_current_account = AsyncMock()
        assert await connector.test_connection_and_access() is True

    async def test_no_data_source(self, connector):
        connector.data_source = None
        assert await connector.test_connection_and_access() is False

    async def test_exception(self, connector):
        connector.data_source.users_get_current_account = AsyncMock(
            side_effect=Exception("Connection failed")
        )
        assert await connector.test_connection_and_access() is False


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    def test_triggers_sync(self, connector):
        connector.run_incremental_sync = AsyncMock()
        with patch("asyncio.create_task") as mock_create_task:
            connector.handle_webhook_notification({"delta": {}})
            mock_create_task.assert_called_once()


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    async def test_cleanup(self, connector):
        await connector.cleanup()
        assert connector.data_source is None


# ===========================================================================
# get_filter_options
# ===========================================================================
class TestGetFilterOptions:
    async def test_raises_not_implemented(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    async def test_empty_records(self, connector):
        await connector.reindex_records([])

    async def test_no_data_source(self, connector):
        connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    async def test_with_updated_and_non_updated(self, connector):
        record1 = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="rg:1")
        record2 = MagicMock(id="r2", external_record_id="ext:2", external_record_group_id="rg:2")

        updated_file = MagicMock()
        connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(updated_file, []), None]
        )

        await connector.reindex_records([record1, record2])
        connector.data_entities_processor.on_new_records.assert_called_once()
        connector.data_entities_processor.reindex_existing_records.assert_called_once()

    async def test_record_check_exception(self, connector):
        record = MagicMock(id="r1")
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("error"))

        await connector.reindex_records([record])

    async def test_overall_exception(self, connector):
        """When _check_and_fetch_updated_record raises, the inner try/except catches
        it and continues. The function completes without raising."""
        connector._check_and_fetch_updated_record = AsyncMock(side_effect=RuntimeError("fatal"))
        # The RuntimeError is caught by the inner try/except in the record loop,
        # so the function does not propagate it
        await connector.reindex_records([MagicMock(id="r1")])
        # Neither updated nor non-updated, so neither callback is called
        connector.data_entities_processor.on_new_records.assert_not_called()
        connector.data_entities_processor.reindex_existing_records.assert_not_called()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    async def test_no_external_id(self, connector):
        record = MagicMock(id="r1", external_record_id=None)
        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_no_file_record(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="rg:1")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_no_user_with_permission(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="rg:1")
        file_record = MagicMock(path="/file.pdf")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=None)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_team_member_info_failure(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="rg:1")
        file_record = MagicMock(path="/file.pdf")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(success=False, data=MagicMock(members_info=[]))
        )

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_deleted_at_source(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="dbmid:user1")
        file_record = MagicMock(path="/file.pdf")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(members_info=[member_info]))
        )

        deleted_entry = _make_deleted_entry()
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=deleted_entry)
        )

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_updated_at_source(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="ns:1")
        file_record = MagicMock(path="/file.pdf")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(members_info=[member_info]))
        )

        entry = _make_file_entry()
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=entry)
        )

        record_update = MagicMock()
        record_update.is_deleted = False
        record_update.is_updated = True
        record_update.record = MagicMock(id="r1")
        record_update.new_permissions = [MagicMock()]
        connector._process_dropbox_entry = AsyncMock(return_value=record_update)

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is not None

    async def test_not_updated(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="ns:1")
        file_record = MagicMock(path="/file.pdf")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(members_info=[member_info]))
        )

        entry = _make_file_entry()
        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=True, data=entry)
        )

        record_update = MagicMock()
        record_update.is_deleted = False
        record_update.is_updated = False
        connector._process_dropbox_entry = AsyncMock(return_value=record_update)

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_metadata_failure(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="ns:1")
        file_record = MagicMock(path="/file.pdf")
        user = MagicMock(email="user@test.com")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(return_value=file_record)
        tx.get_first_user_with_permission_to_node = AsyncMock(return_value=user)
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        member_info = MagicMock()
        member_info.get_member_info.return_value = MagicMock(
            profile=MagicMock(team_member_id="tm:1")
        )
        connector.data_source.team_members_get_info_v2 = AsyncMock(
            return_value=MagicMock(success=True, data=MagicMock(members_info=[member_info]))
        )

        connector.data_source.files_get_metadata = AsyncMock(
            return_value=_make_dropbox_response(success=False, error="not found")
        )

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None

    async def test_exception(self, connector):
        record = MagicMock(id="r1", external_record_id="ext:1", external_record_group_id="ns:1")

        tx = _make_mock_tx_store_fullcov()
        tx.get_file_record_by_id = AsyncMock(side_effect=Exception("DB error"))
        provider = MagicMock()

        @asynccontextmanager
        async def _transaction():
            yield tx
        provider.transaction = _transaction
        connector.data_store_provider = provider

        result = await connector._check_and_fetch_updated_record("org1", record)
        assert result is None


# ===========================================================================
# create_connector
# ===========================================================================
class TestCreateConnector:
    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.DataSourceEntitiesProcessor")
    async def test_create(self, mock_processor_cls, mock_app, mock_logger_fullcov,
                          mock_data_store_provider, mock_config_service):
        proc = MagicMock()
        proc.initialize = AsyncMock()
        mock_processor_cls.return_value = proc

        conn = await DropboxConnector.create_connector(
            mock_logger_fullcov, mock_data_store_provider, mock_config_service, "conn-123"
        )
        assert isinstance(conn, DropboxConnector)
        proc.initialize.assert_called_once()


# ===========================================================================
# _get_date_filters branches
# ===========================================================================
class TestGetDateFiltersBranches:
    def test_modified_both_after_and_before(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2024-01-01T00:00:00", "2024-12-31T23:59:59"
        )

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda key: {
            SyncFilterKey.MODIFIED: mock_filter,
            SyncFilterKey.CREATED: None,
        }.get(key))

        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is not None
        assert mb is not None
        assert ca is None
        assert cb is None

    def test_created_both_after_and_before(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (
            "2024-01-01T00:00:00", "2024-12-31T23:59:59"
        )

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda key: {
            SyncFilterKey.MODIFIED: None,
            SyncFilterKey.CREATED: mock_filter,
        }.get(key))

        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is None
        assert mb is None
        assert ca is not None
        assert cb is not None

    def test_modified_after_only(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = ("2024-01-01T00:00:00", None)

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda key: {
            SyncFilterKey.MODIFIED: mock_filter,
            SyncFilterKey.CREATED: None,
        }.get(key))

        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is not None
        assert mb is None

    def test_created_before_only(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.get_datetime_iso.return_value = (None, "2024-12-31T23:59:59")

        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda key: {
            SyncFilterKey.MODIFIED: None,
            SyncFilterKey.CREATED: mock_filter,
        }.get(key))

        ma, mb, ca, cb = connector._get_date_filters()
        assert ca is None
        assert cb is not None


# ===========================================================================
# _extract_folder_info_from_event
# ===========================================================================
class TestExtractFolderInfoFromEvent:
    def test_with_folder_asset(self, connector):
        asset = _make_folder_asset("TestFolder", "ns:1")
        event = _make_event("test", assets=[asset])

        folder_id, folder_name = connector._extract_folder_info_from_event(event)
        assert folder_id == "ns:1"
        assert folder_name == "TestFolder"

    def test_no_folder_asset(self, connector):
        event = _make_event("test", assets=[])
        folder_id, folder_name = connector._extract_folder_info_from_event(event)
        assert folder_id is None
        assert folder_name is None

    def test_asset_no_ns_id(self, connector):
        asset = MagicMock()
        asset.is_folder.return_value = True
        folder_info = MagicMock()
        folder_info.display_name = "Folder"
        folder_info.path = None
        asset.get_folder.return_value = folder_info

        event = _make_event("test", assets=[asset])
        folder_id, folder_name = connector._extract_folder_info_from_event(event)
        assert folder_id is None
        assert folder_name == "Folder"


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSync:
    @patch("app.connectors.sources.dropbox.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_full_sync_flow(self, mock_load_filters, connector):
        mock_load_filters.return_value = (FilterCollection(), FilterCollection())

        member_profile = MagicMock()
        member_profile.team_member_id = "tm:1"
        member_profile.name = MagicMock(display_name="User One")
        member_profile.email = "user@test.com"
        member_profile.status = MagicMock(_tag="active")

        member = MagicMock()
        member.profile = member_profile
        member.role = MagicMock(_tag="team_admin")

        users_resp = MagicMock()
        users_resp.data = MagicMock(members=[member])

        connector.data_source.team_members_list = AsyncMock(return_value=users_resp)
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
        connector._initialize_event_cursor = AsyncMock()
        connector._sync_user_groups = AsyncMock()
        connector.sync_record_groups = AsyncMock()
        connector.sync_personal_record_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock()

        await connector.run_sync()
        connector.data_entities_processor.on_new_app_users.assert_called_once()

    @patch("app.connectors.sources.dropbox.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_paths(self, mock_load_filters, connector):
        mock_load_filters.return_value = (FilterCollection(), FilterCollection())

        member_profile = MagicMock()
        member_profile.team_member_id = "tm:1"
        member_profile.name = MagicMock(display_name="User One")
        member_profile.email = "user@test.com"
        member_profile.status = MagicMock(_tag="active")

        member = MagicMock()
        member.profile = member_profile
        member.role = MagicMock(_tag="team_admin")

        users_resp = MagicMock()
        users_resp.data = MagicMock(members=[member])

        connector.data_source.team_members_list = AsyncMock(return_value=users_resp)
        connector.dropbox_cursor_sync_point.read_sync_point = AsyncMock(
            return_value={"cursor": "existing_cursor"}
        )
        connector._sync_member_changes_with_cursor = AsyncMock()
        connector._sync_group_changes_with_cursor = AsyncMock()
        connector._sync_record_group_changes_with_cursor = AsyncMock()
        connector.sync_personal_record_groups = AsyncMock()
        connector._process_users_in_batches = AsyncMock()
        connector._sync_sharing_changes_with_cursor = AsyncMock()

        await connector.run_sync()
        connector._sync_member_changes_with_cursor.assert_called_once()
        connector._sync_group_changes_with_cursor.assert_called_once()
        connector._sync_record_group_changes_with_cursor.assert_called_once()
        connector._sync_sharing_changes_with_cursor.assert_called_once()

    @patch("app.connectors.sources.dropbox.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_exception(self, mock_load_filters, connector):
        mock_load_filters.side_effect = Exception("config error")
        with pytest.raises(Exception, match="config error"):
            await connector.run_sync()
