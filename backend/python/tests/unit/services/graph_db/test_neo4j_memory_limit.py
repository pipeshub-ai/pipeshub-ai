"""A bulk statement the server's transaction memory pool refuses fails for good.

Sending it again fails the same way, so it must come out of the client as
``GraphMemoryLimitError``, which nothing above retries. Everything here runs the
real chain: a fake driver raising a real driver ``TransientError`` carrying the
server's code, the real ``Neo4jClient``, a real ``Neo4jProvider``, a real
``GraphDataStore``, and a method under the real ``@retry_on_deadlock`` with the
sleep counted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import Neo4jError, TransientError

from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    GraphTransactionStore,
    ends_explicit_transaction,
    retry_on_deadlock,
)
from app.connectors.sources.localKB.push.retry import is_attempt_lost
from app.exceptions.graph_db_exceptions import GraphMemoryLimitError
from app.services.graph_db.neo4j.neo4j_client import MEMORY_LIMIT_CODES, Neo4jClient
from app.services.graph_db.neo4j.neo4j_provider import BULK_EDGE_ROWS, Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

POOL = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"
SLEEP = "app.connectors.core.base.data_store.graph_data_store.asyncio.sleep"


def _pool_error(code: str = POOL) -> TransientError:
    err = Neo4jError._hydrate_neo4j(code=code, message="dbms.memory.transaction.total.max threshold reached")
    assert isinstance(err, TransientError)
    return err


class _Result:
    def __init__(self, server: _Server) -> None:
        self.server = server

    async def data(self) -> list[dict]:
        if self.server.fail_at == "pull":
            self.server.attempts += 1
            raise self.server.error
        return []

    async def values(self) -> list[list]:
        return []

    def keys(self) -> list[str]:
        return []


class _Server:
    """Raises ``error`` at ``fail_at``: run, pull or commit."""

    def __init__(self, fail_at: str, error: Exception | None = None) -> None:
        self.fail_at = fail_at
        self.error = error or _pool_error()
        self.statements = 0
        self.attempts = 0
        self.commits = 0
        self.rollbacks = 0


class _Tx:
    def __init__(self, server: _Server) -> None:
        self.server = server

    async def run(self, query, parameters=None) -> _Result:
        self.server.statements += 1
        if self.server.fail_at == "run":
            self.server.attempts += 1
            raise self.server.error
        return _Result(self.server)

    async def commit(self) -> None:
        self.server.commits += 1
        if self.server.fail_at == "commit":
            self.server.attempts += 1
            raise self.server.error

    async def rollback(self) -> None:
        self.server.rollbacks += 1


class _Session:
    def __init__(self, server: _Server) -> None:
        self.server = server

    async def begin_transaction(self, timeout=None) -> _Tx:
        return _Tx(self.server)

    async def run(self, query, parameters=None, *args) -> _Result:
        return await _Tx(self.server).run(query, parameters)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


def _provider(server: _Server, *, explicit: bool = True) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock())
    client = Neo4jClient(
        uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j", logger=MagicMock(),
        explicit_transactions=explicit,
    )
    driver = MagicMock()
    driver.session = MagicMock(side_effect=lambda **_: _Session(server))
    client.driver = driver
    provider.client = client
    return provider


def _edge(n: int, to_id: str = "g1") -> dict:
    return {
        "from_id": f"u{n}", "from_collection": "users", "to_id": to_id, "to_collection": "groups",
        "role": "READER", "type": "USER", "createdAtTimestamp": 1, "updatedAtTimestamp": 2,
    }


class _Writer:
    """What a connector method looks like: the real decorator over a store transaction."""

    def __init__(self, store: GraphDataStore, write) -> None:
        self.data_store_provider = store
        self.write = write
        self.calls = 0

    @retry_on_deadlock()
    async def run(self) -> None:
        self.calls += 1
        async with self.data_store_provider.transaction(explicit=True) as tx_store:
            await self.write(tx_store)


def _replace(n: int = 3) -> Callable[[GraphTransactionStore], Awaitable[None]]:
    async def write(tx_store: GraphTransactionStore) -> None:
        await tx_store.replace_edges_to("g1", "groups", [_edge(i) for i in range(n)], "permission")
    return write


def _assert_not_retryable(err: GraphMemoryLimitError, provider: Neo4jProvider) -> None:
    assert not isinstance(err, TransientError)
    assert err.__cause__ is None
    assert err.__context__ is None
    assert not is_attempt_lost(err)
    assert provider.is_transient_error(err) is False
    assert ends_explicit_transaction(err)
    assert "DeadlockDetected" not in str(err)
    assert "LockAcquisitionTimeout" not in str(err)


class TestPoolErrorOnABulkStatement:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("where", ["run", "pull", "commit"])
    async def test_it_fails_once_wherever_the_pool_error_surfaces(self, where: str) -> None:
        server = _Server(where)
        provider = _provider(server)
        writer = _Writer(GraphDataStore(MagicMock(), provider), _replace())

        with patch(SLEEP, AsyncMock()) as sleep, pytest.raises(GraphMemoryLimitError) as raised:
            await writer.run()

        assert writer.calls == 1
        assert server.attempts == 1
        sleep.assert_not_awaited()
        _assert_not_retryable(raised.value, provider)
        assert raised.value.operation == "group replace"
        assert raised.value.size == 3
        assert "NEO4J_HEAP_MAX" in str(raised.value)
        assert "3 rows" in str(raised.value)

    @pytest.mark.asyncio
    async def test_it_fails_once_in_auto_commit_mode(self) -> None:
        server = _Server("run")
        provider = _provider(server, explicit=False)

        with pytest.raises(GraphMemoryLimitError) as raised:
            await provider.replace_edges_to("g1", "groups", [_edge(1)], "permission")

        assert server.attempts == 1
        _assert_not_retryable(raised.value, provider)

    @pytest.mark.asyncio
    async def test_it_is_logged_once_at_the_mapping_site(self) -> None:
        server = _Server("run")
        provider = _provider(server)

        with pytest.raises(GraphMemoryLimitError):
            await provider.replace_edges_to("g1", "groups", [_edge(1), _edge(2)], "permission")

        logged = [c for c in provider.client.logger.error.call_args_list if "memory limit" in str(c.args[0])]
        assert len(logged) == 1
        assert "group replace" in str(logged[0].args[1])
        assert "2 rows" in str(logged[0].args[1])

    @pytest.mark.asyncio
    async def test_execute_in_transaction_runs_it_once(self) -> None:
        server = _Server("run")
        provider = _provider(server)
        store = GraphDataStore(MagicMock(), provider)
        calls = 0

        async def write(tx_store: GraphTransactionStore) -> None:
            nonlocal calls
            calls += 1
            await _replace()(tx_store)

        with patch(SLEEP, AsyncMock()) as sleep, pytest.raises(GraphMemoryLimitError):
            await store.execute_in_transaction(write, explicit=True)

        assert calls == 1
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_second_pool_code_maps_the_same(self) -> None:
        assert "Neo.TransientError.General.TransactionMemoryLimit" in MEMORY_LIMIT_CODES
        server = _Server("run", _pool_error("Neo.TransientError.General.TransactionMemoryLimit"))
        provider = _provider(server)

        with pytest.raises(GraphMemoryLimitError):
            await provider.replace_edges_to("g1", "groups", [_edge(1)], "permission")

    @pytest.mark.asyncio
    async def test_a_deadlock_on_the_same_statement_is_still_transient_and_retried(self) -> None:
        deadlock = Neo4jError._hydrate_neo4j(code="Neo.TransientError.Transaction.DeadlockDetected", message="d")
        server = _Server("run", deadlock)
        writer = _Writer(GraphDataStore(MagicMock(), _provider(server)), _replace())

        with patch(SLEEP, AsyncMock()), pytest.raises(TransientError):
            await writer.run()

        assert writer.calls == 6

    @pytest.mark.asyncio
    async def test_create_user_group_membership_does_not_swallow_it(self) -> None:
        graph = MagicMock()
        graph.batch_create_edges = AsyncMock(side_effect=GraphMemoryLimitError("full"))
        graph.is_transient_error = MagicMock(return_value=False)
        store = GraphTransactionStore(graph, "txn", explicit=True)
        store.get_user_by_source_id = AsyncMock(return_value=MagicMock(id="u", email="u@example.com"))
        store.get_user_group_by_external_id = AsyncMock(return_value=MagicMock(id="g", name="G"))

        with pytest.raises(GraphMemoryLimitError):
            await store.create_user_group_membership("u", "g", "conn-1")
        graph.batch_create_edges.assert_awaited_once()


class TestWhichStatementsAreBulk:
    @pytest.mark.asyncio
    async def test_a_record_permission_replace_is_bulk_at_any_size(self) -> None:
        provider = _provider(_Server("run"))
        with pytest.raises(GraphMemoryLimitError) as raised:
            await provider.replace_record_permissions("r1", [], None, inherit=False)
        assert raised.value.operation == "record permission replace"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["batch_create_edges", "create_edges_if_absent"])
    async def test_batch_edge_writes_are_bulk_from_the_threshold(self, method: str) -> None:
        edges = [_edge(i) for i in range(BULK_EDGE_ROWS)]
        provider = _provider(_Server("run"))
        with pytest.raises(GraphMemoryLimitError) as raised:
            await getattr(provider, method)(edges, "permission")
        assert raised.value.size == BULK_EDGE_ROWS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["batch_create_edges", "create_edges_if_absent"])
    async def test_a_smaller_batch_keeps_the_driver_error_and_its_retries(self, method: str) -> None:
        edges = [_edge(i) for i in range(BULK_EDGE_ROWS - 1)]
        server = _Server("run")
        provider = _provider(server)
        writer = _Writer(
            GraphDataStore(MagicMock(), provider),
            lambda tx: getattr(provider, method)(edges, "permission", transaction=tx.txn),
        )

        with patch(SLEEP, AsyncMock()), pytest.raises(TransientError) as raised:
            await writer.run()

        assert not isinstance(raised.value, GraphMemoryLimitError)
        assert writer.calls == 6

    @pytest.mark.asyncio
    async def test_a_one_edge_victim_succeeds_once_the_pool_is_free_again(self) -> None:
        server = _Server("run")
        provider = _provider(server)
        failures = [_pool_error(), _pool_error()]
        real_run = _Tx.run

        async def run(self, query, parameters=None) -> object:
            if failures:
                raise failures.pop()
            return await real_run(self, query, parameters)

        server.fail_at = "never"
        writer = _Writer(
            GraphDataStore(MagicMock(), provider),
            lambda tx: provider.batch_create_edges([_edge(1)], "permission", transaction=tx.txn),
        )

        with patch(SLEEP, AsyncMock()), patch.object(_Tx, "run", run):
            await writer.run()

        assert writer.calls == 3
        assert server.commits == 1


def test_the_bulk_boundary_is_ten_thousand_rows() -> None:
    """Below this a bulk edge write keeps its bounded retry; the tests above import the constant,
    so pin its value here."""
    assert BULK_EDGE_ROWS == 10_000
