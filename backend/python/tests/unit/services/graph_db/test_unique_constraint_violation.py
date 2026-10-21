"""How both providers surface a unique-constraint violation, and the Arango
client's unique/sparse index options."""

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


class TestPersistentIndexOptions:
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
