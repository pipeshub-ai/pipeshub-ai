"""A group that hides its children hides the whole subtree from listings,
including a declared (RECORD_GROUP_LEVEL) group below it that the user holds a
direct grant on, and that group's records: a GitLab project with hideChildren
must not list its Code repository files in the flatten, so the granted declared
group needs a hidden-ancestor test. The check still admits everything
(hideChildren is navigation only)."""

from __future__ import annotations

import pytest

from .fixture_graph import _node, app, bt, nr, perm, rec, rg
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-had"
USER = "had-u"
APP = "had-app"
RGL = "RECORD_GROUP_LEVEL"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    group = {"connector": "DRIVE", "connectorId": APP, "group_type": "DRIVE"}
    item = {"connector_id": APP}
    nodes = [
        _org(_node("User", USER, userId="had-m", email="had@example.com", fullName="U")),
        _org(app(APP, "Drive", connector="DRIVE", app_group="Google Workspace", scope="team")),
        _org(rg("had-project", "Project", hideChildren=True, **group)),
        _org(rg("had-code", "Code repository", permissionModel=RGL, **group)),
        _org(rg("had-issues", "Work items", **group)),
        _org(rec("had-file", "main.c", **item)),
        _org(rec("had-issue", "Issue 1", **item)),
    ]
    edges = [
        perm(USER, APP),
        nr(APP, "had-project"), bt("had-project", APP), perm(USER, "had-project"),
        nr("had-project", "had-code"), bt("had-code", "had-project"), perm(USER, "had-code"),
        nr("had-project", "had-issues"), bt("had-issues", "had-project"), perm(USER, "had-issues"),
        nr("had-code", "had-file"), bt("had-file", "had-code"),
        nr("had-issues", "had-issue"), bt("had-issue", "had-issues"), perm(USER, "had-issue"),
    ]
    return nodes, edges


@pytest.fixture(params=["neo4j", "arango"])
async def provider(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    nodes, edges = _graph()
    if request.param == "neo4j":
        await neo4j_provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'had-' DETACH DELETE n")
        await load_into_neo4j(neo4j_settings, nodes, edges)
        yield neo4j_provider
        await neo4j_provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'had-' DETACH DELETE n")
    else:
        await load_into_arango(arango_settings, nodes, edges)
        yield arango_provider
        for c in ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation"):
            await arango_provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/had-') OR CONTAINS(e._to, '/had-') REMOVE e IN @@c", {"@c": c})
        for c in ("apps", "recordGroups", "records", "users"):
            await arango_provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'had-') REMOVE d IN @@c", {"@c": c})


async def _flatten(provider) -> set[str]:
    access = await provider.get_knowledge_hub_access_v3(USER, ORG)
    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=APP, org_id=ORG, grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"], limit=500, flatten=True, sort_field="name",
        sort_dir="ASC", include_total=True,
    )
    return {row["id"] for row in page["rows"]}


async def test_a_hidden_group_hides_a_granted_declared_group_below_it(provider) -> None:
    assert await _flatten(provider) == {"had-project"}
    admitted = set((await provider.check_access(
        USER, ORG, node_ids=["had-code", "had-file", "had-issue"])).node_ids)
    assert admitted == {"had-code", "had-file", "had-issue"}
