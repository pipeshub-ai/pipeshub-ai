"""The QPP traversal behaves as the design says, on the doc's own example.

Syntax was proved separately; this is about meaning. Example 2 is the
case that separates the two passes: node 3 is a gap, node 6 is granted directly
beneath it, and 7/8 inherit from 6. A top-down walk alone can never reach 6,
which is the entire reason for the grants pass.
"""

import pytest
from neo4j import AsyncGraphDatabase

pytestmark = pytest.mark.integration

HIERARCHY_TYPES = ["PARENT_CHILD", "ATTACHMENT"]
GRANTEES = ["user-u", "group-g", "role-r", "team-t", "orgnode-1"]

# The per-hop rule, written as boolean algebra
# rather than nested CASE.
#
# Neo4j 5.26 crashes its planner on an EXISTS { } that references two
# QPP-internal variables when that EXISTS sits inside a CASE expression:
#   "FunctionInvocation cannot be cast to LogicalVariable"
#   (Neo.DatabaseError.General.UnknownError)
# Each construct is fine alone — coalesce(), CASE, a two-variable EXISTS — only
# the combination fails, so the rule is expressed as
#   CASE WHEN s THEN a ELSE b END  ==  (s AND a) OR ((NOT s) AND b)
# which is equivalent and plans cleanly.
#
# The three branches are mutually exclusive by construction: accessRule is one
# field, so no (non-strict, restricted) combination exists to guard against.
PER_HOP_RULE = """
    r.relationshipType IN $types
    AND NOT coalesce(c.isDeleted, false)
    AND NOT coalesce(p.hideChildren, false)
    AND ( $skipChecks
          OR (
               // RESTRICTED: needs inheritance AND a grant
               ( coalesce(c.accessRule, 'OPEN') = 'RESTRICTED'
                 AND $allowStrict
                 AND EXISTS { (c)-[:INHERIT_PERMISSIONS]->(p) }
                 AND EXISTS { (g)-[:PERMISSION]->(c) WHERE g.id IN $grantees } )
               // STRICT: inheritance OR a grant
            OR ( coalesce(c.accessRule, 'OPEN') = 'STRICT'
                 AND $allowStrict
                 AND ( EXISTS { (c)-[:INHERIT_PERMISSIONS]->(p) }
                       OR EXISTS { (g)-[:PERMISSION]->(c) WHERE g.id IN $grantees } ) )
               // OPEN: ancestors are irrelevant
            OR ( coalesce(c.accessRule, 'OPEN') = 'OPEN'
                 AND ( EXISTS { (c)-[:INHERIT_PERMISSIONS]->(p) }
                       OR EXISTS { (g)-[:PERMISSION]->(c) WHERE g.id IN $grantees } ) )
             ) )
"""


async def _ids(settings: dict, query: str, **params) -> set[str]:
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            result = await session.run(
                query,
                types=HIERARCHY_TYPES,
                grantees=GRANTEES,
                allowStrict=params.pop("allowStrict", True),
                skipChecks=params.pop("skipChecks", False),
                **params,
            )
            return {r["id"] async for r in result}
    finally:
        await driver.close()


async def test_qpp_anchors_to_the_start_node(loaded_graph, neo4j_settings) -> None:
    """Without a permission predicate, the QPP reaches every descendant.

    This is the anchoring check: if the quantified pattern were not bound to
    `root`, it would match far more than this App's subtree.
    """
    found = await _ids(
        neo4j_settings,
        """
        MATCH (root:App {id:'ex2-app'})
              ((p)-[r:NODE_RELATION]->(c) WHERE r.relationshipType IN $types)+ (n)
        RETURN DISTINCT n.id AS id
        """,
    )
    assert found == {
        "ex2-rg1", "ex2-rg2", "ex2-r3", "ex2-r4",
        "ex2-r5", "ex2-r6", "ex2-r7", "ex2-r8",
    }, f"QPP is not anchored to the start node: {sorted(found)}"


async def test_root_pass_stops_at_the_gap(loaded_graph, neo4j_settings) -> None:
    """Top-down from the App: 3 fails, so 6/7/8 are unreachable this way."""
    found = await _ids(
        neo4j_settings,
        f"""
        MATCH (root:App {{id:'ex2-app'}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """,
    )
    assert found == {"ex2-rg1", "ex2-rg2", "ex2-r4", "ex2-r5"}, sorted(found)


async def test_grants_pass_reaches_below_the_gap(loaded_graph, neo4j_settings) -> None:
    """Seeded at the granted node, its inheriting descendants come with it."""
    descendants = await _ids(
        neo4j_settings,
        f"""
        UNWIND $seeds AS seedId
        MATCH (root {{id: seedId}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """,
        seeds=["ex2-r6"],
        allowStrict=False,
    )
    assert descendants == {"ex2-r7", "ex2-r8"}, sorted(descendants)


async def test_union_matches_example_two(loaded_graph, neo4j_settings) -> None:
    """The two passes together produce exactly Example 2's accessible set."""
    root_pass = await _ids(
        neo4j_settings,
        f"""
        MATCH (root:App {{id:'ex2-app'}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """,
    )
    grants_pass = await _ids(
        neo4j_settings,
        f"""
        UNWIND $seeds AS seedId
        MATCH (root {{id: seedId}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """,
        seeds=["ex2-r6"],
        allowStrict=False,
    )
    accessible = root_pass | grants_pass | {"ex2-r6"}

    print(f"\n[qpp] root={sorted(root_pass)}\n      grants={sorted(grants_pass)}")
    assert accessible == {
        "ex2-rg1", "ex2-rg2", "ex2-r4", "ex2-r5", "ex2-r6", "ex2-r7", "ex2-r8",
    }, sorted(accessible)
    assert "ex2-r3" not in accessible, "the gap must never be returned"
