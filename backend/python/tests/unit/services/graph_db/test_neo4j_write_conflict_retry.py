"""A statement that commits on its own is sent again after it loses a conflict.

Two writers that lock the same nodes in opposite orders deadlock, and Neo4j
fails one of them (DeadlockDetected), or one waits past the lock timeout. The
failed statement was rolled back whole, so it runs again. Inside an explicit
transaction the conflict ended the transaction, and only its owner can run it
again. Batches are written in one lock order, which keeps most deadlocks from
happening at all.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ClientError, TransientError

from app.connectors.core.base.data_store.graph_data_store import is_transient_conflict
from app.services.graph_db.neo4j.neo4j_client import (
    WRITE_CONFLICT_ATTEMPTS,
    Neo4jClient,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import Iterator


def _neo4j_error(cls: type, code: str) -> Exception:
    return cls._hydrate_neo4j(code=code, message="ForsetiClient[1] can't acquire ExclusiveLock")


def deadlock() -> Exception:
    return _neo4j_error(TransientError, "Neo.TransientError.Transaction.DeadlockDetected")


def lock_timeout() -> Exception:
    return _neo4j_error(TransientError, "Neo.TransientError.Transaction.LockAcquisitionTimeout")


class _Runner:
    """A session or transaction whose ``run`` fails as scripted, then succeeds."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.ran: list[str] = []

    async def run(self, query, parameters=None) -> MagicMock:
        self.ran.append(str(query))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        result = MagicMock()
        result.data = AsyncMock(return_value=[{"ok": True}])
        return result


class _Session(_Runner):
    def __init__(self, outcomes: list) -> None:
        super().__init__(outcomes)
        self.close = AsyncMock()
        self.tx = _Runner(outcomes)
        self.tx.commit, self.tx.rollback, self.tx.close = AsyncMock(), AsyncMock(), AsyncMock()
        self.begin_transaction = AsyncMock(return_value=self.tx)

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    with patch("app.services.graph_db.neo4j.neo4j_client.asyncio.sleep", new=AsyncMock()):
        yield


def _client(outcomes: list, **kwargs) -> tuple[Neo4jClient, list[_Session]]:
    client = Neo4jClient(
        uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j",
        logger=MagicMock(), **kwargs,
    )
    sessions: list[_Session] = []

    def _open(**kw) -> _Session:
        sessions.append(_Session(outcomes))
        return sessions[-1]

    client.driver = MagicMock()
    client.driver.session = MagicMock(side_effect=_open)
    return client, sessions


def _ran(sessions: list[_Session]) -> int:
    return sum(len(s.ran) + len(s.tx.ran) for s in sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [deadlock, lock_timeout])
async def test_a_statement_that_lost_a_conflict_runs_again(conflict) -> None:
    client, sessions = _client([conflict()])

    assert await client.execute_query("MERGE (a)-[:R]->(b)") == [{"ok": True}]
    assert _ran(sessions) == 2


@pytest.mark.asyncio
async def test_a_conflict_that_persists_is_raised() -> None:
    client, sessions = _client([deadlock() for _ in range(WRITE_CONFLICT_ATTEMPTS)])

    with pytest.raises(TransientError, match="DeadlockDetected"):
        await client.execute_query("MERGE (a)-[:R]->(b)")
    assert _ran(sessions) == WRITE_CONFLICT_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    lambda: _neo4j_error(TransientError, "Neo.TransientError.General.MemoryPoolOutOfMemoryError"),
    lambda: _neo4j_error(ClientError, "Neo.ClientError.Schema.ConstraintValidationFailed"),
])
async def test_any_other_failure_is_not_run_again(error) -> None:
    client, sessions = _client([error()])

    with pytest.raises((TransientError, ClientError)):
        await client.execute_query("MERGE (a)-[:R]->(b)")
    assert _ran(sessions) == 1


@pytest.mark.asyncio
async def test_a_statement_that_commits_in_batches_is_not_run_again() -> None:
    """Its earlier batches committed; running it again would repeat them."""
    client, sessions = _client([deadlock()])

    with pytest.raises(TransientError):
        await client.execute_query("CALL () { MATCH (n:X) SET n.v = n.v + 1 } IN TRANSACTIONS OF 100 ROWS")
    assert _ran(sessions) == 1


@pytest.mark.asyncio
async def test_a_statement_in_an_explicit_transaction_is_left_to_its_owner() -> None:
    client, sessions = _client([deadlock()], explicit_transactions=True)
    txn = await client.begin_transaction([], [])

    with pytest.raises(TransientError):
        await client.execute_query("MERGE (a)-[:R]->(b)", txn_id=txn)
    assert _ran(sessions) == 1


@pytest.mark.asyncio
async def test_a_statement_in_a_transaction_of_auto_commits_runs_again() -> None:
    """With explicit transactions off each statement of a block commits on its own."""
    client, sessions = _client([deadlock()], explicit_transactions=False)
    txn = await client.begin_transaction([], [])

    assert await client.execute_query("MERGE (a)-[:R]->(b)", txn_id=txn) == [{"ok": True}]
    assert _ran(sessions) == 2


@pytest.mark.parametrize("error, expected", [
    (deadlock(), True),
    (lock_timeout(), True),
    # The driver raises this one as a ClientError: the transaction was stopped, not outrun.
    (_neo4j_error(TransientError, "Neo.TransientError.Transaction.LockClientStopped"), False),
    (_neo4j_error(TransientError, "Neo.TransientError.General.MemoryPoolOutOfMemoryError"), False),
])
def test_what_the_data_store_re_runs_as_a_conflict(error, expected) -> None:
    assert is_transient_conflict(error) is expected


def _provider() -> tuple[Neo4jProvider, AsyncMock]:
    provider = Neo4jProvider(MagicMock(), MagicMock())
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[{"n.id": "x"}])
    return provider, provider.client.execute_query


def _sent(execute: AsyncMock, name: str) -> list[dict]:
    return execute.call_args.kwargs["parameters"][name]


@pytest.mark.asyncio
async def test_nodes_are_written_in_one_lock_order() -> None:
    provider, execute = _provider()

    await provider.batch_upsert_nodes(
        [{"_key": "c", "v": 1}, {"_key": "a", "v": 1}, {"_key": "c", "v": 2}, {"_key": "b", "v": 1}], "apps",
    )

    assert [(n["id"], n["v"]) for n in _sent(execute, "nodes")] == [("a", 1), ("b", 1), ("c", 1), ("c", 2)], (
        "sorted by id, and a node written twice keeps its last write last"
    )


@pytest.mark.asyncio
async def test_node_updates_are_written_in_one_lock_order() -> None:
    provider, execute = _provider()

    await provider.batch_update_nodes([{"_key": "b"}, {"_key": "a"}], "apps")

    assert [n["id"] for n in _sent(execute, "nodes")] == ["a", "b"]


@pytest.mark.asyncio
async def test_edges_are_written_in_one_lock_order() -> None:
    provider, execute = _provider()
    edge = {"from_collection": "users", "to_collection": "records", "type": "USER", "role": "READER"}

    await provider.batch_create_edges([
        {**edge, "from_id": "u2", "to_id": "r1"},
        {**edge, "from_id": "u1", "to_id": "r2"},
        {**edge, "from_id": "u1", "to_id": "r1"},
    ], "permission")

    assert [(e["from_key"], e["to_key"]) for e in _sent(execute, "edges")] == [("u1", "r1"), ("u1", "r2"), ("u2", "r1")]


@pytest.mark.asyncio
async def test_edge_deletes_are_made_in_one_lock_order() -> None:
    provider, execute = _provider()
    execute.return_value = [{"deleted": 1}]

    await provider.batch_delete_edges([
        {"_from": "users/u2", "_to": "records/r1"}, {"_from": "users/u1", "_to": "records/r1"},
    ], "permission")

    assert [(e["from_id"], e["to_id"]) for e in _sent(execute, "edges")] == [("u1", "r1"), ("u2", "r1")]
