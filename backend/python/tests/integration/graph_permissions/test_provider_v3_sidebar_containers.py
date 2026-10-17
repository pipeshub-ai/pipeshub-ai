"""The sidebar lists a record that has children, so the records below it can be opened.

The sidebar asks each level for containers only (``only_containers``): Apps,
record groups, folders, and records that have children. The listing decided
that on a row built before the page is cut, where ``hasChildren`` was always
false, so a Notion page or a Confluence page with child pages never appeared in
the tree, while the main list showed its children.

    App -> space -> page -> child page
                 -> homepage stub -> child page
                 -> leaf page
                 -> page whose only child is deleted
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

APP = "sc-app"
PAGE = {"record_type": "CONFLUENCE_PAGE", "connector_id": APP}
SPACE = {"group_type": "CONFLUENCE_SPACES", "connector": CONFLUENCE, "connectorId": APP}


def _child(parent: str, child: str) -> list[dict]:
    return [nr(parent, child), ip(child, parent), bt(child, "sc-space")]


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app(APP, "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        rg("sc-space", "Software Development", **SPACE),
        rec("sc-parent", "Buffer's OKRs", **PAGE),
        rec("sc-parent-child", "Key results", **PAGE),
        rec("sc-stub", "Software Development", isPlaceholder=True, **PAGE),
        rec("sc-stub-child", "Connectors", **PAGE),
        rec("sc-leaf", "New page", **PAGE),
        rec("sc-gone-parent", "Archive", **PAGE),
        rec("sc-gone-child", "Old notes", isDeleted=True, **PAGE),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP), user_app(USER_V, APP),
        nr(APP, "sc-space"), ip("sc-space", APP), perm(GROUP_G, "sc-space", grant_type="GROUP"),
        *[edge for page in ("sc-parent", "sc-stub", "sc-leaf", "sc-gone-parent") for edge in _child("sc-space", page)],
        *_child("sc-parent", "sc-parent-child"),
        *_child("sc-stub", "sc-stub-child"),
        *_child("sc-gone-parent", "sc-gone-child"),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def containers_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _rows(provider: Any, **overrides: Any) -> dict[str, dict]:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    kwargs = {
        "app_id": APP, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 100, "flatten": False, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True, "start_id": "sc-space", "start_type": "recordGroup",
    }
    kwargs.update(overrides)
    page = await provider.get_knowledge_hub_connector_page_v3(**kwargs)
    return {row["id"]: row for row in page["rows"]}


async def test_the_sidebar_lists_the_records_that_have_children(containers_graph, provider) -> None:
    rows = await _rows(provider, filters={"only_containers": True})

    assert set(rows) == {"sc-parent", "sc-stub"}
    assert all(row["hasChildren"] is True for row in rows.values())


async def test_a_record_whose_children_are_all_deleted_is_a_leaf(containers_graph, provider) -> None:
    rows = await _rows(provider)

    assert rows["sc-gone-parent"]["hasChildren"] is False
    assert rows["sc-leaf"]["hasChildren"] is False


async def test_the_main_list_still_lists_every_child(containers_graph, provider) -> None:
    rows = await _rows(provider)

    assert set(rows) == {"sc-parent", "sc-stub", "sc-leaf", "sc-gone-parent"}
