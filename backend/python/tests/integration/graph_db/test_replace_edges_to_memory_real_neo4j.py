"""Neo4j at the small-VM memory preset: how big a group `replace_edges_to` can move.

Needs a neo4j 5.26 server started at the preset and NEO4J_IT_PRESET=small:

  docker run -d --name <own-name> --memory 2688m --memory-swap 2688m -p 127.0.0.1:27687:7687 \\
    -e NEO4J_AUTH=neo4j/<password> -e NEO4J_server_memory_heap_initial__size=256M \\
    -e NEO4J_server_memory_heap_max__size=1536M -e NEO4J_server_memory_pagecache_size=256M neo4j:5.26.0
  cd backend/python && NEO4J_IT_PRESET=small NEO4J_IT_URI=bolt://localhost:27687 NEO4J_IT_PASSWORD=<password> \\
    pytest tests/integration/graph_db/test_replace_edges_to_memory_real_neo4j.py -m integration -s

Each case seeds its own group with N old edges and runs the production statement through the
driver. The memory table (`test_memory_table`) polls SHOW TRANSACTIONS; set NEO4J_IT_MEASURE_OUT
to a file path to keep it as JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from neo4j.exceptions import TransientError

from app.config.constants.arangodb import CollectionNames
from app.models.permission import PermissionType
from tests.integration.graph_db.replace_edges_support import (
    GROUPS,
    PERMISSION,
    clear_prefix,
    permissions,
    seed_nodes,
)
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from neo4j import AsyncSession

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.timeout(3000),
    pytest.mark.skipif(os.environ.get("NEO4J_IT_PRESET") != "small", reason="needs a neo4j at the small preset"),
]

logger = logging.getLogger("replace-edges-memory-neo4j-it")
POOL_ERROR = "Neo.TransientError.General.MemoryPoolOutOfMemoryError"
OLD_PROPS = "{role: 'READER', type: 'USER', createdAtTimestamp: 1, updatedAtTimestamp: 2}"
KIB = 1024
# The largest pair of concurrent full swaps that passed 3 of 3 fresh containers at the small preset; 70k each failed.
PAIR_THAT_FITS = int(os.environ.get("NEO4J_IT_PAIR_N", "60000"))


class Pair:
    def __init__(self, provider: Neo4jProvider, prefix: str) -> None:
        self.provider, self.prefix, self.users = provider, prefix, 0

    def uid(self, i: int) -> str:
        return f"{self.prefix}-u{i}"

    def ids(self, start: int, stop: int) -> list[str]:
        return [self.uid(i) for i in range(start, stop)]

    async def ensure_users(self, n: int) -> None:
        while self.users < n:
            stop = min(n, self.users + 50_000)
            await self.provider.client.execute_query(
                "UNWIND range($a, $b) AS i CREATE (:User {id: $p + '-u' + toString(i)})",
                parameters={"a": self.users, "b": stop - 1, "p": self.prefix},
            )
            self.users = stop

    async def group(self, name: str, start: int = 0, stop: int = 0) -> str:
        """A group seeded with edges from users start..stop-1, with fixed old timestamps."""
        group = f"{self.prefix}-{name}"
        await seed_nodes(self.provider, "Group", [group])
        await self.ensure_users(stop)
        for a in range(start, stop, 20_000):
            await self.provider.client.execute_query(
                f"""MATCH (g:Group {{id: $g}})
                UNWIND range($a, $b) AS i
                MATCH (u:User {{id: $p + '-u' + toString(i)}})
                CREATE (u)-[:PERMISSION {OLD_PROPS}]->(g)""",
                parameters={"g": group, "a": a, "b": min(stop, a + 20_000) - 1, "p": self.prefix},
            )
        return group

    async def source_range(self, group: str) -> tuple[int, int, int]:
        """(count, lowest, highest) of the user number of the edges into the group."""
        rows = await self.provider.client.execute_query(
            """MATCH (s:User)-[r:PERMISSION]->(:Group {id: $g})
            WITH toInteger(substring(s.id, size($p) + 2)) AS n
            RETURN count(n) AS c, min(n) AS lo, max(n) AS hi""",
            parameters={"g": group, "p": self.prefix},
        )
        return rows[0]["c"], rows[0]["lo"], rows[0]["hi"]

    async def untouched(self, group: str) -> int:
        """Edges still carrying the seeded timestamps."""
        rows = await self.provider.client.execute_query(
            "MATCH (:User)-[r:PERMISSION]->(:Group {id: $g}) "
            "WHERE r.createdAtTimestamp = 1 AND r.updatedAtTimestamp = 2 RETURN count(r) AS n",
            parameters={"g": group},
        )
        return rows[0]["n"]

    def statement(self, group: str, start: int, stop: int, **kw) -> tuple[str, dict]:
        edges = permissions(self.ids(start, stop), group, **kw)
        return self.provider._replace_edges_to_cypher(group, GROUPS, edges, PERMISSION)


async def run_measured(
    provider: Neo4jProvider, statement: str, parameters: dict, marker: str, *, poll: float = 0.05
) -> dict:
    """Run the statement in an explicit transaction; peak pool bytes of that transaction, polled from SHOW TRANSACTIONS."""
    peaks = {"pool_bytes": 0, "query_bytes": 0}
    done = asyncio.Event()

    async def watch() -> None:
        async with provider.client.driver.session(database="system") as session:
            while not done.is_set():
                try:
                    result = await session.run(
                        "SHOW TRANSACTIONS YIELD currentQuery, estimatedUsedHeapMemory, currentQueryAllocatedBytes "
                        "WHERE currentQuery CONTAINS $m AND NOT currentQuery STARTS WITH 'SHOW' "
                        "RETURN estimatedUsedHeapMemory AS pool, currentQueryAllocatedBytes AS q",
                        {"m": marker},
                    )
                    rows = [r async for r in result]
                except TransientError:  # the poll is itself a victim when the pool is full
                    await asyncio.sleep(poll)
                    continue
                for row in rows:
                    peaks["pool_bytes"] = max(peaks["pool_bytes"], row["pool"] or 0)
                    peaks["query_bytes"] = max(peaks["query_bytes"], row["q"] or 0)
                await asyncio.sleep(poll)

    watcher = asyncio.create_task(watch())
    outcome: dict = {}
    try:
        async with provider.client.driver.session(database="neo4j") as session:
            tx = await session.begin_transaction()
            try:
                result = await tx.run(f"/* {marker} */ {statement}\n RETURN count(*) AS done", parameters)
                summary = await result.consume()
                await tx.commit()
                c = summary.counters
                outcome = {
                    "created": c.relationships_created,
                    "deleted": c.relationships_deleted,
                    "properties_set": c.properties_set,
                }
            except TransientError as exc:
                outcome = {"error": exc.code}
    finally:
        done.set()
        await watcher
    logger.info("%s: %s", marker, {**outcome, **peaks})
    return {**outcome, **peaks}


@pytest.fixture
async def pair(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Pair]:
    try:
        provider = await connect_neo4j(logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    p = Pair(provider, f"rm{uuid.uuid4().hex[:8]}")
    try:
        yield p
    finally:
        try:
            await clear_prefix(provider, p.prefix)
        except Exception:
            logger.warning("neo4j cleanup failed", exc_info=True)
        await provider.disconnect()


class TestDiffedReplaceAtThePreset:
    async def test_a_unchanged_200k_resync_writes_nothing(self, pair: Pair) -> None:
        group = await pair.group("a", 0, 200_000)
        stmt, params = pair.statement(group, 0, 200_000)  # new Permission objects: new timestamps

        out = await run_measured(pair.provider, stmt, params, f"a{pair.prefix}")

        assert "error" not in out, out
        assert (out["created"], out["deleted"]) == (0, 0)
        assert out["properties_set"] <= 2  # the group lock
        assert await pair.untouched(group) == 200_000  # no timestamp changed
        logger.info("unchanged 200k: %s", out)

    async def test_b_half_churn_of_200k(self, pair: Pair) -> None:
        group = await pair.group("b", 0, 200_000)
        await pair.ensure_users(300_000)
        stmt, params = pair.statement(group, 100_000, 300_000)

        out = await run_measured(pair.provider, stmt, params, f"b{pair.prefix}")

        assert "error" not in out, out
        assert (out["created"], out["deleted"]) == (100_000, 100_000)
        assert await pair.source_range(group) == (200_000, 100_000, 299_999)
        logger.info("50%% churn of 200k: %s", out)

    async def test_c_full_swap_of_100k(self, pair: Pair) -> None:
        group = await pair.group("c", 0, 100_000)
        await pair.ensure_users(200_000)
        stmt, params = pair.statement(group, 100_000, 200_000)

        out = await run_measured(pair.provider, stmt, params, f"c{pair.prefix}")

        assert "error" not in out, out
        assert (out["created"], out["deleted"]) == (100_000, 100_000)
        assert await pair.source_range(group) == (100_000, 100_000, 199_999)
        logger.info("full swap 100k: %s", out)

    async def _concurrent_swaps(self, pair: Pair, n: int) -> tuple[list[str], list[dict], list[tuple[int, int, int]]]:
        """Two full swaps of n members each, run together over disjoint members (a shared member would serialise them)."""
        groups = [await pair.group("f1", 0, n), await pair.group("f2", 2 * n, 3 * n)]
        await pair.ensure_users(4 * n)
        runs = [pair.statement(groups[0], n, 2 * n), pair.statement(groups[1], 3 * n, 4 * n)]
        outs = await asyncio.gather(
            *(run_measured(pair.provider, s, p, f"f{i}{pair.prefix}") for i, (s, p) in enumerate(runs))
        )
        logger.info("concurrent pair of %d: %s", n, outs)
        old = [(n, 0, n - 1), (n, 2 * n, 3 * n - 1)]
        new = [(n, n, 2 * n - 1), (n, 3 * n, 4 * n - 1)]
        for group, out, before, after in zip(groups, outs, old, new, strict=True):
            if "error" in out:
                assert out["error"] == POOL_ERROR, out
                assert await pair.source_range(group) == before  # previous edges intact: count and members
            else:
                assert await pair.source_range(group) == after
        return groups, outs, old

    async def test_f_two_concurrent_100k_swaps(self, pair: Pair) -> None:
        """Safety, not capacity: each swap commits whole or fails with the pool error and leaves its group as it was.

        How many fail depends on the commit-time peak of both statements together, so no count is asserted
        (see `test_f_two_concurrent_swaps_that_fit`).
        """
        await self._concurrent_swaps(pair, 100_000)

    async def test_f_two_concurrent_swaps_that_fit(self, pair: Pair) -> None:
        """Both succeed at the size measured to pass together on the preset (NEO4J_IT_PAIR_N, in steps of 10k)."""
        _, outs, _ = await self._concurrent_swaps(pair, PAIR_THAT_FITS)
        assert all("error" not in o for o in outs), outs

    async def test_f_overloaded_triple_fails_only_with_the_pool_error(self, pair: Pair) -> None:
        groups = [await pair.group(f"t{i}", i * 10, i * 10 + 10) for i in range(3)]
        await pair.ensure_users(600_030)
        runs = [pair.statement(g, 30 + i * 200_000, 30 + (i + 1) * 200_000) for i, g in enumerate(groups)]

        outs = await asyncio.gather(
            *(run_measured(pair.provider, s, p, f"t{i}{pair.prefix}") for i, (s, p) in enumerate(runs))
        )

        logger.info("overloaded triple: %s", outs)
        for i, (group, out) in enumerate(zip(groups, outs, strict=True)):
            if "error" in out:
                assert out["error"] == POOL_ERROR, out
                assert await pair.source_range(group) == (10, i * 10, i * 10 + 9)  # previous edges intact
            else:
                assert (await pair.source_range(group))[0] == 200_000

    async def test_h_small_writes_succeed_while_a_100k_replace_runs(self, pair: Pair) -> None:
        group = await pair.group("h", 0, 100_000)
        await pair.ensure_users(200_000)
        victim = await pair.group("victim", 0, 1)
        stmt, params = pair.statement(group, 100_000, 200_000)
        replace = asyncio.create_task(run_measured(pair.provider, stmt, params, f"h{pair.prefix}"))
        users = CollectionNames.USERS.value
        calls = failures = 0

        async def attempt(i: int) -> None:
            nonlocal failures
            nodes = [{"id": f"{pair.prefix}-v{i}-{j}", "email": f"v{j}@x"} for j in range(200)]
            edge = permissions([pair.uid(5)], victim)  # one edge: a batch_create_edges victim
            for _ in range(20):  # bounded retry, as the production retry wrapper does
                try:
                    assert await pair.provider.batch_upsert_nodes(nodes, users)
                    await pair.provider.batch_create_edges(edge, PERMISSION)
                    return
                except TransientError:
                    failures += 1
                    await asyncio.sleep(1)
            raise AssertionError("victim statement never succeeded")

        while not replace.done():
            calls += 1
            await attempt(calls)
            await asyncio.sleep(0.5)
        await attempt(calls + 1)  # and after the replace has ended
        out = await replace

        logger.info("victims: %d rounds, %d pool failures; replace %s", calls, failures, out)
        assert "error" not in out, out


SCALES = (20_000, 60_000, 100_000)


async def heap_used(provider: Neo4jProvider) -> int:
    rows = await provider.client.execute_query(
        "CALL dbms.queryJmx('java.lang:type=Memory') YIELD attributes "
        "RETURN attributes.HeapMemoryUsage.value.properties.used AS used"
    )
    return rows[0]["used"]


async def heap_floor(provider: Neo4jProvider, session: AsyncSession, seconds: float = 6.0) -> int:
    """Lowest heap used while garbage is generated to force young collections: close to the live set."""
    lowest = await heap_used(provider)
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        await (await session.run("UNWIND range(1, 300000) AS i WITH toString(i) AS s RETURN count(s)")).consume()
        lowest = min(lowest, await heap_used(provider))
    return lowest


class TestMemoryTable:
    async def test_memory_table(self, pair: Pair) -> None:
        """Pool bytes per removed, added, retained-unchanged and retained-changed member; heap of the key list."""
        provider = pair.provider
        table: dict = {"removed": {}, "added": {}, "retained_unchanged": {}, "retained_changed": {}}
        await pair.ensure_users(max(SCALES) + 1)
        keep_one = 1  # the new list keeps user 0 so the group is not emptied by the "empty list" shape
        for n in SCALES:
            g = await pair.group(f"rm{n}", 0, n)
            s, p = pair.statement(g, 0, keep_one)
            table["removed"][n] = await run_measured(provider, s, p, f"rm{n}{pair.prefix}")
            g = await pair.group(f"ad{n}", 0, 1)
            s, p = pair.statement(g, 0, n)
            table["added"][n] = await run_measured(provider, s, p, f"ad{n}{pair.prefix}")
            g = await pair.group(f"un{n}", 0, n)
            s, p = pair.statement(g, 0, n)
            table["retained_unchanged"][n] = await run_measured(provider, s, p, f"un{n}{pair.prefix}")
            g = await pair.group(f"ch{n}", 0, n)
            s, p = pair.statement(g, 0, n, role=PermissionType.WRITE)
            table["retained_changed"][n] = await run_measured(provider, s, p, f"ch{n}{pair.prefix}")

        def per_member(kind: str) -> float:
            lo, hi = SCALES[0], SCALES[-1]
            return (table[kind][hi]["pool_bytes"] - table[kind][lo]["pool_bytes"]) / (hi - lo)

        table["bytes_per_member"] = {k: round(per_member(k), 1) for k in list(table) if k != "bytes_per_member"}

        # Does SET r = <equal values> write? Compare the counters of the same SET on 1,000 edges.
        g = await pair.group("eq", 0, 1000)
        async with provider.client.driver.session(database="neo4j") as session:
            result = await session.run(
                "MATCH (:User)-[r:PERMISSION]->(:Group {id: $g}) SET r = properties(r)", {"g": g}
            )
            table["set_equal_values_properties_set"] = (await result.consume()).counters.properties_set

        text = json.dumps(table, indent=2, default=str)
        print("\nMEMORY TABLE\n" + text)
        if out := os.environ.get("NEO4J_IT_MEASURE_OUT"):
            with open(out, "w") as fh:
                fh.write(text)
        assert table["bytes_per_member"]["removed"] > 0

    async def test_heap_for_the_key_list(self, pair: Pair) -> None:
        """Heap outside the pool held by the decoded N-key list of statements waiting on the group lock."""
        provider = pair.provider
        # Heap outside the pool: the decoded N-key list held by statements blocked on the group lock.
        heap: dict = {}
        async with provider.client.driver.session(database="neo4j") as probe:
            baseline = await heap_floor(provider, probe)
            for n in (100_000, 200_000, 500_000):
                await pair.ensure_users(n)
                group = await pair.group(f"hp{n}", 0, 1)
                holders = 1
                stmts = [pair.statement(group, 0, n) for _ in range(holders)]
                async with provider.client.driver.session(database="neo4j") as locker:
                    lock_tx = await locker.begin_transaction()
                    await (
                        await lock_tx.run(
                            "MATCH (g:Group {id: $g}) SET g.matchLock = true REMOVE g.matchLock", {"g": group}
                        )
                    ).consume()
                    sessions = [provider.client.driver.session(database="neo4j") for _ in stmts]
                    txs = [await s.begin_transaction() for s in sessions]
                    waits = [
                        asyncio.create_task(_consume(tx, f"{s}\n RETURN count(*) AS done", p))
                        for tx, (s, p) in zip(txs, stmts, strict=True)
                    ]
                    await asyncio.sleep(3)
                    held = await heap_floor(provider, probe)
                    await lock_tx.rollback()
                    # The waiting statements are abandoned, not run: each would hold the group lock to its commit.
                    for task in waits:
                        task.cancel()
                    await asyncio.gather(*waits, return_exceptions=True)
                    for s in sessions:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(s.close(), 30)
                heap[n] = {
                    "baseline_floor": baseline,
                    "held_floor": held,
                    "bytes_per_key": round((held - baseline) / (holders * n), 1),
                }
                logger.info("key list of %d held by %d waiting statement(s): %s", n, holders, heap[n])

        text = json.dumps(heap, indent=2, default=str)
        print("\nHEAP FOR THE KEY LIST\n" + text)
        if out := os.environ.get("NEO4J_IT_MEASURE_OUT"):
            with open(out + ".heap", "w") as fh:
                fh.write(text)
        assert heap


async def _consume(tx, statement: str, parameters: dict) -> None:
    await (await tx.run(statement, parameters)).consume()
