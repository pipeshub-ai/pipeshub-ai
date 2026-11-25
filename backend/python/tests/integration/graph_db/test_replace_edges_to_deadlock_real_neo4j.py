"""Concurrent group syncs whose groups share members must not deadlock on a real Neo4j.

`on_new_user_groups` replaces a group's members with `replace_edges_to`: one statement that
deletes the old member edges and merges the new ones. Edges are locked at both ends, and a
member with many edges (a dense node) also takes its relationship group's lock, exclusively to
delete one. Groups synced at the same time (the hourly syncs of an org's connectors) share
most of their members, so their replaces must take those locks in one order, or two of them
each hold what the other waits for and Neo4j aborts one with DeadlockDetected
(`NODE_RELATIONSHIP_GROUP_DELETE`).

    docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d --wait neo4j-graph-it
    cd backend/python && pytest tests/integration/graph_db/test_replace_edges_to_deadlock_real_neo4j.py -m integration
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from neo4j.exceptions import TransientError

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import AppUser, AppUserGroup
from tests.integration.graph_db.replace_edges_support import (
    permissions,
    replace,
    seed_nodes,
)
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

MEMBERS = 120
EDGES_PER_MEMBER = 60  # past Neo4j's dense-node threshold of 50 relationships
GROUP_COUNT = 6
ROUNDS = 15
SYNC_ROUNDS = 8
SHARE = 0.6


@pytest.fixture
async def provider(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jProvider]:
    try:
        provider = await connect_neo4j(logging.getLogger("replace-deadlock-it"), monkeypatch)
    except Exception as e:
        backend_unavailable("neo4j", e)
    yield provider
    await provider.disconnect()


async def _seed(provider: Neo4jProvider, run_id: str) -> tuple[list[str], list[str]]:
    """Dense members (each with edges to records) and groups that share them."""
    members = [f"{run_id}-u{i:03d}" for i in range(MEMBERS)]
    records = [f"{run_id}-r{i:03d}" for i in range(EDGES_PER_MEMBER)]
    groups = [f"{run_id}-g{i}" for i in range(GROUP_COUNT)]
    await seed_nodes(provider, "User", members)
    await seed_nodes(provider, "Record", records)
    await seed_nodes(provider, "Group", groups)
    await provider.client.execute_query(
        "UNWIND $members AS m MATCH (u:User {id: m}) SET u.email = m + '@example.com', u.orgId = $org "
        "WITH u UNWIND $records AS r MATCH (rec:Record {id: r}) "
        "CREATE (u)-[:PERMISSION {type: 'USER', role: 'READER'}]->(rec)",
        parameters={"members": members, "records": records, "org": run_id},
    )
    return members, groups


async def _cleanup(provider: Neo4jProvider, run_id: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.id STARTS WITH $run_id OR n.orgId = $run_id DETACH DELETE n", parameters={"run_id": run_id}
    )


def _lock_types(deadlocks: list[str]) -> Counter[str]:
    """The lock each deadlock names, e.g. NODE_RELATIONSHIP_GROUP_DELETE."""
    found = (re.search(r"\b([A-Z_]+)\(\d+\)", d) for d in deadlocks)
    return Counter(m.group(1) if m else "other" for m in found)


async def test_concurrent_group_replaces_sharing_dense_members_do_not_deadlock(provider: Neo4jProvider) -> None:
    """Each replace alone in its own transaction, with no retry: every deadlock is counted."""
    run_id = f"dl-{uuid.uuid4().hex[:8]}"
    try:
        members, groups = await _seed(provider, run_id)
        # Every group starts with members, so each replace also deletes edges.
        await asyncio.gather(*[replace(provider, g, permissions(members[: int(MEMBERS * SHARE)], g)) for g in groups])
        rng = random.Random(7)
        deadlocks: list[str] = []

        async def one(group: str, new_members: list[str]) -> None:
            try:
                await replace(provider, group, permissions(new_members, group))
            except TransientError as e:
                if "DeadlockDetected" not in str(e):
                    raise
                deadlocks.append(str(e))

        for _ in range(ROUNDS):
            await asyncio.gather(*[one(g, rng.sample(members, int(MEMBERS * SHARE))) for g in groups])

        assert not deadlocks, (
            f"{len(deadlocks)} of {ROUNDS * GROUP_COUNT} concurrent replaces deadlocked; "
            f"locks: {dict(_lock_types(deadlocks))}; first: {deadlocks[0][:400]}"
        )
    finally:
        await _cleanup(provider, run_id)


class _DeadlockWarnings(logging.Handler):
    """The retries `retry_on_deadlock` reports: each is a deadlock a sync had to run again."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "Deadlock" in message or "DeadlockDetected" in message:
            self.messages.append(message)


async def test_concurrent_syncs_of_overlapping_group_batches_do_not_deadlock(provider: Neo4jProvider) -> None:
    """Through on_new_user_groups, as connectors call it: batches of groups, several syncs at once."""
    run_id = f"dl-{uuid.uuid4().hex[:8]}"
    connector_id = f"{run_id}-conn"
    logger = logging.getLogger("replace-deadlock-it")
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, provider), MagicMock())
    processor.org_id = run_id
    retries = _DeadlockWarnings()
    watched = [logger, logging.getLogger(DataSourceEntitiesProcessor.__module__)]
    for log in watched:
        log.addHandler(retries)
    try:
        members, _ = await _seed(provider, run_id)

        def group(i: int, member_ids: list[str]) -> tuple[AppUserGroup, list[AppUser]]:
            return (
                AppUserGroup(app_name=Connectors.SLACK, connector_id=connector_id, source_user_group_id=f"grp-{i}", name=f"Group {i}"),
                [AppUser(app_name=Connectors.SLACK, connector_id=connector_id, source_user_id=m, email=f"{m}@example.com", full_name=m)
                 for m in member_ids],
            )

        await processor.on_new_user_groups([group(i, members[: int(MEMBERS * SHARE)]) for i in range(GROUP_COUNT)])
        rng = random.Random(11)
        for _ in range(SYNC_ROUNDS):
            # Syncs over the same groups, in opposite orders.
            orders = [[0, 1, 2], [2, 1, 0], [3, 4, 5], [5, 4, 3]]
            await asyncio.gather(*[
                processor.on_new_user_groups([group(i, rng.sample(members, int(MEMBERS * SHARE))) for i in order])
                for order in orders
            ])

        assert not retries.messages, f"{len(retries.messages)} deadlock retries; first: {retries.messages[0][:400]}"
    finally:
        for log in watched:
            log.removeHandler(retries)
        await _cleanup(provider, run_id)


async def test_group_replaces_still_store_exactly_the_given_members(provider: Neo4jProvider) -> None:
    """The order the locks are taken in changes nothing about what a replace stores."""
    run_id = f"dl-{uuid.uuid4().hex[:8]}"
    try:
        members, groups = await _seed(provider, run_id)
        group = groups[0]
        first, second = members[:70], members[40:110]
        await replace(provider, group, permissions(first, group))
        await replace(provider, group, permissions(second, group))
        rows = await provider.client.execute_query(
            "MATCH (u:User)-[:PERMISSION]->(:Group {id: $g}) RETURN u.id AS id", parameters={"g": group}
        )
        assert sorted(r["id"] for r in rows) == sorted(second)
        # The members' edges to records are untouched.
        rows = await provider.client.execute_query(
            "MATCH (u:User)-[:PERMISSION]->(:Record) WHERE u.id STARTS WITH $p RETURN count(*) AS n", parameters={"p": run_id}
        )
        assert rows[0]["n"] == MEMBERS * EDGES_PER_MEMBER
    finally:
        await _cleanup(provider, run_id)
