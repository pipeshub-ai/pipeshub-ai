"""A connector or collection delete also takes the agents' knowledge entries that name it.

An agent names a source by `connectorId`, not by an edge, so before this the
entry outlived the source and the agent builder and chat showed its raw id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.unit.services.graph_db.test_stage_state_provider import (
    _arango,
    _connector_delete_ready,
    _neo4j,
)

if TYPE_CHECKING:
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

AGENT_KNOWLEDGE = CollectionNames.AGENT_KNOWLEDGE.value


def _arango_with_agent_knowledge(keys: list[str]) -> ArangoHTTPProvider:
    provider = _arango()
    _connector_delete_ready(provider)

    async def execute_aql(query, bind_vars=None, txn_id=None, **_) -> list[str]:
        return list(keys) if AGENT_KNOWLEDGE in query else []

    provider.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    provider.delete_nodes_and_edges = AsyncMock()
    return provider


class TestNeo4j:
    @pytest.mark.asyncio
    async def test_the_agents_entries_for_it_go_in_the_delete(self) -> None:
        provider = _neo4j()
        _connector_delete_ready(provider)

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is True
        provider._delete_nodes_by_connector_id.assert_any_await("txn1", "c1", AGENT_KNOWLEDGE)

    @pytest.mark.asyncio
    async def test_a_failed_removal_fails_the_delete_and_keeps_the_connector(self) -> None:
        provider = _neo4j()
        _connector_delete_ready(provider)
        provider._delete_nodes_by_connector_id = AsyncMock(
            side_effect=lambda _txn, _cid, collection: (0, collection != AGENT_KNOWLEDGE)
        )

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is False
        provider._delete_connector_with_its_delete_handles.assert_not_awaited()


class TestArango:
    @pytest.mark.asyncio
    async def test_the_agents_entries_and_their_edges_go_in_the_delete_transaction(self) -> None:
        provider = _arango_with_agent_knowledge(["k1", "k2"])

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is True
        lookup = next(
            c for c in provider.http_client.execute_aql.await_args_list
            if AGENT_KNOWLEDGE in (c.args[0] if c.args else c.kwargs.get("query", ""))
        )
        assert lookup.kwargs == {"bind_vars": {"connector_id": "c1"}, "txn_id": "txn1"}
        provider.delete_nodes_and_edges.assert_awaited_once_with(
            ["k1", "k2"], AGENT_KNOWLEDGE, transaction="txn1"
        )
        assert AGENT_KNOWLEDGE in provider.begin_transaction.await_args.kwargs["write"]
        provider.commit_transaction.assert_awaited_once_with("txn1")

    @pytest.mark.asyncio
    async def test_a_failed_removal_rolls_the_delete_back(self) -> None:
        provider = _arango_with_agent_knowledge(["k1"])
        provider.delete_nodes_and_edges = AsyncMock(side_effect=RuntimeError("down"))

        result = await provider.delete_connector_instance("c1", "org1")

        assert result["success"] is False
        provider.rollback_transaction.assert_awaited_once_with("txn1")
        provider.commit_transaction.assert_not_awaited()
