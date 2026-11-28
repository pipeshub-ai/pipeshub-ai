"""Every conditional write of the PostgreSQL graph provider lets exactly one of N concurrent writers win.

Writers that hold the same expected value race on one node: `create_node_if_absent`, `update_node_if_match`,
`update_node_fields_if_match`, `update_nodes_fields_if_match` and `delete_node_if_match`. Exactly one reports
success and the stored node is that writer's (or, for the delete, the node and its edges are gone). A delete that
holds the expectation of a node removed and created again under its key leaves the new node alone. Deleters and
updaters of one node racing a re-creator have one winner; a delete in the caller's transaction is undone by its rollback;
a delete that fails after the node is gone changes nothing. Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.test_conditional_writes_real_backends import (
    SEED,
    delete_in_a_rolled_back_transaction,
    race_deleters_updaters_and_a_re_creator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

MAPPING = "virtualRecordToDocIdMapping"
SKILLS = "agentSkills"
RELATION = "agentSkillRelation"
WRITERS = 8
TRIALS = 10


@pytest.fixture
async def org(pg_db, org_ctx) -> AsyncIterator[str]:
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


async def _seed(provider, key: str) -> None:
    assert await provider.create_node_if_absent(key, MAPPING, {"id": key, "switchSeq": 1, "documentId": "seed"})


async def test_create_node_if_absent_has_one_winner(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-create-{trial}"
            results = await asyncio.gather(
                *(pg_provider.create_node_if_absent(key, MAPPING, {"id": key, "documentId": f"w{i}", "switchSeq": 1}) for i in range(WRITERS)),
            )
            assert sum(bool(r) for r in results) == 1, (trial, results)
            assert (await pg_provider.get_document(key, MAPPING))["documentId"] == f"w{results.index(True)}"


async def test_update_node_if_match_has_one_winner(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-node-{trial}"
            await _seed(pg_provider, key)
            results = await asyncio.gather(
                *(
                    pg_provider.update_node_if_match(key, MAPPING, {"id": key, "documentId": f"w{i}", "switchSeq": 2 + i}, "switchSeq", 1)
                    for i in range(WRITERS)
                ),
            )
            assert sum(bool(r) for r in results) == 1, (trial, results)
            assert (await pg_provider.get_document(key, MAPPING))["documentId"] == f"w{results.index(True)}"


async def test_update_node_fields_if_match_has_one_winner(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-fields-{trial}"
            await _seed(pg_provider, key)
            results = await asyncio.gather(
                *(
                    pg_provider.update_node_fields_if_match(key, MAPPING, {"documentId": f"w{i}", "switchSeq": 2}, {"switchSeq": 1})
                    for i in range(WRITERS)
                ),
            )
            assert sum(bool(r) for r in results) == 1, (trial, results)
            assert (await pg_provider.get_document(key, MAPPING))["documentId"] == f"w{results.index(True)}"


async def test_update_nodes_fields_if_match_has_one_winner(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-rows-{trial}"
            await _seed(pg_provider, key)
            results = await asyncio.gather(
                *(
                    pg_provider.update_nodes_fields_if_match(MAPPING, [(key, {"documentId": f"w{i}", "switchSeq": 2}, {"switchSeq": 1})])
                    for i in range(WRITERS)
                ),
            )
            assert sum(len(r) for r in results) == 1, (trial, results)
            winner = next(i for i, r in enumerate(results) if r)
            assert (await pg_provider.get_document(key, MAPPING))["documentId"] == f"w{winner}"


async def test_update_node_if_match_also_checks_every_expected_field(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        key = "cw-expected"
        await _seed(pg_provider, key)
        for expected in ({"documentId": "other"}, {"documentId": None}):
            assert not await pg_provider.update_node_if_match(
                key, MAPPING, {"id": key, "documentId": "stale", "switchSeq": 2}, "switchSeq", 1, expected=expected,
            )
        assert (await pg_provider.get_document(key, MAPPING))["documentId"] == "seed"
        for trial in range(TRIALS):
            token = trial + 1
            results = await asyncio.gather(
                *(
                    pg_provider.update_node_if_match(
                        key, MAPPING, {"id": key, "documentId": "seed", "switchSeq": token + 1, "writer": f"w{i}"},
                        "switchSeq", token, expected={"documentId": "seed"},
                    )
                    for i in range(WRITERS)
                ),
            )
            assert sum(bool(r) for r in results) == 1, (trial, results)
            assert (await pg_provider.get_document(key, MAPPING))["writer"] == f"w{results.index(True)}"


def _skill(org: str, key: str, generation: str | None) -> dict:
    node = {
        "id": key, "orgId": org, "name": key, "description": "d", "content": "c", "status": "active",
        "createdBy": "user-a", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }
    if generation is not None:
        node["generation"] = generation
    return node


async def _skill_with_edges(provider, org: str, key: str, generation: str | None) -> str:
    other = f"{key}-other"
    assert await provider.create_node_if_absent(key, SKILLS, _skill(org, key, generation))
    if await provider.get_document(other, SKILLS) is None:
        assert await provider.create_node_if_absent(other, SKILLS, _skill(org, other, "n"))
    assert await provider.batch_create_edges([
        {"from_id": key, "from_collection": SKILLS, "to_id": other, "to_collection": SKILLS, "type": "related", "createdAtTimestamp": 1},
        {"from_id": other, "from_collection": SKILLS, "to_id": key, "to_collection": SKILLS, "type": "requires", "createdAtTimestamp": 1},
    ], RELATION)
    return other


async def _edges_of(provider, key: str) -> tuple[int, int]:
    node_id = f"{SKILLS}/{key}"
    return (len(await provider.get_edges_from_node(node_id, RELATION)), len(await provider.get_edges_to_node(node_id, RELATION)))


async def test_delete_node_if_match_has_one_winner(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-delete-{trial}"
            other = await _skill_with_edges(pg_provider, org, key, "g1")
            assert await _edges_of(pg_provider, key) == (1, 1)
            results = await asyncio.gather(
                *(pg_provider.delete_node_if_match(key, SKILLS, {"generation": "g1", "updatedAtTimestamp": 1}) for _ in range(WRITERS)),
            )
            assert sum(bool(r) for r in results) == 1, (trial, results)
            assert await pg_provider.get_document(key, SKILLS) is None
            assert await _edges_of(pg_provider, key) == (0, 0)
            assert await pg_provider.get_document(other, SKILLS) is not None


async def test_delete_node_if_match_leaves_a_node_that_does_not_match(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        key = "cw-mismatch"
        await _skill_with_edges(pg_provider, org, key, "g2")
        before = await pg_provider.get_document(key, SKILLS)
        for expected in (
            {"generation": "g1", "updatedAtTimestamp": 1},
            {"generation": "g2", "updatedAtTimestamp": 2},
            {"generation": None, "updatedAtTimestamp": 1},
        ):
            assert not await pg_provider.delete_node_if_match(key, SKILLS, expected), expected
            assert await pg_provider.get_document(key, SKILLS) == before
            assert await _edges_of(pg_provider, key) == (1, 1)
        with pytest.raises(ValueError):
            await pg_provider.delete_node_if_match(key, SKILLS, {})
        legacy = "cw-legacy"
        await _skill_with_edges(pg_provider, org, legacy, None)
        assert await pg_provider.delete_node_if_match(legacy, SKILLS, {"generation": None, "updatedAtTimestamp": 1})
        assert await pg_provider.get_document(legacy, SKILLS) is None
        assert await _edges_of(pg_provider, legacy) == (0, 0)


async def test_a_delete_racing_a_re_create_never_removes_the_new_node(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        for trial in range(TRIALS):
            key = f"cw-recreate-{trial}"
            await _skill_with_edges(pg_provider, org, key, "g1")

            async def recreate(key: str = key) -> None:
                # Polls on purpose: the key frees whenever the winning delete commits.
                while not await pg_provider.create_node_if_absent(key, SKILLS, _skill(org, key, "g2")):  # noqa: ASYNC110
                    await asyncio.sleep(0)

            deletes = [pg_provider.delete_node_if_match(key, SKILLS, {"generation": "g1", "updatedAtTimestamp": 1}) for _ in range(WRITERS)]
            results = await asyncio.gather(*deletes, asyncio.wait_for(recreate(), timeout=60))
            assert sum(bool(r) for r in results[:WRITERS]) == 1, (trial, results)
            stored = await pg_provider.get_document(key, SKILLS)
            assert stored is not None and stored["generation"] == "g2", (trial, stored)


async def test_deleters_updaters_and_a_re_creator_leave_one_winner(pg_provider, org, org_ctx) -> None:
    rnd = random.Random(SEED)
    with org_ctx(org):
        for trial in range(TRIALS):
            await race_deleters_updaters_and_a_re_creator(pg_provider, org, f"cw-mixed-{trial}", rnd)


async def test_a_delete_in_the_callers_transaction_is_undone_by_its_rollback(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await delete_in_a_rolled_back_transaction(pg_provider, org, "cw-txn")


class _EdgeStatementFails:
    """A transaction whose edge delete fails, after the node delete ran."""

    def __init__(self, tx: object) -> None:
        self._tx = tx

    def __getattr__(self, name: str) -> object:
        return getattr(self._tx, name)

    async def execute(self, sql: str, *args: object) -> object:
        if "pipeshub:graph.delete_edges_of" in sql:
            raise RuntimeError("edge statement failed")
        return await self._tx.execute(sql, *args)


async def test_a_delete_that_fails_after_the_node_is_gone_changes_nothing(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        key = "cw-partway"
        await _skill_with_edges(pg_provider, org, key, "g1")
        before = (await pg_provider.get_document(key, SKILLS), await _edges_of(pg_provider, key))
        run = pg_provider._q

        async def failing(fn, transaction, *, write=False):  # noqa: ANN202
            return await run(lambda tx: fn(_EdgeStatementFails(tx)), transaction, write=write)

        pg_provider._q = failing
        try:
            with pytest.raises(RuntimeError):
                await pg_provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})
        finally:
            pg_provider._q = run
        assert (await pg_provider.get_document(key, SKILLS), await _edges_of(pg_provider, key)) == before
        assert await pg_provider.delete_node_if_match(key, SKILLS, {"generation": "g1"})
        assert await _edges_of(pg_provider, key) == (0, 0)
