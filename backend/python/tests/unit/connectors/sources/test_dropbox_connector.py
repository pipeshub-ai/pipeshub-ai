"""Tests for Dropbox Team and Dropbox Individual connectors."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper function imports (these are module-level helpers, safe to import)
# ---------------------------------------------------------------------------
from app.connectors.sources.dropbox.connector import (
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
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="admin@test.com"))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


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


# ===========================================================================
# Dropbox helper functions
# ===========================================================================

class TestGetParentPathFromPath:
    """Tests for the get_parent_path_from_path helper."""

    def test_root_path_returns_none(self):
        assert get_parent_path_from_path("/") is None

    def test_empty_path_returns_none(self):
        assert get_parent_path_from_path("") is None

    def test_single_level_returns_root(self):
        assert get_parent_path_from_path("/folder") is None

    def test_nested_path(self):
        result = get_parent_path_from_path("/a/b/file.txt")
        assert result == "/a/b"

    def test_deeply_nested(self):
        result = get_parent_path_from_path("/a/b/c/d/file.txt")
        assert result == "/a/b/c/d"

    def test_two_level_path(self):
        result = get_parent_path_from_path("/folder/subfolder")
        assert result == "/folder"


class TestGetFileExtension:
    """Tests for the get_file_extension helper."""

    def test_simple_extension(self):
        assert get_file_extension("file.txt") == "txt"

    def test_no_extension(self):
        assert get_file_extension("README") is None

    def test_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_uppercase_extension(self):
        assert get_file_extension("image.PNG") == "png"


class TestGetMimetypeEnumForDropbox:
    """Tests for the get_mimetype_enum_for_dropbox helper."""

    def test_folder_returns_folder_type(self):
        from dropbox.files import FolderMetadata
        entry = FolderMetadata(name="test_folder", id="id:123", path_lower="/test_folder")
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER

    def test_paper_file_returns_html(self):
        from dropbox.files import FileMetadata
        from datetime import datetime
        from app.config.constants.arangodb import MimeTypes
        entry = FileMetadata(
            name="doc.paper", id="id:456", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="rev1", size=100
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.HTML

    def test_pdf_file_returns_pdf(self):
        from dropbox.files import FileMetadata
        from datetime import datetime
        from app.config.constants.arangodb import MimeTypes
        entry = FileMetadata(
            name="doc.pdf", id="id:789", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="rev1", size=200
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.PDF

    def test_unknown_extension_returns_bin(self):
        from dropbox.files import FileMetadata
        from datetime import datetime
        from app.config.constants.arangodb import MimeTypes
        entry = FileMetadata(
            name="file.xyz123", id="id:abc", client_modified=datetime.now(),
            server_modified=datetime.now(), rev="rev1", size=50
        )
        assert get_mimetype_enum_for_dropbox(entry) == MimeTypes.BIN


# ===========================================================================
# DropboxConnector initialization
# ===========================================================================

class TestDropboxConnectorInit:
    """Tests for DropboxConnector.__init__ and init() method."""

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    def test_constructor_sets_attributes(self, mock_app,
                                         mock_logger, mock_data_entities_processor,
                                         mock_data_store_provider, mock_config_service):
        from app.connectors.sources.dropbox.connector import DropboxConnector
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
        assert connector.connector_id == "conn-123"
        assert connector.data_source is None
        assert connector.batch_size == 100

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, mock_fetch_oauth,
                                mock_app, mock_logger, mock_data_entities_processor,
                                mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = {
            "config": {"clientId": "app-key", "clientSecret": "app-secret"}
        }
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()

        from app.connectors.sources.dropbox.connector import DropboxConnector
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
        result = await connector.init()
        assert result is True
        assert connector.data_source is not None

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    async def test_init_fails_no_config(self, mock_app,
                                        mock_logger, mock_data_entities_processor,
                                        mock_data_store_provider):
        config_svc = AsyncMock()
        config_svc.get_config = AsyncMock(return_value=None)

        from app.connectors.sources.dropbox.connector import DropboxConnector
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=config_svc,
            connector_id="conn-123",
        )
        result = await connector.init()
        assert result is False

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_no_oauth_config(self, mock_fetch_oauth, mock_app,
                                              mock_logger, mock_data_entities_processor,
                                              mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = None
        from app.connectors.sources.dropbox.connector import DropboxConnector
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
        result = await connector.init()
        assert result is False

    @patch("app.connectors.sources.dropbox.connector.DropboxApp")
    @patch("app.connectors.sources.dropbox.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.dropbox.connector.DropboxClient.build_with_config", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, mock_fetch_oauth,
                                               mock_app, mock_logger,
                                               mock_data_entities_processor,
                                               mock_data_store_provider, mock_config_service):
        mock_fetch_oauth.return_value = {
            "config": {"clientId": "key", "clientSecret": "secret"}
        }
        mock_build.side_effect = Exception("Connection failed")

        from app.connectors.sources.dropbox.connector import DropboxConnector
        connector = DropboxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-123",
        )
        result = await connector.init()
        assert result is False


# ===========================================================================
# DropboxIndividualConnector
# ===========================================================================

class TestDropboxIndividualHelpers:
    """Tests for Dropbox Individual helper functions (shared logic)."""

    def test_get_parent_path(self):
        assert ind_get_parent_path_from_path("/a/b/c") == "/a/b"
        assert ind_get_parent_path_from_path("/") is None

    def test_get_file_extension(self):
        assert ind_get_file_extension("doc.pdf") == "pdf"
        assert ind_get_file_extension("noext") is None

    def test_mimetype_enum_folder(self):
        from dropbox.files import FolderMetadata
        from app.config.constants.arangodb import MimeTypes
        entry = FolderMetadata(name="test", id="id:1", path_lower="/test")
        assert ind_get_mimetype_enum_for_dropbox(entry) == MimeTypes.FOLDER


class TestDropboxIndividualConnectorInit:
    """Tests for DropboxIndividualConnector init."""

    @patch("app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp")
    def test_constructor(self, mock_app, mock_logger,
                         mock_data_entities_processor,
                         mock_data_store_provider, mock_config_service):
        from app.connectors.sources.dropbox_individual.connector import DropboxIndividualConnector
        connector = DropboxIndividualConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="conn-ind-1",
        )
        assert connector.connector_id == "conn-ind-1"
        assert connector.data_source is None
