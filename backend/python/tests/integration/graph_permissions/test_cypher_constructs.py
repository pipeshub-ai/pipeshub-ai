"""Four Cypher constructs the v2 queries need, proven on the real 5.26 server.

Written before the query builders, not after. Neo4j 5.26 already produced one
planner crash in this work — an ``EXISTS {}`` over two QPP-internal variables
inside a ``CASE`` raises ``FunctionInvocation cannot be cast to LogicalVariable``
and poisons the session — and every construct below is the same shape of risk:
supported in isolation, unproven in combination with a quantified path pattern.

A failure here is information, not a defect: it means the builder must reach the
same result another way. Each test therefore asserts the construct's *meaning*,
so "it parsed" is never mistaken for "it is correct".
"""

import pytest
from neo4j import AsyncGraphDatabase

pytestmark = pytest.mark.integration

HIERARCHY_TYPES = ["PARENT_CHILD", "ATTACHMENT"]


async def _run(settings: dict, query: str, **params) -> list[dict]:
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            result = await session.run(query, types=HIERARCHY_TYPES, **params)
            return [record.data() async for record in result]
    finally:
        await driver.close()


async def test_last_on_a_qpp_group_variable(loaded_graph, neo4j_settings) -> None:
    """Placement needs the immediate parent of the node a QPP arrived at.

    Inside a quantified pattern the variables are group variables — lists — so
    the parent is ``last(p)``. If this is unsupported the builder has to emit a
    second MATCH per row, which is a join per result rather than a projection.

    ex2: app -> rg1 -> r3 -> r6 -> r7, so r7's immediate parent is r6.
    """
    rows = await _run(
        neo4j_settings,
        """
        MATCH (root:App {id:'ex2-app'})
              ((p)-[r:NODE_RELATION]->(c) WHERE r.relationshipType IN $types)+ (n)
        WHERE n.id = 'ex2-r7'
        RETURN last(p).id AS parentId, size(c) AS hops
        """,
    )
    assert rows, "the pattern matched nothing; the fixture or the anchor is wrong"
    assert rows[0]["parentId"] == "ex2-r6", rows
    assert rows[0]["hops"] == 4, rows


async def test_path_variable_over_a_qpp(loaded_graph, neo4j_settings) -> None:
    """Breadcrumbs are the placement chain, so the whole trail must be readable.

    If ``nodes(path)`` is unavailable over a quantified pattern, breadcrumbs
    cannot come from the same query as the listing and the per-level walk this
    design exists to delete comes back.
    """
    rows = await _run(
        neo4j_settings,
        """
        MATCH path = (root:App {id:'ex2-app'})
              ((p)-[r:NODE_RELATION]->(c) WHERE r.relationshipType IN $types)+ (n)
        WHERE n.id = 'ex2-r7'
        RETURN [x IN nodes(path) | x.id] AS trail
        """,
    )
    assert rows, "no path bound over the quantified pattern"
    assert rows[0]["trail"] == [
        "ex2-app", "ex2-rg1", "ex2-r3", "ex2-r6", "ex2-r7",
    ], rows[0]["trail"]


async def test_negated_variable_length_exists_in_a_predicate(
    loaded_graph, neo4j_settings
) -> None:
    """The seed predicate must exclude grants beneath a hidden group.

    SEEDS_CYPHER checks deleted, strict and gated but not this, so a granted
    Slack message under a hidden channel would leak. The check is a negated
    variable-length EXISTS, which is the construct being proven here.

    ex-message sits under ex-hidden (hideChildren); ex-deleted sits under the
    open channel and must stay admissible.
    """
    rows = await _run(
        neo4j_settings,
        """
        UNWIND ['ex-message', 'ex-deleted'] AS wanted
        MATCH (x {id: wanted})
        RETURN wanted AS id,
               NOT EXISTS {
                 MATCH (h:RecordGroup)-[:NODE_RELATION*1..20]->(x)
                 WHERE coalesce(h.hideChildren, false)
               } AS admissible
        """,
    )
    by_id = {row["id"]: row["admissible"] for row in rows}
    assert by_id == {"ex-message": False, "ex-deleted": True}, by_id


async def test_qpp_inside_a_call_union_arm(loaded_graph, neo4j_settings) -> None:
    """The passes are unioned, and each arm is a quantified traversal.

    A QPP nested in a CALL/UNION arm is the shape the root pass and the grants
    pass combine into. If it does not plan, the passes have to be run as
    separate round trips and merged in Python.

    Written with the variable scope clause, ``CALL () { ... }``: 5.26 emits a
    deprecation notification for the bare ``CALL {`` form, and the builders
    should not be the last thing in the codebase still emitting it. The scope
    clause also states outright that the arms import nothing, which is true
    here — each anchors itself by id.
    """
    rows = await _run(
        neo4j_settings,
        """
        CALL () {
            MATCH (root:App {id:'ex1-app'})
                  ((p)-[r:NODE_RELATION]->(c) WHERE r.relationshipType IN $types)+ (n)
            RETURN n.id AS id
          UNION
            MATCH (root2:App {id:'ex2-app'})
                  ((p2)-[r2:NODE_RELATION]->(c2) WHERE r2.relationshipType IN $types)+ (n2)
            RETURN n2.id AS id
        }
        RETURN count(DISTINCT id) AS found,
               collect(DISTINCT id) AS ids
        """,
    )
    assert rows, "the union produced no rows"
    ids = set(rows[0]["ids"])
    assert "ex1-r4" in ids and "ex2-r7" in ids, sorted(ids)
    # ex1-app has 6 descendants (rg1, rg2, r3-r6); ex2-app has 8 (rg1, rg2, r3-r8).
    assert rows[0]["found"] == 14, sorted(ids)
