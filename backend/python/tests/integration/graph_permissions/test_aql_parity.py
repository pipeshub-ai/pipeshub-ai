"""The AQL traversal returns the same sets as the Cypher one.

Cypher's QPP ends a branch at the first hop whose predicate fails, so an
inaccessible node hides its whole subtree. AQL cannot reproduce that with
`PRUNE`, because `PRUNE` forbids subqueries and the permission checks are
subqueries. A `FILTER` on the last hop alone is *not* equivalent: the walk
continues past a failing node and then emits its descendants, because each of
them satisfies the rule on its own. Measured on Example 2, that returns 7 nodes
where Cypher returns 4.

The equivalent is to assert the rule over the **whole path** — no hop may have
violated it — which is what this module does and what the shipped AQL will have
to do. `PRUNE` still earns its place on the structural conjuncts, as an
optimisation rather than as the rule.
"""

import aiohttp
import pytest

from .test_qpp_semantics import GRANTEES, HIERARCHY_TYPES

pytestmark = pytest.mark.integration


def _inherits(child: str, parent: str) -> str:
    return (
        f"LENGTH(FOR ip IN inheritPermissions "
        f"FILTER ip._from == {child}._id AND ip._to == {parent}._id "
        f"LIMIT 1 RETURN 1) > 0"
    )


def _granted(child: str) -> str:
    return (
        f"LENGTH(FOR pm IN permission "
        f"FILTER pm._to == {child}._id "
        f"AND PARSE_IDENTIFIER(pm._from).key IN @grantees "
        f"LIMIT 1 RETURN 1) > 0"
    )


def _rule(child: str, parent: str, edge: str) -> str:
    """The per-hop rule, in the same three boolean branches as the Cypher.

    Written against explicit child/parent/edge expressions so the same text
    serves both the last hop and every earlier hop on the path.
    """
    return f"""
      {edge}.relationshipType IN @types
      AND {child}.isDeleted != true
      AND {parent}.hideChildren != true
      AND ( @skipChecks OR (
             ( NOT_NULL({child}.accessRule, "OPEN") == "RESTRICTED" AND @allowStrict
               AND {_inherits(child, parent)} AND {_granted(child)} )
          OR ( NOT_NULL({child}.accessRule, "OPEN") == "STRICT" AND @allowStrict
               AND ({_inherits(child, parent)} OR {_granted(child)}) )
          OR ( NOT_NULL({child}.accessRule, "OPEN") == "OPEN"
               AND ({_inherits(child, parent)} OR {_granted(child)}) ) ) )
    """


# Structural conjuncts only — PRUNE rejects subqueries. Every edge test must
# tolerate a null `e`: on the first step the condition is evaluated before `e`
# is bound, so a bare `e.relationshipType IN @types` prunes the root's entire
# expansion and the traversal returns nothing. The shipped provider guards the
# same way at arango_http_provider.py:15438.
_PRUNE = """
    NOT ( (e == null OR NOT HAS(e, "relationshipType")
           OR e.relationshipType IN @types)
          AND v.isDeleted != true )
"""

_TRAVERSAL = f"""
FOR seedId IN @seeds
  FOR v, e, path IN 1..@maxDepth OUTBOUND CONCAT(@seedCollection, '/', seedId) nodeRelations
    PRUNE {_PRUNE}
    OPTIONS {{ bfs: true, uniqueVertices: "path" }}
    FILTER LENGTH(
      FOR i IN 0..LENGTH(path.edges) - 1
        FILTER NOT ( {_rule('path.vertices[i+1]', 'path.vertices[i]', 'path.edges[i]')} )
        LIMIT 1 RETURN 1
    ) == 0
    RETURN DISTINCT v._key
"""


async def _ids(settings: dict, seeds: list[str], collection: str, *,
               allow_strict: bool = True, skip_checks: bool = False,
               grantees: list[str] | None = None) -> set[str]:
    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    url = f"{settings['url'].rstrip('/')}/_db/{settings['db']}/_api/cursor"
    payload = {
        "query": _TRAVERSAL,
        "bindVars": {
            "seeds": seeds,
            "seedCollection": collection,
            "types": HIERARCHY_TYPES,
            "grantees": GRANTEES if grantees is None else grantees,
            "allowStrict": allow_strict,
            "skipChecks": skip_checks,
            "maxDepth": 50,
        },
    }
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json=payload) as resp:
            body = await resp.json()
            if resp.status not in (200, 201):
                raise AssertionError(f"AQL failed: {resp.status} {body}")
            return set(body["result"])


async def test_aql_root_pass_stops_at_the_gap(loaded_graph, arango_settings) -> None:
    found = await _ids(arango_settings, ["ex2-app"], "apps")
    assert found == {"ex2-rg1", "ex2-rg2", "ex2-r4", "ex2-r5"}, sorted(found)


async def test_aql_grants_pass_reaches_below_the_gap(loaded_graph, arango_settings) -> None:
    found = await _ids(arango_settings, ["ex2-r6"], "records", allow_strict=False)
    assert found == {"ex2-r7", "ex2-r8"}, sorted(found)


async def test_last_hop_filtering_would_be_wrong(loaded_graph, arango_settings) -> None:
    """Guards the reason for whole-path filtering, so nobody 'simplifies' it back.

    Filtering only the final hop lets the walk run past an inaccessible node
    and emit its descendants — here ex2-r6, r7 and r8, which Cypher excludes.
    """
    last_hop_only = f"""
    FOR v, e, path IN 1..50 OUTBOUND 'apps/ex2-app' nodeRelations
      PRUNE {_PRUNE}
      OPTIONS {{ bfs: true, uniqueVertices: "path" }}
      FILTER {_rule('v', 'path.vertices[LENGTH(path.vertices)-2]', 'e')}
      RETURN DISTINCT v._key
    """
    auth = aiohttp.BasicAuth(arango_settings["username"], arango_settings["password"])
    url = f"{arango_settings['url'].rstrip('/')}/_db/{arango_settings['db']}/_api/cursor"
    payload = {
        "query": last_hop_only,
        "bindVars": {
            "types": HIERARCHY_TYPES, "grantees": GRANTEES,
            "allowStrict": True, "skipChecks": False,
        },
    }
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json=payload) as resp:
            leaked = set((await resp.json())["result"])

    assert {"ex2-r6", "ex2-r7", "ex2-r8"} <= leaked, (
        "expected last-hop filtering to leak nodes below the gap; if it no "
        "longer does, whole-path filtering may be unnecessary"
    )


async def test_both_backends_agree_on_example_two(
    loaded_graph, arango_settings, neo4j_settings
) -> None:
    """The parity assertion the harness exists for."""
    from .test_qpp_semantics import PER_HOP_RULE, _ids as cypher_ids

    cypher_root = await cypher_ids(
        neo4j_settings,
        f"""
        MATCH (root:App {{id:'ex2-app'}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """,
    )
    cypher_grants = await cypher_ids(
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

    aql_root = await _ids(arango_settings, ["ex2-app"], "apps")
    aql_grants = await _ids(arango_settings, ["ex2-r6"], "records", allow_strict=False)

    print(
        f"\n[parity] root   cypher={sorted(cypher_root)}\n"
        f"                aql   ={sorted(aql_root)}\n"
        f"         grants cypher={sorted(cypher_grants)}\n"
        f"                aql   ={sorted(aql_grants)}"
    )
    assert cypher_root == aql_root, "root pass diverges between backends"
    assert cypher_grants == aql_grants, "grants pass diverges between backends"
