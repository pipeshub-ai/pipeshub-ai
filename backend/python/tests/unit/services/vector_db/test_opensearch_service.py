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

from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
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
    return OpenSearchService(os_config, is_async=False)


@pytest.fixture
def async_service(os_config):
    return OpenSearchService(os_config, is_async=True)


@pytest.fixture
def connected_service(service):
    service.client = MagicMock()
    return service


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
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
        svc = OpenSearchService(os_config, is_async=False)
        assert svc.config_service is os_config
        assert svc.client is None
        assert svc.is_async is False

    def test_async_flag(self, os_config):
        svc = OpenSearchService(os_config, is_async=True)
        assert svc.is_async is True


# ---------------------------------------------------------------------------
# Factory method
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.OpenSearch")
    async def test_create_sync(self, mock_client_cls, os_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await OpenSearchService.create(os_config, is_async=False)
        assert svc.client is mock_client
        assert svc.is_async is False

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_create_async(self, mock_client_cls, os_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await OpenSearchService.create(os_config, is_async=True)
        assert svc.client is mock_client
        assert svc.is_async is True

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.OpenSearch")
    async def test_create_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = await OpenSearchService.create(mock_config_service, is_async=False)
        assert svc.client is mock_client


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.OpenSearch")
    async def test_connect_sync_with_opensearch_config(self, mock_client_cls, os_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = OpenSearchService(os_config, is_async=False)
        await svc.connect()
        assert svc.client is mock_client
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["hosts"] == [{"host": "localhost", "port": 9200}]
        assert call_kwargs["http_auth"] == ("admin", "admin")

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.AsyncOpenSearch")
    async def test_connect_async_with_opensearch_config(self, mock_client_cls, os_config):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = OpenSearchService(os_config, is_async=True)
        await svc.connect()
        assert svc.client is mock_client
        mock_client_cls.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.OpenSearch")
    async def test_connect_with_config_service(self, mock_client_cls, mock_config_service):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        svc = OpenSearchService(mock_config_service, is_async=False)
        await svc.connect()
        assert svc.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_no_config(self):
        from app.config.configuration_service import ConfigurationService
        mock_cs = MagicMock(spec=ConfigurationService)
        mock_cs.get_config = AsyncMock(return_value=None)
        svc = OpenSearchService(mock_cs, is_async=False)
        with pytest.raises(ValueError, match="OpenSearch configuration not found"):
            await svc.connect()

    @pytest.mark.asyncio
    @patch("app.services.vector_db.opensearch.opensearch.OpenSearch")
    async def test_connect_exception(self, mock_client_cls, os_config):
        mock_client_cls.side_effect = Exception("connection refused")
        svc = OpenSearchService(os_config, is_async=False)
        with pytest.raises(Exception, match="connection refused"):
            await svc.connect()


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
    async def test_disconnect_async_client(self, async_service):
        from opensearchpy import AsyncOpenSearch
        mock_client = AsyncMock(spec=AsyncOpenSearch)
        async_service.client = mock_client
        await async_service.disconnect()
        mock_client.close.assert_awaited_once()
        assert async_service.client is None


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
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        await connected_service.create_collection()

        connected_service.client.indices.create.assert_called_once()
        call_kwargs = connected_service.client.indices.create.call_args[1]
        assert call_kwargs["index"] == "records"
        body = call_kwargs["body"]
        assert body["settings"]["index.knn"] is True
        assert body["mappings"]["properties"]["dense_embedding"]["dimension"] == 1024
        assert body["mappings"]["properties"]["dense_embedding"]["method"]["space_type"] == "cosinesimil"

    @pytest.mark.asyncio
    async def test_create_collection_custom_name(self, connected_service):
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        config = CollectionConfig(embedding_size=768)
        await connected_service.create_collection(
            collection_name="custom_col", config=config
        )
        call_kwargs = connected_service.client.indices.create.call_args[1]
        assert call_kwargs["index"] == "custom_col"

    @pytest.mark.asyncio
    async def test_create_collection_with_config(self, connected_service):
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

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
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        config = CollectionConfig(distance_metric=DistanceMetric.DOT_PRODUCT)
        await connected_service.create_collection(config=config)

        body = connected_service.client.indices.create.call_args[1]["body"]
        assert body["mappings"]["properties"]["dense_embedding"]["method"]["space_type"] == "innerproduct"

    @pytest.mark.asyncio
    async def test_create_collection_creates_pipeline(self, connected_service):
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        await connected_service.create_collection(collection_name="my-idx")

        connected_service.client.transport.perform_request.assert_called_once()
        call_args = connected_service.client.transport.perform_request.call_args[0]
        assert call_args[0] == "PUT"
        assert "my-idx-search-pipeline" in call_args[1]

    @pytest.mark.asyncio
    async def test_create_collection_pipeline_failure_does_not_raise(self, connected_service):
        connected_service.client.indices.create = MagicMock()
        connected_service.client.transport.perform_request = MagicMock(
            side_effect=Exception("pipeline creation failed")
        )

        # Should not raise even when pipeline creation fails
        await connected_service.create_collection()
        connected_service.client.indices.create.assert_called_once()

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
        connected_service.client.indices.get_alias.return_value = {"idx1": {}, "idx2": {}}
        result = await connected_service.get_collections()
        assert result == {"idx1": {}, "idx2": {}}

    @pytest.mark.asyncio
    async def test_get_collections_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.get_collections()

    @pytest.mark.asyncio
    async def test_get_collection(self, connected_service):
        connected_service.client.indices.get.return_value = {"my-idx": {"mappings": {}}}
        result = await connected_service.get_collection("my-idx")
        assert result == {"my-idx": {"mappings": {}}}
        connected_service.client.indices.get.assert_called_once_with(index="my-idx")

    @pytest.mark.asyncio
    async def test_get_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.get_collection("my-idx")

    @pytest.mark.asyncio
    async def test_delete_collection(self, connected_service):
        connected_service.client.indices.exists.return_value = True
        connected_service.client.indices.delete = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        await connected_service.delete_collection("my-idx")

        connected_service.client.indices.delete.assert_called_once_with(index="my-idx")
        # Should also attempt to delete the search pipeline
        connected_service.client.transport.perform_request.assert_called_once_with(
            "DELETE", "/_search/pipeline/my-idx-search-pipeline"
        )

    @pytest.mark.asyncio
    async def test_delete_collection_index_does_not_exist(self, connected_service):
        connected_service.client.indices.exists.return_value = False
        connected_service.client.indices.delete = MagicMock()
        connected_service.client.transport.perform_request = MagicMock()

        await connected_service.delete_collection("nonexistent")

        connected_service.client.indices.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_collection_pipeline_cleanup_error_ignored(self, connected_service):
        connected_service.client.indices.exists.return_value = True
        connected_service.client.indices.delete = MagicMock()
        connected_service.client.transport.perform_request = MagicMock(
            side_effect=Exception("pipeline not found")
        )

        # Should not raise even if pipeline deletion fails
        await connected_service.delete_collection("my-idx")
        connected_service.client.indices.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_collection_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.delete_collection("my-idx")


# ---------------------------------------------------------------------------
# create_index
# ---------------------------------------------------------------------------


class TestCreateIndex:
    @pytest.mark.asyncio
    async def test_create_keyword_index(self, connected_service):
        connected_service.client.indices.put_mapping = MagicMock()
        await connected_service.create_index("my-idx", "field1", {"type": "keyword"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["index"] == "my-idx"
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "keyword"

    @pytest.mark.asyncio
    async def test_create_text_index(self, connected_service):
        connected_service.client.indices.put_mapping = MagicMock()
        await connected_service.create_index("my-idx", "field1", {"type": "text"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_create_non_keyword_index_defaults_to_text(self, connected_service):
        connected_service.client.indices.put_mapping = MagicMock()
        await connected_service.create_index("my-idx", "field1", {"type": "integer"})
        call_kwargs = connected_service.client.indices.put_mapping.call_args[1]
        assert call_kwargs["body"]["properties"]["field1"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_create_index_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
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
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.filter_collection(orgId="123")


# ---------------------------------------------------------------------------
# upsert_points
# ---------------------------------------------------------------------------


class TestUpsertPoints:
    @patch("app.services.vector_db.opensearch.opensearch.helpers")
    def test_upsert_points(self, mock_helpers, connected_service):
        mock_helpers.bulk.return_value = (2, [])
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
        connected_service.upsert_points("my-idx", points)

        mock_helpers.bulk.assert_called_once()
        call_args = mock_helpers.bulk.call_args
        actions = call_args[0][1]
        assert len(actions) == 2
        assert actions[0]["_id"] == "p1"
        assert actions[0]["_index"] == "my-idx"
        assert actions[0]["_source"]["dense_embedding"] == [0.1, 0.2]
        assert actions[0]["_source"]["metadata"] == {"orgId": "org1"}
        assert actions[1]["_id"] == "p2"

    @patch("app.services.vector_db.opensearch.opensearch.helpers")
    def test_upsert_points_batch_size(self, mock_helpers, connected_service):
        mock_helpers.bulk.return_value = (25, [])
        points = [VectorPoint(id=str(i), dense_vector=[0.1] * 384, payload={}) for i in range(25)]
        connected_service.upsert_points("my-idx", points, batch_size=10)
        call_kwargs = mock_helpers.bulk.call_args[1]
        assert call_kwargs["chunk_size"] == 10

    @patch("app.services.vector_db.opensearch.opensearch.helpers")
    def test_upsert_points_with_errors(self, mock_helpers, connected_service):
        mock_helpers.bulk.return_value = (1, [{"index": {"_id": "p2", "error": "mapping err"}}])
        points = [VectorPoint(id="p1", dense_vector=[0.1], payload={})]
        with pytest.raises(RuntimeError, match="Bulk upsert failed"):
            connected_service.upsert_points("my-idx", points)

    @patch("app.services.vector_db.opensearch.opensearch.helpers")
    def test_upsert_empty_list(self, mock_helpers, connected_service):
        connected_service.upsert_points("my-idx", [])
        mock_helpers.bulk.assert_not_called()

    def test_upsert_not_connected(self, service):
        points = [VectorPoint(id="1", dense_vector=[0.1], payload={})]
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.upsert_points("my-idx", points)


# ---------------------------------------------------------------------------
# delete_points (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestDeletePoints:
    def test_delete_points_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        connected_service.delete_points("my-idx", filter_expr)

        connected_service.client.delete_by_query.assert_called_once()
        call_kwargs = connected_service.client.delete_by_query.call_args[1]
        assert call_kwargs["index"] == "my-idx"
        assert "bool" in call_kwargs["body"]["query"]

    def test_delete_points_empty_filter(self, connected_service):
        filter_expr = FilterExpression()
        connected_service.delete_points("my-idx", filter_expr)

        call_kwargs = connected_service.client.delete_by_query.call_args[1]
        assert call_kwargs["body"]["query"] == {"match_all": {}}

    def test_delete_points_not_connected(self, service):
        filter_expr = FilterExpression()
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.delete_points("my-idx", filter_expr)


# ---------------------------------------------------------------------------
# query_nearest_points (accepts HybridSearchRequest)
# ---------------------------------------------------------------------------


class TestQueryNearestPoints:
    def test_hybrid_query(self, connected_service):
        connected_service.client.search.return_value = {
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
        }

        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            text_query="hello",
            limit=10,
            fusion_method=FusionMethod.RRF,
        )
        results = connected_service.query_nearest_points("my-idx", [req])

        assert len(results) == 1
        assert len(results[0]) == 1
        assert results[0][0].id == "doc-1"
        assert results[0][0].score == 0.95
        assert results[0][0].payload["page_content"] == "hello"

    def test_query_multiple_requests(self, connected_service):
        connected_service.client.search.return_value = {
            "hits": {"hits": [
                {"_id": "d1", "_score": 0.9, "_source": {"metadata": {}, "page_content": "a"}},
            ]}
        }

        req1 = HybridSearchRequest(dense_query=[0.1], limit=5)
        req2 = HybridSearchRequest(text_query="test", limit=5)
        results = connected_service.query_nearest_points("my-idx", [req1, req2])

        assert len(results) == 2
        assert connected_service.client.search.call_count == 2

    def test_query_with_filter(self, connected_service):
        connected_service.client.search.return_value = {
            "hits": {"hits": []}
        }

        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            filter=filter_expr,
            limit=10,
        )
        results = connected_service.query_nearest_points("my-idx", [req])
        assert len(results) == 1
        assert results[0] == []

    def test_query_fallback_without_pipeline(self, connected_service):
        call_count = 0

        def search_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("pipeline not found")
            return {"hits": {"hits": [
                {"_id": "d1", "_score": 0.8, "_source": {"metadata": {}, "page_content": "fallback"}},
            ]}}

        connected_service.client.search.side_effect = search_side_effect

        req = HybridSearchRequest(dense_query=[0.1], limit=5)
        results = connected_service.query_nearest_points("my-idx", [req])

        assert connected_service.client.search.call_count == 2
        assert results[0][0].id == "d1"

    def test_query_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.query_nearest_points("my-idx", [])


# ---------------------------------------------------------------------------
# scroll (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestScroll:
    @pytest.mark.asyncio
    async def test_scroll_success(self, connected_service):
        call_count = 0

        def search_side_effect(**kwargs):
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

        connected_service.client.search.side_effect = search_side_effect

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 100)

        assert isinstance(result, tuple)
        points, next_offset = result
        assert len(points) == 1
        assert points[0].id == "doc-1"
        assert points[0].payload["page_content"] == "hello"
        assert next_offset is None

    @pytest.mark.asyncio
    async def test_scroll_pagination(self, connected_service):
        call_count = 0

        def search_side_effect(**kwargs):
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

        connected_service.client.search.side_effect = search_side_effect

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 100)

        points, _ = result
        assert len(points) == 3

    @pytest.mark.asyncio
    async def test_scroll_respects_limit(self, connected_service):
        connected_service.client.search.return_value = {
            "hits": {
                "hits": [
                    {"_id": f"doc-{i}", "sort": [f"doc-{i}"], "_source": {"metadata": {}, "page_content": f"p{i}"}}
                    for i in range(10)
                ]
            }
        }

        filter_expr = FilterExpression()
        result = await connected_service.scroll("my-idx", filter_expr, 5)

        points, _ = result
        assert len(points) == 5

    @pytest.mark.asyncio
    async def test_scroll_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.scroll("my-idx", FilterExpression(), 100)


# ---------------------------------------------------------------------------
# overwrite_payload (accepts FilterExpression)
# ---------------------------------------------------------------------------


class TestOverwritePayload:
    def test_overwrite_payload_success(self, connected_service):
        filter_expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org1")]
        )
        connected_service.overwrite_payload("my-idx", {"status": "active"}, filter_expr)

        connected_service.client.update_by_query.assert_called_once()
        call_kwargs = connected_service.client.update_by_query.call_args[1]
        assert call_kwargs["index"] == "my-idx"
        assert "script" in call_kwargs["body"]
        assert "query" in call_kwargs["body"]

    def test_overwrite_payload_multiple_fields(self, connected_service):
        filter_expr = FilterExpression()
        connected_service.overwrite_payload(
            "my-idx",
            {"status": "active", "version": 2},
            filter_expr,
        )

        call_kwargs = connected_service.client.update_by_query.call_args[1]
        script = call_kwargs["body"]["script"]
        assert "p_status" in script["params"]
        assert "p_version" in script["params"]
        assert script["params"]["p_status"] == "active"
        assert script["params"]["p_version"] == 2

    def test_overwrite_payload_not_connected(self, service):
        with pytest.raises(RuntimeError, match="Client not connected"):
            service.overwrite_payload("my-idx", {}, FilterExpression())
