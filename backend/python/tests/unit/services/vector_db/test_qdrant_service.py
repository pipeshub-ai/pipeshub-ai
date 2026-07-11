"""
Unit tests for QdrantService (app/services/vector_db/qdrant/qdrant.py).

Tests cover:
- __init__: attribute initialization
- create / create_sync / create_async: factory methods (all delegate to async create)
- connect: client creation with gRPC config, ConfigurationService vs QdrantConfig
- disconnect: success, error, already disconnected
- get_service_name / get_service / get_service_client
- create_collection: default params, custom params, client not connected
- get_collection / get_collections / delete_collection: success, client not connected
- create_index: keyword type, other type, client not connected
- filter_collection: returns FilterExpression with correct conditions
- upsert_points: single batch, multi-batch, client not connected
- delete_points: success, client not connected
- query_nearest_points: success, client not connected
- scroll: success, client not connected
- overwrite_payload: success, client not connected
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("qdrant_client", reason="qdrant_client not installed")

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
    ScrollResult,
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
    return QdrantService(qdrant_config)


@pytest.fixture
def connected_service(service):
    service.client = AsyncMock()
    return service


@pytest.fixture
def mock_config_service():
    from app.config.configuration_service import ConfigurationService
    cs = AsyncMock(spec=ConfigurationService)
    cs.get_config = AsyncMock(return_value={
        "host": "localhost",
        "port": 6333,
        "apiKey": "test_key",
    })
    return cs


# ---------------------------------------------------------------------------
# Capabilities — merged from test_qdrant_provider
# ---------------------------------------------------------------------------


class TestQdrantCapabilities:
    def test_qdrant_supports_sparse_vectors(self, service):
        caps = service.get_capabilities()
        assert caps.supports_sparse_vectors is True

    def test_qdrant_does_not_support_server_side_text_search(self, service):
        caps = service.get_capabilities()
        assert caps.supports_server_side_text_search is False

    def test_qdrant_supports_rrf(self, service):
        from app.services.vector_db.models import FusionMethod
        caps = service.get_capabilities()
        assert FusionMethod.RRF in caps.supported_fusion_methods


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_attributes(self, qdrant_config):
        svc = QdrantService(qdrant_config)
        assert svc.config_service is qdrant_config
        assert svc.client is None


# ---------------------------------------------------------------------------
# Factory methods
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_create(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await QdrantService.create(qdrant_config)
        assert svc.client is mock_client

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_create_sync_compat(self, mock_client_cls, qdrant_config):
        """create_sync is kept for backward compat and delegates to create."""
        mock_client_cls.return_value = MagicMock()
        svc = await QdrantService.create_sync(qdrant_config)
        assert svc.client is not None

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_create_async_compat(self, mock_client_cls, qdrant_config):
        """create_async is kept for backward compat and delegates to create."""
        mock_client_cls.return_value = MagicMock()
        svc = await QdrantService.create_async(qdrant_config)
        assert svc.client is not None

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_create_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client_cls.return_value = MagicMock()
        svc = await QdrantService.create(mock_config_service)
        assert svc.client is not None


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_connect_with_qdrant_config(self, mock_client_cls, qdrant_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = QdrantService(qdrant_config)
        await svc.connect()
        assert svc.client is mock_client
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 6333
        assert call_kwargs["prefer_grpc"] is True

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_connect_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client_cls.return_value = MagicMock()

        svc = QdrantService(mock_config_service)
        await svc.connect()
        assert svc.client is not None

    @pytest.mark.asyncio
    async def test_connect_no_config(self):
        from app.config.configuration_service import ConfigurationService
        mock_cs = MagicMock(spec=ConfigurationService)
        mock_cs.get_config = AsyncMock(return_value=None)
        svc = QdrantService(mock_cs)
        with pytest.raises(ValueError, match="Qdrant configuration not found"):
            await svc.connect()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_connect_exception(self, mock_client_cls, qdrant_config):
        mock_client_cls.side_effect = Exception("connection refused")
        svc = QdrantService(qdrant_config)
        with pytest.raises(Exception, match="connection refused"):
            await svc.connect()


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
# Per-event-loop clients
# ---------------------------------------------------------------------------


class TestPerLoopClients:
    """The grpc.aio transport binds to the loop that first uses it, so the
    service must hand out a separate AsyncQdrantClient per event loop (e.g.
    the main loop vs the indexing consumer's worker-thread loop)."""

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_distinct_client_per_event_loop(self, mock_client_cls, qdrant_config):
        mock_client_cls.side_effect = lambda **kwargs: MagicMock()

        svc = QdrantService(qdrant_config)
        await svc.connect()
        main_client = svc.client
        assert main_client is not None

        result = {}

        async def get_client():
            return svc.client

        def run_in_worker_loop():
            loop = asyncio.new_event_loop()
            try:
                result["client"] = loop.run_until_complete(get_client())
            finally:
                loop.close()

        thread = threading.Thread(target=run_in_worker_loop)
        thread.start()
        thread.join()

        assert result["client"] is not None
        assert result["client"] is not main_client

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_same_loop_reuses_cached_client(self, mock_client_cls, qdrant_config):
        mock_client_cls.side_effect = lambda **kwargs: MagicMock()

        svc = QdrantService(qdrant_config)
        await svc.connect()
        assert svc.client is svc.client
        assert mock_client_cls.call_count == 1

    @pytest.mark.asyncio
    @patch("app.services.vector_db.qdrant.qdrant.AsyncQdrantClient")
    async def test_disconnect_closes_all_loop_clients(self, mock_client_cls, qdrant_config):
        clients = []

        def make_client(**kwargs):
            client = MagicMock()
            client.close = AsyncMock()
            clients.append(client)
            return client

        mock_client_cls.side_effect = make_client

        svc = QdrantService(qdrant_config)
        await svc.connect()

        async def touch_client():
            return svc.client

        def worker():
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(touch_client())
            finally:
                loop.close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert len(clients) == 2
        await svc.disconnect()
        assert svc.client is None
        for client in clients:
            client.close.assert_awaited_once()


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
        connected_service.client.create_collection.assert_awaited_once()
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
        connected_service.client.create_collection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
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
        with pytest.raises(RuntimeError, match="not connected"):
            await service.get_collections()

    @pytest.mark.asyncio
    async def test_get_collection(self, connected_service):
        connected_service.client.get_collection.return_value = {"name": "col1"}
        result = await connected_service.get_collection("col1")
        assert result == {"name": "col1"}

    @pytest.mark.asyncio
    async def test_get_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
            await service.get_collection("col1")

    @pytest.mark.asyncio
    async def test_delete_collection(self, connected_service):
        await connected_service.delete_collection("col1")
        connected_service.client.delete_collection.assert_awaited_once_with("col1")

    @pytest.mark.asyncio
    async def test_delete_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
            await service.delete_collection("col1")


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    @pytest.mark.asyncio
    async def test_create_keyword_index(self, connected_service):
        """KeywordIndexParams is auto-mocked when qdrant_client is absent;
        patch at the point of use to verify it was called."""
        from unittest.mock import patch, MagicMock as _MagicMock

        sentinel = _MagicMock(name="KeywordIndexParamsInstance")
        with patch(
            "app.services.vector_db.qdrant.qdrant.KeywordIndexParams",
            return_value=sentinel,
        ) as mock_kip:
            await connected_service.create_index("col", "field1", {"type": "keyword"})

            connected_service.client.create_payload_index.assert_awaited_once()
            mock_kip.assert_called_once()
            call_args = connected_service.client.create_payload_index.call_args[0]
            assert call_args[0] == "col"
            assert call_args[1] == "field1"
            assert call_args[2] is sentinel, (
                "create_index must pass a KeywordIndexParams instance"
            )

    @pytest.mark.asyncio
    async def test_create_non_keyword_index(self, connected_service):
        schema = {"type": "integer"}
        await connected_service.create_index("col", "field1", schema)
        connected_service.client.create_payload_index.assert_awaited_once_with(
            "col", "field1", schema
        )

    @pytest.mark.asyncio
    async def test_create_index_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
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
        # filter_collection builds a FilterExpression without needing a connection
        result = await service.filter_collection(orgId="123")
        assert isinstance(result, FilterExpression)
        assert len(result.must) == 1


# ---------------------------------------------------------------------------
# upsert_points (accepts VectorPoint)
# ---------------------------------------------------------------------------


class TestUpsertPoints:
    @pytest.mark.asyncio
    async def test_single_batch(self, connected_service):
        points = [VectorPoint(id=str(i), dense_vector=[0.1] * 384, payload={}) for i in range(10)]
        await connected_service.upsert_points("col", points, batch_size=100)
        connected_service.client.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_batch(self, connected_service):
        points = [VectorPoint(id=str(i), dense_vector=[0.1] * 384, payload={}) for i in range(25)]
        await connected_service.upsert_points("col", points, batch_size=10)
        assert connected_service.client.upsert.await_count == 3

    @pytest.mark.asyncio
    async def test_not_connected(self, service):
        points = [VectorPoint(id="1", dense_vector=[0.1], payload={})]
        with pytest.raises(RuntimeError, match="not connected"):
            await service.upsert_points("col", points)


# ---------------------------------------------------------------------------
# delete_points (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestDeletePoints:
    @pytest.mark.asyncio
    async def test_delete_points_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        await connected_service.delete_points("col", filter_expr)
        connected_service.client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_points_not_connected(self, service):
        # Use a non-empty filter so the empty-filter guard doesn't fire first
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await service.delete_points("col", filter_expr)


# ---------------------------------------------------------------------------
# query_nearest_points (accepts HybridSearchRequest)
# ---------------------------------------------------------------------------


class TestQueryNearestPoints:
    @pytest.mark.asyncio
    async def test_query_success(self, connected_service):
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
        result = await connected_service.query_nearest_points("col", [request])
        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].id == "p1"
        assert result[0][0].score == 0.95

    @pytest.mark.asyncio
    async def test_query_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
            await service.query_nearest_points("col", [])


# ---------------------------------------------------------------------------
# scroll (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_success(self, connected_service):
        filter_expr = FilterExpression()
        connected_service.client.scroll.return_value = ([], None)
        result = await connected_service.scroll("col", filter_expr, 100)
        assert isinstance(result, ScrollResult)
        connected_service.client.scroll.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scroll_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
            await service.scroll("col", FilterExpression(), 100)


# ---------------------------------------------------------------------------
# overwrite_payload (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestOverwritePayload:
    @pytest.mark.asyncio
    async def test_overwrite_payload_success(self, connected_service):
        filter_expr = FilterExpression()
        await connected_service.overwrite_payload("col", {"key": "val"}, filter_expr)
        connected_service.client.overwrite_payload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_overwrite_payload_passes_filter(self, connected_service):
        """Regression: overwrite_payload must forward the converted filter to
        the Qdrant client, not discard it and match all points.

        Because qdrant_client is auto-mocked in the test environment we patch
        FilterSelector at the point of use so we can verify it was called.
        """
        from unittest.mock import patch, MagicMock as _MagicMock

        sentinel = _MagicMock(name="FilterSelectorInstance")
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )

        with patch(
            "app.services.vector_db.qdrant.qdrant.FilterSelector",
            return_value=sentinel,
        ) as mock_fs:
            await connected_service.overwrite_payload("col", {"status": "new"}, filter_expr)

            # FilterSelector must have been called (not bypassed)
            mock_fs.assert_called_once()
            # The filter kwarg must be present and non-None
            _, call_kw = mock_fs.call_args
            assert call_kw.get("filter") is not None, (
                "FilterSelector must be constructed with filter=<non-None>"
            )

        # The sentinel instance must have been forwarded to the Qdrant client
        call_kw_client = connected_service.client.overwrite_payload.call_args.kwargs
        assert call_kw_client.get("points") is sentinel, (
            "overwrite_payload must pass FilterSelector(filter=...) as `points`, "
            "not discard it and match all points"
        )

    @pytest.mark.asyncio
    async def test_overwrite_payload_not_connected(self, service):
        with pytest.raises(RuntimeError, match="not connected"):
            await service.overwrite_payload("col", {}, FilterExpression())


# ===========================================================================
# Phase 2 regression: data safety, 404 discrimination, config
# ===========================================================================

class TestDeletePointsEmptyFilterGuard:
    @pytest.mark.asyncio
    async def test_empty_filter_raises_value_error(self, connected_service):
        """delete_points with an empty FilterExpression must raise ValueError.

        This prevents accidental full-collection wipe when the caller builds an
        empty filter due to a bug or missing context.
        """
        with pytest.raises(ValueError, match="empty filter"):
            await connected_service.delete_points("col", FilterExpression())

    @pytest.mark.asyncio
    async def test_non_empty_filter_does_not_raise(self, connected_service):
        """delete_points with a non-empty filter must proceed normally."""
        connected_service.client.delete = AsyncMock()
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        # Must not raise
        await connected_service.delete_points("col", filter_expr)
        connected_service.client.delete.assert_awaited_once()


class TestCollectionExistsDiscriminates404:
    @pytest.mark.asyncio
    async def test_404_returns_false(self, connected_service):
        """collection_exists returns False for NotFoundError (404)."""
        from qdrant_client.http.exceptions import UnexpectedResponse  # type: ignore
        exc = UnexpectedResponse(status_code=404, reason_phrase="Not Found", content=b"", headers={})
        connected_service.client.get_collection = AsyncMock(side_effect=exc)
        result = await connected_service.collection_exists("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_outage_reraises(self, connected_service):
        """collection_exists re-raises connectivity errors (not a 404)."""
        connected_service.client.get_collection = AsyncMock(
            side_effect=ConnectionError("cluster unreachable")
        )
        with pytest.raises(ConnectionError):
            await connected_service.collection_exists("some_col")

    @pytest.mark.asyncio
    async def test_404_get_collection_info_returns_not_exists(self, connected_service):
        """get_collection_info returns exists=False on 404."""
        from qdrant_client.http.exceptions import UnexpectedResponse  # type: ignore
        exc = UnexpectedResponse(status_code=404, reason_phrase="Not Found", content=b"", headers={})
        connected_service.client.get_collection = AsyncMock(side_effect=exc)
        info = await connected_service.get_collection_info("nonexistent")
        assert info.exists is False

    @pytest.mark.asyncio
    async def test_outage_get_collection_info_reraises(self, connected_service):
        """get_collection_info re-raises non-404 errors."""
        connected_service.client.get_collection = AsyncMock(
            side_effect=TimeoutError("timeout")
        )
        with pytest.raises(TimeoutError):
            await connected_service.get_collection_info("some_col")


class TestQdrantConfig:
    def test_api_key_naming_round_trip_apikey(self):
        """QdrantConfig.from_dict accepts 'apiKey' (Node.js style)."""
        cfg = QdrantConfig.from_dict({"host": "h", "port": 6333, "apiKey": "secret"})
        assert cfg.api_key == "secret"

    def test_api_key_naming_round_trip_snake(self):
        """QdrantConfig.from_dict accepts 'api_key' (snake_case)."""
        cfg = QdrantConfig.from_dict({"host": "h", "port": 6333, "api_key": "mykey"})
        assert cfg.api_key == "mykey"

    def test_port_defaults_to_6333(self):
        """Port defaults to 6333 when not specified."""
        cfg = QdrantConfig.from_dict({"host": "h"})
        assert cfg.port == 6333

    def test_https_defaults_false(self):
        cfg = QdrantConfig.from_dict({"host": "h"})
        assert cfg.https is False

    def test_prefer_grpc_defaults_true(self):
        cfg = QdrantConfig.from_dict({"host": "h"})
        assert cfg.prefer_grpc is True

