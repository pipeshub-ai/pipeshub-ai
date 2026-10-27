"""Copying a duplicate's taxonomy survives a deadlock with a concurrent writer.

copy_document_relationships writes edges onto category and topic nodes that
many records share. Two copies reaching those nodes in a different order
deadlocked on Neo4j, the copy returned False, and the duplicate was marked
FAILED. Edges are now written in one order, and a step that still collides is
re-run, since it re-reads what the target lacks.
"""

from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import Neo4jError

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

BELONGS_TO_TOPIC = "BELONGS_TO_TOPIC"


def _error(code: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message="collision")


DEADLOCK = "Neo.TransientError.Transaction.DeadlockDetected"


def _provider() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    return p


def _source_topics(*hubs: str) -> list[dict]:
    return [{"target_id": hub, "target_labels": ["Topic"], "timestamp": 1} for hub in hubs]


def _writes(p: Neo4jProvider) -> list:
    return [c for c in p.client.execute_query.await_args_list if "MERGE" in c.args[0]]


def _respond(
    *topic_reads: list[dict] | Exception, writes: list | None = None,
) -> Callable[..., Awaitable[list[dict]]]:
    """Answer the BELONGS_TO_TOPIC read with each of *topic_reads* in turn,
    every other read with nothing, and each MERGE with the next of *writes*."""
    reads = iter(topic_reads)
    outcomes = iter(writes or [])

    async def execute(query: str, **_: object) -> list[dict]:
        if "MERGE" in query:
            outcome = next(outcomes, [])
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if f":{BELONGS_TO_TOPIC}]" in query:
            return next(reads, [])
        return []

    return execute


@pytest.fixture(autouse=True)
def _no_backoff() -> Iterator[None]:
    with patch("app.services.graph_db.neo4j.neo4j_provider.asyncio.sleep", new=AsyncMock()):
        yield


class TestEdgeOrder:
    async def test_edges_onto_shared_nodes_are_written_in_one_order(self) -> None:
        p = _provider()
        p.client.execute_query = AsyncMock(return_value=[])
        edges = [
            {"from_id": "dup", "from_collection": CollectionNames.RECORDS.value,
             "to_id": hub, "to_collection": CollectionNames.TOPICS.value}
            for hub in ("t-c", "t-a", "t-b")
        ]

        await p.batch_create_edges(edges, CollectionNames.BELONGS_TO_TOPIC.value)

        rows = p.client.execute_query.await_args.kwargs["parameters"]["edges"]
        assert [row["to_key"] for row in rows] == ["t-a", "t-b", "t-c"]
        query = p.client.execute_query.await_args.args[0]
        assert query.index("ORDER BY edge.from_key, edge.to_key") < query.index("MERGE"), (
            "the query itself fixes the order, before the MERGE takes the locks"
        )


class TestCopyRetries:
    async def test_a_deadlocked_step_is_rerun_and_the_copy_succeeds(self) -> None:
        p = _provider()
        p.client.execute_query = AsyncMock(side_effect=_respond(
            _source_topics("t-a", "t-b"), _source_topics("t-a", "t-b"),
            writes=[_error(DEADLOCK), []],
        ))

        assert await p.copy_document_relationships("src", "dup") is True

        writes = _writes(p)
        assert len(writes) == 2, "the deadlocked write is run again"
        assert [e["to_key"] for e in writes[-1].kwargs["parameters"]["edges"]] == ["t-a", "t-b"]

    async def test_the_rerun_writes_only_what_the_target_still_lacks(self) -> None:
        p = _provider()
        # The second read is what is left once the first, partly committed,
        # attempt's edges are on the target.
        p.client.execute_query = AsyncMock(side_effect=_respond(
            _source_topics("t-a", "t-b"), _source_topics("t-b"),
            writes=[_error(DEADLOCK), []],
        ))

        assert await p.copy_document_relationships("src", "dup") is True

        assert [e["to_key"] for e in _writes(p)[-1].kwargs["parameters"]["edges"]] == ["t-b"]

    async def test_a_copy_that_keeps_deadlocking_fails(self) -> None:
        p = _provider()
        p.client.execute_query = AsyncMock(side_effect=_respond(
            *[_source_topics("t-a")] * 10, writes=[_error(DEADLOCK)] * 10,
        ))

        assert await p.copy_document_relationships("src", "dup") is False
        assert len(_writes(p)) == 6

    async def test_inside_a_callers_transaction_a_deadlock_is_not_rerun(self) -> None:
        """The deadlock rolled the caller's whole transaction back; re-running
        one step would write into a transaction that no longer exists."""
        p = _provider()
        p.client.execute_query = AsyncMock(side_effect=_respond(
            _source_topics("t-a"), _source_topics("t-a"), writes=[_error(DEADLOCK), []],
        ))

        assert await p.copy_document_relationships("src", "dup", transaction="txn-1") is False
        assert len(_writes(p)) == 1

    @pytest.mark.parametrize("code", [
        "Neo.TransientError.General.MemoryPoolOutOfMemoryError",
        "Neo.ClientError.Statement.SyntaxError",
    ])
    async def test_other_errors_are_not_rerun(self, code: str) -> None:
        p = _provider()
        p.client.execute_query = AsyncMock(side_effect=_respond(
            _source_topics("t-a"), _source_topics("t-a"), writes=[_error(code), []],
        ))

        assert await p.copy_document_relationships("src", "dup") is False
        assert len(_writes(p)) == 1
