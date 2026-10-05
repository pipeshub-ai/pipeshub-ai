"""A connector's creator who authenticated it as another source account (the
AUTHENTICATED_AS link, upstream #3313) sees what that account sees, inside that
connector only: the link opens the connector's gate, and the account's grants,
and its groups', count there and nowhere else. On the new model this enters
through the access context, so the listing, the batch check and everything built
on them agree."""

from __future__ import annotations

import pytest

from .fixture_graph import _node, app, authenticated_as, bt, nr, perm, rec, rg, user_app
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-shu"
CREATOR = "shu-creator"
SOURCE = "shu-source"
LINKED = "shu-app-linked"
OTHER = "shu-app-other"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph(*, linked: bool = True) -> tuple[list[dict], list[dict]]:
    def group(app_id: str) -> dict:
        return {"connector": "JIRA", "connectorId": app_id, "group_type": "PROJECT"}

    nodes = [
        _org(_node("User", CREATOR, userId="shu-m-creator", email="creator@example.com", fullName="C")),
        _org(_node("User", SOURCE, userId="shu-m-source", email="svc@source.example", fullName="S")),
        _org(_node("Group", "shu-group", name="Source team")),
        _org(app(LINKED, "Jira linked", connector="JIRA", app_group="Atlassian")),
        _org(app(OTHER, "Jira other", connector="JIRA", app_group="Atlassian")),
        _org(rg("shu-proj", "Project", **group(LINKED))),
        _org(rec("shu-direct", "Direct issue", connector_id=LINKED)),
        _org(rec("shu-via-group", "Group issue", connector_id=LINKED)),
        _org(rec("shu-internal", "Internal stub", connector_id=LINKED, isInternal=True)),
        _org(rg("shu-other-proj", "Other project", **group(OTHER))),
        _org(rec("shu-other", "Other issue", connector_id=OTHER)),
    ]
    edges = [
        user_app(SOURCE, LINKED), user_app(SOURCE, OTHER),
        nr(LINKED, "shu-proj"), bt("shu-proj", LINKED),
        nr("shu-proj", "shu-direct"), bt("shu-direct", "shu-proj"), perm(SOURCE, "shu-direct"),
        nr("shu-proj", "shu-via-group"), bt("shu-via-group", "shu-proj"),
        perm(SOURCE, "shu-group"), perm("shu-group", "shu-via-group", grant_type="GROUP"),
        nr("shu-proj", "shu-internal"), bt("shu-internal", "shu-proj"), perm(SOURCE, "shu-internal"),
        nr(OTHER, "shu-other-proj"), bt("shu-other-proj", OTHER),
        nr("shu-other-proj", "shu-other"), bt("shu-other", "shu-other-proj"), perm(SOURCE, "shu-other"),
    ]
    if linked:
        edges.append(authenticated_as(CREATOR, SOURCE, LINKED))
    return nodes, edges


async def _clear_neo4j(provider) -> None:
    await provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'shu-' DETACH DELETE n")


async def _clear_arango(provider) -> None:
    for c in ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation",
              "authenticatedAs"):
        await provider.http_client.execute_aql(
            "FOR e IN @@c FILTER CONTAINS(e._from, '/shu-') OR CONTAINS(e._to, '/shu-') REMOVE e IN @@c", {"@c": c})
    for c in ("apps", "recordGroups", "records", "users", "groups"):
        await provider.http_client.execute_aql(
            "FOR d IN @@c FILTER STARTS_WITH(d._key, 'shu-') REMOVE d IN @@c", {"@c": c})


async def _load(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings, *, linked: bool):
    nodes, edges = _graph(linked=linked)
    if request.param == "neo4j":
        await _clear_neo4j(neo4j_provider)
        await load_into_neo4j(neo4j_settings, nodes, edges)
        return neo4j_provider, _clear_neo4j
    await _clear_arango(arango_provider)
    await load_into_arango(arango_settings, nodes, edges)
    return arango_provider, _clear_arango


@pytest.fixture(params=["neo4j", "arango"])
async def provider(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    p, clear = await _load(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings, linked=True)
    yield p
    await clear(p)


@pytest.fixture(params=["neo4j", "arango"])
async def unlinked(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    p, clear = await _load(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings, linked=False)
    yield p
    await clear(p)


async def _flatten(provider, app_id: str) -> set[str]:
    access = await provider.get_knowledge_hub_access_v3(CREATOR, ORG)
    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=app_id, org_id=ORG, grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"], limit=500, flatten=True, sort_field="name",
        sort_dir="ASC", include_total=True,
    )
    return {row["id"] for row in page["rows"]}


async def test_the_link_opens_its_connector_and_no_other(provider) -> None:
    access = await provider.get_knowledge_hub_access_v3(CREATOR, ORG)
    assert LINKED in access["gated_app_ids"]
    assert OTHER not in access["gated_app_ids"]
    assert set(access["by_connector"][LINKED]) >= {"shu-direct", "shu-via-group"}
    assert OTHER not in access["by_connector"]
    assert {a.get("id") or a.get("_key") for a in await provider.get_gated_apps(CREATOR, ORG)} == {LINKED}


async def test_the_source_accounts_grants_count_inside_the_linked_connector_only(provider) -> None:
    admitted = set((await provider.check_access(
        CREATOR, ORG, node_ids=["shu-direct", "shu-via-group", "shu-other"])).node_ids)
    assert admitted == {"shu-direct", "shu-via-group"}
    assert {"shu-direct", "shu-via-group"} <= await _flatten(provider, LINKED)


async def test_traversal_filter_drops_internal_records(provider) -> None:
    """``filter_accessible_record_ids`` (upstream #3590) keeps the batch check's
    answer minus internal stubs."""
    assert "shu-internal" in (await provider.check_access(CREATOR, ORG, node_ids=["shu-internal"])).node_ids
    readable = await provider.filter_accessible_record_ids(
        ["shu-direct", "shu-internal", "shu-other"], "shu-m-creator", ORG)
    assert readable == {"shu-direct"}


async def test_without_the_link_the_creator_sees_nothing(unlinked) -> None:
    access = await unlinked.get_knowledge_hub_access_v3(CREATOR, ORG)
    assert access["gated_app_ids"] == []
    admitted = (await unlinked.check_access(
        CREATOR, ORG, node_ids=["shu-direct", "shu-via-group", "shu-other"])).node_ids
    assert not admitted
