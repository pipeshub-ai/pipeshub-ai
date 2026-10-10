"""A grant tested from the node says what the grant list says, node by node.

Browsing below an App no longer lists the user's grants: each ``x.id IN
$grantedIds`` is rewritten (``Neo4jProvider._kh_grants_by_probe``) into "x is a
record or group of the connector, not deleted, and one of the user's grantees in
this connector holds a PERMISSION edge on it". The grantees come from their own
statement (``_kh_v3_connector_grantees``). This pins, on the chain-top graph, the
shapes graph and the linked-account graph, that for every user and every App the nodes
passing that test are exactly ``get_knowledge_hub_connector_grants``.
"""

from __future__ import annotations

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from .fixture_graph import ORG
from .loaders import load_into_neo4j
from .test_provider_v3_chain_tops import _graph as chain_top_graph
from .test_provider_v3_shapes import _graph as shapes_graph
from .test_shadow_user import CREATOR, LINKED, ORG as SHADOW_ORG, OTHER, SOURCE, _clear_neo4j, _graph

pytestmark = pytest.mark.integration

_PROBED = Neo4jProvider._kh_grants_by_probe("""
    MATCH (x:Record|RecordGroup)
    WHERE x.id IN $grantedIds
    RETURN collect(x.id) AS ids
""")


async def _by_probe(provider, user_key: str, app_id: str) -> set[str]:
    grantees = await provider._kh_v3_connector_grantees(user_key, app_id, None)
    rows = await provider.client.execute_query(
        _PROBED, parameters={"kh_conn": app_id, "kh_grantees": grantees},
    )
    return set(rows[0]["ids"])


async def _compare(provider, org_id: str) -> tuple[int, int]:
    users = [r["id"] for r in await provider.client.execute_query("MATCH (u:User) RETURN u.id AS id")]
    apps = [r["id"] for r in await provider.client.execute_query("MATCH (a:App) RETURN a.id AS id")]
    pairs = granted_somewhere = 0
    for user in users:
        for app_id in apps:
            listed = set(await provider.get_knowledge_hub_connector_grants(user, org_id, app_id))
            assert await _by_probe(provider, user, app_id) == listed, (user, app_id)
            pairs += 1
            granted_somewhere += bool(listed)
    return pairs, granted_somewhere


@pytest.mark.parametrize("graph", [chain_top_graph, shapes_graph], ids=["chain-tops", "shapes"])
async def test_the_node_side_test_agrees_with_the_grant_list_for_every_user_and_app(
    graph, neo4j_provider, neo4j_settings,
) -> None:
    await neo4j_provider.client.execute_query("MATCH (n) DETACH DELETE n")
    nodes, edges = graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    pairs, with_grants = await _compare(neo4j_provider, ORG)
    assert pairs > 0 and with_grants > 0, "the graph must grant something for this to mean anything"


async def test_a_linked_source_account_counts_in_its_connector_only(neo4j_provider, neo4j_settings) -> None:
    nodes, edges = _graph(linked=True)
    await _clear_neo4j(neo4j_provider)
    await load_into_neo4j(neo4j_settings, nodes, edges)
    try:
        pairs, with_grants = await _compare(neo4j_provider, SHADOW_ORG)
        assert with_grants > 0
        # The creator holds nothing itself: what it reaches in the linked connector is the source account's.
        assert await _by_probe(neo4j_provider, CREATOR, LINKED) == await _by_probe(neo4j_provider, SOURCE, LINKED)
        assert await _by_probe(neo4j_provider, CREATOR, LINKED)
        assert await _by_probe(neo4j_provider, CREATOR, OTHER) == set()
    finally:
        await _clear_neo4j(neo4j_provider)
