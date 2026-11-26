"""Every conditional write of the graph providers lets exactly one of N concurrent writers win.

Writers that hold the same expected value race on one node: `create_node_if_absent`,
`update_node_if_match`, `update_node_fields_if_match`, `update_nodes_fields_if_match` and
`delete_node_if_match`. Exactly one reports success, and the stored node is that writer's
(or, for the delete, the node and every edge touching it are gone). A provider that
checks before it takes the write lock lets several of them win and loses updates, which
shows only under real concurrency, so this runs against the real stores, on every trial.
A delete that holds the expectation of a node removed and created again under its key
must leave the new node and its edges alone. Deleters and updaters of one node racing a
re-creator have one winner (CONDITIONAL_IT_SEED picks the interleaving). A delete in the
caller's transaction is undone by its rollback, and an ArangoDB delete that fails partway
changes nothing.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "conditional_writes_it"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
SKILLS = CollectionNames.AGENT_SKILLS.value
RELATION = CollectionNames.AGENT_SKILL_RELATION.value
WRITERS = 8
TRIALS = 10
SEED = int(os.environ.get("CONDITIONAL_IT_SEED") or random.SystemRandom().randrange(1 << 30))
logger = logging.getLogger("conditional-writes-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = f"cw-{uuid.uuid4().hex[:10]}"
        yield provider, run
        if isinstance(provider, Neo4jProvider):
            for collection in (MAPPING, SKILLS):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(collection)}) WHERE n.id STARTS WITH $p DETACH DELETE n",
                    parameters={"p": run},
                )
        else:
            await provider.http_client.execute_aql(
                f"FOR n IN {MAPPING} FILTER STARTS_WITH(n._key, @p) REMOVE n IN {MAPPING}", {"p": run},
            )
            await provider.delete_nodes_and_edges(
                [d["_key"] for d in await provider.get_nodes_by_filters(SKILLS, {"orgId": run})], SKILLS,
            )


async def _seed(provider: IGraphDBProvider, key: str) -> None:
    assert await provider.create_node_if_absent(key, MAPPING, {"id": key, "switchSeq": 1, "documentId": "seed"})


async def _stored(provider: IGraphDBProvider, key: str) -> dict:
    node = await provider.get_document(key, MAPPING)
    assert node is not None
    return node


async def test_create_node_if_absent_has_one_winner(graph) -> None:
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-create-{trial}"
        results = await asyncio.gather(
            *(provider.create_node_if_absent(key, MAPPING, {"id": key, "documentId": f"w{i}", "switchSeq": 1}) for i in range(WRITERS)),
        )
        assert sum(bool(r) for r in results) == 1, (trial, results)
        assert (await _stored(provider, key))["documentId"] == f"w{results.index(True)}"


async def test_update_node_if_match_has_one_winner(graph) -> None:
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-node-{trial}"
        await _seed(provider, key)
        results = await asyncio.gather(
            *(
                provider.update_node_if_match(
                    key, MAPPING, {"id": key, "documentId": f"w{i}", "switchSeq": 2 + i}, "switchSeq", 1,
                )
                for i in range(WRITERS)
            ),
        )
        assert sum(bool(r) for r in results) == 1, (trial, results)
        assert (await _stored(provider, key))["documentId"] == f"w{results.index(True)}"


async def test_update_node_fields_if_match_has_one_winner(graph) -> None:
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-fields-{trial}"
        await _seed(provider, key)
        results = await asyncio.gather(
            *(
                provider.update_node_fields_if_match(key, MAPPING, {"documentId": f"w{i}", "switchSeq": 2}, {"switchSeq": 1})
                for i in range(WRITERS)
            ),
        )
        assert sum(bool(r) for r in results) == 1, (trial, results)
        assert (await _stored(provider, key))["documentId"] == f"w{results.index(True)}"


async def test_update_nodes_fields_if_match_has_one_winner(graph) -> None:
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-rows-{trial}"
        await _seed(provider, key)
        results = await asyncio.gather(
            *(
                provider.update_nodes_fields_if_match(
                    MAPPING, [(key, {"documentId": f"w{i}", "switchSeq": 2}, {"switchSeq": 1})],
                )
                for i in range(WRITERS)
            ),
        )
        assert sum(len(r) for r in results) == 1, (trial, results)
        winner = next(i for i, r in enumerate(results) if r)
        assert (await _stored(provider, key))["documentId"] == f"w{winner}"


async def test_update_node_if_match_also_checks_every_expected_field(graph) -> None:
    provider, run = graph
    key = f"{run}-expected"
    await _seed(provider, key)
    assert not await provider.update_node_if_match(
        key, MAPPING, {"id": key, "documentId": "stale", "switchSeq": 2}, "switchSeq", 1, expected={"documentId": "other"},
    )
    assert (await _stored(provider, key))["documentId"] == "seed"
    assert not await provider.update_node_if_match(
        key, MAPPING, {"id": key, "documentId": "stale", "switchSeq": 2}, "switchSeq", 1, expected={"documentId": None},
    )
    for trial in range(TRIALS):
        token = trial + 1
        results = await asyncio.gather(
            *(
                provider.update_node_if_match(
                    key, MAPPING, {"id": key, "documentId": "seed", "switchSeq": token + 1, "writer": f"w{i}"},
                    "switchSeq", token, expected={"documentId": "seed"},
                )
                for i in range(WRITERS)
            ),
        )
        assert sum(bool(r) for r in results) == 1, (trial, results)
        assert (await _stored(provider, key))["writer"] == f"w{results.index(True)}"


def _skill(run: str, key: str, generation: str | None, updated_at: int = 1) -> dict:
    node = {
        "orgId": run, "name": key, "description": "d", "content": "c", "status": "active",
        "createdBy": "user-a", "createdAtTimestamp": 1, "updatedAtTimestamp": updated_at,
    }
    if generation is not None:
        node["generation"] = generation
    return node


async def _skill_with_edges(provider: IGraphDBProvider, run: str, key: str, generation: str | None) -> str:
    """`key` with a neighbour it points at and that points back; returns the neighbour's key."""
    other = f"{key}-other"
    assert await provider.create_node_if_absent(key, SKILLS, _skill(run, key, generation))
    if await provider.get_document(other, SKILLS) is None:
        assert await provider.create_node_if_absent(other, SKILLS, _skill(run, other, "n"))
    assert await provider.batch_create_edges([
        {"from_id": key, "from_collection": SKILLS, "to_id": other, "to_collection": SKILLS,
         "type": "related", "createdAtTimestamp": 1},
        {"from_id": other, "from_collection": SKILLS, "to_id": key, "to_collection": SKILLS,
         "type": "requires", "createdAtTimestamp": 1},
    ], RELATION)
    return other


async def _edges_of(provider: IGraphDBProvider, key: str) -> tuple[int, int]:
    node_id = f"{SKILLS}/{key}"
    return (
        len(await provider.get_edges_from_node(node_id, RELATION)),
        len(await provider.get_edges_to_node(node_id, RELATION)),
    )


async def test_delete_node_if_match_has_one_winner(graph) -> None:
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-delete-{trial}"
        other = await _skill_with_edges(provider, run, key, "g1")
        assert await _edges_of(provider, key) == (1, 1)
        results = await asyncio.gather(
            *(provider.delete_node_if_match(key, SKILLS, {"generation": "g1", "updatedAtTimestamp": 1}) for _ in range(WRITERS)),
        )
        assert sum(bool(r) for r in results) == 1, (trial, results)
        assert await provider.get_document(key, SKILLS) is None
        assert await _edges_of(provider, key) == (0, 0)
        assert await provider.get_document(other, SKILLS) is not None
        assert await _edges_of(provider, other) == (0, 0)


async def test_delete_node_if_match_leaves_a_node_that_does_not_match(graph) -> None:
    provider, run = graph
    key = f"{run}-mismatch"
    await _skill_with_edges(provider, run, key, "g2")
    before = await provider.get_document(key, SKILLS)
    for expected in (
        {"generation": "g1", "updatedAtTimestamp": 1},
        {"generation": "g2", "updatedAtTimestamp": 2},
        {"generation": None, "updatedAtTimestamp": 1},
    ):
        assert not await provider.delete_node_if_match(key, SKILLS, expected), expected
        assert await provider.get_document(key, SKILLS) == before
        assert await _edges_of(provider, key) == (1, 1)
    assert not await provider.delete_node_if_match(f"{run}-absent", SKILLS, {"generation": "g2"})
    with pytest.raises(ValueError):
        await provider.delete_node_if_match(key, SKILLS, {})

    legacy = f"{run}-legacy"
    await _skill_with_edges(provider, run, legacy, None)
    assert await provider.delete_node_if_match(legacy, SKILLS, {"generation": None, "updatedAtTimestamp": 1})
    assert await provider.get_document(legacy, SKILLS) is None
    assert await _edges_of(provider, legacy) == (0, 0)


async def test_a_delete_racing_a_re_create_never_removes_the_new_node(graph) -> None:
    """Deleters holding the first creation's expectation race a writer that creates the key
    again as soon as it is free: exactly one deleter wins, and the new node survives."""
    provider, run = graph
    for trial in range(TRIALS):
        key = f"{run}-recreate-{trial}"
        await _skill_with_edges(provider, run, key, "g1")

        async def recreate(key: str = key) -> None:
            # Polls on purpose: the key frees whenever the winning delete commits.
            while not await provider.create_node_if_absent(key, SKILLS, _skill(run, key, "g2")):  # noqa: ASYNC110
                await asyncio.sleep(0)

        deletes = [provider.delete_node_if_match(key, SKILLS, {"generation": "g1", "updatedAtTimestamp": 1}) for _ in range(WRITERS)]
        results = await asyncio.gather(*deletes, asyncio.wait_for(recreate(), timeout=60))
        assert sum(bool(r) for r in results[:WRITERS]) == 1, (trial, results)
        stored = await provider.get_document(key, SKILLS)
        assert stored is not None and stored["generation"] == "g2", (trial, stored)


def _settled(results: list) -> list:
    """`gather(..., return_exceptions=True)`'s results once every task has finished; the first failure is raised."""
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


async def race_deleters_updaters_and_a_re_creator(provider: IGraphDBProvider, org: str, key: str, rnd: random.Random) -> str:
    """6 deleters, 3 merges and 3 replaces holding one node's expectation race a writer that
    creates the key again the moment it is free. Exactly one of the 12 wins. When the delete
    wins, a late wave holding the same expectation misses the new node, which keeps its own
    edge; when an update wins, the node keeps both of its edges. Returns the winner's kind."""
    await _skill_with_edges(provider, org, key, "g1")
    expected = {"generation": "g1", "updatedAtTimestamp": 1}
    other = f"{key}-other"
    stop, re_created = asyncio.Event(), asyncio.Event()

    async def jitter() -> None:
        await asyncio.sleep(rnd.random() * 0.01)

    async def delete() -> tuple[str, bool]:
        await jitter()
        return "delete", await provider.delete_node_if_match(key, SKILLS, expected)

    async def merge(i: int) -> tuple[str, bool]:
        await jitter()
        return "merge", await provider.update_node_fields_if_match(
            key, SKILLS, {"description": f"merge-{i}", "updatedAtTimestamp": 2}, expected,
        )

    async def replace(i: int) -> tuple[str, bool]:
        await jitter()
        return "replace", await provider.update_node_if_match(
            key, SKILLS, {**_skill(org, key, "g1", updated_at=2), "description": f"replace-{i}"},
            "updatedAtTimestamp", 1, expected={"generation": "g1"},
        )

    async def re_create() -> None:
        while not stop.is_set():
            if await provider.get_document(key, SKILLS) is None and await provider.create_node_if_absent(
                key, SKILLS, _skill(org, key, "g2"),
            ):
                assert await provider.batch_create_edges([
                    {"from_id": other, "from_collection": SKILLS, "to_id": key, "to_collection": SKILLS,
                     "type": "requires", "createdAtTimestamp": 2},
                ], RELATION)
                re_created.set()
                return
            await asyncio.sleep(0.002)

    creator = asyncio.create_task(re_create())
    try:
        racers = [delete() for _ in range(6)] + [merge(i) for i in range(3)] + [replace(i) for i in range(3)]
        rnd.shuffle(racers)
        results = _settled(await asyncio.gather(*racers, return_exceptions=True))
        winners = [kind for kind, won in results if won]
        assert len(winners) == 1, results
        if winners[0] == "delete":
            await asyncio.wait_for(re_created.wait(), timeout=30)
            late = _settled(await asyncio.gather(delete(), delete(), merge(9), replace(9), return_exceptions=True))
            assert not any(won for _, won in late), late
            stored = await provider.get_document(key, SKILLS)
            assert stored is not None and stored["generation"] == "g2" and stored["description"] == "d"
            assert await _edges_of(provider, key) == (0, 1)
        else:
            stop.set()
            stored = await provider.get_document(key, SKILLS)
            assert stored["generation"] == "g1" and stored["description"].startswith(winners[0])
            assert await _edges_of(provider, key) == (1, 1)
    finally:
        # The creator stops (or is cancelled) and every racer has settled before the fixture tears down.
        stop.set()
        await asyncio.wait_for(creator, timeout=30)
    return winners[0]


async def test_deleters_updaters_and_a_re_creator_leave_one_winner(graph) -> None:
    provider, run = graph
    rnd = random.Random(SEED)
    winners = [await race_deleters_updaters_and_a_re_creator(provider, run, f"{run}-mixed-{t}", rnd) for t in range(TRIALS)]
    logger.info("mixed race seed=%s winners=%s", SEED, winners)


async def begin_delete_transaction(provider: IGraphDBProvider) -> str:
    """A caller's transaction for `delete_node_if_match` on agentSkills. On ArangoDB it has to
    cover every edge collection of the graph, as for `delete_nodes_and_edges`."""
    if isinstance(provider, Neo4jProvider):
        return await provider.begin_transaction(read=[], write=[SKILLS], explicit=True)
    edge_collections = getattr(provider, "_edge_collections_of_graph", None)
    if edge_collections is None:
        return await provider.begin_transaction(read=[], write=[SKILLS])
    edges = [c for g in await edge_collections("knowledgeGraph") for c in provider._logical_edge_collections(g)]
    return await provider.begin_transaction(read=[], write=[SKILLS, *dict.fromkeys(edges)])


async def delete_in_a_rolled_back_transaction(provider: IGraphDBProvider, org: str, key: str) -> None:
    """A delete inside the caller's transaction is undone, node and edges, by its rollback."""
    await _skill_with_edges(provider, org, key, "g1")
    before = (await provider.get_document(key, SKILLS), await _edges_of(provider, key))
    txn = await begin_delete_transaction(provider)
    assert await provider.delete_node_if_match(key, SKILLS, {"generation": "g1"}, transaction=txn)
    await provider.rollback_transaction(txn)
    assert (await provider.get_document(key, SKILLS), await _edges_of(provider, key)) == before
    assert await provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})


async def test_a_delete_in_the_callers_transaction_is_undone_by_its_rollback(graph) -> None:
    provider, run = graph
    await delete_in_a_rolled_back_transaction(provider, run, f"{run}-txn")


@pytest.mark.parametrize("graph", ["arango"], indirect=True)
async def test_an_arango_delete_that_fails_partway_changes_nothing(graph) -> None:
    """ArangoDB deletes the node and then its edges, statement by statement, in its own
    transaction: a failure after the node is gone, or at commit, leaves everything in place.
    (Neo4j deletes in one statement; the PostgreSQL version of this case is in postgres/.)"""
    provider, run = graph
    key = f"{run}-partway"
    await _skill_with_edges(provider, run, key, "g1")
    before = (await provider.get_document(key, SKILLS), await _edges_of(provider, key))
    execute = provider.http_client.execute_aql
    seen = {"edge statements": 0}

    async def fail_on_the_second_edge_statement(query: str, *args: object, **kwargs: object) -> object:
        if kwargs.get("txn_id") and "REMOVE edge" in query:
            seen["edge statements"] += 1
            if seen["edge statements"] == 2:
                raise RuntimeError("edge statement failed")
        return await execute(query, *args, **kwargs)

    provider.http_client.execute_aql = fail_on_the_second_edge_statement
    try:
        with pytest.raises(RuntimeError):
            await provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})
    finally:
        provider.http_client.execute_aql = execute
    assert (await provider.get_document(key, SKILLS), await _edges_of(provider, key)) == before

    commit = provider.commit_transaction

    async def failing_commit(txn: str) -> None:
        raise RuntimeError("commit failed")

    provider.commit_transaction = failing_commit
    try:
        with pytest.raises(RuntimeError):
            await provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})
    finally:
        provider.commit_transaction = commit
    assert (await provider.get_document(key, SKILLS), await _edges_of(provider, key)) == before
    assert await provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})
    assert await _edges_of(provider, key) == (0, 0)
