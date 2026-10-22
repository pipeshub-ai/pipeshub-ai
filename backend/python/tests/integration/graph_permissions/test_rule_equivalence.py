"""Any rewrite of the per-hop rule must be provably identical to the shipped one.

The shipped rule is three branches keyed on ``accessRule``. On paper it compacts
to three conjuncts that read ``inherits``/``granted`` once each instead of once
per branch::

    ( accessRule = 'OPEN' OR $allowStrict )
    AND ( inherits OR granted )
    AND ( accessRule <> 'RESTRICTED' OR ( inherits AND granted ) )

That form is **rejected**, and this module is the record of why. It agrees for
all three declared values and diverges on a fourth: an unrecognised
``accessRule`` matches no branch of the shipped rule and is hidden, but it
satisfies ``accessRule <> 'RESTRICTED'`` and is *returned* by the compact form.
The model reads an unknown value as RESTRICTED and the traversal hides it;
the compact form fails open instead.

No acceptance scenario catches that, because every fixture node carries a
declared value — which is the trap. The sweep below passes for the naive form
too; only a deliberately corrupted node separates them. A rule rewrite gated on
scenario coverage alone would have shipped the leak.

``_COMPACT_RULE`` here is the *corrected* compaction, which closes the fourth
case and is proven equivalent on both engines. It is kept as the sanctioned
target for anyone who does want to compact the rule later.
"""

import aiohttp
import pytest

from .fixture_graph import build_fixture
from .test_aql_parity import _PRUNE, _granted, _inherits
from .test_qpp_semantics import GRANTEES, HIERARCHY_TYPES, PER_HOP_RULE
from .test_qpp_semantics import _ids as cypher_ids

pytestmark = pytest.mark.integration

_STRUCTURAL = """
    r.relationshipType IN $types
    AND NOT coalesce(c.isDeleted, false)
    AND NOT coalesce(p.hideChildren, false)
"""

_INHERITS = "EXISTS { (c)-[:INHERIT_PERMISSIONS]->(p) }"
_GRANTED = "EXISTS { (g)-[:PERMISSION]->(c) WHERE g.id IN $grantees }"

# The compaction, with the unknown-value hole closed: an undeclared value
# satisfies neither disjunct of the first conjunct, so it is hidden.
_COMPACT_RULE = f"""
    {_STRUCTURAL}
    AND ( $skipChecks
          OR (
               ( coalesce(c.accessRule, 'OPEN') = 'OPEN'
                 OR ( coalesce(c.accessRule, 'OPEN') IN ['STRICT', 'RESTRICTED']
                      AND $allowStrict ) )
               AND ( {_INHERITS} OR {_GRANTED} )
               AND ( coalesce(c.accessRule, 'OPEN') <> 'RESTRICTED'
                     OR ( {_INHERITS} AND {_GRANTED} ) )
             ) )
"""

# The compaction exactly as first proposed. Retained only as the negative
# control below; never use it.
_NAIVE_COMPACT_RULE = f"""
    {_STRUCTURAL}
    AND ( $skipChecks
          OR (
               ( coalesce(c.accessRule, 'OPEN') = 'OPEN' OR $allowStrict )
               AND ( {_INHERITS} OR {_GRANTED} )
               AND ( coalesce(c.accessRule, 'OPEN') <> 'RESTRICTED'
                     OR ( {_INHERITS} AND {_GRANTED} ) )
             ) )
"""


def _compact_rule_aql(child: str, parent: str, edge: str) -> str:
    return f"""
      {edge}.relationshipType IN @types
      AND {child}.isDeleted != true
      AND {parent}.hideChildren != true
      AND ( @skipChecks OR (
             ( NOT_NULL({child}.accessRule, "OPEN") == "OPEN"
               OR ( NOT_NULL({child}.accessRule, "OPEN") IN ["STRICT", "RESTRICTED"]
                    AND @allowStrict ) )
             AND ( {_inherits(child, parent)} OR {_granted(child)} )
             AND ( NOT_NULL({child}.accessRule, "OPEN") != "RESTRICTED"
                   OR ( {_inherits(child, parent)} AND {_granted(child)} ) ) ) )
    """


def _cypher_query(rule: str) -> str:
    return f"""
    UNWIND $seeds AS seedId
    MATCH (root {{id: seedId}})
          ((p)-[r:NODE_RELATION]->(c) WHERE {rule})+ (n)
    RETURN DISTINCT n.id AS id
    """


def _aql_query(rule_fn) -> str:
    return f"""
    FOR seedId IN @seeds
      FOR v, e, path IN 1..@maxDepth OUTBOUND CONCAT(@seedCollection, '/', seedId) nodeRelations
        PRUNE {_PRUNE}
        OPTIONS {{ bfs: true, uniqueVertices: "path" }}
        FILTER LENGTH(
          FOR i IN 0..LENGTH(path.edges) - 1
            FILTER NOT ( {rule_fn('path.vertices[i+1]', 'path.vertices[i]', 'path.edges[i]')} )
            LIMIT 1 RETURN 1
        ) == 0
        RETURN DISTINCT v._key
    """


async def _arango(settings: dict, query: str, bind_vars: dict) -> set[str]:
    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    url = f"{settings['url'].rstrip('/')}/_db/{settings['db']}/_api/cursor"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json={"query": query, "bindVars": bind_vars}) as resp:
            body = await resp.json()
            if resp.status not in (200, 201):
                raise AssertionError(f"AQL failed: {resp.status} {body}")
            return set(body["result"])


async def _aql_ids(settings: dict, rule_fn, seeds: list[str], collection: str,
                   *, allow_strict: bool, skip_checks: bool = False) -> set[str]:
    return await _arango(settings, _aql_query(rule_fn), {
        "seeds": seeds, "seedCollection": collection,
        "types": HIERARCHY_TYPES, "grantees": GRANTEES,
        "allowStrict": allow_strict, "skipChecks": skip_checks, "maxDepth": 50,
    })


def _seed_plan() -> list[tuple[str, str, bool]]:
    """Every start point the fixture offers: (seed id, collection, allowStrict).

    Apps are swept both ways because ``allowStrict`` is a term of the rule
    itself, and a rewrite could agree on one setting while diverging on the
    other. Granted records are swept as the grants pass runs them.
    """
    nodes, edges = build_fixture()
    kind_by_id = {n["id"]: n["kind"] for n in nodes}
    plan: list[tuple[str, str, bool]] = []
    for node in nodes:
        if node["kind"] == "App":
            plan.append((node["id"], "apps", True))
            plan.append((node["id"], "apps", False))
    granted = sorted({
        e["to"] for e in edges
        if e["type"] == "PERMISSION" and kind_by_id.get(e["to"]) == "Record"
    })
    plan.extend((node_id, "records", False) for node_id in granted)
    return plan


@pytest.mark.parametrize("rule_name", ["corrected", "naive"])
async def test_compaction_matches_on_every_declared_value_cypher(
    loaded_graph, neo4j_settings, rule_name
) -> None:
    """Both compactions agree with the shipped rule across the whole fixture.

    The naive form is included deliberately: it passes here. That is the point —
    scenario coverage cannot distinguish the two, so it must not be what a rule
    rewrite is gated on.
    """
    rule = _COMPACT_RULE if rule_name == "corrected" else _NAIVE_COMPACT_RULE
    for seed, _collection, allow_strict in _seed_plan():
        shipped = await cypher_ids(
            neo4j_settings, _cypher_query(PER_HOP_RULE),
            seeds=[seed], allowStrict=allow_strict,
        )
        candidate = await cypher_ids(
            neo4j_settings, _cypher_query(rule),
            seeds=[seed], allowStrict=allow_strict,
        )
        assert shipped == candidate, (
            f"{rule_name} compaction diverges from seed {seed!r} "
            f"(allowStrict={allow_strict}): "
            f"only shipped={sorted(shipped - candidate)} "
            f"only compact={sorted(candidate - shipped)}"
        )


async def test_corrected_compaction_matches_on_arango(
    loaded_graph, arango_settings
) -> None:
    """The same equivalence on the other engine, whole-path filtered."""
    from .test_aql_parity import _rule as shipped_rule_aql

    for seed, collection, allow_strict in _seed_plan():
        shipped = await _aql_ids(arango_settings, shipped_rule_aql, [seed],
                                 collection, allow_strict=allow_strict)
        candidate = await _aql_ids(arango_settings, _compact_rule_aql, [seed],
                                   collection, allow_strict=allow_strict)
        assert shipped == candidate, (
            f"compaction diverges on Arango from seed {seed!r} "
            f"(allowStrict={allow_strict}): "
            f"only shipped={sorted(shipped - candidate)} "
            f"only compact={sorted(candidate - shipped)}"
        )


async def test_an_unrecognised_rule_separates_the_two_compactions(
    loaded_graph, neo4j_settings
) -> None:
    """The case the acceptance graph cannot express, and the reason for this file.

    ``flag-open`` inherits from its group and holds no grant. Corrupt its
    ``accessRule`` and the shipped rule hides it — no branch matches. The naive
    compaction returns it, because an unknown value is not ``'RESTRICTED'`` and
    ``$allowStrict`` is true on a root pass. That is a live node becoming
    visible to everyone who can reach its parent.
    """
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        neo4j_settings["uri"],
        auth=(neo4j_settings["username"], neo4j_settings["password"]),
    )
    try:
        async with driver.session(database=neo4j_settings["database"]) as session:
            await session.run(
                "MATCH (n {id:'flag-open'}) SET n.accessRule = 'NOT_A_RULE'"
            )

        query = _cypher_query(PER_HOP_RULE)
        shipped = await cypher_ids(neo4j_settings, query, seeds=["flag-app"])
        corrected = await cypher_ids(
            neo4j_settings, _cypher_query(_COMPACT_RULE), seeds=["flag-app"]
        )
        naive = await cypher_ids(
            neo4j_settings, _cypher_query(_NAIVE_COMPACT_RULE), seeds=["flag-app"]
        )

        assert "flag-open" not in shipped, (
            "an unrecognised accessRule must fail closed"
        )
        assert "flag-open" not in corrected, (
            "the corrected compaction must fail closed too, or it is not equivalent"
        )
        assert "flag-open" in naive, (
            "expected the naive compaction to leak an unrecognised accessRule; "
            "if it no longer does, the first conjunct has changed and this "
            "guard needs rewriting rather than deleting"
        )
    finally:
        async with driver.session(database=neo4j_settings["database"]) as session:
            await session.run("MATCH (n {id:'flag-open'}) SET n.accessRule = 'OPEN'")
        await driver.close()


async def test_arango_storage_refuses_an_unrecognised_rule(
    loaded_graph, arango_settings
) -> None:
    """Arango closes the same hole one layer earlier, at the validator.

    The collection validators run at ``level: strict`` with an ``enum`` on
    ``accessRule``, so the corrupt state above is not writable here at all.
    Neo4j has no equivalent and accepts anything — which is why the rule itself,
    not the schema, has to be the thing that fails closed.
    """
    auth = aiohttp.BasicAuth(arango_settings["username"], arango_settings["password"])
    url = (
        f"{arango_settings['url'].rstrip('/')}/_db/{arango_settings['db']}"
        f"/_api/document/records/flag-open"
    )
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.patch(url, json={"accessRule": "NOT_A_RULE"}) as resp:
            body = await resp.text()
            assert resp.status >= 400, (
                f"Arango accepted an undeclared accessRule ({resp.status}); the "
                f"enum on the records validator is not doing its job: {body}"
            )

    survived = await _arango(
        arango_settings,
        "FOR d IN records FILTER d._key == 'flag-open' RETURN d.accessRule",
        {},
    )
    assert survived == {"OPEN"}, f"the rejected write still landed: {survived}"
