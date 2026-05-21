"""Extended coverage tests for Dropbox Individual connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.exceptions import ApiError
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import FileRecord, RecordGroupType, RecordType
from app.models.permission import EntityType, Permission, PermissionType

MODULE = "app.connectors.sources.dropbox_individual.connector"
_VALID_REV = "0123456789abcdef"


class _CursorApiError(ApiError):
    def __str__(self):
        return "cursor expired"


class _PathNotFoundApiError(ApiError):
    def __str__(self):
        return "path/not_found"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


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


def _mock_sharing_success(c, url="https://dropbox.com/s/abc"):
    c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
        return_value=_make_response(True, MagicMock(url=url))
    )


def _mock_temp_link(c, link="https://dl.dropbox.com/temp", success=True):
    c.data_source.files_get_temporary_link = AsyncMock(
        return_value=_make_response(success, MagicMock(link=link) if success else None)
    )


def _make_connector(existing_record=None):
    logger = logging.getLogger("test.dropbox_ind.cov")
    dep = MagicMock()
    dep.org_id = "org-dbx-cov"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.initialize = AsyncMock()

    dsp = _make_mock_data_store_provider(existing_record)
    cs = AsyncMock()
    cs.get_config = AsyncMock()

    with patch(f"{MODULE}.DropboxIndividualApp"):
        c = DropboxIndividualConnector(
            logger, dep, dsp, cs, "conn-dbx-cov", "personal", "test-user-id"
        )

    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()
    c.data_source = AsyncMock()
    c.data_source.files_get_metadata = AsyncMock(return_value=_make_response(False))
    c.dropbox_cursor_sync_point = AsyncMock()
    c.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={})
    c.dropbox_cursor_sync_point.update_sync_point = AsyncMock()
    c.rate_limiter = MagicMock()
    c.rate_limiter.__aenter__ = AsyncMock(return_value=None)
    c.rate_limiter.__aexit__ = AsyncMock(return_value=None)
    return c, dep, dsp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.dropbox_ind.cov")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-dbx-cov"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="user@test.com"))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "credentials": {"access_token": "test_token", "refresh_token": "test_refresh"},
        "auth": {"oauthConfigId": "oauth-1"},
    })
    return svc


@pytest.fixture()
def dropbox_connector(mock_logger, mock_data_entities_processor,
                      mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp"):
        connector = DropboxIndividualConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="dbx-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


# ===========================================================================
# Helper functions
# ===========================================================================
class TestDropboxHelpers:
    def test_get_parent_path_root(self):
        assert get_parent_path_from_path("/") is None

    def test_get_parent_path_empty(self):
        assert get_parent_path_from_path("") is None

    def test_get_parent_path_nested(self):
        assert get_parent_path_from_path("/a/b/c.txt") == "/a/b"

    def test_get_parent_path_single_level(self):
        assert get_parent_path_from_path("/file.txt") is None

    def test_get_parent_path_deep(self):
        assert get_parent_path_from_path("/a/b/c/d/file.txt") == "/a/b/c/d"

    def test_get_file_extension(self):
        assert get_file_extension("file.pdf") == "pdf"
        assert get_file_extension("Makefile") is None
        assert get_file_extension("archive.tar.gz") == "gz"
        assert get_file_extension(".gitignore") == "gitignore"
        assert get_file_extension("file.") == ""

    def test_mimetype_folder(self):
        entry = _make_folder_entry()
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_mimetype_pdf(self):
        entry = _make_file_entry(name="report.pdf")
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF

    def test_mimetype_unknown(self):
        entry = _make_file_entry(name="data.xyz999")
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.BIN

    def test_mimetype_paper_file(self):
        entry = _make_file_entry(name="document.paper")
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_mimetype_no_extension(self):
        entry = _make_file_entry(name="Makefile")
        result = get_mimetype_enum_for_dropbox(entry)
        assert result == MimeTypes.BIN


# ===========================================================================
# Constructor
# ===========================================================================
class TestDropboxIndividualConstructor:
    def test_constructor(self, dropbox_connector):
        assert dropbox_connector.connector_id == "dbx-cov-1"
        assert dropbox_connector.data_source is None
        assert dropbox_connector.batch_size == 100


# ===========================================================================
# init
# ===========================================================================
class TestDropboxIndividualInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, dropbox_connector):
        dropbox_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await dropbox_connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, dropbox_connector):
        dropbox_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "t"},
            "auth": {},
        })
        result = await dropbox_connector.init()
        assert result is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.dropbox_individual.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_no_oauth_config(self, mock_fetch, dropbox_connector):
        mock_fetch.return_value = None
        result = await dropbox_connector.init()
        assert result is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.dropbox_individual.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox_individual.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox_individual.connector.DropboxDataSource")
    async def test_init_success(self, mock_ds, mock_build, mock_fetch, dropbox_connector):
        mock_fetch.return_value = {"config": {"clientId": "cid", "clientSecret": "csec"}}
        mock_build.return_value = MagicMock()
        mock_ds.return_value = MagicMock()
        result = await dropbox_connector.init()
        assert result is True

    @pytest.mark.asyncio
    @patch("app.connectors.sources.dropbox_individual.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox_individual.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    async def test_init_client_error(self, mock_build, mock_fetch, dropbox_connector):
        mock_fetch.return_value = {"config": {"clientId": "cid", "clientSecret": "csec"}}
        mock_build.side_effect = Exception("Client error")
        result = await dropbox_connector.init()
        assert result is False


# ===========================================================================
# _get_current_user_info
# ===========================================================================
class TestGetCurrentUserInfo:
    @pytest.mark.asyncio
    async def test_cached(self, dropbox_connector):
        dropbox_connector.current_user_id = "uid-1"
        dropbox_connector.current_user_email = "user@test.com"
        uid, email = await dropbox_connector._get_current_user_info()
        assert uid == "uid-1"
        assert email == "user@test.com"

    @pytest.mark.asyncio
    async def test_empty_response(self, dropbox_connector):
        dropbox_connector.data_source = MagicMock()
        dropbox_connector.data_source.users_get_current_account = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="empty response"):
            await dropbox_connector._get_current_user_info()

    @pytest.mark.asyncio
    async def test_failed_response(self, dropbox_connector):
        dropbox_connector.data_source = MagicMock()
        dropbox_connector.data_source.users_get_current_account = AsyncMock(
            return_value=_make_response(False, error="Auth error")
        )
        with pytest.raises(ValueError, match="Failed to retrieve"):
            await dropbox_connector._get_current_user_info()

    @pytest.mark.asyncio
    async def test_no_data(self, dropbox_connector):
        dropbox_connector.data_source = MagicMock()
        dropbox_connector.data_source.users_get_current_account = AsyncMock(
            return_value=_make_response(True, None)
        )
        with pytest.raises(ValueError, match="no payload"):
            await dropbox_connector._get_current_user_info()

    @pytest.mark.asyncio
    async def test_success(self, dropbox_connector):
        account_data = MagicMock()
        account_data.account_id = "uid-1"
        account_data.email = "user@test.com"
        dropbox_connector.data_source = MagicMock()
        dropbox_connector.data_source.users_get_current_account = AsyncMock(
            return_value=_make_response(True, account_data)
        )
        uid, email = await dropbox_connector._get_current_user_info()
        assert uid == "uid-1"
        assert email == "user@test.com"


# ===========================================================================
# _get_current_user_as_app_user
# ===========================================================================
class TestGetCurrentUserAsAppUser:
    def test_with_display_name(self, dropbox_connector):
        account_data = MagicMock()
        account_data.account_id = "uid-1"
        account_data.email = "user@test.com"
        account_data.name = MagicMock(display_name="John Doe")
        result = dropbox_connector._get_current_user_as_app_user(account_data)
        assert result.full_name == "John Doe"
        assert result.email == "user@test.com"

    def test_no_display_name_fallback(self, dropbox_connector):
        account_data = MagicMock()
        account_data.account_id = "uid-1"
        account_data.email = "user@test.com"
        account_data.name = MagicMock(display_name=None)
        result = dropbox_connector._get_current_user_as_app_user(account_data)
        assert result.full_name == "user"

    def test_no_name_attribute(self, dropbox_connector):
        account_data = MagicMock(spec=[])
        account_data.account_id = "uid-1"
        account_data.email = "user@test.com"
        result = dropbox_connector._get_current_user_as_app_user(account_data)
        assert result.full_name == "user"


# ===========================================================================
# _pass_date_filters
# ===========================================================================
class TestDropboxPassDateFilters:
    def test_folder_always_passes(self, dropbox_connector):
        entry = _make_folder_entry()
        assert dropbox_connector._pass_date_filters(entry, datetime.min, None, None, None) is True

    def test_deleted_entry_passes(self, dropbox_connector):
        entry = _make_deleted_entry()
        assert dropbox_connector._pass_date_filters(entry, datetime.min, None, None, None) is True

    def test_no_filters(self, dropbox_connector):
        entry = _make_file_entry()
        assert dropbox_connector._pass_date_filters(entry, None, None, None, None) is True

    def test_modified_after_fail(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert dropbox_connector._pass_date_filters(entry, cutoff, None, None, None) is False

    def test_modified_before_fail(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2025, 6, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert dropbox_connector._pass_date_filters(entry, None, cutoff, None, None) is False

    def test_created_after_fail(self, dropbox_connector):
        entry = _make_file_entry(
            client_modified=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert dropbox_connector._pass_date_filters(entry, None, None, cutoff, None) is False

    def test_created_before_fail(self, dropbox_connector):
        entry = _make_file_entry(
            client_modified=datetime(2025, 6, 1, tzinfo=timezone.utc)
        )
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert dropbox_connector._pass_date_filters(entry, None, None, None, cutoff) is False

    def test_passes_all_filters(self, dropbox_connector):
        entry = _make_file_entry(
            server_modified=datetime(2025, 3, 1, tzinfo=timezone.utc),
            client_modified=datetime(2025, 3, 1, tzinfo=timezone.utc),
        )
        after = datetime(2025, 1, 1, tzinfo=timezone.utc)
        before = datetime(2025, 6, 1, tzinfo=timezone.utc)
        assert dropbox_connector._pass_date_filters(entry, after, before, after, before) is True

    def test_naive_datetime_handling(self, dropbox_connector):
        """Test files with naive datetime (no timezone info)."""
        entry = _make_file_entry(
            server_modified=datetime(2025, 3, 1)  # no tzinfo
        )
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        # Should still work - converts to UTC
        result = dropbox_connector._pass_date_filters(entry, cutoff, None, None, None)
        assert result is True


# ===========================================================================
# _pass_extension_filter
# ===========================================================================
class TestDropboxPassExtensionFilter:
    def test_folder_always_passes(self, dropbox_connector):
        entry = _make_folder_entry()
        assert dropbox_connector._pass_extension_filter(entry) is True

    def test_deleted_always_passes(self, dropbox_connector):
        entry = _make_deleted_entry()
        assert dropbox_connector._pass_extension_filter(entry) is True

    def test_no_filter_configured(self, dropbox_connector):
        dropbox_connector.sync_filters = FilterCollection()
        entry = _make_file_entry()
        assert dropbox_connector._pass_extension_filter(entry) is True

    def test_file_no_extension_in_operator(self, dropbox_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        dropbox_connector.sync_filters = MagicMock()
        dropbox_connector.sync_filters.get.return_value = mock_filter
        entry = _make_file_entry(name="Makefile")
        assert dropbox_connector._pass_extension_filter(entry) is False

    def test_file_no_extension_not_in_operator(self, dropbox_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        dropbox_connector.sync_filters = MagicMock()
        dropbox_connector.sync_filters.get.return_value = mock_filter
        entry = _make_file_entry(name="Makefile")
        assert dropbox_connector._pass_extension_filter(entry) is True


# ===========================================================================
# _process_dropbox_entry
# ===========================================================================
class TestProcessDropboxEntry:
    @pytest.mark.asyncio
    async def test_content_changed_when_revision_differs(self):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "doc.pdf"
        existing.external_revision_id = _VALID_REV
        existing.version = 2
        c, _, _ = _make_connector(existing_record=existing)
        entry = _make_file_entry(rev="fedcba9876543210")
        _mock_temp_link(c)
        _mock_sharing_success(c)

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.content_changed is True
        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_temp_link_failure_still_processes(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c, success=False)
        _mock_sharing_success(c)

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result is not None
        assert result.record.signed_url is None

    @pytest.mark.asyncio
    async def test_shared_link_second_call_succeeds(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        first = _make_response(False, error="shared_link_already_exists")
        second = _make_response(True, MagicMock(url="https://dropbox.com/existing"))
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first, second]
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.weburl == "https://dropbox.com/existing"

    @pytest.mark.asyncio
    async def test_shared_link_url_extracted_from_error(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        first = _make_response(False, error="shared_link_already_exists")
        second = _make_response(
            False,
            error="shared_link_already_exists: SharedLinkAlreadyExistsMetadata(url='https://dropbox.com/preview')",
        )
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first, second]
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.weburl == "https://dropbox.com/preview"

    @pytest.mark.asyncio
    async def test_shared_link_unexpected_second_error(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        first = _make_response(False, error="shared_link_already_exists")
        second = _make_response(False, error="rate_limit")
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first, second]
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.weburl is None

    @pytest.mark.asyncio
    async def test_shared_link_unexpected_first_error(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            return_value=_make_response(False, error="access_denied")
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.weburl is None

    @pytest.mark.asyncio
    async def test_shared_link_regex_miss_logs_error(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        first = _make_response(False, error="shared_link_already_exists")
        second = _make_response(False, error="shared_link_already_exists: no url here")
        c.data_source.sharing_create_shared_link_with_settings = AsyncMock(
            side_effect=[first, second]
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.weburl is None

    @pytest.mark.asyncio
    async def test_parent_metadata_lookup(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry(path="/parent/child.pdf")
        _mock_temp_link(c)
        _mock_sharing_success(c)
        parent = _make_folder_entry(folder_id="id:parent", path="/parent")
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(True, parent)
        )

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result.record.parent_external_record_id == "id:parent"

    @pytest.mark.asyncio
    async def test_permission_fallback_on_exception(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        _mock_temp_link(c)
        _mock_sharing_success(c)

        with patch(f"{MODULE}.Permission") as mock_perm_cls:
            mock_perm_cls.side_effect = [
                Exception("perm fail"),
                Permission(
                    external_id="uid",
                    email="u@test.com",
                    type=PermissionType.OWNER,
                    entity_type=EntityType.USER,
                ),
            ]
            result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")

        assert result.new_permissions[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_entry_processing_exception_returns_none(self):
        c, _, dsp = _make_connector()
        entry = _make_file_entry()
        tx = AsyncMock()
        tx.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db down"))

        @asynccontextmanager
        async def _broken_tx():
            yield tx

        dsp.transaction = _broken_tx

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result is None

    @pytest.mark.asyncio
    async def test_root_path_skips_parent_lookup(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry(path="/doc.pdf")
        entry.path_display = "/"
        _mock_temp_link(c)
        _mock_sharing_success(c)

        result = await c._process_dropbox_entry(entry, "uid", "u@test.com", "uid")
        assert result is not None
        assert result.record.parent_external_record_id is None
        c.data_source.files_get_metadata.assert_not_called()


# ===========================================================================
# _process_dropbox_items_generator
# ===========================================================================
class TestProcessDropboxItemsGenerator:
    @pytest.mark.asyncio
    async def test_yields_records(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        record = MagicMock(is_shared=False)
        update = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        c._process_dropbox_entry = AsyncMock(return_value=update)

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "uid", "u@test.com", "uid"
        ):
            items.append(item)

        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_files_indexing_filter_off(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        record = MagicMock(is_shared=False)
        update = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        c._process_dropbox_entry = AsyncMock(return_value=update)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(
            side_effect=lambda key, default=True: key != IndexingFilterKey.FILES
        )

        async for rec, _, _ in c._process_dropbox_items_generator(
            [entry], "uid", "u@test.com", "uid"
        ):
            assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_shared_indexing_filter_off(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        record = MagicMock(is_shared=True)
        update = RecordUpdate(
            record=record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        c._process_dropbox_entry = AsyncMock(return_value=update)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(
            side_effect=lambda key, default=True: key != IndexingFilterKey.SHARED
        )

        async for rec, _, _ in c._process_dropbox_items_generator(
            [entry], "uid", "u@test.com", "uid"
        ):
            assert rec.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_skips_none_record_update(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        c._process_dropbox_entry = AsyncMock(return_value=None)

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "uid", "u@test.com", "uid"
        ):
            items.append(item)

        assert items == []

    @pytest.mark.asyncio
    async def test_generator_swallows_item_errors(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        c._process_dropbox_entry = AsyncMock(side_effect=RuntimeError("boom"))

        items = []
        async for item in c._process_dropbox_items_generator(
            [entry], "uid", "u@test.com", "uid"
        ):
            items.append(item)

        assert items == []


# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestGetDateFilters:
    def test_modified_and_created_filters(self):
        c, _, _ = _make_connector()
        mf = MagicMock()
        mf.is_empty.return_value = False
        mf.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")
        cf = MagicMock()
        cf.is_empty.return_value = False
        cf.get_datetime_iso.return_value = ("2023-06-01T00:00:00", "2023-12-31T23:59:59")
        filters_map = {
            SyncFilterKey.MODIFIED: mf,
            SyncFilterKey.CREATED: cf,
        }
        mock_fc = MagicMock()
        mock_fc.get = lambda key, default=None: filters_map.get(key, default)
        c.sync_filters = mock_fc

        m_after, m_before, c_after, c_before = c._get_date_filters()
        assert m_after is not None
        assert m_before is not None
        assert c_after is not None
        assert c_before is not None

    def test_modified_filter_after_only(self):
        c, _, _ = _make_connector()
        mf = MagicMock()
        mf.is_empty.return_value = False
        mf.get_datetime_iso.return_value = ("2024-01-01T00:00:00", None)
        mock_fc = MagicMock()
        mock_fc.get = lambda key, default=None: mf if key == SyncFilterKey.MODIFIED else default
        c.sync_filters = mock_fc

        m_after, m_before, _, _ = c._get_date_filters()
        assert m_after is not None
        assert m_before is None

    def test_created_filter_before_only(self):
        c, _, _ = _make_connector()
        cf = MagicMock()
        cf.is_empty.return_value = False
        cf.get_datetime_iso.return_value = (None, "2024-12-31T23:59:59")
        mock_fc = MagicMock()
        mock_fc.get = lambda key, default=None: cf if key == SyncFilterKey.CREATED else default
        c.sync_filters = mock_fc

        _, _, _, c_before = c._get_date_filters()
        assert c_before is not None


# ===========================================================================
# _run_sync_with_cursor
# ===========================================================================
class TestRunSyncWithCursor:
    def _list_result(self, entries=None, cursor="c1", has_more=False):
        data = MagicMock()
        data.entries = entries or []
        data.cursor = cursor
        data.has_more = has_more
        return _make_response(True, data)

    @pytest.mark.asyncio
    async def test_first_sync_batches_new_records(self):
        c, dep, _ = _make_connector()
        c.batch_size = 2
        entries = [
            _make_file_entry(name=f"f{i}.pdf", file_id=f"id:{i}", path=f"/f{i}.pdf")
            for i in range(3)
        ]
        c.data_source.files_list_folder = AsyncMock(return_value=self._list_result(entries))
        _mock_temp_link(c)
        _mock_sharing_success(c)

        await c._run_sync_with_cursor("uid", "u@test.com")

        assert dep.on_new_records.await_count == 2
        c.dropbox_cursor_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_continues_with_existing_cursor(self):
        c, _, _ = _make_connector()
        c.dropbox_cursor_sync_point.read_sync_point = AsyncMock(return_value={"cursor": "old-c"})
        c.data_source.files_list_folder_continue = AsyncMock(
            return_value=self._list_result()
        )

        await c._run_sync_with_cursor("uid", "u@test.com")

        c.data_source.files_list_folder_continue.assert_awaited_once_with("old-c")
        c.data_source.files_list_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_folder_failure_stops_sync(self):
        c, dep, _ = _make_connector()
        c.data_source.files_list_folder = AsyncMock(
            return_value=_make_response(False, error="rate limited")
        )

        await c._run_sync_with_cursor("uid", "u@test.com")

        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_updated_records_call_handle_updates(self):
        c, dep, _ = _make_connector()
        entry = _make_file_entry()
        c.data_source.files_list_folder = AsyncMock(return_value=self._list_result([entry]))
        record = MagicMock(record_name="doc.pdf", is_shared=False)
        updated = RecordUpdate(
            record=record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )

        async def _updated_gen(*_args, **_kwargs):
            yield (record, [], updated)

        c._process_dropbox_items_generator = _updated_gen

        await c._run_sync_with_cursor("uid", "u@test.com")

        c.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        dep.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleted_records_call_handle_updates(self):
        c, _, _ = _make_connector()
        entry = _make_file_entry()
        c.data_source.files_list_folder = AsyncMock(return_value=self._list_result([entry]))
        deleted = RecordUpdate(
            record=None,
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            external_record_id="id:deleted",
        )

        async def _deleted_gen(*_args, **_kwargs):
            yield (None, [], deleted)

        c._process_dropbox_items_generator = _deleted_gen

        await c._run_sync_with_cursor("uid", "u@test.com")

        c.data_entities_processor.on_record_deleted.assert_awaited_once_with(
            record_id="id:deleted"
        )

    @pytest.mark.asyncio
    async def test_api_error_invalid_cursor_stops(self):
        c, _, _ = _make_connector()
        c.data_source.files_list_folder = AsyncMock(
            side_effect=_CursorApiError("req", MagicMock(), "cursor expired", "en")
        )

        await c._run_sync_with_cursor("uid", "u@test.com")

    @pytest.mark.asyncio
    async def test_api_error_path_not_found_stops(self):
        c, _, _ = _make_connector()
        c.data_source.files_list_folder = AsyncMock(
            side_effect=_PathNotFoundApiError("req", MagicMock(), "path/not_found", "en")
        )

        await c._run_sync_with_cursor("uid", "u@test.com")

    @pytest.mark.asyncio
    async def test_api_error_other_reraises(self):
        c, _, _ = _make_connector()
        c.data_source.files_list_folder = AsyncMock(
            side_effect=ApiError("req", MagicMock(), "msg", "other_failure")
        )

        with pytest.raises(ApiError):
            await c._run_sync_with_cursor("uid", "u@test.com")

    @pytest.mark.asyncio
    async def test_generic_loop_exception_stops(self):
        c, _, _ = _make_connector()
        c.data_source.files_list_folder = AsyncMock(side_effect=RuntimeError("unexpected"))

        await c._run_sync_with_cursor("uid", "u@test.com")


# ===========================================================================
# _handle_record_updates
# ===========================================================================
class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_is_updated_without_change_flags(self):
        c, dep, _ = _make_connector()
        update = RecordUpdate(
            record=MagicMock(record_name="same.pdf"),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await c._handle_record_updates(update)
        dep.on_record_metadata_update.assert_not_called()
        dep.on_record_content_update.assert_not_called()


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        c, _, _ = _make_connector()
        record = MagicMock(record_name="test.pdf", mime_type="application/pdf", id="r1")
        c.get_signed_url = AsyncMock(return_value="https://download.url")

        with patch(f"{MODULE}.create_stream_record_response") as mock_stream, \
             patch(f"{MODULE}.stream_content"):
            mock_stream.return_value = MagicMock()
            await c.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_signed_url_raises(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1")
        c.get_signed_url = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == 404


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_only_updated_records(self):
        c, dep, _ = _make_connector()
        c._get_current_user_info = AsyncMock(return_value=("uid", "u@test.com"))
        updated = MagicMock()
        c._check_and_fetch_updated_record = AsyncMock(return_value=(updated, []))

        await c.reindex_records([MagicMock(id="r1")])

        dep.on_new_records.assert_awaited_once()
        dep.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_record_error_continues(self):
        c, dep, _ = _make_connector()
        c._get_current_user_info = AsyncMock(return_value=("uid", "u@test.com"))
        c._check_and_fetch_updated_record = AsyncMock(
            side_effect=[RuntimeError("check failed"), None]
        )
        rec1 = MagicMock(id="r1")
        rec2 = MagicMock(id="r2")

        await c.reindex_records([rec1, rec2])

        dep.reindex_existing_records.assert_awaited_once_with([rec2])

    @pytest.mark.asyncio
    async def test_outer_exception_reraises(self):
        c, _, _ = _make_connector()
        c._get_current_user_info = AsyncMock(side_effect=RuntimeError("fatal"))

        with pytest.raises(RuntimeError, match="fatal"):
            await c.reindex_records([MagicMock(id="r1")])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_external_id(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id=None)
        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_metadata_fetch_failure(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(False, error="not found")
        )

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_at_source(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        deleted = DeletedMetadata(name="gone.txt")
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(True, deleted)
        )

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_updated_record(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        entry = _make_file_entry()
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(True, entry)
        )
        updated_file = FileRecord(
            id="should-be-replaced",
            record_name="doc.pdf",
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE.value,
            external_record_group_id="uid",
            external_record_id="id:f1",
            external_revision_id=_VALID_REV,
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.DROPBOX_PERSONAL,
            connector_id="conn-dbx-cov",
            created_at=1,
            updated_at=1,
            source_created_at=1,
            source_updated_at=1,
            weburl="https://dropbox.com/s/x",
            signed_url=None,
            parent_external_record_id=None,
            size_in_bytes=1024,
            is_file=True,
            preview_renderable=True,
            extension="pdf",
            path="/folder/doc.pdf",
            mime_type=MimeTypes.PDF,
            sha256_hash="a" * 64,
        )
        update = RecordUpdate(
            record=updated_file,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
        )
        c._process_dropbox_entry = AsyncMock(return_value=update)

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")

        assert result is not None
        updated, perms = result
        assert updated.id == "r1"
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_deleted_record_update_returns_none(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        entry = _make_file_entry()
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(True, entry)
        )
        deleted_update = RecordUpdate(
            record=MagicMock(),
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        c._process_dropbox_entry = AsyncMock(return_value=deleted_update)

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_update_returns_none(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        entry = _make_file_entry()
        c.data_source.files_get_metadata = AsyncMock(
            return_value=_make_response(True, entry)
        )
        update = RecordUpdate(
            record=MagicMock(),
            is_new=False,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
        )
        c._process_dropbox_entry = AsyncMock(return_value=update)

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c, _, _ = _make_connector()
        record = MagicMock(id="r1", external_record_id="id:f1", external_record_group_id="uid")
        c.data_source.files_get_metadata = AsyncMock(side_effect=RuntimeError("api down"))

        result = await c._check_and_fetch_updated_record("org", record, "uid", "u@test.com")
        assert result is None


# ===========================================================================
# Misc
# ===========================================================================
class TestDropboxIndividualMisc:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch(f"{MODULE}.DataSourceEntitiesProcessor") as MockProc, \
             patch(f"{MODULE}.DropboxIndividualApp"):
            proc = AsyncMock()
            proc.initialize = AsyncMock()
            MockProc.return_value = proc
            conn = await DropboxIndividualConnector.create_connector(
                logging.getLogger("test"),
                MagicMock(),
                AsyncMock(),
                "conn-new",
                "personal",
                "creator",
            )
            assert isinstance(conn, DropboxIndividualConnector)
            proc.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_filter_options_not_implemented(self):
        c, _, _ = _make_connector()
        with pytest.raises(NotImplementedError):
            await c.get_filter_options("any")

    @pytest.mark.asyncio
    async def test_get_signed_url_path_fallback(self):
        c, _, _ = _make_connector()
        c.data_source.files_get_temporary_link = AsyncMock(
            return_value=_make_response(True, MagicMock(link="https://dl/path"))
        )
        record = MagicMock(external_record_id=None, path="/fallback.txt", id="r1")
        result = await c.get_signed_url(record)
        assert result == "https://dl/path"

    def test_handle_webhook_notification(self):
        c, _, _ = _make_connector()
        c.run_incremental_sync = AsyncMock()
        with patch("asyncio.create_task") as mock_task:
            c.handle_webhook_notification({})
            mock_task.assert_called_once()
