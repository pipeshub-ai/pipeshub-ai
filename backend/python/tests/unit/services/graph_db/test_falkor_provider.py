from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock
from falkordb.node import Node

import pytest

from app.services.graph_db.falkor.falkor_provider import FalkorProvider

@pytest.fixture
def falkor_provider() -> FalkorProvider:
    provider = FalkorProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider

class TestEdgeOperations:
    @pytest.mark.asyncio
    async def test_get_edge(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[[OrderedDict({'role': 'READER', 'type': 'USER'})]])
        
        result = await falkor_provider.get_edge(
            "u1", "users", "a1", "agentInstances", "permission", transaction="txn-ge"
        )
        

        assert result == {
            "role": "READER",
            "type": "USER",
            "from_id": "u1",
            "from_collection": "users",
            "to_id": "a1",
            "to_collection": "agentInstances",
        }

    @pytest.mark.asyncio
    async def test_get_edge_returns_none_when_missing(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[])

        result = await falkor_provider.get_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_edge_returns_none_on_exception(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await falkor_provider.get_edge("u1", "users", "a1", "agentInstances", "permission")

        assert result is None

    @pytest.mark.asyncio
    async def test_batch_create_edges_returns_true_for_empty_input(self, falkor_provider: FalkorProvider):
        result = await falkor_provider.batch_create_edges([], "permission")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_arango_format_success(self, falkor_provider: FalkorProvider):
        falkor_provider._parse_arango_id = MagicMock(  # type: ignore[method-assign]
            side_effect=[("users", "u1"), ("agentInstances", "a1")]
        )
        falkor_provider.client.execute_query = AsyncMock(return_value=[{"created": 1}])

        result = await falkor_provider.batch_create_edges(
            [{"_from": "users/u1", "_to": "agentInstances/a1", "role": "READER"}],
            "permission",
            transaction="txn-e1",
        )

        assert result is True
        kwargs = falkor_provider.client.execute_query.await_args.kwargs
        assert kwargs["txn_id"] == "txn-e1"
        assert kwargs["parameters"]["edges"][0]["from_key"] == "u1"
        assert kwargs["parameters"]["edges"][0]["to_key"] == "a1"
        assert kwargs["parameters"]["edges"][0]["props"]["role"] == "READER"

    @pytest.mark.asyncio
    async def test_batch_create_edges_generic_format_success(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[{"created": 1}])

        result = await falkor_provider.batch_create_edges(
            [
                {
                    "from_id": "u1",
                    "to_id": "a1",
                    "from_collection": "users",
                    "to_collection": "agentInstances",
                    "type": "USER",
                }
            ],
            "permission",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_batch_create_edges_skips_invalid_entries_and_returns_true(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock()

        result = await falkor_provider.batch_create_edges(
            [
                {"foo": "bar"},  # invalid
                {"from_id": "u1", "to_id": "a1", "from_collection": "", "to_collection": "agentInstances"},  # missing fields
            ],
            "permission",
        )

        assert result is True
        falkor_provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_create_edges_raises_on_exception(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("edge fail"))

        with pytest.raises(RuntimeError, match="edge fail"):
            await falkor_provider.batch_create_edges(
                [{"from_id": "u1", "to_id": "a1", "from_collection": "users", "to_collection": "agentInstances"}],
                "permission",
            )


class TestNodeOperations:
    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_returns_true_for_empty_input(self, falkor_provider: FalkorProvider):
        result = await falkor_provider.batch_upsert_nodes([], "apps")
        assert result is True

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_converts_and_validates_and_executes(
        self, falkor_provider: FalkorProvider
    ):
        falkor_provider._to_native_node = MagicMock(  # type: ignore[method-assign]
            side_effect=[{"_key": "n1", "name": "A"}, {"id": "n2", "name": "B"}]
        )
        falkor_provider.validator.validate_node_update = MagicMock()
        falkor_provider.client.execute_query = AsyncMock(return_value=[[2]])

        result = await falkor_provider.batch_upsert_nodes(
            [{"_key": "n1", "name": "A"}, {"id": "n2", "name": "B"}],
            "apps",
            transaction="txn-bu",
        )

        assert result is True
        falkor_provider.client.execute_query.assert_awaited_once()
        params = falkor_provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["nodes"][0]["id"] == "n1"  # derived from _key
        assert params["nodes"][1]["id"] == "n2"

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_raises_on_exception(self, falkor_provider: FalkorProvider):
        falkor_provider._to_native_node = MagicMock(return_value={"id": "n1"})  # type: ignore[method-assign]
        falkor_provider.validator.validate_node_update = MagicMock(side_effect=RuntimeError("invalid"))

        with pytest.raises(RuntimeError, match="invalid"):
            await falkor_provider.batch_upsert_nodes([{"id": "n1"}], "apps")

class TestDocumentOperations:
    @pytest.mark.asyncio
    async def test_get_document_returns_transformed_doc(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[[Node(node_id="doc1", labels=["apps"], properties={"id": "doc1", "name": "Doc"})]])
        falkor_provider._to_arango_node = MagicMock(return_value={"_key": "doc1", "name": "Doc"})  # type: ignore[method-assign]

        result = await falkor_provider.get_document("doc1", "apps", transaction="txn-doc")

        assert result == {"_key": "doc1", "name": "Doc"}

    

        falkor_provider.client.execute_query.assert_awaited_once()
        kwargs = falkor_provider.client.execute_query.await_args.kwargs
        assert kwargs["parameters"] == {"key": "doc1"}
        assert kwargs["txn_id"] == "txn-doc"

    @pytest.mark.asyncio
    async def test_get_document_returns_none_when_missing(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[])

        result = await falkor_provider.get_document("missing", "apps")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_document_returns_none_on_exception(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await falkor_provider.get_document("doc1", "apps")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_transformed_list(self, falkor_provider: FalkorProvider):
        # falkor_provider.client.execute_query = AsyncMock(
        #     return_value=[[{"id": "d1", "name": "A"}], [{"id": "d2", "name": "B"}]]
        # )

        falkor_provider.client.execute_query = AsyncMock(
            return_value=[
                [Node(node_id="d1", labels=["apps"], properties={"id": "d1", "name": "A"})],
                [Node(node_id="d2", labels=["apps"], properties={"id": "d2", "name": "B"})]
            ]
        )

        falkor_provider._to_arango_node = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda node, _collection: {"_key": node["id"], "name": node["name"]}
        )

        result = await falkor_provider.get_all_documents("apps", transaction="txn-all-docs")

        assert result == [{"_key": "d1", "name": "A"}, {"_key": "d2", "name": "B"}]

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_empty_on_no_rows(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(return_value=[])

        result = await falkor_provider.get_all_documents("apps")

        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_documents_returns_empty_on_exception(self, falkor_provider: FalkorProvider):
        falkor_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db fail"))

        result = await falkor_provider.get_all_documents("apps")

        assert result == []
