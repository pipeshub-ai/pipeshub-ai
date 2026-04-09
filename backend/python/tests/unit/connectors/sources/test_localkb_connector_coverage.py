"""Additional tests for KnowledgeBaseConnector targeting remaining uncovered lines.

Covers:
- get_signed_url (cloud storage JSON response, local storage, error responses, exception)
- stream_record (success with dict data, success with list data, success with raw data, exception)
- init exception path
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector
from app.models.entities import FileRecord, RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a KnowledgeBaseConnector with mocked dependencies."""
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    ds_provider = MagicMock()
    config_service = AsyncMock()
    return KnowledgeBaseConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=ds_provider,
        config_service=config_service,
        connector_id="kb-conn-1",
        scope="personal",
        created_by="test-user-id",
    )


def _make_record(**overrides):
    """Build a minimal Record for testing."""
    defaults = {
        "org_id": "org-1",
        "external_record_id": "ext-file-1",
        "record_name": "test.pdf",
        "origin": OriginTypes.UPLOAD,
        "connector_name": Connectors.KNOWLEDGE_BASE,
        "connector_id": "kb-conn-1",
        "record_type": RecordType.FILE,
        "version": 1,
        "mime_type": "application/pdf",
        "is_file": True,
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


def _make_mock_response(status=200, content_type="application/json", json_data=None, text_data="", read_data=b""):
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    mock_headers = MagicMock()
    mock_headers.get = MagicMock(return_value=content_type)
    mock_headers.__getitem__ = MagicMock(return_value=content_type)
    resp.headers = mock_headers
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value=text_data)
    resp.read = AsyncMock(return_value=read_data)
    return resp


def _make_async_context_manager(return_value):
    """Create an async context manager mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


# ===================================================================
# get_signed_url
# ===================================================================

class TestGetSignedUrl:

    @pytest.mark.asyncio
    async def test_cloud_storage_returns_signed_url(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        mock_resp = _make_mock_response(
            status=200,
            content_type="application/json",
            json_data={"signedUrl": "https://signed.url/file"},
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.aiohttp.ClientSession") as MockSession:
                mock_session = MagicMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_session.get = MagicMock(
                    return_value=_make_async_context_manager(mock_resp)
                )
                MockSession.return_value = mock_session

                result = await conn.get_signed_url(record)
                assert result == "https://signed.url/file"

    @pytest.mark.asyncio
    async def test_cloud_storage_no_signed_url_in_response(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        mock_resp = _make_mock_response(
            status=200,
            content_type="application/json",
            json_data={},  # No signedUrl
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.aiohttp.ClientSession") as MockSession:
                mock_session = MagicMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_session.get = MagicMock(
                    return_value=_make_async_context_manager(mock_resp)
                )
                MockSession.return_value = mock_session

                result = await conn.get_signed_url(record)
                assert result is None

    @pytest.mark.asyncio
    async def test_local_storage_returns_none(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        mock_resp = _make_mock_response(
            status=200,
            content_type="application/octet-stream",  # Not JSON
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.aiohttp.ClientSession") as MockSession:
                mock_session = MagicMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_session.get = MagicMock(
                    return_value=_make_async_context_manager(mock_resp)
                )
                MockSession.return_value = mock_session

                result = await conn.get_signed_url(record)
                assert result is None

    @pytest.mark.asyncio
    async def test_error_response(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        mock_resp = _make_mock_response(status=404, text_data="Not found")

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.aiohttp.ClientSession") as MockSession:
                mock_session = MagicMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=None)
                mock_session.get = MagicMock(
                    return_value=_make_async_context_manager(mock_resp)
                )
                MockSession.return_value = mock_session

                result = await conn.get_signed_url(record)
                assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            side_effect=Exception("config error")
        )
        result = await conn.get_signed_url(record)
        assert result is None


# ===================================================================
# stream_record
# ===================================================================

class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_success_with_dict_data(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.make_api_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {
                    "data": {"data": [72, 101, 108, 108, 111]}  # "Hello" as bytes list
                }
                result = await conn.stream_record(record)
                assert result is not None
                assert result.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_success_with_raw_bytes_data(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.make_api_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"data": b"raw binary data"}
                result = await conn.stream_record(record)
                assert result is not None

    @pytest.mark.asyncio
    async def test_success_with_dict_data_bytes_type(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.make_api_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {
                    "data": {"data": b"direct bytes"}
                }
                result = await conn.stream_record(record)
                assert result is not None

    @pytest.mark.asyncio
    async def test_no_mime_type_defaults(self):
        conn = _make_connector()
        record = _make_record()
        record.mime_type = None  # Set to None after construction to bypass validation
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.make_api_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"data": b"data"}
                result = await conn.stream_record(record)
                assert result is not None
                assert result.media_type == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_exception_raises_http_exception(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            side_effect=Exception("config error")
        )
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_buffer(self):
        conn = _make_connector()
        record = _make_record()
        conn.config_service.get_config = AsyncMock(
            return_value={"storage": {"endpoint": "http://storage:3000"}}
        )

        with patch("app.connectors.sources.localKB.connector.generate_jwt", new_callable=AsyncMock, return_value="jwt-token"):
            with patch("app.connectors.sources.localKB.connector.make_api_call", new_callable=AsyncMock) as mock_call:
                mock_call.return_value = {"data": {"data": None}}
                result = await conn.stream_record(record)
                assert result is not None
                assert result.body == b""


# ===================================================================
# init exception
# ===================================================================

class TestInitException:

    @pytest.mark.asyncio
    async def test_init_exception_returns_false(self):
        conn = _make_connector()
        # Force an exception during init by making logger.info raise
        conn.logger.info = MagicMock(side_effect=Exception("unexpected"))
        result = await conn.init()
        assert result is False
