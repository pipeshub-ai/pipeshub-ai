"""A disabled connector (App ``isActive`` false) is readable by nobody until it
is enabled again, on both backends: it does not sync, so its grants are frozen.
A KB has no enable switch and is never closed by the flag."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames

from .fixture_graph import KB, _node, app, bt, nr, perm, rec, rg, user_app
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-dis"
USER = "dis-u"
ON = "dis-app-on"
OFF = "dis-app-off"
KB_APP = "dis-kb"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    def group(app_id: str) -> dict:
        return {"connector": "JIRA", "connectorId": app_id, "group_type": "PROJECT"}

    nodes = [
        _org(_node("User", USER, userId="dis-m", email="dis@example.com", fullName="D", isActive=True)),
        _org(app(ON, "Jira on", connector="JIRA", app_group="Atlassian")),
        _org(app(OFF, "Jira off", connector="JIRA", app_group="Atlassian", isActive=False)),
        # Never written false today; the flag must still not close a collection.
        _org(app(KB_APP, "Collection", connector=KB, app_group="Local Storage", scope="personal", isActive=False)),
        _org(rg("dis-proj-on", "Project on", **group(ON))),
        _org(rec("dis-on", "On issue", connector_id=ON)),
        _org(rg("dis-proj-off", "Project off", **group(OFF))),
        _org(rec("dis-off", "Off issue", connector_id=OFF)),
    ]
    edges = [
        user_app(USER, ON), user_app(USER, OFF), perm(USER, KB_APP, role="OWNER"),
        nr(ON, "dis-proj-on"), bt("dis-proj-on", ON), nr("dis-proj-on", "dis-on"), bt("dis-on", "dis-proj-on"),
        perm(USER, "dis-on"),
        nr(OFF, "dis-proj-off"), bt("dis-proj-off", OFF), nr("dis-proj-off", "dis-off"),
        bt("dis-off", "dis-proj-off"), perm(USER, "dis-off"),
    ]
    return nodes, edges


async def _clear_neo4j(provider) -> None:
    await provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'dis-' DETACH DELETE n")


async def _clear_arango(provider) -> None:
    for c in ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation"):
        await provider.http_client.execute_aql(
            "FOR e IN @@c FILTER CONTAINS(e._from, '/dis-') OR CONTAINS(e._to, '/dis-') REMOVE e IN @@c", {"@c": c})
    for c in ("apps", "recordGroups", "records", "users"):
        await provider.http_client.execute_aql(
            "FOR d IN @@c FILTER STARTS_WITH(d._key, 'dis-') REMOVE d IN @@c", {"@c": c})


@pytest.fixture(params=["neo4j", "arango"])
async def provider(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    nodes, edges = _graph()
    if request.param == "neo4j":
        p, clear = neo4j_provider, _clear_neo4j
        await clear(p)
        await load_into_neo4j(neo4j_settings, nodes, edges)
    else:
        p, clear = arango_provider, _clear_arango
        await clear(p)
        await load_into_arango(arango_settings, nodes, edges)
    yield p
    await clear(p)


async def _set_active(provider, app_id: str, active: bool) -> None:
    await provider.batch_upsert_nodes([{"_key": app_id, "id": app_id, "isActive": active}], collection=CollectionNames.APPS.value)


async def test_a_disabled_connector_is_closed_and_a_kb_is_not(provider) -> None:
    access = await provider.get_knowledge_hub_access_v3(USER, ORG)
    assert ON in access["gated_app_ids"]
    assert KB_APP in access["gated_app_ids"]
    assert OFF not in access["gated_app_ids"]
    assert OFF not in {a.get("id") or a.get("_key") for a in await provider.get_gated_apps(USER, ORG)}
    admitted = set((await provider.check_access(USER, ORG, node_ids=["dis-on", "dis-off", OFF])).node_ids)
    assert admitted == {"dis-on"}


async def test_enabling_it_again_reopens_it(provider) -> None:
    await _set_active(provider, OFF, True)
    admitted = set((await provider.check_access(USER, ORG, node_ids=["dis-on", "dis-off"])).node_ids)
    assert admitted == {"dis-on", "dis-off"}
