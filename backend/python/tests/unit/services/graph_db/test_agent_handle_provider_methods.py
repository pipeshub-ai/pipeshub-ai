"""Agent-handle contract of both providers against mocked clients: the schema
objects they create, the queries they send and how unique violations surface.
The same behaviour on real databases is in test_agent_handle_parity.py."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.errors import (
    UniqueConstraintViolation,
    violates_unique_constraint,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

AGENTS = CollectionNames.AGENT_INSTANCES.value


class _Response:
    status = 200

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _neo4j(rows=None, error: Exception | None = None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows or [], side_effect=error)
    return p


def _arango(rows=None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(MagicMock(), AsyncMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(return_value=rows or [])  # type: ignore[method-assign]
    return p


class TestIndexesAndConstraints:
    def test_neo4j_gets_the_composite_unique_constraint(self) -> None:
        constraints = _neo4j()._generate_unique_id_constraints()

        assert (
            "CREATE CONSTRAINT agent_org_handle_unique IF NOT EXISTS "
            "FOR (a:AgentInstance) REQUIRE (a.orgId, a.handle) IS UNIQUE"
        ) in constraints

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            # Main's client always states `unique`; `sparse` is sent only when asked.
            ({}, {"type": "persistent", "fields": ["a", "b"], "unique": False}),
            ({"unique": True}, {"type": "persistent", "fields": ["a", "b"], "unique": True}),
            ({"unique": True, "sparse": True}, {"type": "persistent", "fields": ["a", "b"], "unique": True, "sparse": True}),
            ({"sparse": True}, {"type": "persistent", "fields": ["a", "b"], "unique": False, "sparse": True}),
        ],
    )
    async def test_arango_client_sends_unique_and_sparse_when_asked(self, kwargs: dict, expected: dict) -> None:
        client = ArangoHTTPClient("http://x", "root", "pw", "db", MagicMock())
        session = MagicMock()
        session.post.return_value = _Response()
        client._get_session = AsyncMock(return_value=session)  # type: ignore[method-assign]

        assert await client.ensure_persistent_index("agentInstances", ["a", "b"], **kwargs) is True

        assert session.post.call_args.kwargs["json"] == expected

    @pytest.mark.asyncio
    async def test_arango_creates_the_unique_sparse_org_handle_index(self) -> None:
        p = _arango()

        await p._ensure_indexes()

        p.http_client.ensure_persistent_index.assert_any_await(AGENTS, ["orgId", "handle"], unique=True, sparse=True)


class TestNeo4jQueries:
    @pytest.mark.asyncio
    async def test_get_agent_by_handle_is_scoped_to_the_org(self) -> None:
        p = _neo4j([{"a": {"id": "k1", "handle": "h", "orgId": "o"}}])

        found = await p.get_agent_by_handle("o", "h")

        assert found["_key"] == "k1" and found["handle"] == "h"
        assert p.client.execute_query.call_args.kwargs["parameters"] == {"org_id": "o", "handle": "h"}

    @pytest.mark.asyncio
    async def test_get_agent_by_handle_misses(self) -> None:
        assert await _neo4j([]).get_agent_by_handle("o", "h") is None

    @pytest.mark.asyncio
    async def test_search_agent_handles(self) -> None:
        p = _neo4j([{"handle": "sales-bot"}, {"handle": "sales-bot-2"}])

        assert await p.search_agent_handles("o", "sales", 5) == ["sales-bot", "sales-bot-2"]
        assert p.client.execute_query.call_args.kwargs["parameters"] == {"org_id": "o", "prefix": "sales", "limit": 5}
        assert "STARTS WITH" in p.client.execute_query.call_args.args[0]

    @pytest.mark.asyncio
    async def test_list_agents_missing_handle_orders_oldest_first_and_skips_orgless(self) -> None:
        p = _neo4j([{"id": "a", "name": "A", "orgId": "o"}])

        assert await p.list_agents_missing_handle(7) == [{"id": "a", "name": "A", "orgId": "o"}]
        query = p.client.execute_query.call_args.args[0]
        assert "ORDER BY a.createdAtTimestamp, a.id" in query and "org IS NOT NULL" in query
        assert p.client.execute_query.call_args.kwargs["parameters"] == {"batch": 7}


class TestArangoQueries:
    @pytest.mark.asyncio
    async def test_get_agent_by_handle(self) -> None:
        p = _arango([{"_key": "k1", "handle": "h"}])

        assert (await p.get_agent_by_handle("o", "h"))["_key"] == "k1"
        assert p.execute_query.call_args.kwargs["bind_vars"] == {"org_id": "o", "handle": "h"}

    @pytest.mark.asyncio
    async def test_get_agent_by_handle_misses(self) -> None:
        assert await _arango([]).get_agent_by_handle("o", "h") is None

    @pytest.mark.asyncio
    async def test_search_agent_handles(self) -> None:
        p = _arango(["sales-bot", "sales-bot-2"])

        assert await p.search_agent_handles("o", "sales", 5) == ["sales-bot", "sales-bot-2"]
        assert p.execute_query.call_args.kwargs["bind_vars"] == {"org_id": "o", "prefix": "sales", "limit": 5}

    @pytest.mark.asyncio
    async def test_list_agents_missing_handle(self) -> None:
        p = _arango([{"id": "a", "name": "A", "orgId": "o"}])

        assert await p.list_agents_missing_handle(7) == [{"id": "a", "name": "A", "orgId": "o"}]
        query = p.execute_query.call_args.args[0]
        assert "SORT a.createdAtTimestamp, a._key" in query and "FILTER org != null" in query
        assert p.execute_query.call_args.kwargs["bind_vars"] == {"batch": 7}


class TestUniqueViolationTranslation:
    @pytest.mark.parametrize(
        "message",
        [
            "Node(7) already exists with label `AgentInstances` and properties `orgId` = 'o', `handle` = 'h'",
            "{code: Neo.ClientError.Schema.ConstraintValidationFailed} {message: ...}",
            'Failed to update document (status=409): {"error":true,"errorNum":1210,"errorMessage":"unique constraint violated"}',
            "Batch insert failed with 1 error(s): Item 0: [1210] unique constraint violated - in index idx_1 of type persistent over ['orgId', 'handle']",
            "Batch insert failed with 1 error(s): Item 0: [1200] write-write conflict - in index idx_1 of type persistent over 'orgId, handle'",
            "Batch insert failed with 1 error(s): Item 0: [1200] timeout waiting to lock key Operation timed out: Timeout waiting to lock key - in index idx_1 of type persistent over 'orgId, handle'",
        ],
    )
    def test_recognises_both_backends(self, message: str) -> None:
        assert violates_unique_constraint(message)

    @pytest.mark.parametrize("message", ["connection reset", "document not found [1202]", "", "[1200] write-write conflict on document x/1"])
    def test_ignores_everything_else(self, message: str) -> None:
        assert not violates_unique_constraint(message)

    @pytest.mark.asyncio
    async def test_neo4j_upsert_and_update_raise_the_neutral_error(self) -> None:
        clash = RuntimeError("Node(7) already exists with label `AgentInstances` and properties `handle` = 'h'")
        p = _neo4j(error=clash)

        with pytest.raises(UniqueConstraintViolation):
            await p.batch_upsert_nodes([{"_key": "k", "name": "n", "handle": "my-bot", "orgId": "o"}], AGENTS)
        with pytest.raises(UniqueConstraintViolation):
            await p.update_node("k", AGENTS, {"handle": "my-bot"})

    @pytest.mark.asyncio
    async def test_arango_upsert_and_update_raise_the_neutral_error(self) -> None:
        p = ArangoHTTPProvider(MagicMock(), AsyncMock())
        p.http_client = AsyncMock()
        clash = Exception("Batch insert failed with 1 error(s): Item 0: [1210] unique constraint violated")
        p.http_client.batch_insert_documents = AsyncMock(side_effect=clash)
        p.http_client.update_document = AsyncMock(side_effect=clash)

        with pytest.raises(UniqueConstraintViolation):
            await p.batch_upsert_nodes([{"_key": "k", "handle": "h"}], AGENTS)
        with pytest.raises(UniqueConstraintViolation):
            await p.update_node("k", AGENTS, {"handle": "h"})

    @pytest.mark.asyncio
    async def test_other_errors_pass_through_unchanged(self) -> None:
        p = _neo4j(error=RuntimeError("connection reset"))

        with pytest.raises(RuntimeError, match="connection reset"):
            await p.update_node("k", AGENTS, {"handle": "my-bot"})
