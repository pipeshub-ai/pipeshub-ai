"""The connectors a user sees (tool presence, the connector list) come from
their own org only, on both backends."""

from __future__ import annotations

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors

from .fixture_graph import _node, app, bt, perm, user_app
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

TS = 1700000000000


@pytest.fixture(params=["arango", "neo4j"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def test_another_orgs_team_connectors_are_not_listed(provider) -> None:
    suffix = uuid.uuid4().hex[:8]
    mine, theirs = f"org-a-{suffix}", f"org-b-{suffix}"
    apps = [
        {"_key": f"pg-{org}", "orgId": org, "name": f"Postgres {org}", "type": Connectors.POSTGRESQL.value,
         "appGroup": "Database", "scope": "team", "isActive": True, "createdAtTimestamp": TS}
        for org in (mine, theirs)
    ]
    await provider.batch_upsert_nodes(apps, collection=CollectionNames.APPS.value)

    listed = await provider.get_user_connector_instances(
        collection=CollectionNames.APPS.value, user_id="u-1", org_id=mine,
        team_scope="team", personal_scope="personal",
    )

    ids = {a.get("_key") or a.get("id") for a in listed}
    assert f"pg-{mine}" in ids
    assert f"pg-{theirs}" not in ids


async def test_the_filtered_connector_list_stays_in_the_callers_org(provider) -> None:
    """``GET /api/v1/connectors/?scope=team`` (and /configured, /agents/active) list
    through ``get_filtered_connector_instances``, which ignored ``org_id``: an org
    admin listed every team connector in the database. An App with no orgId
    (not yet backfilled) is found through its org relation."""
    suffix = uuid.uuid4().hex[:8]
    mine, theirs = f"org-f-{suffix}", f"org-x-{suffix}"
    unstamped = f"unstamped-{suffix}"
    await provider.batch_upsert_nodes(
        [{"_key": org, "name": org, "accountType": "enterprise", "isActive": True, "createdAtTimestamp": TS}
         for org in (mine, theirs)],
        collection=CollectionNames.ORGS.value,
    )
    await provider.batch_upsert_nodes(
        [{"_key": f"pgf-{org}", "orgId": org, "name": f"Postgres {org}", "type": Connectors.POSTGRESQL.value,
          "appGroup": "Database", "scope": "team", "isActive": True, "createdAtTimestamp": TS}
         for org in (mine, theirs)]
        + [{"_key": unstamped, "name": f"Unstamped {suffix}", "type": Connectors.POSTGRESQL.value,
            "appGroup": "Database", "scope": "team", "isActive": True, "createdAtTimestamp": TS}],
        collection=CollectionNames.APPS.value,
    )
    await provider.batch_create_edges(
        [{"from_id": mine, "from_collection": CollectionNames.ORGS.value, "to_id": unstamped,
          "to_collection": CollectionNames.APPS.value, "createdAtTimestamp": TS}],
        collection=CollectionNames.ORG_APP_RELATION.value,
    )

    docs, total = await provider.get_filtered_connector_instances(
        collection=CollectionNames.APPS.value, edge_collection=CollectionNames.USER_APP_RELATION.value,
        org_id=mine, user_id="u-1", scope="team", search=suffix, limit=50, is_admin=True,
    )
    ids = {d.get("_key") or d.get("id") for d in docs}
    assert f"pgf-{mine}" in ids and unstamped in ids
    assert f"pgf-{theirs}" not in ids
    assert total == len(ids)


async def test_a_connector_from_before_org_ids_is_listed_after_the_backfill(provider) -> None:
    """Apps created before instances carried an orgId: the backfill takes it from
    the one organization linked to the App. An App linked from two organizations
    is left alone."""
    suffix = uuid.uuid4().hex[:8]
    mine, theirs = f"org-c-{suffix}", f"org-d-{suffix}"
    legacy, shared = f"legacy-{suffix}", f"shared-{suffix}"
    await provider.batch_upsert_nodes(
        [{"_key": org, "name": org, "accountType": "enterprise", "isActive": True, "createdAtTimestamp": TS}
         for org in (mine, theirs)],
        collection=CollectionNames.ORGS.value,
    )
    await provider.batch_upsert_nodes(
        [{"_key": key, "name": key, "type": Connectors.POSTGRESQL.value, "appGroup": "Database",
          "scope": "team", "isActive": True, "createdAtTimestamp": TS} for key in (legacy, shared)],
        collection=CollectionNames.APPS.value,
    )
    await provider.batch_create_edges(
        [{"from_id": org, "from_collection": CollectionNames.ORGS.value,
          "to_id": app_key, "to_collection": CollectionNames.APPS.value, "createdAtTimestamp": TS}
         for org, app_key in ((mine, legacy), (mine, shared), (theirs, shared))],
        collection=CollectionNames.ORG_APP_RELATION.value,
    )

    async def listed() -> set[str]:
        apps = await provider.get_user_connector_instances(
            collection=CollectionNames.APPS.value, user_id="u-1", org_id=mine,
            team_scope="team", personal_scope="personal",
        )
        return {a.get("_key") or a.get("id") for a in apps}

    assert legacy not in await listed()
    result = await provider.backfill_app_org_ids()
    assert result["backfilled"] >= 1 and result["ambiguous"] >= 1
    assert legacy in await listed()
    assert shared not in await listed()
    assert (await provider.backfill_app_org_ids())["backfilled"] == 0


async def test_every_gate_entry_point_admits_the_same_apps(provider, neo4j_settings, arango_settings) -> None:
    """The connector gate has one definition: the hub (v3 and v2 contexts), the
    connector list for agents and the containers all see the same Apps. Through
    a team's user-app relation too, and never another org's."""
    suffix = uuid.uuid4().hex[:8]
    org, other = f"org-g-{suffix}", f"org-h-{suffix}"
    ids = {name: f"{name}-{suffix}" for name in ("u", "t", "g", "direct", "team", "group", "kb", "outside", "foreign")}

    def _app(key: str, connector: str, app_org: str = org) -> dict:
        return app(ids[key], key, connector=connector, app_group="Test", orgId=app_org)

    nodes = [
        _node("User", ids["u"], orgId=org, userId=f"m-{suffix}", email=f"g-{suffix}@example.com", fullName="Gate"),
        _node("Teams", ids["t"], orgId=org, name="Team"),
        _node("Group", ids["g"], orgId=org, name="Group"),
        _node("Organization", org, with_org=False, name="Org", accountType="enterprise", isActive=True),
        _app("direct", "DRIVE"), _app("team", "SLACK"), _app("group", "JIRA"), _app("kb", "KB"),
        _app("outside", "CONFLUENCE"), _app("foreign", "DRIVE", app_org=other),
    ]
    edges = [
        perm(ids["u"], ids["t"]), perm(ids["u"], ids["g"]), bt(ids["u"], org, entity_type="ORGANIZATION"),
        user_app(ids["u"], ids["direct"]), user_app(ids["t"], ids["team"]),
        perm(ids["g"], ids["group"], grant_type="GROUP"), perm(ids["t"], ids["kb"], grant_type="TEAM"),
        {"type": "ORG_APP_RELATION", "from": org, "to": ids["outside"], "props": {"createdAtTimestamp": TS}},
        user_app(ids["u"], ids["foreign"]),
    ]
    arango = provider.__class__.__name__.startswith("Arango")
    if arango:
        await load_into_arango(arango_settings, nodes, edges)
    else:
        await load_into_neo4j(neo4j_settings, nodes, edges)
    expected = {ids["direct"], ids["team"], ids["group"], ids["kb"]}
    user_key = ids["u"]

    v3 = await provider.get_knowledge_hub_access_v3(user_key, org)
    v2 = await provider.get_knowledge_hub_access_context_v2(user_key, org)
    user_apps = await provider.get_user_apps(user_key)
    gated = await provider.get_gated_apps(user_key, org)
    types = await provider.get_accessible_connector_types(f"m-{suffix}", org)

    assert set(v3["gated_app_ids"]) == expected
    assert set(v2["gated_app_ids"]) == expected
    assert {a.get("id") or a.get("_key") for a in user_apps} == expected
    assert {a.get("id") or a.get("_key") for a in gated} == expected
    assert set(types) == {"DRIVE", "SLACK", "JIRA", "KB"}
    assert set(v3["grantee_ids"]) == {ids["u"], ids["t"], ids["g"], org}
    assert await provider.get_gated_apps(user_key, "") == []
