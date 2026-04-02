"""Extended coverage tests for Dropbox Individual connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dropbox.files import DeletedMetadata, FileMetadata, FolderMetadata

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
    get_file_extension,
    get_mimetype_enum_for_dropbox,
    get_parent_path_from_path,
)


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

# =============================================================================
# Merged from test_dropbox_individual_coverage.py
# =============================================================================
class TestDropboxHelpersCoverage:
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
class TestDropboxIndividualConstructorCoverage:
    def test_constructor(self, dropbox_connector):
        assert dropbox_connector.connector_id == "dbx-cov-1"
        assert dropbox_connector.data_source is None
        assert dropbox_connector.batch_size == 100


# ===========================================================================
# init
# ===========================================================================
class TestDropboxIndividualInitCoverage:
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
class TestGetCurrentUserInfoCoverage:
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
class TestGetCurrentUserAsAppUserCoverage:
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
class TestDropboxPassDateFiltersCoverage:
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
class TestDropboxPassExtensionFilterCoverage:
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
