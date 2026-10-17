"""The acceptance graph loads identically into both backends.

This is the precondition for every parity assertion that follows: if the two
stores do not hold the same graph, comparing query results proves nothing.
"""

from collections import Counter

import aiohttp
import pytest
from neo4j import AsyncGraphDatabase

from .fixture_graph import build_fixture
from .loaders import (
    EDGE_TARGETS,
    NEO4J_LABELS,
    NODE_TARGETS,
    load_into_arango,
    load_into_neo4j,
)

pytestmark = pytest.mark.integration


async def _arango_count(settings: dict, query: str) -> int:
    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    url = f"{settings['url'].rstrip('/')}/_db/{settings['db']}/_api/cursor"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json={"query": query}) as resp:
            body = await resp.json()
            if resp.status not in (200, 201):
                raise RuntimeError(f"AQL failed: {resp.status} {body}")
            return body["result"][0]


async def test_fixture_loads_into_both_backends(
    neo4j_provider, arango_provider, neo4j_settings, arango_settings
) -> None:
    nodes, edges = build_fixture()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, nodes, edges)

    ids = [n["id"] for n in nodes]
    edge_types = list(EDGE_TARGETS)

    # Counted per label and per type, not in total. A bare count is both
    # label-agnostic and endpoint-agnostic, so loading every Record as :Role or
    # writing every NODE_RELATION backwards leaves the totals matching — which
    # is precisely the failure the loader's own comment warns about.
    expected_nodes = Counter(n["kind"] for n in nodes)
    expected_edges = Counter(e["type"] for e in edges)
    kind_by_id = {n["id"]: n["kind"] for n in nodes}
    expected_app_to_rg = sum(
        1 for e in edges
        if e["type"] == "NODE_RELATION"
        and kind_by_id.get(e["from"]) == "App"
        and kind_by_id.get(e["to"]) == "RecordGroup"
    )

    driver = AsyncGraphDatabase.driver(
        neo4j_settings["uri"],
        auth=(neo4j_settings["username"], neo4j_settings["password"]),
    )
    try:
        async with driver.session(database=neo4j_settings["database"]) as session:
            result = await session.run(
                "MATCH (n) WHERE n.id IN $ids "
                "RETURN labels(n)[0] AS label, count(n) AS c",
                ids=ids,
            )
            neo4j_nodes = {r["label"]: r["c"] async for r in result}
            result = await session.run(
                "MATCH ()-[r]->() WHERE type(r) IN $types "
                "RETURN type(r) AS t, count(r) AS c",
                types=edge_types,
            )
            neo4j_edges = {r["t"]: r["c"] async for r in result}
            # The edge the named graph would refuse: App -> RecordGroup.
            result = await session.run(
                "MATCH (:App)-[r:NODE_RELATION]->(:RecordGroup) RETURN count(r) AS c"
            )
            neo4j_app_to_rg = (await result.single())["c"]
    finally:
        await driver.close()

    arango_nodes = {
        kind: await _arango_count(arango_settings, f"RETURN LENGTH({collection})")
        for kind, collection in NODE_TARGETS.items()
    }
    arango_edges = {
        edge_type: await _arango_count(arango_settings, f"RETURN LENGTH({collection})")
        for edge_type, collection in EDGE_TARGETS.items()
    }
    arango_app_to_rg = await _arango_count(
        arango_settings,
        "RETURN LENGTH(FOR e IN nodeRelations "
        "FILTER STARTS_WITH(e._from, 'apps/') AND STARTS_WITH(e._to, 'recordGroups/') "
        "RETURN 1)",
    )

    print(
        f"\n[load] fixture nodes={len(nodes)} edges={len(edges)} | "
        f"neo4j {sorted(neo4j_nodes.items())} | arango {sorted(arango_nodes.items())} | "
        f"App->RG neo4j={neo4j_app_to_rg} arango={arango_app_to_rg}"
    )

    expected_labels = {
        NEO4J_LABELS[kind]: count for kind, count in expected_nodes.items()
    }
    assert neo4j_nodes == expected_labels, "Neo4j node labels or counts are wrong"
    assert neo4j_edges == dict(expected_edges), "Neo4j edge types or counts are wrong"

    # Every declared collection is asserted, so a kind loaded into the wrong
    # one shows up as a pair of mismatches rather than cancelling out.
    assert arango_nodes == {
        kind: expected_nodes.get(kind, 0) for kind in NODE_TARGETS
    }, "Arango collections or counts are wrong"
    assert arango_edges == {
        edge_type: expected_edges.get(edge_type, 0) for edge_type in EDGE_TARGETS
    }, "Arango edge collections or counts are wrong"

    assert neo4j_app_to_rg == arango_app_to_rg == expected_app_to_rg > 0, (
        "App -> RecordGroup hierarchy edges must exist in both backends; the "
        "Arango named graph's edge definitions forbid them, which is why the "
        "loader writes through the document API"
    )
