"""Helpers shared by the replace_edges_to integration tests: seeding, edge inputs and the statement run through the driver."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from neo4j import SummaryCounters

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

USERS = CollectionNames.USERS.value
GROUPS = CollectionNames.GROUPS.value
RECORDS = CollectionNames.RECORDS.value
PERMISSION = CollectionNames.PERMISSION.value
SEED_BATCH = 20_000


async def seed_nodes(provider: Neo4jProvider, label: str, ids: list[str]) -> None:
    for i in range(0, len(ids), SEED_BATCH):
        await provider.client.execute_query(
            f"UNWIND $ids AS id CREATE (:{label} {{id: id}})", parameters={"ids": ids[i : i + SEED_BATCH]}
        )


def permissions(
    members: list[str], group: str, *, role: PermissionType = PermissionType.READ, from_collection: str = USERS,
    to_collection: str = GROUPS,
) -> list[dict]:
    """Fresh `Permission` objects each call: their timestamps differ from every earlier call."""
    return [
        Permission(type=role, entity_type=EntityType.USER).to_arango_permission(m, from_collection, group, to_collection)
        for m in members
    ]


async def run(provider: Neo4jProvider, statement: str, parameters: dict[str, Any]) -> SummaryCounters:
    """The statement through the driver in its own transaction; returns the result summary counters."""
    async with provider.client.driver.session(database="neo4j") as session:
        result = await session.run(f"{statement}\n RETURN count(*) AS done", parameters)
        records = [r async for r in result]
        assert [r["done"] for r in records] == [1]
        return (await result.consume()).counters


async def replace(provider: Neo4jProvider, group: str, edges: list[dict], *, to_collection: str = GROUPS) -> SummaryCounters:
    statement, parameters = provider._replace_edges_to_cypher(group, to_collection, edges, PERMISSION)
    return await run(provider, statement, parameters)


async def stored(provider: Neo4jProvider, group: str) -> dict[str, dict]:
    rows = await provider.client.execute_query(
        "MATCH (s)-[r:PERMISSION]->(:Group {id: $g}) RETURN labels(s)[0] + '|' + s.id AS k, properties(r) AS p",
        parameters={"g": group},
    )
    return {r["k"]: r["p"] for r in rows}


async def stored_count(provider: Neo4jProvider, group: str) -> int:
    rows = await provider.client.execute_query(
        "MATCH ()-[r:PERMISSION]->(:Group {id: $g}) RETURN count(r) AS n", parameters={"g": group}
    )
    return rows[0]["n"]


async def clear_prefix(provider: Neo4jProvider, prefix: str) -> None:
    while True:
        rows = await provider.client.execute_query(
            "MATCH (n) WHERE n.id STARTS WITH $p WITH n LIMIT 20000 DETACH DELETE n RETURN count(*) AS c",
            parameters={"p": prefix},
        )
        if not rows or not rows[0]["c"]:
            break
