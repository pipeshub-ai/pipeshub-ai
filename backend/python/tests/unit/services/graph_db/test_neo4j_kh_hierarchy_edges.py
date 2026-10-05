"""Only PARENT_CHILD and ATTACHMENT edges are hierarchy, in every Neo4j knowledge hub walk.

``NODE_RELATION`` also holds edges that are not hierarchy: an untyped edge stays on it by
design, and a link stays until the split migration has moved it. The access check tests
the relationship type on every hop; the listing, its trail and its helpers must test it
too, or a link is listed, browsed and named as a parent. ArangoDB filters every walk.

Each test runs the real provider method against a stubbed driver and reads what it sent.
A real graph is in tests/integration/graph_permissions/test_provider_v3_shapes.py.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_TYPES = r"IN \['PARENT_CHILD', 'ATTACHMENT'\]"
_HOP = re.compile(r"\[(?P<name>\w*):NODE_RELATION(?P<many>\*[\d.]*)?\]")
_EVERY_STEP = re.compile(
    rf"all\((?P<r>\w+) IN relationships\(\w+\)\s+WHERE (?P=r)\.relationshipType {_TYPES}\)"
)


def _untyped_hops(query: str) -> list[str]:
    """NODE_RELATION hops of a statement that do not test the relationship type."""
    loose = []
    for match in _HOP.finditer(query):
        following = query[match.end():match.end() + 400]
        if match.group("many"):
            typed = _EVERY_STEP.search(following)
        else:
            name = match.group("name")
            typed = name and re.search(rf"\b{name}\.relationshipType {_TYPES}", following)
        if not typed:
            line = query[:match.end()].rsplit("\n", 1)[-1].strip()
            loose.append(line)
    return loose


def _provider(*, state: bool) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    provider._kh_listing_state_ready = AsyncMock(return_value=state)
    provider._kh_v3_connector_and_size = AsyncMock(return_value=("app1", 0))
    return provider


def _sent(provider: Neo4jProvider) -> list[str]:
    return [call.args[0] for call in provider.client.execute_query.await_args_list]


def _assert_only_hierarchy(provider: Neo4jProvider, *, walks: int) -> None:
    queries = _sent(provider)
    hops = sum(len(_HOP.findall(q)) for q in queries)
    assert hops >= walks, f"{hops} NODE_RELATION hops were sent, expected at least {walks}"
    loose = [hop for q in queries for hop in _untyped_hops(q)]
    assert not loose, "NODE_RELATION is walked without its type:\n" + "\n".join(loose)


@pytest.mark.parametrize("query", [
    "MATCH (app) ((pa)-[:NODE_RELATION]->(ca) WHERE NOT ca.isDeleted){1,50} (na)",
    "MATCH (app)-[:NODE_RELATION]->(ca) WHERE ca.id IN $grantedIds",
    "MATCH (app)-[r:NODE_RELATION]->(ca) WHERE r.relationshipType IN ['LINKED_TO']",
    "OPTIONAL MATCH (anc)-[:NODE_RELATION*1..50]->(start)",
    "OPTIONAL MATCH p = (anc)-[:NODE_RELATION*1..50]->(start) WHERE all(r IN relationships(p) WHERE r.x = 1)",
])
def test_a_hop_without_the_type_is_found(query: str) -> None:
    assert _untyped_hops(query)


@pytest.mark.parametrize("query", [
    "MATCH (t) ((c)<-[h:NODE_RELATION]-(p) WHERE h.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT']){1,50} (app)",
    "MATCH p = (a)-[:NODE_RELATION*1..50]->(s)\n"
    " WHERE all(r IN relationships(p) WHERE r.relationshipType IN ['PARENT_CHILD', 'ATTACHMENT'])",
])
def test_a_hop_with_the_type_is_accepted(query: str) -> None:
    assert not _untyped_hops(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [False, True], ids=["derived", "listing-state"])
class TestListingWalksOnlyHierarchyEdges:
    async def test_the_whole_connector(self, state: bool) -> None:
        provider = _provider(state=state)
        await provider.get_knowledge_hub_connector_page_v3(
            "app1", "org1", ["uk1"], ["app1"], ["r9"], flatten=True,
        )
        # From the App, under a declared group, below a seed, and the parents of a row.
        _assert_only_hierarchy(provider, walks=4)

    async def test_the_children_of_the_app(self, state: bool) -> None:
        provider = _provider(state=state)
        provider._kh_v3_chain_tops = AsyncMock(return_value={"app1": [{"id": "r9"}]})
        await provider.get_knowledge_hub_connector_page_v3(
            "app1", "org1", ["uk1"], ["app1"], ["r9"], flatten=False,
        )
        _assert_only_hierarchy(provider, walks=2)

    @pytest.mark.parametrize("flatten", [False, True], ids=["browse", "flatten"])
    async def test_below_a_folder_or_group(self, state: bool, flatten: bool) -> None:
        provider = _provider(state=state)
        provider._kh_v3_chain_tops = AsyncMock(return_value={})
        await provider.get_knowledge_hub_connector_page_v3(
            "app1", "org1", ["uk1"], ["app1"], ["r9"], flatten=flatten,
            start_id="g1", start_type="recordGroup", include_scope=True,
        )
        # The walk up to the App, the ancestors of the trail and its edges, the walk down.
        _assert_only_hierarchy(provider, walks=6)


@pytest.mark.asyncio
class TestListingHelpersWalkOnlyHierarchyEdges:
    async def test_the_ancestors_a_browse_start_is_checked_against(self) -> None:
        provider = _provider(state=False)
        await provider._kh_v3_start_lineage("r1", "app1", None)
        _assert_only_hierarchy(provider, walks=2)

    async def test_the_groups_under_a_declared_group(self) -> None:
        provider = _provider(state=False)
        await provider._kh_v3_declared_groups(["g1"], None)
        _assert_only_hierarchy(provider, walks=1)

    async def test_the_granted_children_of_the_app(self) -> None:
        provider = _provider(state=False)
        await provider._kh_v3_grants_and_chain_top_groups(
            "uk1", "org1", "app1", None, None, with_ids=False, app_children_only=True,
        )
        _assert_only_hierarchy(provider, walks=1)
