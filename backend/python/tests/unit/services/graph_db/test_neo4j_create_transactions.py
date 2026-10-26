"""The short creates run in a real Neo4j transaction while NEO4J_EXPLICIT_TRANSACTIONS is off.

With the flag off every statement commits on its own, so an agent, team or knowledge
base create that failed partway left a half-made object behind. Those four creates now
ask for ``begin_transaction(explicit=True)``. Each path below runs through the real
GraphDataStore, Neo4jProvider and Neo4jClient onto a fake driver that records what
committed: with the opt-in honoured a failure partway lands nothing, and with
NEO4J_CREATE_TRANSACTIONS=false (the kill switch) the earlier statements land as today.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from neo4j.exceptions import Neo4jError

from app.agents.constants.mcp_server_constants import get_mcp_instance_path
from app.api.routes.agent import create_agent
from app.api.routes.entity import create_team
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.exceptions.graph_db_exceptions import GraphWriteError
from app.services.graph_db.neo4j import neo4j_provider as provider_module
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.messaging.kafka.handlers.entity import EntityEventService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _deadlock() -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code="Neo.TransientError.Transaction.DeadlockDetected", message="deadlock")


class _Server:
    """What reached Neo4j: statements auto-committed on a session, and transactions.

    Statement number *fail_at* raises *error*, counted across every statement sent.
    """

    def __init__(self, *, fail_at: int | None = None, error: Exception | None = None) -> None:
        self.fail_at = fail_at
        self.error = error or RuntimeError("connection reset by peer")
        self.sent = 0
        self.autocommitted: list[str] = []
        self.reads: list[str] = []
        self.transactions: list[_Tx] = []

    def run(self, query: str, tx: _Tx | None) -> MagicMock:
        self.sent += 1
        if self.sent == self.fail_at:
            raise self.error
        if not re.search(r"\b(MERGE|CREATE|DELETE|SET)\b", query):
            self.reads.append(query)
        else:
            (tx.ran if tx is not None else self.autocommitted).append(query)
        result = MagicMock()
        result.data = AsyncMock(return_value=[])
        return result

    @property
    def landed(self) -> list[str]:
        """Every statement that is on the server now."""
        return self.autocommitted + [q for tx in self.transactions if tx.committed for q in tx.ran]


class _Tx:
    def __init__(self, server: _Server) -> None:
        self.server = server
        self.ran: list[str] = []
        self.committed = False
        self.rolled_back = False

    async def run(self, query, parameters=None) -> MagicMock:
        return self.server.run(query, self)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _Session:
    def __init__(self, server: _Server) -> None:
        self.server = server

    async def begin_transaction(self, timeout=None) -> _Tx:
        tx = _Tx(self.server)
        self.server.transactions.append(tx)
        return tx

    async def run(self, query, parameters=None) -> MagicMock:
        return self.server.run(query, None)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


def _provider(server: _Server, *, explicit_transactions: bool = False, honour: bool = True) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock())
    client = Neo4jClient(
        uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j", logger=MagicMock(),
        explicit_transactions=explicit_transactions, honour_explicit_requests=honour,
    )
    driver = MagicMock()
    driver.session = MagicMock(side_effect=lambda **_: _Session(server))
    client.driver = driver
    provider.client = client
    return provider


class TestTheOptIn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("flag", "honour", "explicit", "real"), [
        (False, True, True, True),
        (False, True, None, False),
        (False, False, True, False),
        (True, False, True, True),
        (True, True, None, True),
        (True, True, False, False),
    ])
    async def test_begin_transaction_opens_a_real_one_only_when_asked_and_allowed(
        self, flag: bool, honour: bool, explicit: bool | None, real: bool,
    ) -> None:
        server = _Server()
        provider = _provider(server, explicit_transactions=flag, honour=honour)

        txn = await provider.begin_transaction([], [], explicit=explicit)
        await provider.client.execute_query("CREATE (n)", txn_id=txn)
        await provider.rollback_transaction(txn)

        assert len(server.transactions) == int(real)
        assert server.landed == ([] if real else ["CREATE (n)"])

    @pytest.mark.parametrize(("honour", "explicit", "retry"), [
        (True, True, True), (True, None, False), (False, True, False),
    ])
    def test_a_deadlock_is_retryable_only_after_a_real_transaction(
        self, honour: bool, explicit: bool | None, retry: bool,
    ) -> None:
        provider = _provider(_Server(), honour=honour)
        assert provider.is_transient_error(_deadlock(), explicit=explicit) is retry
        assert provider.is_write_conflict(_deadlock())

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("value", "honour"), [(None, True), ("true", True), ("false", False), ("FALSE", False)])
    async def test_the_kill_switch_is_read_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None, honour: bool,
    ) -> None:
        monkeypatch.setenv("NEO4J_PASSWORD", "pw")
        monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
        if value is None:
            monkeypatch.delenv("NEO4J_CREATE_TRANSACTIONS", raising=False)
        else:
            monkeypatch.setenv("NEO4J_CREATE_TRANSACTIONS", value)
        client_cls = MagicMock()
        client_cls.return_value.connect = AsyncMock(return_value=True)
        with patch.object(provider_module, "Neo4jClient", client_cls):
            assert await Neo4jProvider(MagicMock(), MagicMock()).connect()

        kwargs = client_cls.call_args.kwargs
        assert kwargs["explicit_transactions"] is True
        assert kwargs["honour_explicit_requests"] is honour

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("value", "expected"), [(None, True), ("true", True), ("false", False)])
    async def test_the_explicit_transactions_flag_defaults_on_and_false_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool,
    ) -> None:
        monkeypatch.setenv("NEO4J_PASSWORD", "pw")
        monkeypatch.delenv("NEO4J_CREATE_TRANSACTIONS", raising=False)
        if value is None:
            monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
        else:
            monkeypatch.setenv("NEO4J_EXPLICIT_TRANSACTIONS", value)
        client_cls = MagicMock()
        client_cls.return_value.connect = AsyncMock(return_value=True)
        with patch.object(provider_module, "Neo4jClient", client_cls):
            assert await Neo4jProvider(MagicMock(), MagicMock()).connect()

        assert client_cls.call_args.kwargs["explicit_transactions"] is expected

    @pytest.mark.asyncio
    async def test_a_plain_block_that_fails_midway_lands_nothing_by_default(self) -> None:
        server = _Server(fail_at=2, error=RuntimeError("boom"))
        store = GraphDataStore(MagicMock(), _provider(server, explicit_transactions=True))

        with pytest.raises(RuntimeError, match="boom"):
            async with store.transaction() as tx_store:
                assert tx_store.explicit
                await tx_store.batch_upsert_nodes([{"id": "t1", "name": "T"}], "teams")
                await tx_store.batch_upsert_nodes([{"id": "t2", "name": "U"}], "teams")

        (tx,) = server.transactions
        assert tx.rolled_back and not tx.committed
        assert not server.landed

    @pytest.mark.asyncio
    async def test_with_the_flag_off_a_plain_block_is_not_marked_explicit(self) -> None:
        store = GraphDataStore(MagicMock(), _provider(_Server()))
        async with store.transaction() as tx_store:
            assert not tx_store.explicit

    @pytest.mark.asyncio
    async def test_a_deadlock_in_an_explicit_block_is_rerun_from_a_clean_slate(self) -> None:
        server = _Server(fail_at=2, error=_deadlock())
        store = GraphDataStore(MagicMock(), _provider(server))

        async def write(tx_store) -> str:
            await tx_store.batch_upsert_nodes([{"id": "t1", "name": "T"}], "teams")
            await tx_store.batch_upsert_nodes([{"id": "t2", "name": "U"}], "teams")
            return "done"

        with patch("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock()):
            assert await store.execute_in_transaction(write, explicit=True) == "done"

        first, second = server.transactions
        assert first.rolled_back and not first.committed
        assert second.committed
        assert len(server.landed) == 2


# The four create paths, each failing on one of its later statements.

async def _create_agent(provider: Neo4jProvider) -> None:
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1", "email": "a@b.co"})
    request = MagicMock()
    request.state.user = {"userId": "user-1", "orgId": "org-1"}
    container = request.app.container
    container.retrieval_service = AsyncMock()
    container.graph_provider = AsyncMock(return_value=provider)
    container.logger.return_value = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({
        "name": "Helper",
        "shareWithOrg": True,
        "toolsets": [{"name": "jira", "tools": [{"name": "search"}]}],
        "knowledge": [{"connectorId": "c1"}],
    }).encode())
    await create_agent(request)


async def _create_team(provider: Neo4jProvider) -> None:
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
    provider.get_graph_user_keys_by_mongo_user_ids = AsyncMock(return_value={"user-2": "uk2"})
    provider.get_team_with_users = AsyncMock(return_value={"id": "team"})
    request = MagicMock()
    request.state.user = {"userId": "user-1", "orgId": "org-1"}
    request.app.state.graph_provider = provider
    request.app.container.logger.return_value = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({
        "name": "Team", "userRoles": [{"userId": "user-2", "role": "READER"}],
    }).encode())
    await create_team(request)


async def _create_kb(provider: Neo4jProvider) -> None:
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1", "fullName": "A"})
    service = KnowledgeBaseService(MagicMock(), provider, MagicMock())
    with patch("app.connectors.sources.localKB.handlers.kb_service.assign_lane_to_new_connector", AsyncMock()):
        result = await service.create_knowledge_base("user-1", "org-1", "Notes")
    if not result.get("success"):
        raise RuntimeError(result.get("reason"))


async def _create_default_kb(provider: Neo4jProvider) -> None:
    provider.get_nodes_by_filters = AsyncMock(return_value=[])
    service = EntityEventService(MagicMock(), provider, MagicMock())
    with patch(
        "app.services.messaging.kafka.handlers.entity.ConnectorFactory.create_and_start_sync",
        AsyncMock(return_value=None),
    ):
        await service._get_or_create_knowledge_base("uk1", "user-1", "org-1")


async def _repair_default_kb(provider: Neo4jProvider) -> None:
    provider.get_nodes_by_filters = AsyncMock(return_value=[{"id": "kb-1", "isDeleted": False}])
    await EntityEventService(MagicMock(), provider, MagicMock())._get_or_create_knowledge_base(
        "uk1", "user-1", "org-1",
    )


_PATHS: dict[str, tuple[Callable[[Neo4jProvider], Awaitable[None]], int]] = {
    # name: (create, statements it sends when nothing fails)
    "agent": (_create_agent, 9),
    "team": (_create_team, 2),
    "knowledge_base": (_create_kb, 4),
    "default_knowledge_base": (_create_default_kb, 4),
    "default_knowledge_base_repair": (_repair_default_kb, 3),
}


# A write a provider can report as failed by returning False (ArangoDB's
# batch_upsert_nodes does), and the statement it would have been.
_REPORTED_FAILURES: dict[str, tuple[str, str, int]] = {
    "agent": ("batch_create_edges", "agentHasKnowledge", 9),
    "team": ("batch_create_edges", "permission", 2),
    "knowledge_base": ("batch_create_edges", "userAppRelation", 4),
    "default_knowledge_base": ("batch_upsert_nodes", "apps", 1),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(_REPORTED_FAILURES))
async def test_a_write_reported_as_failed_rolls_the_create_back(path: str) -> None:
    create, _ = _PATHS[path]
    method, refused, statement = _REPORTED_FAILURES[path]
    server = _Server()
    provider = _provider(server)
    real = getattr(provider, method)

    async def reports_failure(items: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == refused:
            return False
        return await real(items, collection, transaction=transaction)
    setattr(provider, method, reports_failure)

    with pytest.raises((RuntimeError, HTTPException, GraphWriteError)):
        await create(provider)

    (tx,) = server.transactions
    assert tx.rolled_back and not tx.committed
    assert len(tx.ran) == statement - 1
    assert server.landed == []


@pytest.mark.parametrize("path", sorted(_PATHS))
class TestCreatePaths:
    @pytest.mark.asyncio
    async def test_it_sends_its_statements_in_one_committed_transaction(self, path: str) -> None:
        create, statements = _PATHS[path]
        server = _Server()

        await create(_provider(server))

        (tx,) = server.transactions
        assert tx.committed
        assert server.autocommitted == []
        assert len(tx.ran) == statements

    @pytest.mark.asyncio
    async def test_a_failure_on_the_last_statement_lands_nothing(self, path: str) -> None:
        create, statements = _PATHS[path]
        server = _Server(fail_at=statements)

        with pytest.raises((RuntimeError, HTTPException)):
            await create(_provider(server))

        (tx,) = server.transactions
        assert tx.rolled_back and not tx.committed
        assert len(tx.ran) == statements - 1
        assert server.landed == []

    @pytest.mark.asyncio
    async def test_with_the_kill_switch_off_the_earlier_statements_land_as_before(self, path: str) -> None:
        create, statements = _PATHS[path]
        server = _Server(fail_at=statements)

        with pytest.raises((RuntimeError, HTTPException)):
            await create(_provider(server, honour=False))

        assert server.transactions == []
        assert len(server.landed) == statements - 1


# Agent create attaches its MCP servers after the agent's own transaction commits,
# one transaction each, so a failing server leaves the agent ("partial_success").
_AGENT_STATEMENTS = 2
_MCP_STATEMENTS = 4


def _writes(server: "_Server") -> list[str]:
    """What landed, less the deletes with which a failed server's attach removes what it may
    have left: with a real rollback they find nothing."""
    return [statement for statement in server.landed if "DELETE" not in statement]


async def _create_agent_with_mcp_servers(provider: Neo4jProvider) -> dict:
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1", "email": "a@b.co"})
    request = MagicMock()
    request.state.user = {"userId": "user-1", "orgId": "org-1"}
    container = request.app.container
    container.retrieval_service = AsyncMock()
    container.graph_provider = AsyncMock(return_value=provider)
    container.logger.return_value = MagicMock()
    # The org's servers, which create checks each attachment against.
    servers = {
        get_mcp_instance_path(instance_id): {"_id": instance_id, "orgId": "org-1", "name": name}
        for instance_id, name in (("i1", "github"), ("i2", "linear"))
    }
    container.config_service.return_value.get_config = AsyncMock(side_effect=lambda key, **_: servers.get(key))
    request.body = AsyncMock(return_value=json.dumps({"name": "Helper", "mcpServers": [
        {"instanceId": "i1", "name": "github", "displayName": "GitHub", "tools": [{"name": "issues"}]},
        {"instanceId": "i2", "name": "linear", "tools": [{"name": "search"}]},
    ]}).encode())
    response = await create_agent(request)
    return json.loads(response.body)


class TestAgentMcpServers:
    @pytest.mark.asyncio
    async def test_the_agent_and_each_mcp_server_commit_in_their_own_transaction(self) -> None:
        server = _Server()

        body = await _create_agent_with_mcp_servers(_provider(server))

        assert body["status"] == "success"
        assert body["warnings"] is None
        assert [m["name"] for m in body["agent"]["mcpServers"]] == ["github", "linear"]
        assert [len(tx.ran) for tx in server.transactions] == [_AGENT_STATEMENTS, _MCP_STATEMENTS, _MCP_STATEMENTS]
        assert all(tx.committed for tx in server.transactions)
        assert server.autocommitted == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("step", range(1, _MCP_STATEMENTS + 1))
    async def test_a_failed_mcp_server_is_reported_and_leaves_the_agent_and_the_others(self, step: int) -> None:
        server = _Server(fail_at=_AGENT_STATEMENTS + step)

        body = await _create_agent_with_mcp_servers(_provider(server))

        assert body["status"] == "partial_success"
        assert [w["name"] for w in body["warnings"]] == ["github"]
        assert body["warnings"][0]["error"].startswith("We couldn't add GitHub to the agent.")
        assert [m["name"] for m in body["agent"]["mcpServers"]] == ["linear"]
        # The check for a leftover link, which a real rollback leaves nothing for.
        assert len(server.reads) == 1
        agent_tx, github_tx, linear_tx = server.transactions
        assert agent_tx.committed and linear_tx.committed
        assert github_tx.rolled_back and not github_tx.committed
        assert len(_writes(server)) == _AGENT_STATEMENTS + _MCP_STATEMENTS

    @pytest.mark.asyncio
    async def test_a_deadlock_attaching_an_mcp_server_retries_only_that_server(self) -> None:
        server = _Server(fail_at=_AGENT_STATEMENTS + 2, error=_deadlock())

        with patch("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock()):
            body = await _create_agent_with_mcp_servers(_provider(server))

        assert body["status"] == "success"
        agent_tx, failed_try, github_tx, linear_tx = server.transactions
        assert failed_try.rolled_back and not failed_try.committed
        assert agent_tx.committed and github_tx.committed and linear_tx.committed
        assert len(server.landed) == _AGENT_STATEMENTS + 2 * _MCP_STATEMENTS

    @pytest.mark.asyncio
    async def test_a_failed_agent_write_saves_nothing_and_attaches_no_mcp_server(self) -> None:
        server = _Server(fail_at=_AGENT_STATEMENTS)

        with pytest.raises(HTTPException):
            await _create_agent_with_mcp_servers(_provider(server))

        (tx,) = server.transactions
        assert tx.rolled_back and not tx.committed
        assert server.landed == []

    @pytest.mark.asyncio
    async def test_with_the_kill_switch_off_a_failed_mcp_server_still_leaves_the_agent(self) -> None:
        server = _Server(fail_at=_AGENT_STATEMENTS + 1)

        body = await _create_agent_with_mcp_servers(_provider(server, honour=False))

        assert body["status"] == "partial_success"
        assert [w["name"] for w in body["warnings"]] == ["github"]
        assert server.transactions == []
        assert len(_writes(server)) == _AGENT_STATEMENTS + _MCP_STATEMENTS
