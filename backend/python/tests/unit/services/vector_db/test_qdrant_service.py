"""
Unit tests for QdrantService (app/services/vector_db/qdrant/qdrant.py).

Tests cover:
- __init__: attribute initialization
- create_sync / create_async: factory methods
- connect_sync / connect_async: client creation with gRPC config, ConfigurationService vs QdrantConfig
- connect: dispatches based on is_async flag
- disconnect: success, error, already disconnected
- get_service_name / get_service / get_service_client
- create_collection: default params, custom params, client not connected
- get_collection / get_collections / delete_collection: success, client not connected
- create_index: keyword type, other type, client not connected
- filter_collection: returns FilterExpression with correct conditions
- upsert_points: single batch, multi-batch parallel, client not connected
- delete_points: success, client not connected
- query_nearest_points: success, client not connected
- scroll: success, client not connected
- overwrite_payload: success, client not connected
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.http.models import (
    KeywordIndexParams,
)

from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.qdrant.config import QdrantConfig
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FilterExpression,
    FilterMode,
    FieldCondition as GenericFieldCondition,
    FusionMethod,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def qdrant_config():
    return QdrantConfig(
        host="localhost",
        port=6333,
        api_key="test_key",
        prefer_grpc=True,
        https=False,
        timeout=180,
    )


@pytest.fixture
def service(qdrant_config):
    return QdrantService(qdrant_config, is_async=False)


@pytest.fixture
def async_service(qdrant_config):
    return QdrantService(qdrant_config, is_async=True)


@pytest.fixture
def connected_service(service):
    service.client = MagicMock()
    return service


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "host": "localhost",
        "port": 6333,
        "apiKey": "test_key",
    })
    return cs


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_attributes(self, qdrant_config):
        svc = QdrantService(qdrant_config, is_async=False)
        assert svc.config_service is qdrant_config
        assert svc.client is None
        assert svc.is_async is False

    def test_async_flag(self, qdrant_config):
        svc = QdrantService(qdrant_config, is_async=True)
        assert svc.is_async is True


# ---------------------------------------------------------------------------
# Factory methods
# ---------------------------------------------------------------------------


class TestCreateSync:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_create_sync(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await QdrantService.create_sync(qdrant_config)
        assert svc.client is mock_client
        assert svc.is_async is False

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_create_sync_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await QdrantService.create_sync(mock_config_service)
        assert svc.client is mock_client


class TestCreateAsync:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_create_async(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await QdrantService.create_async(qdrant_config)
        assert svc.client is mock_client
        assert svc.is_async is True


# ---------------------------------------------------------------------------
# connect_sync / connect_async / connect
# ---------------------------------------------------------------------------


class TestConnectSync:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_connect_sync_with_qdrant_config(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = QdrantService(qdrant_config, is_async=False)
        await svc.connect_sync()
        assert svc.client is mock_client
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 6333
        assert call_kwargs["prefer_grpc"] is True

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_connect_sync_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = QdrantService(mock_config_service, is_async=False)
        await svc.connect_sync()
        assert svc.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_sync_no_config(self):
        from app.config.configuration_service import ConfigurationService
        mock_cs = MagicMock(spec=ConfigurationService)
        mock_cs.get_config = AsyncMock(return_value=None)
        svc = QdrantService(mock_cs, is_async=False)
        with pytest.raises(ValueError, match="Qdrant configuration not found"):
            await svc.connect_sync()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_connect_sync_exception(self, mock_client_cls, qdrant_config):
        mock_client_cls.side_effect = Exception("connection refused")
        svc = QdrantService(qdrant_config, is_async=False)
        with pytest.raises(Exception, match="connection refused"):
            await svc.connect_sync()


class TestConnectAsync:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_connect_async(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = QdrantService(qdrant_config, is_async=True)
        await svc.connect_async()
        assert svc.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_async_no_config(self):
        from app.config.configuration_service import ConfigurationService
        mock_cs = MagicMock(spec=ConfigurationService)
        mock_cs.get_config = AsyncMock(return_value=None)
        svc = QdrantService(mock_cs, is_async=True)
        with pytest.raises(ValueError, match="Qdrant configuration not found"):
            await svc.connect_async()


class TestConnect:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.QdrantClient")
    async def test_connect_dispatches_sync(self, mock_client_cls, qdrant_config):
        mock_client_cls.return_value = MagicMock()
        svc = QdrantService(qdrant_config, is_async=False)
        await svc.connect()
        assert svc.client is not None

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_connect_dispatches_async(self, mock_client_cls, qdrant_config):
        mock_client_cls.return_value = MagicMock()
        svc = QdrantService(qdrant_config, is_async=True)
        await svc.connect()
        assert svc.client is not None


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_success(self, connected_service):
        await connected_service.disconnect()
        assert connected_service.client is None

    @pytest.mark.asyncio
    async def test_disconnect_no_client(self, service):
        await service.disconnect()
        assert service.client is None

    @pytest.mark.asyncio
    async def test_disconnect_close_error(self, connected_service):
        connected_service.client.close.side_effect = Exception("close failed")
        await connected_service.disconnect()
        assert connected_service.client is None


# ---------------------------------------------------------------------------
# Service metadata
# ---------------------------------------------------------------------------


class TestServiceMetadata:
    def test_get_service_name(self, service):
        assert service.get_service_name() == "qdrant"

    def test_get_service(self, service):
        assert service.get_service() is service

    def test_get_service_client_none(self, service):
        assert service.get_service_client() is None

    def test_get_service_client_connected(self, connected_service):
        assert connected_service.get_service_client() is connected_service.client


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


class TestCreateCollection:
    @pytest.mark.asyncio
    async def test_create_collection_defaults(self, connected_service):
        await connected_service.create_collection()
        connected_service.client.create_collection.assert_called_once()
        call_kwargs = connected_service.client.create_collection.call_args
        assert call_kwargs[1]["collection_name"] == "records"

    @pytest.mark.asyncio
    async def test_create_collection_custom_name(self, connected_service):
        config = CollectionConfig(embedding_size=768)
        await connected_service.create_collection(
            collection_name="custom_col", config=config
        )
        call_kwargs = connected_service.client.create_collection.call_args
        assert call_kwargs[1]["collection_name"] == "custom_col"

    @pytest.mark.asyncio
    async def test_create_collection_with_config(self, connected_service):
        config = CollectionConfig(
            embedding_size=384,
            sparse_idf=True,
            distance_metric=DistanceMetric.L2,
        )
        await connected_service.create_collection(config=config)
        connected_service.client.create_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.create_collection()


# ---------------------------------------------------------------------------
# get_collection / get_collections / delete_collection
# ---------------------------------------------------------------------------


class TestCollectionOperations:
    @pytest.mark.asyncio
    async def test_get_collections(self, connected_service):
        connected_service.client.get_collections.return_value = ["col1", "col2"]
        result = await connected_service.get_collections()
        assert result == ["col1", "col2"]

    @pytest.mark.asyncio
    async def test_get_collections_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.get_collections()

    @pytest.mark.asyncio
    async def test_get_collection(self, connected_service):
        connected_service.client.get_collection.return_value = {"name": "col1"}
        result = await connected_service.get_collection("col1")
        assert result == {"name": "col1"}

    @pytest.mark.asyncio
    async def test_get_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.get_collection("col1")

    @pytest.mark.asyncio
    async def test_delete_collection(self, connected_service):
        await connected_service.delete_collection("col1")
        connected_service.client.delete_collection.assert_called_once_with("col1")

    @pytest.mark.asyncio
    async def test_delete_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.delete_collection("col1")


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    @pytest.mark.asyncio
    async def test_create_keyword_index(self, connected_service):
        await connected_service.create_index("col", "field1", {"type": "keyword"})
        connected_service.client.create_payload_index.assert_called_once()
        call_args = connected_service.client.create_payload_index.call_args[0]
        assert call_args[0] == "col"
        assert call_args[1] == "field1"
        assert isinstance(call_args[2], KeywordIndexParams)

    @pytest.mark.asyncio
    async def test_create_non_keyword_index(self, connected_service):
        schema = {"type": "integer"}
        await connected_service.create_index("col", "field1", schema)
        connected_service.client.create_payload_index.assert_called_once_with(
            "col", "field1", schema
        )

    @pytest.mark.asyncio
    async def test_create_index_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.create_index("col", "field1", {"type": "keyword"})


# ---------------------------------------------------------------------------
# filter_collection (returns FilterExpression)
# ---------------------------------------------------------------------------


class TestFilterCollection:
    @pytest.mark.asyncio
    async def test_must_mode_default(self, connected_service):
        result = await connected_service.filter_collection(
            orgId="org1", status="active"
        )
        assert isinstance(result, FilterExpression)
        assert len(result.must) == 2

    @pytest.mark.asyncio
    async def test_should_mode(self, connected_service):
        result = await connected_service.filter_collection(
            filter_mode=FilterMode.SHOULD,
            department="IT", role="admin"
        )
        assert isinstance(result, FilterExpression)
        assert len(result.should) == 2

    @pytest.mark.asyncio
    async def test_must_not_mode(self, connected_service):
        result = await connected_service.filter_collection(
            filter_mode=FilterMode.MUST_NOT,
            status="deleted"
        )
        assert isinstance(result, FilterExpression)
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_string_mode_conversion(self, connected_service):
        result = await connected_service.filter_collection(
            filter_mode="should", department="IT"
        )
        assert isinstance(result, FilterExpression)
        assert result.should is not None

    @pytest.mark.asyncio
    async def test_invalid_string_mode(self, connected_service):
        with pytest.raises(ValueError, match="Invalid mode"):
            await connected_service.filter_collection(
                filter_mode="invalid_mode", field="value"
            )

    @pytest.mark.asyncio
    async def test_explicit_must_should_must_not(self, connected_service):
        result = await connected_service.filter_collection(
            must={"orgId": "123"},
            should={"department": "IT", "role": "admin"},
            must_not={"status": "deleted"},
        )
        assert len(result.must) == 1
        assert len(result.should) == 2
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_empty_filter(self, connected_service):
        result = await connected_service.filter_collection()
        assert isinstance(result, FilterExpression)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_list_values_use_match_any(self, connected_service):
        result = await connected_service.filter_collection(
            must={"roles": ["admin", "user"]}
        )
        assert len(result.must) == 1
        assert result.must[0].values == ["admin", "user"]

    @pytest.mark.asyncio
    async def test_none_values_ignored(self, connected_service):
        result = await connected_service.filter_collection(
            must={"orgId": "123", "nullField": None}
        )
        assert len(result.must) == 1

    @pytest.mark.asyncio
    async def test_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.filter_collection(orgId="123")


# ---------------------------------------------------------------------------
# upsert_points (accepts VectorPoint)
# ---------------------------------------------------------------------------


class TestUpsertPoints:
    def test_single_batch(self, connected_service):
        points = [VectorPoint(id=str(i), dense_vector=[0.1] * 384, payload={}) for i in range(10)]
        connected_service.upsert_points("col", points, batch_size=100)
        connected_service.client.upsert.assert_called_once()

    def test_multi_batch(self, connected_service):
        points = [VectorPoint(id=str(i), dense_vector=[0.1] * 384, payload={}) for i in range(25)]
        connected_service.upsert_points("col", points, batch_size=10, max_workers=2)
        assert connected_service.client.upsert.call_count == 3

    def test_not_connected(self, service):
        points = [VectorPoint(id="1", dense_vector=[0.1], payload={})]
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.upsert_points("col", points)


# ---------------------------------------------------------------------------
# delete_points (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestDeletePoints:
    def test_delete_points_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        connected_service.delete_points("col", filter_expr)
        connected_service.client.delete.assert_called_once()

    def test_delete_points_not_connected(self, service):
        filter_expr = FilterExpression()
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.delete_points("col", filter_expr)


# ---------------------------------------------------------------------------
# query_nearest_points (accepts HybridSearchRequest)
# ---------------------------------------------------------------------------


class TestQueryNearestPoints:
    def test_query_success(self, connected_service):
        # Mock the Qdrant response structure
        mock_point = MagicMock()
        mock_point.id = "p1"
        mock_point.score = 0.95
        mock_point.payload = {"metadata": {"orgId": "org1"}, "page_content": "hello"}

        mock_batch = MagicMock()
        mock_batch.points = [mock_point]

        connected_service.client.query_batch_points.return_value = [mock_batch]

        request = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            sparse_query=SparseVector(indices=[0, 1], values=[1.0, 0.5]),
            limit=10,
            fusion_method=FusionMethod.RRF,
        )
        result = connected_service.query_nearest_points("col", [request])
        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].id == "p1"
        assert result[0][0].score == 0.95

    def test_query_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.query_nearest_points("col", [])


# ---------------------------------------------------------------------------
# scroll (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_success(self, connected_service):
        filter_expr = FilterExpression()
        connected_service.client.scroll.return_value = ([], None)
        result = await connected_service.scroll("col", filter_expr, 100)
        connected_service.client.scroll.assert_called_once()

    @pytest.mark.asyncio
    async def test_scroll_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.scroll("col", FilterExpression(), 100)


# ---------------------------------------------------------------------------
# overwrite_payload (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestOverwritePayload:
    def test_overwrite_payload_success(self, connected_service):
        filter_expr = FilterExpression()
        connected_service.overwrite_payload("col", {"key": "val"}, filter_expr)
        connected_service.client.overwrite_payload.assert_called_once()

    def test_overwrite_payload_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.overwrite_payload("col", {}, FilterExpression())
