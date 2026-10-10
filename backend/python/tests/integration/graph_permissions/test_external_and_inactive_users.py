"""Who a connector's org-wide grant reaches, on both backends.

SHAREPOINT-01 / ONEDRIVE-01 / LINEAR-01: a user the connector syncs as external
(``isExternalUser`` on their gate edge: a tenant guest) is an org member, yet the
connector's org-wide grant stands for its own tenant's members and must not reach
them. Their direct grants still do, and another connector where they are a member
still hands them its org-wide grants.

GATE-NEVER-REMOVED: a user deleted from PipesHub (``isActive`` false) passes no
gate, and ``remove_app_users_except`` takes the gate from users a complete user
sync no longer lists.
"""

from __future__ import annotations

import pytest

from .fixture_graph import TS, _node, app, bt, ip, nr, perm, rec, rg
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-xg"
ORG_NODE = "xg-orgnode"
APP = "xg-app"            # the guest's tenant: they are external to it
OTHER = "xg-other"        # a connector where the guest is a member
MEMBER, GUEST, DELETED, COLLAB = "xg-member", "xg-guest", "xg-deleted", "xg-collab"
SITE, PAGE, SHARED, OTHER_SPACE = "xg-site", "xg-page", "xg-shared", "xg-other-space"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _gate(user: str, app_id: str, *, external: bool, source_id: str | None = "src") -> dict:
    props = {"syncState": "COMPLETED", "lastSyncUpdate": TS, "isExternalUser": external}
    if source_id:
        props["sourceUserId"] = f"{source_id}-{user}"
    return {"type": "USER_APP_RELATION", "from": user, "to": app_id, "props": props}


def _indexed(record_id: str) -> dict:
    return {"indexingStatus": "COMPLETED", "virtualRecordId": f"{record_id}-vr"}


def _graph() -> tuple[list[dict], list[dict]]:
    def group(app_id: str) -> dict:
        return {"connector": "SHAREPOINT ONLINE", "connectorId": app_id, "group_type": "SHAREPOINT_SITE"}

    nodes = [
        _node("Organization", ORG_NODE, with_org=False, name="XG", accountType="enterprise", isActive=True),
        *(_org(_node("User", u, userId=f"m-{u}", email=f"{u}@example.com", fullName=u, isActive=active))
          for u, active in ((MEMBER, True), (GUEST, True), (DELETED, False), (COLLAB, True))),
        _org(app(APP, "SharePoint", connector="SHAREPOINT ONLINE", app_group="Microsoft")),
        _org(app(OTHER, "Other", connector="SHAREPOINT ONLINE", app_group="Microsoft")),
        _org(rg(SITE, "EEEU site", **group(APP))),
        _org(rec(PAGE, "Page", connector_id=APP, **_indexed(PAGE))),
        _org(rec(SHARED, "Shared with the guest", connector_id=APP, **_indexed(SHARED))),
        _org(rg(OTHER_SPACE, "Org-wide elsewhere", **group(OTHER))),
    ]
    edges = [
        *(bt(u, ORG_NODE, entity_type="ORGANIZATION") for u in (MEMBER, GUEST, DELETED, COLLAB)),
        _gate(MEMBER, APP, external=False), _gate(GUEST, APP, external=True),
        _gate(DELETED, APP, external=False), _gate(COLLAB, APP, external=True, source_id=None),
        _gate(GUEST, OTHER, external=False), _gate(MEMBER, OTHER, external=False),
        nr(APP, SITE), bt(SITE, APP), perm(ORG_NODE, SITE, grant_type="ORG"),
        nr(SITE, PAGE), bt(PAGE, SITE), ip(PAGE, SITE),
        nr(SITE, SHARED), bt(SHARED, SITE), perm(GUEST, SHARED),
        nr(OTHER, OTHER_SPACE), bt(OTHER_SPACE, OTHER), perm(ORG_NODE, OTHER_SPACE, grant_type="ORG"),
    ]
    return nodes, edges


async def _clear_neo4j(provider) -> None:
    await provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'xg-' DETACH DELETE n")


async def _clear_arango(provider) -> None:
    for c in ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation"):
        await provider.http_client.execute_aql(
            "FOR e IN @@c FILTER CONTAINS(e._from, '/xg-') OR CONTAINS(e._to, '/xg-') REMOVE e IN @@c", {"@c": c})
    for c in ("apps", "recordGroups", "records", "users", "organizations"):
        await provider.http_client.execute_aql(
            "FOR d IN @@c FILTER STARTS_WITH(d._key, 'xg-') REMOVE d IN @@c", {"@c": c})


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


ASKED = [SITE, PAGE, SHARED, OTHER_SPACE]


async def _admitted(provider, user: str) -> set[str]:
    return set((await provider.check_access(user, ORG, node_ids=ASKED)).node_ids)


async def test_a_member_gets_the_org_wide_grants(provider) -> None:
    assert await _admitted(provider, MEMBER) == {SITE, PAGE, OTHER_SPACE}


async def test_an_external_user_gets_their_own_grants_and_no_org_wide_grant_of_that_connector(provider) -> None:
    assert await _admitted(provider, GUEST) == {SHARED, OTHER_SPACE}
    access = await provider.get_knowledge_hub_access_v3(GUEST, ORG)
    assert set(access["by_connector"][APP]) == {SHARED}
    assert set(access["by_connector"][OTHER]) == {OTHER_SPACE}
    assert set(await provider.get_knowledge_hub_connector_grants(GUEST, ORG, APP)) == {SHARED}
    assert set(await provider.get_knowledge_hub_connector_grants(MEMBER, ORG, APP)) == {SITE}


async def test_the_search_prefilter_skips_the_org_wide_grants_of_a_connector_the_user_is_external_to(
    provider,
) -> None:
    """R1-07: the vector filter held every org-wide record of the connector for a guest."""
    async def searchable(user: str) -> dict[str, str]:
        return await provider._get_virtual_ids_for_connector(f"m-{user}", ORG, APP, None, raise_on_error=True)

    assert await searchable(MEMBER) == {f"{PAGE}-vr": PAGE}
    assert await searchable(GUEST) == {f"{SHARED}-vr": SHARED}


async def test_a_deleted_user_passes_no_gate(provider) -> None:
    assert await _admitted(provider, DELETED) == set()
    assert await provider.get_gated_apps(DELETED, ORG) == []


async def test_users_a_complete_sync_no_longer_lists_lose_the_gate(provider) -> None:
    removed = await provider.remove_app_users_except(APP, [f"{MEMBER}@example.com"])

    assert removed == 2  # the guest and the deleted user; the collaborator's edge has no sourceUserId
    gated = {u: {a.get("id") or a.get("_key") for a in await provider.get_gated_apps(u, ORG)}
             for u in (MEMBER, GUEST, COLLAB)}
    assert gated == {MEMBER: {APP, OTHER}, GUEST: {OTHER}, COLLAB: {APP}}
    assert await _admitted(provider, GUEST) == {OTHER_SPACE}
