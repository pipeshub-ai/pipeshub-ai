"""An arrow, a container-only sidebar and a linked record's children say only what the viewer can read.

``hasChildren`` used to be "has a live child", so a page whose children were all
hidden from the viewer still showed an expand arrow, and the sidebar kept it as a
container.

    App -> space -> mixed   -> readable child, hidden child
                 -> closed  -> hidden child only
                 -> leaf
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

APP = "vc-app"
SPACE = "vc-space"
PAGE = {"record_type": "CONFLUENCE_PAGE", "connector_id": APP}


def _child(parent: str, child: str) -> list[dict]:
    return [nr(parent, child), ip(child, parent), bt(child, SPACE)]


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app(APP, "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        rg(SPACE, "Space", group_type="CONFLUENCE_SPACES", connector=CONFLUENCE, connectorId=APP),
        *[rec(f"vc-{n}", n, **PAGE) for n in ("mixed", "closed", "leaf", "readable", "hidden", "hidden-only")],
        rec("vc-source", "Source", **PAGE),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP), user_app(USER_V, APP),
        nr(APP, SPACE), ip(SPACE, APP), perm(GROUP_G, SPACE, grant_type="GROUP"),
        *[e for page in ("vc-mixed", "vc-closed", "vc-leaf") for e in _child(SPACE, page)],
        *_child("vc-mixed", "vc-readable"),
        # No inheritance edge and no grant: the viewer cannot read these two.
        nr("vc-mixed", "vc-hidden"), bt("vc-hidden", SPACE),
        nr("vc-closed", "vc-hidden-only"), bt("vc-hidden-only", SPACE),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def children_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    for target in ("vc-mixed", "vc-closed"):
        await neo4j_provider.client.execute_query(
            "MATCH (a:Record {id: 'vc-source'}), (b:Record {id: $b}) "
            "CREATE (a)-[:RECORD_LINK {relationshipType: 'LINKED_TO'}]->(b)", parameters={"b": target},
        )
        await arango_provider.http_client.execute_aql(
            "INSERT {_from: 'records/vc-source', _to: CONCAT('records/', @b), relationshipType: 'LINKED_TO'} "
            "INTO recordLinks", {"b": target},
        )


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
        "include_total": True, "start_id": SPACE, "start_type": "recordGroup",
    }
    kwargs.update(overrides)
    page = await provider.get_knowledge_hub_connector_page_v3(**kwargs)
    return {row["id"]: row for row in page["rows"]}


async def test_an_arrow_needs_a_child_the_viewer_can_read(children_graph, provider) -> None:
    rows = await _rows(provider)

    assert {k: rows[k]["hasChildren"] for k in ("vc-mixed", "vc-closed", "vc-leaf")} == {
        "vc-mixed": True, "vc-closed": False, "vc-leaf": False,
    }


async def test_the_flattened_list_gives_the_same_arrows(children_graph, provider) -> None:
    rows = await _rows(provider, flatten=True, start_id=APP, start_type="app")

    assert rows["vc-mixed"]["hasChildren"] is True
    assert rows["vc-closed"]["hasChildren"] is False


async def test_the_sidebar_drops_a_page_whose_children_are_all_hidden(children_graph, provider) -> None:
    rows = await _rows(provider, filters={"only_containers": True})

    assert set(rows) == {"vc-mixed"}


async def test_opening_the_page_lists_the_readable_child_only(children_graph, provider) -> None:
    rows = await _rows(provider, start_id="vc-mixed", start_type="record")

    assert set(rows) == {"vc-readable"}


async def test_linked_records_report_only_readable_children(children_graph, provider) -> None:
    linked = await provider.get_linked_records("vc-source", ORG, USER_U, ["LINKED_TO"])

    assert {item["id"]: item["hasChildren"] for item in linked} == {"vc-mixed": True, "vc-closed": False}
    assert all("childIds" not in item for item in linked)
