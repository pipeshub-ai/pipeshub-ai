"""The fixture graph holds the shapes it is meant to hold.

Counts alone prove nothing: a graph can hold the right number of nodes and
still get Example 1 backwards. These assertions pin the shapes the traversal
will be judged against, so that a later failure is read as a query bug rather
than sending us back to re-examine the data.

Everything is asserted through `loaded_graph`, which loads into both backends
after the per-module wipe.
"""

import pytest
from neo4j import AsyncGraphDatabase

pytestmark = pytest.mark.integration


async def _rows(settings: dict, query: str, **params) -> list[dict]:
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            result = await session.run(query, **params)
            return [r.data() async for r in result]
    finally:
        await driver.close()


async def _one(settings: dict, query: str, **params):
    rows = await _rows(settings, query, **params)
    assert rows, f"query returned no rows: {query.strip().splitlines()[0]}"
    return next(iter(rows[0].values()))


async def test_graph_is_actually_loaded(loaded_graph, neo4j_settings) -> None:
    """Guards the wipe-at-setup ordering: a module must load its own graph."""
    nodes = await _one(
        neo4j_settings,
        "MATCH (n) WHERE any(l IN labels(n) WHERE l IN ['App','RecordGroup','Record']) "
        "RETURN count(n) AS c",
    )
    edges = await _one(
        neo4j_settings, "MATCH ()-[r:NODE_RELATION]->() RETURN count(r) AS c"
    )
    print(f"\n[semantics] graph nodes={nodes} NODE_RELATION edges={edges}")
    assert nodes > 0 and edges > 0, "loaded_graph did not populate Neo4j"


async def test_example_one_shapes(loaded_graph, neo4j_settings) -> None:
    """Example 1: 4 is restricted with no grant, 6 sits beneath it."""
    r4 = (await _rows(
        neo4j_settings,
        "MATCH (n:Record {id:'ex1-r4'}) RETURN n.accessRule AS rule",
    ))[0]
    assert r4 == {"rule": "RESTRICTED"}

    inherits = await _one(
        neo4j_settings,
        "MATCH (:Record {id:'ex1-r4'})-[r:INHERIT_PERMISSIONS]->(:RecordGroup {id:'ex1-rg1'}) "
        "RETURN count(r) AS c",
    )
    assert inherits == 1, "ex1-r4 must inherit from its space (restricted AND inheriting)"

    grants = await _one(
        neo4j_settings,
        "MATCH (g)-[p:PERMISSION]->(:Record {id:'ex1-r4'}) RETURN count(p) AS c",
    )
    assert grants == 0, "ex1-r4 must hold no grant — that is what makes it invisible"

    below = await _one(
        neo4j_settings,
        "MATCH (:Record {id:'ex1-r4'})-[r:NODE_RELATION]->(:Record {id:'ex1-r6'}) "
        "RETURN count(r) AS c",
    )
    assert below == 1, "ex1-r6 must hang beneath the restricted page"


async def test_example_two_gap(loaded_graph, neo4j_settings) -> None:
    """Example 2: 3 is the gap, 6 is granted directly beneath it."""
    r3_inherits = await _one(
        neo4j_settings,
        "MATCH (:Record {id:'ex2-r3'})-[r:INHERIT_PERMISSIONS]->() RETURN count(r) AS c",
    )
    r3_grants = await _one(
        neo4j_settings,
        "MATCH ()-[p:PERMISSION]->(:Record {id:'ex2-r3'}) RETURN count(p) AS c",
    )
    assert (r3_inherits, r3_grants) == (0, 0), "ex2-r3 is the gap: no inheritance, no grant"

    r6_grant = await _one(
        neo4j_settings,
        "MATCH (:User {id:'user-u'})-[p:PERMISSION]->(:Record {id:'ex2-r6'}) "
        "RETURN count(p) AS c",
    )
    assert r6_grant == 1, "ex2-r6 must be granted directly to U"

    parent = await _one(
        neo4j_settings,
        "MATCH (p:Record)-[:NODE_RELATION]->(:Record {id:'ex2-r6'}) RETURN p.id AS id",
    )
    assert parent == "ex2-r3", "ex2-r6 must sit beneath the gap"


async def test_two_parent_record(loaded_graph, neo4j_settings) -> None:
    """Shared with Me is a real second hierarchy parent."""
    parents = sorted(
        r["id"]
        for r in await _rows(
            neo4j_settings,
            "MATCH (p)-[:NODE_RELATION]->(:Record {id:'swm-x'}) RETURN p.id AS id",
        )
    )
    assert parents == ["swm-f1", "swm-inbox"], f"swm-x should have two parents, got {parents}"


async def test_declarations_and_exclusions(loaded_graph, neo4j_settings) -> None:
    """Declarations sit where the validators allow, and flags are set."""
    app_level = await _one(
        neo4j_settings, "MATCH (a:App {id:'dec-app'}) RETURN a.permissionModel AS m"
    )
    assert app_level == "APP_LEVEL"

    group_level = await _one(
        neo4j_settings,
        "MATCH (g:RecordGroup {id:'dec-rg2'}) RETURN g.permissionModel AS m",
    )
    assert group_level == "RECORD_GROUP_LEVEL"

    assert await _one(
        neo4j_settings, "MATCH (n:Record {id:'ex-deleted'}) RETURN n.isDeleted AS d"
    ) is True
    assert await _one(
        neo4j_settings, "MATCH (n:Record {id:'ex-stub'}) RETURN n.isPlaceholder AS p"
    ) is True
    assert await _one(
        neo4j_settings, "MATCH (g:RecordGroup {id:'ex-hidden'}) RETURN g.hideChildren AS h"
    ) is True


async def test_all_five_grant_paths_present(loaded_graph, neo4j_settings) -> None:
    """Every grant path must be representable, including ORG."""
    rows = await _rows(
        neo4j_settings,
        "MATCH (g)-[p:PERMISSION]->(n:Record) WHERE n.id STARTS WITH 'gp-' "
        "RETURN p.type AS type, n.id AS node ORDER BY type",
    )
    by_type = {r["type"]: r["node"] for r in rows}
    assert by_type == {
        "USER": "gp-user",
        "GROUP": "gp-group",
        "ROLE": "gp-role",
        "TEAM": "gp-team",
        "ORG": "gp-org",
    }, f"missing grant paths: {by_type}"
