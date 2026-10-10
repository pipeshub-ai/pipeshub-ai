"""Smoke checks for the parity harness itself.

These do not test the traversal. They prove the fixtures reach real engines and
report what each backend's ``connect()`` builds, because the traversal's fixture
loader has to work with whatever schema is actually created — in particular
Arango's named-graph edge definitions, which are narrower than this permission
model needs.
"""

import os

import aiohttp
import pytest

pytestmark = pytest.mark.integration


def _arango_base() -> tuple[str, aiohttp.BasicAuth, str]:
    url = os.environ.get("KH_PERM_ARANGO_URL", "http://localhost:8530").rstrip("/")
    auth = aiohttp.BasicAuth(
        os.environ.get("KH_PERM_ARANGO_USERNAME", "root"),
        os.environ.get("KH_PERM_ARANGO_PASSWORD", "khpermpass"),
    )
    db = os.environ.get("KH_PERM_ARANGO_DB", "kh_perm_test")
    return url, auth, db


async def test_neo4j_connects_and_initialises_schema(neo4j_provider) -> None:
    """The provider connected, the database is empty, and schema init ran."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        async with driver.session(database=os.environ["NEO4J_DATABASE"]) as session:
            result = await session.run(
                "MATCH (n) WHERE any(l IN labels(n) WHERE l IN ['App','RecordGroup','Record']) "
                "RETURN count(n) AS c"
            )
            graph_nodes = (await result.single())["c"]

            # ensure_schema() also seeds Departments reference data, so the
            # database is never literally empty after init — only free of the
            # node kinds the fixture graph owns.
            result = await session.run("MATCH (n:Departments) RETURN count(n) AS c")
            departments = (await result.single())["c"]

            constraints = [r.data() async for r in await session.run("SHOW CONSTRAINTS")]
            indexes = [r.data() async for r in await session.run("SHOW INDEXES")]
    finally:
        await driver.close()

    unique = [c for c in constraints if "UNIQUE" in str(c.get("type", "")).upper()]
    # A fresh Neo4j always reports two built-in token-lookup indexes, so only
    # growth beyond those shows that schema init actually ran.
    builtin_lookup_indexes = 2

    print(
        f"\n[neo4j] constraints={len(constraints)} (uniqueness={len(unique)}) "
        f"indexes={len(indexes)} departments={departments} graph_nodes={graph_nodes}"
    )
    assert graph_nodes == 0, (
        f"harness expects no App/RecordGroup/Record nodes before loading, found {graph_nodes}"
    )
    assert unique, "ensure_schema() should have created uniqueness constraints"
    assert len(indexes) > builtin_lookup_indexes, (
        f"ensure_schema() should have created indexes beyond the "
        f"{builtin_lookup_indexes} built-in lookup indexes, found {len(indexes)}"
    )


async def test_arango_connects_and_reports_graph_definitions(arango_provider) -> None:
    """Record which collections and edge definitions Arango's connect() created.

    The edge definitions decide whether the fixture loader can use the graph API
    at all: this model needs App -> RecordGroup and RecordGroup -> RecordGroup
    hierarchy edges, and inheritance pointing child -> parent.
    """
    url, auth, db = _arango_base()
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(f"{url}/_db/{db}/_api/collection?excludeSystem=true") as resp:
            assert resp.status == 200, await resp.text()
            collections = sorted(c["name"] for c in (await resp.json())["result"])

        async with session.get(f"{url}/_db/{db}/_api/gharial") as resp:
            graphs = (await resp.json()).get("graphs", []) if resp.status == 200 else []

    print(f"\n[arango] collections ({len(collections)}): {collections}")
    for graph in graphs:
        print(f"[arango] graph {graph.get('_key')!r} edge definitions:")
        for edge_def in graph.get("edgeDefinitions", []):
            print(
                f"    {edge_def['collection']}: "
                f"{edge_def['from']} -> {edge_def['to']}"
            )

    assert collections, "ArangoHTTPProvider.connect() should have created collections"
