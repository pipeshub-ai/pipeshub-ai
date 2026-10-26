"""A placeholder parent keeps the readable records below it navigable.

A connector writes a placeholder (``isPlaceholder``) for a parent it did not
read: a Confluence space's homepage when sync filters name only some pages, a
Jira project outside the selection. Browsing lists the stub, flagged, so the
records under it stay reachable; the frontend shows it read-only. A flatten
and a content filter never return it: a stub has no content. The access
check does return one asked by id, so navigate can open it, and does not
return one asked only by virtual record id.

The graph is the saas-test Confluence shape that hid a whole space:

    App -> space (RESTRICTED, granted to U's group)
        -> homepage stub -> page -> child page
        -> empty stub (a parent whose children are all filtered out)
        -> folder
"""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import (
    CONFLUENCE,
    GROUP_G,
    ORG,
    USER_U,
    USER_V,
    _principals,
    app,
    bt,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from .loaders import arango_shape, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

APP = "ph-app"
PAGE = {"record_type": "CONFLUENCE_PAGE", "connector_id": APP}
SPACE = {"group_type": "CONFLUENCE_SPACES", "connector": CONFLUENCE, "connectorId": APP}


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app(APP, "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        rg("ph-space", "Software Development", rule="RESTRICTED", **SPACE),
        rec("ph-home", "Software Development", isPlaceholder=True, **PAGE),
        rec("ph-page", "Daily Bug Bash", **PAGE),
        rec("ph-child", "Bug bash notes", **PAGE),
        rec("ph-empty-stub", "Feature Design Docs", isPlaceholder=True, **PAGE),
        rec("ph-folder", "Permissions", **PAGE),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP), user_app(USER_V, APP),
        nr(APP, "ph-space"), ip("ph-space", APP), perm(GROUP_G, "ph-space", grant_type="GROUP"),
        nr("ph-space", "ph-home"), ip("ph-home", "ph-space"), bt("ph-home", "ph-space"),
        nr("ph-home", "ph-page"), ip("ph-page", "ph-home"), bt("ph-page", "ph-space"),
        nr("ph-page", "ph-child"), ip("ph-child", "ph-page"), bt("ph-child", "ph-space"),
        nr("ph-space", "ph-empty-stub"), ip("ph-empty-stub", "ph-space"), bt("ph-empty-stub", "ph-space"),
        nr("ph-space", "ph-folder"), ip("ph-folder", "ph-space"), bt("ph-folder", "ph-space"),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def placeholder_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _page(provider: Any, user: str = USER_U, **overrides: Any) -> dict:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user_key=user, org_id=ORG)
    kwargs = {
        "app_id": APP, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 100, "flatten": False, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True, "include_scope": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


def _rows(page: dict) -> dict[str, dict]:
    return {row["id"]: row for row in page["rows"]}


async def test_browsing_the_space_lists_the_placeholder_flagged(placeholder_graph, provider) -> None:
    rows = _rows(await _page(provider, start_id="ph-space", start_type="recordGroup"))
    assert set(rows) == {"ph-home", "ph-empty-stub", "ph-folder"}
    assert rows["ph-home"]["isPlaceholder"] is True
    assert rows["ph-home"]["hasChildren"] is True
    assert rows["ph-empty-stub"]["isPlaceholder"] is True
    assert rows["ph-folder"]["isPlaceholder"] is False


async def test_opening_the_placeholder_lists_the_pages_below_it(placeholder_graph, provider) -> None:
    page = await _page(provider, start_id="ph-home", start_type="record")
    assert set(_rows(page)) == {"ph-page"}
    assert page["scope"]["admitted"] is True
    assert [c["id"] for c in page["scope"]["breadcrumbs"]] == [APP, "ph-space", "ph-home"]


async def test_a_page_below_the_placeholder_keeps_its_trail(placeholder_graph, provider) -> None:
    page = await _page(provider, start_id="ph-page", start_type="record")
    assert set(_rows(page)) == {"ph-child"}
    assert [c["id"] for c in page["scope"]["breadcrumbs"]] == [APP, "ph-space", "ph-home", "ph-page"]


@pytest.mark.parametrize("overrides", [
    {"flatten": True, "include_scope": False},
    {"flatten": True, "start_id": "ph-space", "start_type": "recordGroup"},
], ids=["global", "below-the-space"])
async def test_a_flatten_returns_the_pages_and_never_a_stub(placeholder_graph, provider, overrides) -> None:
    ids = set(_rows(await _page(provider, **overrides)))
    assert {"ph-page", "ph-child", "ph-folder"} <= ids
    assert not ids & {"ph-home", "ph-empty-stub"}


@pytest.mark.parametrize("filters", [
    {"search_query": "software"},
    {"record_types": ["CONFLUENCE_PAGE"]},
], ids=["search", "record-type"])
async def test_a_content_filter_never_returns_a_stub(placeholder_graph, provider, filters) -> None:
    ids = set(_rows(await _page(provider, start_id="ph-space", start_type="recordGroup", filters=filters)))
    assert not ids & {"ph-home", "ph-empty-stub"}


async def test_someone_without_the_space_sees_neither(placeholder_graph, provider) -> None:
    page = await _page(provider, user=USER_V, start_id="ph-home", start_type="record")
    assert page["rows"] == []
    assert page["scope"]["admitted"] is False
    rows = _rows(await _page(provider, user=USER_V, start_id="ph-space", start_type="recordGroup"))
    assert rows == {}


async def test_the_access_check_opens_a_stub_asked_by_id(placeholder_graph, provider) -> None:
    """Navigate asks by node id, so a stub the hub lists is openable. A search
    citation asks by virtual record id; that arm still drops a stub."""
    check = await provider.check_access(USER_U, ORG, node_ids=["ph-home", "ph-page"])
    assert check.node_ids == frozenset({"ph-home", "ph-page"})


async def test_both_backends_give_the_same_pages(placeholder_graph, neo4j_provider, arango_provider) -> None:
    for start_id, start_type in (("ph-space", "recordGroup"), ("ph-home", "record"), ("ph-page", "record")):
        expected = await _page(neo4j_provider, start_id=start_id, start_type=start_type)
        actual = await _page(arango_provider, start_id=start_id, start_type=start_type)
        assert actual == expected, start_id
