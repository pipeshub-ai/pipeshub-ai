"""Browsing a record of a RECORD_GROUP_LEVEL group lists what the check admits.

The check admits by membership: a record belonging to a declared group the user
is admitted to opens, whether or not a hierarchy edge links it to the group.
Browse built the declared scope from hierarchy ancestors only, so a record that
belongs to the group without hanging off it lost its children.

    App -> group D (RECORD_GROUP_LEVEL, granted to the user's group)
    S belongs to D (no hierarchy edge from D)
    S -> C (C belongs to D, neither inherits nor is granted)
"""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import (
    GROUP_G,
    ORG,
    USER_U,
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

APP = "dm-app"
GROUP = "dm-group"


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app(APP, "Mail", connector="GMAIL", app_group="Google Workspace"),
        rg(GROUP, "Mailbox", group_type="MAILBOX", connector="GMAIL", connectorId=APP,
           permissionModel="RECORD_GROUP_LEVEL"),
        rec("dm-s", "Thread", connector_id=APP, recordGroupId=GROUP),
        rec("dm-c", "Attachment", connector_id=APP, recordGroupId=GROUP),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP), nr(APP, GROUP), ip(GROUP, APP), perm(GROUP_G, GROUP, grant_type="GROUP"),
        bt("dm-s", GROUP), bt("dm-c", GROUP), nr("dm-s", "dm-c"),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def membership_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def test_browsing_the_record_lists_what_the_check_admits(membership_graph, provider: Any) -> None:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    assert set((await provider.check_access(USER_U, ORG, node_ids=["dm-s", "dm-c"])).node_ids) == {"dm-s", "dm-c"}

    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=APP, org_id=ORG,
        grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"],
        limit=50, flatten=False, sort_field="name", sort_dir="ASC", include_total=True,
        start_id="dm-s", start_type="record",
    )

    assert {row["id"] for row in page["rows"]} == {"dm-c"}
