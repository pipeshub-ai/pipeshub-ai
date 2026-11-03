"""The viewer-scope views against real Neo4j and ArangoDB: every Cypher and AQL query runs, reads one organization's
data, and the resolver over them agrees with `check_access` on the same graph."""

from __future__ import annotations

import pytest

from app.services.permissions.adapters.graph import GraphPrincipalView
from app.services.permissions.viewer import ViewerScopeResolver

from .fixture_graph import (
    CONFLUENCE,
    ORG,
    USER_U,
    USER_V,
    _principals,
    app,
    rg,
    user_app,
)
from .loaders import arango_shape, load_into_arango, load_into_neo4j
from .test_provider_v3_viewer_children import APP, SPACE, _graph

pytestmark = pytest.mark.integration

RGL_APP, RGL_GROUP = "sv-rgl-app", "sv-rgl-group"
OTHER_ORG = "org-other"


def _extra() -> tuple[list, list]:
    nodes = [
        app(RGL_APP, "Confluence with a group of its own", connector=CONFLUENCE, app_group="Atlassian"),
        rg(RGL_GROUP, "Own readers", group_type="CONFLUENCE_SPACES", connector=CONFLUENCE, connectorId=RGL_APP,
           permissionModel="RECORD_GROUP_LEVEL"),
    ]
    return nodes, [user_app(USER_U, RGL_APP)]


@pytest.fixture(scope="module")
async def views_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    more_nodes, more_edges = _extra()
    nodes, edges = [*nodes, *more_nodes], [*edges, *more_edges]
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def test_the_structure_views_list_one_organizations_groups_scopes_and_facts(views_graph, provider) -> None:
    view = provider.permission_hierarchy_view()
    assert await view.record_group_ids(ORG, APP) == {SPACE}
    scopes = {s.id: s for s in await view.scope_nodes(ORG, APP)}
    assert "vc-hidden" in scopes and scopes["vc-hidden"].restricted    # a page that does not inherit its parent
    assert "vc-readable" not in scopes                                 # one that does is not a scope
    facts = await view.app_facts(ORG, APP)
    assert facts.connector_type == CONFLUENCE and not facts.opens and not facts.record_group_level


async def test_the_structure_views_read_nothing_of_another_organization(views_graph, provider) -> None:
    view = provider.permission_hierarchy_view()
    assert await view.record_group_ids(OTHER_ORG, APP) == frozenset()
    assert await view.scope_nodes(OTHER_ORG, APP) == ()
    facts = await view.app_facts(OTHER_ORG, APP)
    assert facts.connector_type == "" and not facts.opens and not facts.record_group_level


async def test_an_app_with_a_record_group_level_group_is_reported(views_graph, provider) -> None:
    facts = await provider.permission_hierarchy_view().app_facts(ORG, RGL_APP)
    assert facts.record_group_level and facts.connector_type == CONFLUENCE


async def test_the_resolver_over_the_real_adapters_agrees_with_check_access(views_graph, provider) -> None:
    resolver = ViewerScopeResolver(provider.permission_hierarchy_view(), GraphPrincipalView(provider))
    mine = await resolver.resolve(ORG, USER_U)
    assert RGL_APP in mine.legacy_apps and APP not in mine.legacy_apps       # the app it cannot describe is named
    view = provider.permission_hierarchy_view()
    universe = await view.record_group_ids(ORG, APP) | {s.id for s in await view.scope_nodes(ORG, APP) if not s.needs_grant}
    checked = (await provider.check_access(USER_U, ORG, node_ids=sorted(universe))).node_ids
    assert mine.readable_groups | mine.readable_scopes == {n for n in universe if n in checked}
    assert SPACE in mine.readable_groups


async def test_a_viewer_who_does_not_hold_the_app_gets_nothing_of_it(views_graph, provider) -> None:
    resolver = ViewerScopeResolver(provider.permission_hierarchy_view(), GraphPrincipalView(provider))
    theirs = await resolver.resolve(ORG, USER_V)
    assert RGL_APP not in theirs.gated_apps and RGL_APP not in theirs.legacy_apps
    assert not theirs.readable_groups & {RGL_GROUP}
    assert _principals()                                                       # the fixture's principals exist
