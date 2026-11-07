"""Neo4j: ``replace_edges_to`` / ``replace_record_permissions`` move only the difference.

Runs the production statement through the driver, so the result summary counters can
be asserted. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD.

  cd backend/python && pytest tests/integration/graph_db/test_replace_edges_to_diffed_real_neo4j.py -m integration
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from app.models.permission import EntityType, PermissionType
from tests.integration.graph_db.replace_edges_support import (
    GROUPS,
    PERMISSION,
    RECORDS,
    clear_prefix,
    permissions,
    replace,
    run,
    seed_nodes,
    stored,
    stored_count,
)
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("replace-edges-diffed-neo4j-it")


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Neo4jProvider, str]]:
    try:
        provider = await connect_neo4j(logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    prefix = f"rd{uuid.uuid4().hex[:8]}"
    try:
        yield provider, prefix  # type: ignore[misc]
    finally:
        try:
            await clear_prefix(provider, prefix)
        except Exception:
            logger.warning("neo4j cleanup failed", exc_info=True)
        await provider.disconnect()


@pytest.fixture
async def world(neo4j: tuple[Neo4jProvider, str]) -> tuple[Neo4jProvider, list[str], str]:
    provider, prefix = neo4j
    users = [f"{prefix}-u{i}" for i in range(30)]
    group = f"{prefix}-g"
    await seed_nodes(provider, "User", users)
    await seed_nodes(provider, "Group", [group, f"{prefix}-g2"])
    return provider, users, group


class TestUnchangedResync:
    async def test_writes_nothing_and_keeps_timestamps(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users, group))
        before = await stored(provider, group)
        await asyncio.sleep(0.01)

        counters = await replace(provider, group, permissions(users, group))  # new timestamps

        assert (counters.relationships_created, counters.relationships_deleted) == (0, 0)
        # The group lock sets and removes one property; nothing else is written.
        assert counters.properties_set <= 2
        assert await stored(provider, group) == before

    async def test_changed_role_rewrites_only_that_edge(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users, group))
        before = await stored(provider, group)
        await asyncio.sleep(0.01)
        edges = permissions(users, group)
        edges[3] = permissions([users[3]], group, role=PermissionType.WRITE)[0]

        counters = await replace(provider, group, edges)

        assert (counters.relationships_created, counters.relationships_deleted) == (0, 0)
        after = await stored(provider, group)
        changed = f"User|{users[3]}"
        assert {k for k in after if after[k] != before[k]} == {changed}
        assert after[changed]["role"] == "WRITER"
        assert after[changed]["createdAtTimestamp"] == before[changed]["createdAtTimestamp"]
        assert after[changed]["updatedAtTimestamp"] > before[changed]["updatedAtTimestamp"]

    async def test_changed_type_is_semantic_too(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users[:3], group))
        edges = permissions(users[:3], group)
        edges[0]["type"] = EntityType.GROUP.value

        await replace(provider, group, edges)

        assert (await stored(provider, group))[f"User|{users[0]}"]["type"] == "GROUP"


class TestAddAndRemove:
    async def test_removed_deleted_added_merged(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users[:20], group))
        before = await stored(provider, group)

        counters = await replace(provider, group, permissions(users[10:30], group))

        assert (counters.relationships_created, counters.relationships_deleted) == (10, 10)
        after = await stored(provider, group)
        assert set(after) == {f"User|{u}" for u in users[10:30]}
        for u in users[10:20]:
            assert after[f"User|{u}"] == before[f"User|{u}"]

    async def test_fresh_group_gets_all_members(self, world) -> None:
        provider, users, group = world
        assert await stored_count(provider, group) == 0

        counters = await replace(provider, group, permissions(users, group))

        assert counters.relationships_created == len(users)
        assert await stored_count(provider, group) == len(users)

    async def test_empty_list_deletes_all(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users, group))

        counters = await replace(provider, group, [])

        assert counters.relationships_deleted == len(users)
        assert await stored_count(provider, group) == 0

    async def test_only_the_target_group_is_touched(self, world) -> None:
        provider, users, group = world
        other = group + "2"
        await replace(provider, other, permissions(users[:5], other))

        await replace(provider, group, permissions(users[:3], group))
        await replace(provider, group, [])

        assert await stored_count(provider, other) == 5


    async def test_a_member_listed_only_into_another_group_loses_its_edge_into_the_target(self, world) -> None:
        """The interface deletes every old edge into the target: an edge supplied for a second node of the
        same label keeps no old grant into the first."""
        provider, users, group = world
        other = group + "2"
        await replace(provider, group, permissions(users[:2], group))

        await replace(provider, group, permissions([users[0]], group) + permissions([users[1]], other))

        assert set(await stored(provider, group)) == {f"User|{users[0]}"}
        assert set(await stored(provider, other)) == {f"User|{users[1]}"}


class TestDuplicates:
    async def test_same_principal_twice_last_wins(self, world) -> None:
        provider, users, group = world
        edges = permissions([users[0]], group) + permissions([users[0]], group, role=PermissionType.OWNER)

        await replace(provider, group, edges)

        assert {k: v["role"] for k, v in (await stored(provider, group)).items()} == {f"User|{users[0]}": "OWNER"}

    async def test_stored_parallel_duplicate_is_healed(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users[:3], group))
        await provider.client.execute_query(
            "MATCH (u:User {id: $u}), (g:Group {id: $g}) CREATE (u)-[:PERMISSION {role: 'READER', type: 'USER'}]->(g)",
            parameters={"u": users[1], "g": group},
        )
        assert await stored_count(provider, group) == 4

        counters = await replace(provider, group, permissions(users[:3], group))

        assert counters.relationships_deleted == 1
        assert await stored_count(provider, group) == 3

    async def test_same_id_under_two_labels_is_kept_apart(self, neo4j) -> None:
        provider, prefix = neo4j
        shared, group = f"{prefix}-same", f"{prefix}-g"
        await seed_nodes(provider, "User", [shared])
        await seed_nodes(provider, "Group", [shared, group])
        both = permissions([shared], group) + permissions([shared], group, from_collection=GROUPS)
        await replace(provider, group, both)
        assert set(await stored(provider, group)) == {f"User|{shared}", f"Group|{shared}"}

        await replace(provider, group, permissions([shared], group))

        assert set(await stored(provider, group)) == {f"User|{shared}"}


class TestRecordPermissions:
    async def test_inherit_flip_with_unchanged_permissions_still_runs_once(self, neo4j) -> None:
        provider, prefix = neo4j
        users, record, rgroup = [f"{prefix}-u{i}" for i in range(3)], f"{prefix}-r", f"{prefix}-rg"
        await seed_nodes(provider, "User", users)
        await seed_nodes(provider, "Record", [record])
        await seed_nodes(provider, "RecordGroup", [rgroup])

        async def links() -> int:
            rows = await provider.client.execute_query(
                "MATCH (:Record {id: $r})-[l:INHERIT_PERMISSIONS]->(:RecordGroup) RETURN count(l) AS n",
                parameters={"r": record},
            )
            return rows[0]["n"]

        await provider.replace_record_permissions(
            record, permissions(users, record, to_collection=RECORDS), rgroup, inherit=True
        )
        assert await links() == 1
        await provider.replace_record_permissions(
            record, permissions(users, record, to_collection=RECORDS), rgroup, inherit=False
        )
        assert await links() == 0
        await provider.replace_record_permissions(
            record, permissions(users, record, to_collection=RECORDS), rgroup, inherit=True
        )
        assert await links() == 1

    async def test_unchanged_resync_with_inherit_counts(self, neo4j) -> None:
        provider, prefix = neo4j
        users, record, rgroup = [f"{prefix}-u{i}" for i in range(3)], f"{prefix}-r", f"{prefix}-rg"
        await seed_nodes(provider, "User", users)
        await seed_nodes(provider, "Record", [record])
        await seed_nodes(provider, "RecordGroup", [rgroup])
        await provider.replace_record_permissions(
            record, permissions(users, record, to_collection=RECORDS), rgroup, inherit=True
        )
        before = await stored_to_record(provider, record)

        await provider.replace_record_permissions(
            record, permissions(users, record, to_collection=RECORDS), rgroup, inherit=True
        )

        assert await stored_to_record(provider, record) == before


async def stored_to_record(provider: Neo4jProvider, record: str) -> dict[str, dict]:
    rows = await provider.client.execute_query(
        "MATCH (s)-[r:PERMISSION]->(:Record {id: $r}) RETURN s.id AS k, properties(r) AS p", parameters={"r": record}
    )
    return {r["k"]: r["p"] for r in rows}


async def blocked_on_a_lock(provider: Neo4jProvider, timeout: float = 30.0) -> bool:
    """Whether a transaction reports waiting on a lock within ``timeout``: the server's own view of it."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        rows = await provider.client.execute_query("SHOW TRANSACTIONS YIELD status RETURN status")
        if any(str(r.get("status", "")).startswith("Blocked") for r in rows):
            return True
        await asyncio.sleep(0.05)
    return False


class TestConcurrentOverlap:
    """A holds its open transaction after its statement; B starts and must wait for the group lock."""

    @pytest.mark.parametrize("a_first", [True, False])
    async def test_final_state_is_the_last_committed_request(self, world, a_first: bool) -> None:
        provider, users, group = world
        x, y, z = users[:3]
        await replace(provider, group, permissions([x], group))
        want_a, want_b = [x, y], [z]
        first, second = (want_a, want_b) if a_first else (want_b, want_a)

        async with provider.client.driver.session(database="neo4j") as s1, provider.client.driver.session(
            database="neo4j"
        ) as s2:
            tx1 = await s1.begin_transaction()
            tx2 = None
            second_run = None
            try:
                stmt, params = provider._replace_edges_to_cypher(group, GROUPS, permissions(first, group), PERMISSION)
                await (await tx1.run(f"{stmt}\n RETURN count(*) AS done", params)).consume()
                tx2 = await s2.begin_transaction()
                stmt, params = provider._replace_edges_to_cypher(group, GROUPS, permissions(second, group), PERMISSION)

                async def second_replace() -> None:
                    await (await tx2.run(f"{stmt}\n RETURN count(*) AS done", params)).consume()

                second_run = asyncio.create_task(second_replace())
                assert await blocked_on_a_lock(provider), "the second replace must wait for the group lock"
                assert not second_run.done()
                await tx1.commit()
                await second_run
                await tx2.commit()
            finally:
                # A failure above must not close the sessions under a statement that still runs in them.
                if second_run is not None and not second_run.done():
                    second_run.cancel()
                    await asyncio.gather(second_run, return_exceptions=True)
                for tx in (tx2, tx1):
                    if tx is not None and not tx.closed():
                        await tx.rollback()

        assert set(await stored(provider, group)) == {f"User|{u}" for u in second}


class TestAtomicity:
    async def test_a_failure_in_the_statement_leaves_previous_edges(self, world) -> None:
        provider, users, group = world
        await replace(provider, group, permissions(users[:10], group))
        before = await stored(provider, group)
        stmt, params = provider._replace_edges_to_cypher(group, GROUPS, permissions(users[10:20], group), PERMISSION)

        with pytest.raises(Exception, match="/ by zero"):
            await run(provider, f"{stmt}\n WITH count(*) AS _ UNWIND [1, 0] AS x WITH 1 / x AS boom", params)

        assert await stored(provider, group) == before


class TestPlan:
    async def test_no_collect_over_old_edges_and_one_end_row(self, world) -> None:
        provider, users, group = world
        stmt, params = provider._replace_edges_to_cypher(group, GROUPS, permissions(users, group), PERMISSION)
        async with provider.client.driver.session(database="neo4j") as session:
            result = await session.run(f"EXPLAIN {stmt}\n RETURN count(*) AS done", params)
            plan = (await result.consume()).plan

        operators: list[str] = []

        def walk(node: dict) -> None:
            operators.append(node["operatorType"].split("@")[0])
            for child in node.get("children", []):
                walk(child)

        walk(plan)
        logger.info("operators: %s", sorted(set(operators)))
        assert not [o for o in operators if o.startswith("Collect") or o == "NodeByIdSeek"], operators
        # The end row count of the statement is 1, asserted by `run` on every call above.


def test_permission_objects_get_distinct_timestamps() -> None:
    first = permissions(["a"], "g")[0]["updatedAtTimestamp"]
    time.sleep(0.005)
    assert permissions(["a"], "g")[0]["updatedAtTimestamp"] > first
