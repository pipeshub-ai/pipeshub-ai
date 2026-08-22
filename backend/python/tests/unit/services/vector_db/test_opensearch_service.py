"""
Unit tests for OpenSearchService (app/services/vector_db/opensearch/opensearch.py).

Tests cover:
- __init__: attribute initialization
- create: factory method
- connect: client creation with OpenSearchConfig vs ConfigurationService
- disconnect: success, error, already disconnected, async client
- get_service_name / get_service / get_service_client
- create_collection: default params, custom params, pipeline creation, client not connected
- get_collection / get_collections / delete_collection: success, client not connected
- create_index: keyword type, text type, client not connected
- filter_collection: returns FilterExpression with correct conditions
- upsert_points: bulk call, errors, client not connected
- delete_points: success, client not connected
- query_nearest_points: hybrid query, fallback without pipeline, client not connected
- scroll: success, pagination, client not connected
- overwrite_payload: success, client not connected
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("opensearchpy", reason="opensearch-py not installed")

from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FieldCondition as GenericFieldCondition,
    FilterExpression,
    FilterMode,
    FusionMethod,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def os_config():
    return OpenSearchConfig(
        host="localhost",
        port=9200,
        username="admin",
        password="admin",
        use_ssl=False,
        verify_certs=False,
        ssl_show_warn=False,
        timeout=300,
    )


@pytest.fixture
def service(os_config):
    return OpenSearchService(os_config)


@pytest.fixture
def async_service(os_config):
    return OpenSearchService(os_config)


@pytest.fixture
def connected_service(service):
    client = AsyncMock()
    client.indices = AsyncMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.create = AsyncMock(return_value={"acknowledged": True})
    client.indices.delete = AsyncMock(return_value={"acknowledged": True})
    client.indices.get = AsyncMock(return_value={
        "records": {
            "mappings": {
                "properties": {
                    "dense_embedding": {"dimension": 1024}
                }
            }
        }
    })
    client.transport = AsyncMock()
    client.transport.perform_request = AsyncMock(return_value={"acknowledged": True})
    client.search = AsyncMock(return_value={"hits": {"hits": []}})
    client.delete_by_query = AsyncMock(return_value={})
    client.update_by_query = AsyncMock(return_value={})
    client.count = AsyncMock(return_value={"count": 0})
    service.client = client
    return service


@pytest.fixture
def mock_config_service():
    from app.config.configuration_service import ConfigurationService

    cs = AsyncMock(spec=ConfigurationService)
    cs.get_config = AsyncMock(return_value={
        "host": "localhost",
        "port": 9200,
        "username": "admin",
        "password": "admin",
        "useSsl": False,
        "verifyCerts": False,
        "timeout": 300,
    })
    return cs


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_attributes(self, os_config):
        svc = OpenSearchService(os_config)
        assert svc.config_service is os_config
        assert svc.client is None

    def test_async_flag(self, os_config):
        # Always async now; is_async param removed
        svc = OpenSearchService(os_config)
        assert svc.client is None


# ---------------------------------------------------------------------------
# Factory method
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_create_async(self, mock_client_cls, os_config):
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(return_value={"version": {"number": "3.0.0"}})
        mock_client_cls.return_value = mock_client

        svc = await OpenSearchService.create(os_config)
        # connect() only parses config; client created lazily on first use
        assert svc._cfg is not None
        assert svc.client is None
        # Trigger lazy init
        await svc._ensure_client()
        assert svc.client is mock_client

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_create_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(return_value={"version": {"number": "3.0.0"}})
        mock_client_cls.return_value = mock_client

        svc = await OpenSearchService.create(mock_config_service)
        assert svc._cfg is not None
        await svc._ensure_client()
        assert svc.client is mock_client


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_connect_async_with_opensearch_config(self, mock_client_cls, os_config):
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(return_value={"version": {"number": "3.0.0"}})
        mock_client_cls.return_value = mock_client

        svc = OpenSearchService(os_config)
        await svc.connect()
        # connect() parses config only; client created on first use
        assert svc._cfg is not None
        assert svc.client is None
        # Trigger lazy init and verify client construction args
        await svc._ensure_client()
        assert svc.client is mock_client
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["hosts"] == [{"host": "localhost", "port": 9200}]
        assert call_kwargs["http_auth"] == ("admin", "admin")

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_connect_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(return_value={"version": {"number": "3.0.0"}})
        mock_client_cls.return_value = mock_client

        svc = OpenSearchService(mock_config_service)
        await svc.connect()
        assert svc._cfg is not None
        await svc._ensure_client()
        assert svc.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_no_config(self):
        from app.config.configuration_service import ConfigurationService
        mock_cs = MagicMock(spec=ConfigurationService)
        mock_cs.get_config = AsyncMock(return_value=None)
        svc = OpenSearchService(mock_cs)
        with pytest.raises(ValueError, match="OpenSearch configuration not found"):
            await svc.connect()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_connect_exception(self, mock_client_cls, os_config):
        mock_client = AsyncMock()
        mock_client.info = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client
        svc = OpenSearchService(os_config)
        await svc.connect()
        with pytest.raises(Exception, match="connection refused"):
            await svc._ensure_client()
        assert svc.client is None


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_sync_success(self, connected_service):
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

    @pytest.mark.asyncio
    async def test_disconnect_async_client(self, service):
        from opensearchpy import AsyncOpenSearch
        mock_client = AsyncMock(spec=AsyncOpenSearch)
        service.client = mock_client
        await service.disconnect()
        mock_client.close.assert_awaited_once()
        assert service.client is None


# ---------------------------------------------------------------------------
# Service metadata
# ---------------------------------------------------------------------------


class TestServiceMetadata:
    def test_get_service_name(self, service):
        assert service.get_service_name() == "opensearch"

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

        connected_service.client.indices.create.assert_awaited_once()
        call_kwargs = connected_service.client.indices.create.call_args[1]
        assert call_kwargs["index"] == "records"
        body = call_kwargs["body"]
        assert body["settings"]["index.knn"] is True
        assert body["mappings"]["properties"]["dense_embedding"]["dimension"] == 1024
        assert body["mappings"]["properties"]["dense_embedding"]["method"]["space_type"] == "cosinesimil"

    @pytest.mark.asyncio
    async def test_create_collection_custom_name(self, connected_service):
        config = CollectionConfig(embedding_size=768)
        await connected_service.create_collection(
            collection_name="custom_col", config=config
        )
        call_kwargs = connected_service.client.indices.create.call_args[1]
        assert call_kwargs["index"] == "custom_col"

    @pytest.mark.asyncio
    async def test_create_collection_with_config(self, connected_service):
        config = CollectionConfig(
            embedding_size=384,
            sparse_idf=True,
            distance_metric=DistanceMetric.L2,
        )
        await connected_service.create_collection(config=config)

        call_kwargs = connected_service.client.indices.create.call_args[1]
        body = call_kwargs["body"]
        assert body["mappings"]["properties"]["dense_embedding"]["dimension"] == 384
        assert body["mappings"]["properties"]["dense_embedding"]["method"]["space_type"] == "l2"

    @pytest.mark.asyncio
    async def test_create_collection_dot_product(self, connected_service):
        config = CollectionConfig(distance_metric=DistanceMetric.DOT_PRODUCT)
        await connected_service.create_collection(config=config)

        body = connected_service.client.indices.create.call_args[1]["body"]
        assert body["mappings"]["properties"]["dense_embedding"]["method"]["space_type"] == "innerproduct"

    @pytest.mark.asyncio
    async def test_create_collection_creates_pipeline(self, connected_service):
        # Idempotent: GET checks existence; PUT only when absent.
        connected_service.client.transport.perform_request = AsyncMock(
            side_effect=[
                Exception("not found"),
                {"acknowledged": True},
            ]
        )
        await connected_service.create_collection(collection_name="my-idx")

        calls = connected_service.client.transport.perform_request.call_args_list
        assert calls[0][0][0] == "GET"
        assert calls[1][0][0] == "PUT"

    @pytest.mark.asyncio
    async def test_create_collection_pipeline_failure_raises(self, connected_service):
        """_ensure_rrf_pipeline now fails fast so misconfigured clusters are caught early."""
        connected_service.client.transport.perform_request = AsyncMock(
            side_effect=Exception("pipeline creation failed")
        )

        with pytest.raises(Exception, match="pipeline creation failed"):
            await connected_service.create_collection()

    @pytest.mark.asyncio
    async def test_create_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.create_collection()


# ---------------------------------------------------------------------------
# get_collection / get_collections / delete_collection
# ---------------------------------------------------------------------------


class TestCollectionOperations:
    @pytest.mark.asyncio
    async def test_get_collections(self, connected_service):
        connected_service.client.indices.get_alias.return_value = {"idx1": {}, "idx2": {}}
        result = await connected_service.get_collections()
        assert result == {"idx1": {}, "idx2": {}}

    @pytest.mark.asyncio
    async def test_get_collections_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.get_collections()

    @pytest.mark.asyncio
    async def test_get_collection(self, connected_service):
        connected_service.client.indices.get.return_value = {"my-idx": {"mappings": {}}}
        result = await connected_service.get_collection("my-idx")
        assert result == {"my-idx": {"mappings": {}}}
        connected_service.client.indices.get.assert_called_once_with(index="my-idx")

    @pytest.mark.asyncio
    async def test_get_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.get_collection("my-idx")

    @pytest.mark.asyncio
    async def test_delete_collection(self, connected_service):
        connected_service.client.indices.exists = AsyncMock(return_value=True)
        connected_service.client.indices.delete = AsyncMock(return_value={"acknowledged": True})
        connected_service.client.transport.perform_request = AsyncMock(return_value={})

        await connected_service.delete_collection("my-idx")

        connected_service.client.indices.delete.assert_awaited_once_with(index="my-idx")

    @pytest.mark.asyncio
    async def test_delete_collection_index_does_not_exist(self, connected_service):
        connected_service.client.indices.exists = AsyncMock(return_value=False)
        connected_service.client.indices.delete = AsyncMock()
        connected_service.client.transport.perform_request = AsyncMock()

        await connected_service.delete_collection("nonexistent")

        connected_service.client.indices.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_collection_pipeline_cleanup_error_ignored(self, connected_service):
        connected_service.client.indices.exists = AsyncMock(return_value=True)
        connected_service.client.indices.delete = AsyncMock(return_value={"acknowledged": True})
        connected_service.client.transport.perform_request = AsyncMock(
            side_effect=Exception("pipeline not found")
        )

        # Should not raise even if pipeline deletion fails
        await connected_service.delete_collection("my-idx")
        connected_service.client.indices.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.delete_collection("my-idx")


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    @pytest.mark.asyncio
    async def test_create_keyword_index(self, connected_service):
        connected_service.client.indices.put_mapping = AsyncMock(return_value={"acknowledged": True})
        await connected_service.create_index("my-idx", "field1", {"type": "keyword"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["index"] == "my-idx"
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "keyword"

    @pytest.mark.asyncio
    async def test_create_text_index(self, connected_service):
        connected_service.client.indices.put_mapping = AsyncMock(return_value={"acknowledged": True})
        await connected_service.create_index("my-idx", "field1", {"type": "text"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_create_non_keyword_index_defaults_to_text(self, connected_service):
        connected_service.client.indices.put_mapping = AsyncMock(return_value={"acknowledged": True})
        await connected_service.create_index("my-idx", "field1", {"type": "integer"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_create_index_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.create_index("my-idx", "field1", {"type": "keyword"})


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
    async def test_min_should_match(self, connected_service):
        result = await connected_service.filter_collection(
            should={"department": "IT", "role": "admin"},
            min_should_match=1,
        )
        assert result.min_should_match == 1
        assert len(result.should) == 2

    @pytest.mark.asyncio
    async def test_min_should_match_not_set_without_should(self, connected_service):
        result = await connected_service.filter_collection(
            must={"orgId": "123"},
            min_should_match=1,
        )
        assert result.min_should_match is None

    @pytest.mark.asyncio
    async def test_not_connected(self, service):
        # filter_collection constructs a FilterExpression and does not require client
        result = await service.filter_collection(orgId="123")
        from app.services.vector_db.models import FilterExpression
        assert isinstance(result, FilterExpression)


# ---------------------------------------------------------------------------
# upsert_points
# ---------------------------------------------------------------------------


class TestUpsertPoints:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_upsert_points(self, mock_helpers, connected_service):
        mock_helpers.async_bulk = AsyncMock(return_value=(2, []))
        points = [
            VectorPoint(
                id="p1",
                dense_vector=[0.1, 0.2],
                payload={"metadata": {"orgId": "org1"}, "page_content": "hello"},
            ),
            VectorPoint(
                id="p2",
                dense_vector=[0.3, 0.4],
                payload={"metadata": {}, "page_content": "world"},
            ),
        ]
        await connected_service.upsert_points("my-idx", points)
        mock_helpers.async_bulk.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_upsert_empty_list(self, mock_helpers, connected_service):
        mock_helpers.async_bulk = AsyncMock(return_value=(0, []))
        await connected_service.upsert_points("my-idx", [])
        # Empty list still calls async_bulk with no actions
        mock_helpers.async_bulk.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_not_connected(self, service):
        points = [VectorPoint(id="1", dense_vector=[0.1], payload={})]
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.upsert_points("my-idx", points)


# ---------------------------------------------------------------------------
# delete_points (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestDeletePoints:
    @pytest.mark.asyncio
    async def test_delete_points_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        await connected_service.delete_points("my-idx", filter_expr)

        connected_service.client.delete_by_query.assert_awaited_once()
        call_kwargs = connected_service.client.delete_by_query.call_args[1]
        assert call_kwargs["index"] == "my-idx"

    @pytest.mark.asyncio
    async def test_delete_points_empty_filter(self, connected_service):
        filter_expr = FilterExpression()
        with pytest.raises(ValueError, match="empty filter"):
            await connected_service.delete_points("my-idx", filter_expr)

    @pytest.mark.asyncio
    async def test_delete_points_not_connected(self, service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.delete_points("my-idx", filter_expr)


# ---------------------------------------------------------------------------
# query_nearest_points (accepts HybridSearchRequest)
# ---------------------------------------------------------------------------


class TestQueryNearestPoints:
    @pytest.mark.asyncio
    async def test_hybrid_query(self, connected_service):
        connected_service.client.search = AsyncMock(return_value={
            "hits": {
                "hits": [
                    {
                        "_id": "doc-1",
                        "_score": 0.95,
                        "_source": {
                            "metadata": {"orgId": "org1"},
                            "page_content": "hello",
                        },
                    }
                ]
            }
        })

        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            text_query="hello",
            limit=10,
            fusion_method=FusionMethod.RRF,
        )
        results = await connected_service.query_nearest_points("my-idx", [req])

        assert len(results) == 1
        assert len(results[0]) == 1
        assert results[0][0].id == "doc-1"
        assert results[0][0].score == 0.95
        assert results[0][0].payload["page_content"] == "hello"

    @pytest.mark.asyncio
    async def test_query_multiple_requests(self, connected_service):
        connected_service.client.search = AsyncMock(return_value={
            "hits": {"hits": [
                {"_id": "d1", "_score": 0.9, "_source": {"metadata": {}, "page_content": "a"}},
            ]}
        })

        req1 = HybridSearchRequest(dense_query=[0.1], limit=5)
        req2 = HybridSearchRequest(text_query="test", limit=5)
        results = await connected_service.query_nearest_points("my-idx", [req1, req2])

        assert len(results) == 2
        assert connected_service.client.search.await_count == 2

    @pytest.mark.asyncio
    async def test_query_with_filter(self, connected_service):
        connected_service.client.search = AsyncMock(return_value={"hits": {"hits": []}})

        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            filter=filter_expr,
            limit=10,
        )
        results = await connected_service.query_nearest_points("my-idx", [req])
        assert len(results) == 1
        assert results[0] == []

    @pytest.mark.asyncio
    async def test_query_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.query_nearest_points("my-idx", [])


# ---------------------------------------------------------------------------
# scroll (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_success(self, connected_service):
        call_count = 0

        async def search_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "hits": {
                        "hits": [
                            {
                                "_id": "doc-1",
                                "sort": ["doc-1"],
                                "_source": {
                                    "metadata": {"orgId": "org1"},
                                    "page_content": "hello",
                                },
                            }
                        ]
                    }
                }
            return {"hits": {"hits": []}}

        connected_service.client.search = search_side_effect

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 100)

        from app.services.vector_db.models import ScrollResult
        assert isinstance(result, ScrollResult)
        assert len(result.points) == 1
        assert result.points[0].id == "doc-1"
        assert result.points[0].payload["page_content"] == "hello"
        assert result.next_offset is None

    @pytest.mark.asyncio
    async def test_scroll_pagination(self, connected_service):
        call_count = 0

        async def search_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "hits": {
                        "hits": [
                            {"_id": f"doc-{i}", "sort": [f"doc-{i}"], "_source": {"metadata": {}, "page_content": f"p{i}"}}
                            for i in range(3)
                        ]
                    }
                }
            return {"hits": {"hits": []}}

        connected_service.client.search = search_side_effect

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 100)

        from app.services.vector_db.models import ScrollResult
        assert isinstance(result, ScrollResult)
        assert len(result.points) == 3

    @pytest.mark.asyncio
    async def test_scroll_respects_limit(self, connected_service):
        connected_service.client.search = AsyncMock(return_value={
            "hits": {
                "hits": [
                    {"_id": f"doc-{i}", "sort": [f"doc-{i}"], "_source": {"metadata": {}, "page_content": f"p{i}"}}
                    for i in range(10)
                ]
            }
        })

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 5)

        from app.services.vector_db.models import ScrollResult
        assert isinstance(result, ScrollResult)
        assert len(result.points) == 5

    @pytest.mark.asyncio
    async def test_scroll_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.scroll("my-idx", FilterExpression(), 100)


# ---------------------------------------------------------------------------
# overwrite_payload (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestOverwritePayload:
    @pytest.mark.asyncio
    async def test_overwrite_payload_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        await connected_service.overwrite_payload("my-idx", {"status": "active"}, filter_expr)

        connected_service.client.update_by_query.assert_awaited_once()
        call_kwargs = connected_service.client.update_by_query.call_args[1]
        assert call_kwargs["index"] == "my-idx"
        assert "script" in call_kwargs["body"]
        assert "query" in call_kwargs["body"]

    @pytest.mark.asyncio
    async def test_overwrite_payload_multiple_fields(self, connected_service):
        filter_expr = FilterExpression()
        await connected_service.overwrite_payload(
            "my-idx",
            {"status": "active", "version": 2},
            filter_expr,
        )

        call_kwargs = connected_service.client.update_by_query.call_args[1]
        script = call_kwargs["body"]["script"]
        assert script["params"]["p_status"] == "active"
        assert script["params"]["p_version"] == 2

    @pytest.mark.asyncio
    async def test_overwrite_payload_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.overwrite_payload("my-idx", {}, FilterExpression())


# ---------------------------------------------------------------------------
# Hybrid query builder (OpenSearchUtils) — merged from test_opensearch_provider
# ---------------------------------------------------------------------------


class TestOpenSearchHybridQueryBuilder:
    def test_both_legs_when_text_and_dense(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            text_query="hello",
            limit=10,
        )
        body = OpenSearchUtils.build_hybrid_query(req)
        assert "query" in body
        query = body["query"]
        assert "hybrid" in query
        assert len(query["hybrid"]["queries"]) == 2

    def test_dense_only_when_no_text(self):
        req = HybridSearchRequest(dense_query=[0.1, 0.2], limit=5)
        body = OpenSearchUtils.build_hybrid_query(req)
        assert "query" in body
        query = body["query"]
        assert "knn" in str(query)

    def test_filter_inside_knn_clause(self):
        """Filter must be placed INSIDE the knn clause for pre-filtering (critical for recall)."""
        req = HybridSearchRequest(
            dense_query=[0.1],
            text_query="test",
            filter=FilterExpression(
                must=[FieldCondition(key="metadata.orgId", value="org1")]
            ),
            limit=5,
        )
        body = OpenSearchUtils.build_hybrid_query(req)
        query_str = str(body["query"])
        # Filter must NOT be a top-level post_filter
        assert "post_filter" not in str(body)
        # Filter value must appear somewhere (inside knn or bool leg)
        assert "org1" in query_str
        # Verify filter is inside the knn subquery
        hybrid_queries = body["query"]["hybrid"]["queries"]
        knn_queries = [q for q in hybrid_queries if "knn" in q]
        assert len(knn_queries) == 1
        knn_params = knn_queries[0]["knn"]["dense_embedding"]
        assert "filter" in knn_params, (
            "Filter must be embedded inside knn.dense_embedding for pre-filtering"
        )

    def test_match_all_when_no_legs(self):
        req = HybridSearchRequest(limit=5)
        body = OpenSearchUtils.build_hybrid_query(req)
        assert body["query"] == {"match_all": {}}


# ---------------------------------------------------------------------------
# OpenSearch filter expression builder — merged from test_opensearch_provider
# ---------------------------------------------------------------------------


class TestOpenSearchFilterBuilder:
    def test_must_filter(self):
        expr = FilterExpression(must=[FieldCondition(key="metadata.orgId", value="o1")])
        q = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert "bool" in q
        assert "must" in q["bool"]
        assert q["bool"]["must"][0]["term"]["metadata.orgId"] == "o1"

    def test_should_with_terms(self):
        expr = FilterExpression(
            should=[FieldCondition(key="metadata.virtualRecordId", values=["a", "b"])]
        )
        q = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert "should" in q["bool"]

    def test_empty_returns_match_all(self):
        q = OpenSearchUtils.filter_expression_to_bool_query(FilterExpression())
        assert q == {"match_all": {}}


# ===========================================================================
# Phase 3 regression: OpenSearch correctness + auth seam
# ===========================================================================

from unittest.mock import AsyncMock, MagicMock, patch


def _make_os_service():
    """Create an OpenSearchService with a mocked async client."""
    from app.services.vector_db.opensearch.opensearch import OpenSearchService
    from app.services.vector_db.models import FilterExpression, VectorDBCapabilities

    svc = OpenSearchService.__new__(OpenSearchService)
    svc.config_service = MagicMock()
    svc._cfg = None
    svc._client_loop = None
    client = MagicMock()
    # Make transport.perform_request awaitable for pipeline tests
    client.transport = MagicMock()
    client.transport.perform_request = AsyncMock()
    client.indices = MagicMock()
    client.indices.exists = AsyncMock(return_value=False)
    client.indices.create = AsyncMock()
    client.search = AsyncMock(return_value={"hits": {"hits": []}})
    client.delete_by_query = AsyncMock()
    client.update_by_query = AsyncMock()
    svc.client = client
    return svc


class TestOpenSearchPipelineGating:
    @pytest.mark.asyncio
    async def test_single_leg_query_omits_search_pipeline(self):
        """Dense-only request must not pass search_pipeline kwarg to client.search."""
        from app.services.vector_db.models import HybridSearchRequest, FilterExpression
        svc = _make_os_service()

        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            text_query=None,  # single-leg (dense only)
            filter=FilterExpression(),
            limit=5,
        )
        await svc.query_nearest_points("records", [req])

        call_kwargs = svc.client.search.call_args.kwargs
        assert "params" not in call_kwargs or "search_pipeline" not in call_kwargs.get("params", {}), (
            "Single-leg query must NOT use search_pipeline"
        )

    @pytest.mark.asyncio
    async def test_hybrid_query_uses_search_pipeline(self):
        """Dense + text_query request must pass search_pipeline to client.search."""
        from app.services.vector_db.models import HybridSearchRequest, FilterExpression
        svc = _make_os_service()

        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            text_query="machine learning",  # hybrid
            filter=FilterExpression(),
            limit=5,
        )
        await svc.query_nearest_points("records", [req])

        call_kwargs = svc.client.search.call_args.kwargs
        params = call_kwargs.get("params", {})
        assert "search_pipeline" in params, "Hybrid query must use search_pipeline"


class TestOpenSearchAuthSeam:
    def test_build_client_basic_auth_honors_ssl_flags(self):
        """_build_client with basic auth + SSL must produce correct client kwargs."""
        from app.services.vector_db.opensearch.opensearch import OpenSearchService
        from app.services.vector_db.opensearch.config import OpenSearchConfig

        cfg = OpenSearchConfig(
            host="myhost", port=9200,
            username="admin", password="secret",
            use_ssl=True, verify_certs=True, ssl_show_warn=False,
            timeout=60, auth_type="basic"
        )
        with patch(
            "app.services.vector_db.opensearch.opensearch.AsyncOpenSearch"
        ) as mock_client_cls:
            mock_client_cls.return_value = MagicMock()
            OpenSearchService._build_client(cfg)

        call_kw = mock_client_cls.call_args.kwargs
        assert call_kw.get("use_ssl") is True
        assert call_kw.get("verify_certs") is True
        assert call_kw.get("http_auth") == ("admin", "secret")

    def test_unknown_auth_type_raises(self):
        """auth_type='aws_iam' must raise ValueError naming the unsupported type."""
        from app.services.vector_db.opensearch.opensearch import OpenSearchService
        from app.services.vector_db.opensearch.config import OpenSearchConfig

        cfg = OpenSearchConfig(auth_type="aws_iam")
        with pytest.raises(ValueError, match="aws_iam"):
            OpenSearchService._build_client(cfg)


class TestOverwritePayloadPainlessScript:
    @pytest.mark.asyncio
    async def test_overwrite_payload_painless_nested(self):
        """Dotted key 'metadata.status' must generate ctx._source.metadata.status."""
        svc = _make_os_service()
        from app.services.vector_db.models import FilterExpression, FieldCondition

        filter_expr = FilterExpression(
            must=[FieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        await svc.overwrite_payload("records", {"metadata.status": "archived"}, filter_expr)

        body = svc.client.update_by_query.call_args.kwargs.get("body") or \
               svc.client.update_by_query.call_args.args[0] if svc.client.update_by_query.call_args.args else \
               svc.client.update_by_query.call_args.kwargs.get("body", {})
        script_source = body.get("script", {}).get("source", "")
        assert "ctx._source.metadata.status" in script_source, (
            f"Expected nested Painless path 'ctx._source.metadata.status' in: {script_source!r}"
        )
        assert "ctx._source['metadata.status']" not in script_source, (
            "Must not use literal top-level key with dotted name"
        )

    @pytest.mark.asyncio
    async def test_overwrite_payload_top_level_key(self):
        """A plain (non-dotted) key uses ctx._source['key'] syntax."""
        svc = _make_os_service()
        from app.services.vector_db.models import FilterExpression, FieldCondition

        filter_expr = FilterExpression(
            must=[FieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        await svc.overwrite_payload("records", {"page_content": "new"}, filter_expr)

        body = svc.client.update_by_query.call_args.kwargs.get("body", {})
        script_source = body.get("script", {}).get("source", "")
        assert "ctx._source['page_content']" in script_source


class TestOpenSearchDeletePoints:
    @pytest.mark.asyncio
    async def test_empty_filter_raises(self):
        """delete_points with empty FilterExpression must raise ValueError."""
        from app.services.vector_db.models import FilterExpression
        svc = _make_os_service()
        with pytest.raises(ValueError, match="empty filter"):
            await svc.delete_points("records", FilterExpression())

    @pytest.mark.asyncio
    async def test_delete_by_query_params(self):
        """delete_by_query must be called with conflicts/slices/wait_for_completion."""
        from app.services.vector_db.models import FilterExpression, FieldCondition
        svc = _make_os_service()

        filt = FilterExpression(
            must=[FieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        await svc.delete_points("records", filt)

        call_kwargs = svc.client.delete_by_query.call_args.kwargs
        assert call_kwargs.get("conflicts") == "proceed"
        assert call_kwargs.get("slices") == "auto"
        assert call_kwargs.get("wait_for_completion") is True


class TestEnsureRrfPipelineIdempotent:
    @pytest.mark.asyncio
    async def test_create_collection_skips_existing_pipeline(self):
        """_ensure_rrf_pipeline must skip PUT when pipeline GET returns success."""
        svc = _make_os_service()
        svc.client.indices.exists = AsyncMock(return_value=False)
        svc.client.indices.create = AsyncMock()
        # GET pipeline returns 200 (pipeline already exists)
        svc.client.transport.perform_request = AsyncMock(return_value={})

        from app.services.vector_db.models import CollectionConfig
        await svc.create_collection("records", CollectionConfig(embedding_size=1024))

        # transport.perform_request called once (GET) — no PUT because pipeline exists
        calls = svc.client.transport.perform_request.call_args_list
        methods = [c.args[0] if c.args else c.kwargs.get("method") for c in calls]
        assert "PUT" not in methods, (
            "Pipeline PUT must be skipped when GET returns 200 (pipeline already exists)"
        )


# ===========================================================================
# New tests for performance optimisation changes
# ===========================================================================


class TestOpenSearchConfigTuningParams:
    """Verify OpenSearchConfig new tuning fields default and parse correctly."""

    def test_defaults(self):
        cfg = OpenSearchConfig()
        assert cfg.m == 16
        assert cfg.ef_construction == 128
        assert cfg.ef_search == 100
        assert cfg.quantization_bits == 7
        assert cfg.confidence_interval == 0.99
        assert cfg.rrf_rank_constant == 60

    def test_from_dict_snake_case(self):
        cfg = OpenSearchConfig.from_dict({
            "host": "myhost",
            "port": 9201,
            "m": 32,
            "ef_search": 200,
            "quantization_bits": 0,
        })
        assert cfg.m == 32
        assert cfg.ef_search == 200
        assert cfg.quantization_bits == 0

    def test_from_dict_camel_case(self):
        cfg = OpenSearchConfig.from_dict({
            "quantizationBits": 7,
            "confidenceInterval": 0.95,
            "efSearch": 128,
            "rrfRankConstant": 30,
        })
        assert cfg.quantization_bits == 7
        assert cfg.confidence_interval == 0.95
        assert cfg.ef_search == 128
        assert cfg.rrf_rank_constant == 30

    def test_quantization_disabled_when_bits_zero(self):
        """bits=0 must signal: omit encoder block from index mapping."""
        cfg = OpenSearchConfig.from_dict({"quantizationBits": 0})
        assert cfg.quantization_bits == 0

    def test_opensearch_config_property_round_trips(self):
        """opensearch_config dict must include all new fields for etcd serialisation."""
        cfg = OpenSearchConfig(m=24, ef_search=150, quantization_bits=0, rrf_rank_constant=45)
        d = cfg.opensearch_config
        assert d["m"] == 24
        assert d["efSearch"] == 150
        assert d["quantizationBits"] == 0
        assert d["rrfRankConstant"] == 45


class TestCreateCollectionOptimisations:
    """Extend TestCreateCollection with checks for the performance optimisation changes."""

    @pytest.mark.asyncio
    async def test_create_collection_uses_m16_by_default(self, connected_service):
        """m must default to 16, not 48."""
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        hnsw_params = body["mappings"]["properties"]["dense_embedding"]["method"]["parameters"]
        assert hnsw_params["m"] == 16

    @pytest.mark.asyncio
    async def test_create_collection_ef_search_in_settings(self, connected_service):
        """ef_search must appear in index settings."""
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        assert body["settings"].get("index.knn.algo_param.ef_search") == 100

    @pytest.mark.asyncio
    async def test_create_collection_has_sq_encoder(self, connected_service):
        """7-bit scalar quantization encoder must be present by default."""
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        params = body["mappings"]["properties"]["dense_embedding"]["method"]["parameters"]
        assert "encoder" in params
        assert params["encoder"]["name"] == "sq"
        assert params["encoder"]["parameters"]["bits"] == 7

    @pytest.mark.asyncio
    async def test_create_collection_quantization_disabled(self, connected_service):
        """When _cfg.quantization_bits == 0, encoder block must be omitted."""
        connected_service._cfg = OpenSearchConfig(quantization_bits=0)
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        params = body["mappings"]["properties"]["dense_embedding"]["method"]["parameters"]
        assert "encoder" not in params

    @pytest.mark.asyncio
    async def test_create_collection_custom_m_from_config(self, connected_service):
        """m from _cfg must be forwarded into the index body."""
        connected_service._cfg = OpenSearchConfig(m=32)
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        hnsw_params = body["mappings"]["properties"]["dense_embedding"]["method"]["parameters"]
        assert hnsw_params["m"] == 32

    @pytest.mark.asyncio
    async def test_create_collection_custom_ef_search_from_config(self, connected_service):
        """ef_search from _cfg must appear in index settings."""
        connected_service._cfg = OpenSearchConfig(ef_search=200)
        await connected_service.create_collection()
        body = connected_service.client.indices.create.call_args[1]["body"]
        assert body["settings"].get("index.knn.algo_param.ef_search") == 200


class TestUpsertPointsRefreshBehavior:
    """Verify upsert_points is lean: no per-call refresh_interval toggling and
    no forced refresh, since the pipeline calls it in many small concurrent
    batches where index-wide refresh management is racy and segment-proliferating.
    """

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_no_refresh_interval_toggling(self, mock_helpers, connected_service):
        """upsert_points must NOT toggle index.refresh_interval (racy under concurrency)."""
        mock_helpers.async_bulk = AsyncMock(return_value=(2, []))
        connected_service.client.indices.put_settings = AsyncMock()
        connected_service.client.indices.refresh = AsyncMock()

        points = [VectorPoint(id="p1", dense_vector=[0.1], payload={})]
        await connected_service.upsert_points("my-idx", points)

        connected_service.client.indices.put_settings.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_no_forced_refresh_by_default(self, mock_helpers, connected_service):
        """Default upsert must not force an indices.refresh (avoids per-batch segments)."""
        mock_helpers.async_bulk = AsyncMock(return_value=(2, []))
        connected_service.client.indices.refresh = AsyncMock()

        points = [VectorPoint(id="p1", dense_vector=[0.1], payload={})]
        await connected_service.upsert_points("my-idx", points)

        connected_service.client.indices.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_async_bulk_refresh_false_by_default(self, mock_helpers, connected_service):
        """async_bulk must be called with refresh=False by default."""
        mock_helpers.async_bulk = AsyncMock(return_value=(1, []))

        points = [VectorPoint(id="p1", dense_vector=[0.1], payload={})]
        await connected_service.upsert_points("my-idx", points)

        call_kwargs = mock_helpers.async_bulk.call_args[1]
        assert call_kwargs.get("refresh") is False

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.os_helpers")
    async def test_async_bulk_refresh_true_when_requested(self, mock_helpers, connected_service):
        """refresh=True must be forwarded to async_bulk for read-after-write callers."""
        mock_helpers.async_bulk = AsyncMock(return_value=(1, []))

        points = [VectorPoint(id="p1", dense_vector=[0.1], payload={})]
        await connected_service.upsert_points("my-idx", points, refresh=True)

        call_kwargs = mock_helpers.async_bulk.call_args[1]
        assert call_kwargs.get("refresh") is True


class TestForceMerge:
    """Verify force_merge delegates correctly to the forcemerge API."""

    @pytest.mark.asyncio
    async def test_force_merge_calls_forcemerge_api(self, connected_service):
        connected_service.client.indices.forcemerge = AsyncMock(return_value={})
        await connected_service.force_merge("my-idx")
        connected_service.client.indices.forcemerge.assert_awaited_once_with(
            index="my-idx",
            max_num_segments=1,
            request_timeout=600,
        )

    @pytest.mark.asyncio
    async def test_force_merge_custom_segments(self, connected_service):
        connected_service.client.indices.forcemerge = AsyncMock(return_value={})
        await connected_service.force_merge("my-idx", max_segments=5)
        call_kwargs = connected_service.client.indices.forcemerge.call_args[1]
        assert call_kwargs["max_num_segments"] == 5

    @pytest.mark.asyncio
    async def test_force_merge_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.force_merge("my-idx")


class TestWarmup:
    """Verify warmup delegates correctly to the k-NN warmup API."""

    @pytest.mark.asyncio
    async def test_warmup_calls_knn_warmup_api(self, connected_service):
        connected_service.client.transport.perform_request = AsyncMock(return_value={})
        await connected_service.warmup("my-idx")
        connected_service.client.transport.perform_request.assert_awaited_once_with(
            "GET", "/_plugins/_knn/warmup/my-idx"
        )

    @pytest.mark.asyncio
    async def test_warmup_not_connected(self, service):
        with pytest.raises(RuntimeError, match="config not loaded"):
            await service.warmup("my-idx")


class TestRrfPipelineConfigurable:
    """Verify the RRF pipeline body uses rank_constant from OpenSearchConfig."""

    @pytest.mark.asyncio
    async def test_rrf_pipeline_default_rank_constant(self, connected_service):
        """_ensure_rrf_pipeline must use rank_constant=60 by default."""
        connected_service.client.transport.perform_request = AsyncMock(
            side_effect=[Exception("not found"), {"acknowledged": True}]
        )
        await connected_service._ensure_rrf_pipeline("my-idx")

        put_call = connected_service.client.transport.perform_request.call_args_list[1]
        body = put_call[1]["body"]
        rrf_cfg = body["phase_results_processors"][0]["score-ranker-processor"]["combination"]
        assert rrf_cfg["technique"] == "rrf"
        assert rrf_cfg["rank_constant"] == 60

    @pytest.mark.asyncio
    async def test_rrf_pipeline_custom_rank_constant(self, connected_service):
        """When _cfg sets rrf_rank_constant, the pipeline body must reflect it."""
        connected_service._cfg = OpenSearchConfig(rrf_rank_constant=30)
        connected_service.client.transport.perform_request = AsyncMock(
            side_effect=[Exception("not found"), {"acknowledged": True}]
        )
        await connected_service._ensure_rrf_pipeline("my-idx")

        put_call = connected_service.client.transport.perform_request.call_args_list[1]
        body = put_call[1]["body"]
        rrf_cfg = body["phase_results_processors"][0]["score-ranker-processor"]["combination"]
        assert rrf_cfg["rank_constant"] == 30

