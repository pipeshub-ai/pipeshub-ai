"""App access is not space access.

This is the regression guard for bug B2, the leak that made every Confluence
space visible to every user of the connector. It is the property the whole
model rests on, so it gets its own module.

The spaces in Example 1 *do* inherit from the App, and that is safe. What keeps
them private is the restriction flag: a
restricted node needs inheritance **and** a direct grant, so inheriting from an
App the user can reach is never sufficient on its own.

U holds grants on both spaces. V has Confluence app access and no space
permission anywhere. U must see both spaces; V must see nothing.
"""

import pytest

from .test_aql_parity import _ids as aql_ids
from .test_qpp_semantics import GRANTEES, HIERARCHY_TYPES, PER_HOP_RULE

pytestmark = pytest.mark.integration

# The grantee set for V: V's own id only. V is in no group, holds no role, and
# belongs to no team, so nothing reaches the spaces on V's behalf.
V_GRANTEES = ["user-v"]

_FROM_CONFLUENCE_APP = f"""
MATCH (root:App {{id:'ex1-app'}})
      ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
RETURN DISTINCT n.id AS id
"""


async def _as_user(settings: dict, grantees: list[str]) -> set[str]:
    """Run the root pass over the Confluence app for a given grantee set."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            result = await session.run(
                _FROM_CONFLUENCE_APP,
                types=HIERARCHY_TYPES,
                grantees=grantees,
                allowStrict=True,
                skipChecks=False,
            )
            return {r["id"] async for r in result}
    finally:
        await driver.close()


async def test_spaces_do_inherit_from_the_app(loaded_graph, neo4j_settings) -> None:
    """The edge exists — the leak is prevented by the rule, not by its absence."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        neo4j_settings["uri"],
        auth=(neo4j_settings["username"], neo4j_settings["password"]),
    )
    try:
        async with driver.session(database=neo4j_settings["database"]) as session:
            result = await session.run(
                "MATCH (:RecordGroup)-[r:INHERIT_PERMISSIONS]->(:App {id:'ex1-app'}) "
                "RETURN count(r) AS c"
            )
            inheriting_spaces = (await result.single())["c"]
    finally:
        await driver.close()

    assert inheriting_spaces == 2, (
        "Example 1's spaces must inherit from the App; if this is 0 the test "
        "below passes for the wrong reason"
    )


async def test_user_with_grants_sees_both_spaces(loaded_graph, neo4j_settings) -> None:
    seen = await _as_user(neo4j_settings, ["user-u", "group-g", "role-r", "team-t", "orgnode-1"])
    assert {"ex1-rg1", "ex1-rg2"} <= seen, sorted(seen)


async def test_app_access_alone_sees_no_space(loaded_graph, neo4j_settings) -> None:
    """V can open Confluence and holds no space permission."""
    seen = await _as_user(neo4j_settings, V_GRANTEES)
    print(f"\n[b2] user V (app access, no grants) sees: {sorted(seen)}")
    assert seen == set(), (
        f"B2 leak: app access alone exposed {sorted(seen)}. A restricted space "
        f"must require a direct grant on top of inheritance."
    )


async def test_b2_holds_on_arango_too(loaded_graph, arango_settings) -> None:
    """The same guard on the other backend, since the rules are enforced twice."""
    import aiohttp

    from .test_aql_parity import _TRAVERSAL

    auth = aiohttp.BasicAuth(arango_settings["username"], arango_settings["password"])
    url = f"{arango_settings['url'].rstrip('/')}/_db/{arango_settings['db']}/_api/cursor"
    payload = {
        "query": _TRAVERSAL,
        "bindVars": {
            "seeds": ["ex1-app"],
            "seedCollection": "apps",
            "types": HIERARCHY_TYPES,
            "grantees": V_GRANTEES,
            "allowStrict": True,
            "skipChecks": False,
            "maxDepth": 50,
        },
    }
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json=payload) as resp:
            body = await resp.json()
            assert resp.status in (200, 201), body
            seen = set(body["result"])

    assert seen == set(), f"B2 leak on Arango: app access alone exposed {sorted(seen)}"


async def test_both_backends_agree_for_u_and_v(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """Compare the same two users on both engines.

    U's view is asserted non-empty so that a backend quietly returning nothing
    cannot make V's empty view look like a passing result.
    """
    cypher_u = await _as_user(neo4j_settings, GRANTEES)
    aql_u = await aql_ids(arango_settings, ["ex1-app"], "apps", grantees=GRANTEES)
    cypher_v = await _as_user(neo4j_settings, V_GRANTEES)
    aql_v = await aql_ids(arango_settings, ["ex1-app"], "apps", grantees=V_GRANTEES)

    print(f"\n[b2] U: cypher={sorted(cypher_u)} aql={sorted(aql_u)}")
    print(f"     V: cypher={sorted(cypher_v)} aql={sorted(aql_v)}")

    assert cypher_u, "U must see something, or V's empty view proves nothing"
    assert cypher_u == aql_u, "U's view diverges between backends"
    assert cypher_v == aql_v == set(), "V must see nothing on either backend"
