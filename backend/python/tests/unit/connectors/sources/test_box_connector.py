"""Tests for Box connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.box.connector import (
    BoxConnector,
    get_file_extension,
    get_mimetype_enum_for_box,
    get_parent_path_from_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.box")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-box-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "clientId": "box-client-id",
            "clientSecret": "box-client-secret",
            "enterpriseId": "box-ent-123",
        },
    })
    return svc


@pytest.fixture()
def box_connector(mock_logger, mock_data_entities_processor,
                  mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.box.connector.BoxApp"):
        connector = BoxConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="box-conn-1",
        )
    return connector


# ===========================================================================
# Helper functions
# ===========================================================================

class TestBoxGetParentPath:
    def test_root_returns_none(self):
        assert get_parent_path_from_path("/") is None

    def test_empty_returns_none(self):
        assert get_parent_path_from_path("") is None

    def test_nested(self):
        assert get_parent_path_from_path("/a/b/c.txt") == "/a/b"

    def test_single_level(self):
        assert get_parent_path_from_path("/file.txt") is None


class TestBoxGetFileExtension:
    def test_normal(self):
        assert get_file_extension("report.pdf") == "pdf"

    def test_no_ext(self):
        assert get_file_extension("Makefile") is None

    def test_compound(self):
        assert get_file_extension("backup.tar.gz") == "gz"


class TestBoxMimeType:
    def test_folder(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_box("folder") == MimeTypes.FOLDER

    def test_file_pdf(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_box("file", "report.pdf") == MimeTypes.PDF

    def test_file_unknown(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_box("file", "data.xyz999") == MimeTypes.BIN

    def test_file_no_filename(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_enum_for_box("file") == MimeTypes.BIN


# ===========================================================================
# BoxConnector
# ===========================================================================

class TestBoxConnectorInit:
    def test_constructor(self, box_connector):
        assert box_connector.connector_id == "box-conn-1"
        assert box_connector.data_source is None
        assert box_connector.batch_size == 100

    @patch("app.connectors.sources.box.connector.BoxClient.build_with_config", new_callable=AsyncMock)
    @patch("app.connectors.sources.box.connector.BoxDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, box_connector):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        mock_client.get_client.return_value.create_client = AsyncMock()
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()

        result = await box_connector.init()
        assert result is True

    async def test_init_fails_no_config(self, box_connector):
        box_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await box_connector.init()
        assert result is False

    async def test_init_fails_missing_credentials(self, box_connector):
        box_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"clientId": "id"}
        })
        result = await box_connector.init()
        assert result is False

    @patch("app.connectors.sources.box.connector.BoxClient.build_with_config", new_callable=AsyncMock)
    async def test_init_fails_client_error(self, mock_build, box_connector):
        mock_build.side_effect = Exception("Auth failure")
        result = await box_connector.init()
        assert result is False


class TestBoxConnectorParseTimestamp:
    def test_parse_valid_timestamp(self, box_connector):
        ts = "2024-01-15T10:30:00Z"
        result = box_connector._parse_box_timestamp(ts, "created", "file.txt")
        expected = int(datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert result == expected

    def test_parse_none_timestamp(self, box_connector):
        result = box_connector._parse_box_timestamp(None, "created", "file.txt")
        # Should return a recent timestamp (fallback to now)
        assert result > 0

    def test_parse_invalid_timestamp(self, box_connector):
        result = box_connector._parse_box_timestamp("not-a-date", "modified", "file.txt")
        assert result > 0


class TestBoxConnectorToDict:
    def test_none_returns_empty(self, box_connector):
        assert box_connector._to_dict(None) == {}

    def test_dict_passthrough(self, box_connector):
        d = {"key": "value"}
        assert box_connector._to_dict(d) == d

    def test_object_with_to_dict(self, box_connector):
        obj = MagicMock()
        obj.to_dict.return_value = {"id": "123"}
        assert box_connector._to_dict(obj) == {"id": "123"}

    def test_object_with_response_object(self, box_connector):
        obj = MagicMock(spec=[])
        obj.response_object = {"data": True}
        assert box_connector._to_dict(obj) == {"data": True}
