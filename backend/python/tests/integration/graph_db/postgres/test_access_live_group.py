"""A membership whose group no longer exists grants nothing: a source account the user signed in as keeps the grants of its groups only while each group is a node, as
the user's own groups do. Both reads of a viewer's grants follow it: the read of every grant (``get_knowledge_hub_access_v3``) and the access check's read per node."""
from __future__ import annotations

import logging

import pytest

from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org
from .test_kg_derivation import ctx, edge

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


def _perm(frm: str, fc: str, to: str, tc: str, t: str = "USER") -> dict:
    return edge(frm, fc, to, tc, type=t, role="READER")


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_membership_whose_group_no_longer_exists_grants_nothing(pg_db, monkeypatch, member) -> None:
    """u1 signed in to app1 as src. src is a member of g (a node) and of gone (none: its node was deleted). Each grants a record; only g's counts, on either read."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    monkeypatch.setenv("PG_KG_READ", "0")      # the walk's answer, whatever kg holds
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k in ("r1", "r2")], "records")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("u1", "src")], "users")
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("u1", "users", "src", "users", connectorId="app1")], "authenticatedAs")
        await p.batch_create_edges([_perm("src", "users", "g", member), _perm("src", "users", "gone", member),
                                    _perm("g", member, "r1", "records", "GROUP"), _perm("gone", member, "r2", "records", "GROUP")], "permission")
        access = await p.get_knowledge_hub_access_v3("u1", org)
        checked = await p.check_access("u1", org, node_ids={"r1", "r2"})
    assert access["by_connector"]["app1"] == ["r1"]
    assert set(checked.node_ids) == {"r1"}


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_the_collaborator_reaper_keeps_only_whom_an_existing_group_still_grants(pg_db, member) -> None:
    """External collaborators of app1: x1 held by a grant of g (a node), x2 by one of gone (none), x3 by a grant of its own; x0 an ordinary member with
    no grant. The reaper removes x2 alone."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k in ("r1", "r2")], "records")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("x0", "x1", "x2", "x3")], "users")
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        await p.batch_create_edges([*(edge(x, "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1) for x in ("x1", "x2", "x3")),
                                    edge("x0", "users", "app1", "apps", isExternalUser=False, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        await p.batch_create_edges([_perm("x1", "users", "g", member), _perm("x2", "users", "gone", member), _perm("x3", "users", "r1", "records"),
                                    _perm("g", member, "r1", "records", "GROUP"), _perm("gone", member, "r2", "records", "GROUP")], "permission")
        await p.reap_stale_external_app_relations("app1")
        kept = await pg_db.run(lambda tx: tx.fetch("SELECT from_id FROM graph.edges WHERE org_id = $1 AND coll = 'userAppRelation' AND to_id = 'app1'", org))
    assert sorted(r["from_id"] for r in kept) == ["x0", "x1", "x3"]


async def test_a_team_that_no_longer_exists_opens_no_collection_app_or_agent_template(pg_db) -> None:
    """u is a member of the team t (a node) and of gone (none). Each is shared a collection, a team connector and an agent template; only t's are u's."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": org, "name": "Org", "orgId": org}], "organizations")
        await p.batch_upsert_nodes([{"_key": "u", "orgId": org, "email": "u@example.com", "userId": "u-uid"}], "users")
        await p.batch_upsert_nodes([{"_key": "t", "name": "t", "orgId": org}], "teams")
        await p.batch_upsert_nodes([*({"_key": f"k-{h}", "name": f"kb {h}", "type": "KB", "orgId": org, "createdAtTimestamp": 1, "updatedAtTimestamp": 1} for h in ("t", "gone")),
                                    *({"_key": f"a-{h}", "name": f"app {h}", "type": "DRIVE", "scope": "team", "orgId": org, "createdAtTimestamp": 1} for h in ("t", "gone"))], "apps")
        await p.batch_upsert_nodes([{"_key": f"tp-{h}", "name": f"tp {h}", "description": "d", "startMessage": "s", "systemPrompt": "p", "isDeleted": False,
                                     "createdAtTimestamp": 1, "updatedAtTimestamp": 1} for h in ("t", "gone")], "agentTemplates")
        await p.batch_create_edges([edge(org, "organizations", f"a-{h}", "apps") for h in ("t", "gone")], "orgAppRelation")
        await p.batch_create_edges([edge(h, "teams", f"a-{h}", "apps", syncState="COMPLETED", lastSyncUpdate=1) for h in ("t", "gone")], "userAppRelation")
        await p.batch_create_edges([_perm("u", "users", "t", "teams"), _perm("u", "users", "gone", "teams"),
                                    *(edge(h, "teams", f"k-{h}", "apps", type="TEAM", role="WRITER") for h in ("t", "gone")),
                                    *(edge(h, "teams", f"tp-{h}", "agentTemplates", type="TEAM", role="READER") for h in ("t", "gone"))], "permission")
        kbs, _, _ = await p.list_user_knowledge_bases("u", org, 0, 100)
        instances, _ = await p.get_filtered_connector_instances("apps", "orgAppRelation", org, "u-uid", scope="team")
        got = {
            "kb role": {h: await p.get_user_kb_permission(f"k-{h}", "u") for h in ("t", "gone")},
            "kbs listed": {k["id"] for k in kbs},
            "kbs shared": set(await p.get_user_permission_app_ids("u", org)),
            "apps": {a for a in await p.get_user_app_ids("u") if a.startswith("a-")},
            "team connectors": {i.get("_key") or i.get("id") for i in instances},
            "templates": {t.get("_key") or t.get("id") for t in await p.get_all_agent_templates("u")},
            "template": {h: await p.get_template(f"tp-{h}", "u") is not None for h in ("t", "gone")},
        }
    assert got == {"kb role": {"t": "WRITER", "gone": None}, "kbs listed": {"k-t"}, "kbs shared": {"k-t"}, "apps": {"a-t"}, "team connectors": {"a-t"},
                   "templates": {"tp-t"}, "template": {"t": True, "gone": False}}
