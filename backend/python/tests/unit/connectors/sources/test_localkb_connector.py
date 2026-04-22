"""Tests for the LocalKB connector, KB service, Knowledge Hub service, and migration service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.connectors.sources.localKB.connector import (
    KB_CONNECTOR_NAME,
    KBApp,
    KnowledgeBaseConnector,
)
from app.connectors.sources.localKB.handlers.kb_service import (
    KnowledgeBaseService,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.models.entities import FileRecord, RecordType
from fastapi import HTTPException
from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.sources.localKB.connector import KnowledgeBaseConnector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a KnowledgeBaseConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    connector_id = "kb-conn-1"
    connector = KnowledgeBaseConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
        scope="team",
        created_by="test-user",
    )
    return connector


def _make_kb_service():
    """Build a KnowledgeBaseService with mocked dependencies."""
    logger = MagicMock()
    graph_provider = AsyncMock()
    kafka_service = MagicMock()
    return KnowledgeBaseService(logger=logger, graph_provider=graph_provider, kafka_service=kafka_service)


def _make_knowledge_hub_service():
    """Build a KnowledgeHubService with mocked dependencies."""
    logger = MagicMock()
    graph_provider = AsyncMock()
    return KnowledgeHubService(logger=logger, graph_provider=graph_provider)


def _make_record(**overrides):
    """Build a minimal Record for testing the connector."""
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
        "source_created_at": 1000,
        "source_updated_at": 2000,
        "is_file": True,
        "extension": "pdf",
        "size_in_bytes": 500,
        "weburl": "https://example.com/file.pdf",
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


# ===================================================================
# KBApp tests
# ===================================================================

class TestKBApp:
    def test_app_initialization(self):
        app = KBApp("conn-1")
        assert app.app_name == Connectors.KNOWLEDGE_BASE

    def test_connector_name_constant(self):
        assert KB_CONNECTOR_NAME == "Collections"


# ===================================================================
# KnowledgeBaseConnector tests
# ===================================================================

class TestKnowledgeBaseConnector:
    @pytest.mark.asyncio
    async def test_init_returns_true(self):
        connector = _make_connector()
        result = await connector.init()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_and_access_always_true(self):
        connector = _make_connector()
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_run_sync_is_noop(self):
        connector = _make_connector()
        result = await connector.run_sync()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_is_noop(self):
        connector = _make_connector()
        result = await connector.run_incremental_sync()
        assert result is None

    def test_handle_webhook_is_noop(self):
        connector = _make_connector()
        connector.handle_webhook_notification({"type": "test"})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        await connector.cleanup()
        connector.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_reindex_records(self):
        connector = _make_connector()
        records = [_make_record()]
        await connector.reindex_records(records)
        connector.logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_get_filter_options_returns_empty(self):
        connector = _make_connector()
        result = await connector.get_filter_options("any_key")
        assert result.options == []
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_get_signed_url_non_upload_origin_returns_none(self):
        connector = _make_connector()
        record = _make_record(origin=OriginTypes.CONNECTOR)
        result = await connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_signed_url_no_external_record_id_returns_none(self):
        connector = _make_connector()
        record = _make_record(external_record_id="")
        result = await connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_stream_record_non_upload_raises(self):
        connector = _make_connector()
        record = _make_record(origin=OriginTypes.CONNECTOR)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_record_no_external_id_raises(self):
        connector = _make_connector()
        record = _make_record(external_record_id="")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_connector_factory_method(self):
        logger = MagicMock()
        data_store_provider = MagicMock()
        config_service = AsyncMock()
        with patch(
            "app.connectors.sources.localKB.connector.DataSourceEntitiesProcessor"
        ) as MockProcessor:
            mock_proc = MagicMock()
            mock_proc.initialize = AsyncMock()
            MockProcessor.return_value = mock_proc
            connector = await KnowledgeBaseConnector.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id="kb-conn-1",
                scope="team",
                created_by="user-1",
            )
            assert isinstance(connector, KnowledgeBaseConnector)
            mock_proc.initialize.assert_awaited_once()


# ===================================================================
# KnowledgeBaseService tests
# ===================================================================

class TestKnowledgeBaseService:
    @pytest.mark.asyncio
    async def test_create_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "fullName": "Test User"}
        )
        svc.graph_provider.begin_transaction = AsyncMock(return_value="txn-1")
        svc.graph_provider.batch_upsert_nodes = AsyncMock()
        svc.graph_provider.batch_create_edges = AsyncMock()
        svc.graph_provider.commit_transaction = AsyncMock()

        result = await svc.create_knowledge_base("user-1", "org-1", "My KB")
        assert result["success"] is True
        assert result["name"] == "My KB"
        assert "id" in result

    @pytest.mark.asyncio
    async def test_create_kb_user_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await svc.create_knowledge_base("user-1", "org-1", "My KB")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_create_kb_transaction_failure(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "fullName": "Test User"}
        )
        svc.graph_provider.begin_transaction = AsyncMock(side_effect=Exception("DB down"))

        result = await svc.create_knowledge_base("user-1", "org-1", "My KB")
        assert result["success"] is False
        assert result["code"] == 500

    @pytest.mark.asyncio
    async def test_get_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "_key": "user-key-1"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.get_knowledge_base = AsyncMock(
            return_value={"id": "kb-1", "name": "Test KB"}
        )

        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["id"] == "kb-1"

    @pytest.mark.asyncio
    async def test_get_kb_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "_key": "user-key-1"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await svc.get_knowledge_base("kb-1", "user-1")
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_list_user_knowledge_bases(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "_key": "user-key-1"}
        )
        svc.graph_provider.list_user_knowledge_bases = AsyncMock(
            return_value=(
                [{"id": "kb-1", "name": "KB1"}],
                1,
                {"permissions": ["OWNER", "WRITER"]},
            )
        )

        result = await svc.list_user_knowledge_bases("user-1", "org-1")
        assert "knowledgeBases" in result
        assert result["pagination"]["totalCount"] == 1

    @pytest.mark.asyncio
    async def test_update_kb_insufficient_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await svc.update_knowledge_base("kb-1", "user-1", {"groupName": "New"})
        assert result["success"] is False
        assert result["code"] == "403"

    @pytest.mark.asyncio
    async def test_delete_kb_owner_only(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "fullName": "Test"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_delete_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1", "fullName": "Test User"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        svc.graph_provider.delete_knowledge_base = AsyncMock(
            return_value={"success": True, "eventData": []}
        )

        result = await svc.delete_knowledge_base("kb-1", "user-1")
        assert result["success"] is True
        assert result["code"] == 200

    @pytest.mark.asyncio
    async def test_create_folder_in_kb_success(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": True}
        )
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(return_value=None)
        svc.graph_provider.create_folder = AsyncMock(
            return_value={"success": True, "id": "folder-1"}
        )

        result = await svc.create_folder_in_kb("kb-1", "Docs", "user-1", "org-1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_create_folder_name_conflict(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": True}
        )
        svc.graph_provider.find_folder_by_name_in_parent = AsyncMock(
            return_value={"id": "existing-folder"}
        )

        result = await svc.create_folder_in_kb("kb-1", "Docs", "user-1", "org-1")
        assert result["success"] is False
        assert result["code"] == 409

    @pytest.mark.asyncio
    async def test_create_nested_folder_parent_not_found(self):
        svc = _make_kb_service()
        svc.graph_provider._validate_folder_creation = AsyncMock(
            return_value={"valid": True}
        )
        svc.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=False)

        result = await svc.create_nested_folder("kb-1", "parent-1", "Sub", "user-1", "org-1")
        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_get_folder_contents_no_permission(self):
        svc = _make_kb_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-key-1"}
        )
        svc.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await svc.get_folder_contents("kb-1", "folder-1", "user-1")
        assert result["success"] is False
        assert result["code"] == "403"


# ===================================================================
# KnowledgeHubService tests
# ===================================================================

class TestKnowledgeHubService:
    @pytest.mark.asyncio
    async def test_get_nodes_user_not_found(self):
        svc = _make_knowledge_hub_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await svc.get_nodes(user_id="user-1", org_id="org-1")
        assert result.success is False
        assert result.error == "User not found"

    def test_has_search_filters_with_query(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_search_filters("search", None, None, None, None, None, None, None, None) is True

    def test_has_search_filters_all_none(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_search_filters(None, None, None, None, None, None, None, None, None) is False

    def test_has_flattening_filters_with_node_types(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_flattening_filters(None, ["FILE"], None, None, None, None, None, None, None) is True

    def test_has_flattening_filters_all_none(self):
        svc = _make_knowledge_hub_service()
        assert svc._has_flattening_filters(None, None, None, None, None, None, None, None, None) is False

# =============================================================================
# Merged from test_localkb_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector_cov():
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


def _make_record_cov(**overrides):
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        record = _make_record_cov()
        conn.config_service.get_config = AsyncMock(
            side_effect=Exception("config error")
        )
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_buffer(self):
        conn = _make_connector_cov()
        record = _make_record_cov()
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
        conn = _make_connector_cov()
        # Force an exception during init by making logger.info raise
        conn.logger.info = MagicMock(side_effect=Exception("unexpected"))
        result = await conn.init()
        assert result is False
