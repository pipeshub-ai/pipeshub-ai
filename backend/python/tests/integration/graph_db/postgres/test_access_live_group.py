"""A membership whose group no longer exists grants nothing: a source account the user signed in as keeps the grants of its groups only while each group is a node, as
the user's own groups do. Both reads of a viewer's grants follow it: the read of every grant (``get_knowledge_hub_access_v3``) and the access check's read per node."""
from __future__ import annotations

import asyncio
import contextlib
import json
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


async def test_the_reaper_keeps_a_collaborator_whose_grant_commits_while_it_decides(pg_db) -> None:
    """A writer grants x2 a record and ensures its membership in one transaction, still open while the reaper runs. The writer holds x2's edge,
    so the reaper waits for it and then sees the committed grant: x2 keeps the App."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "r1", "recordName": "r1", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com"}], "users")
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        tid = await p.begin_transaction([], [])
        committed = False
        try:
            await p.batch_create_edges([_perm("x2", "users", "r1", "records")], "permission", transaction=tid)
            await p.ensure_app_membership("x2", "users", "app1", is_external=True, transaction=tid)
            reap = asyncio.create_task(p.reap_stale_external_app_relations("app1"))
            await asyncio.sleep(1.0)      # the reaper decides (or waits on the writer's lock) while the grant is uncommitted
            await p.commit_transaction(tid)
            committed = True
            await reap
        finally:
            if not committed:
                await p.rollback_transaction(tid)
        kept = await pg_db.run(lambda tx: tx.fetch("SELECT from_id FROM graph.edges WHERE org_id = $1 AND coll = 'userAppRelation' AND to_id = 'app1'", org))
    assert [r["from_id"] for r in kept] == ["x2"]


async def _world_x(p, org: str) -> None:
    await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
    await p.batch_upsert_nodes([{"_key": "r1", "recordName": "r1", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")


async def _reap_while(p, writer_tid: str, *, in_transaction: bool = False) -> None:
    """The reaper runs while the writer's transaction is open; the writer commits; the reaper's own transaction (if any) commits only after a reaper
    that returned. A reaper that lost to the writer runs again once it has committed; any other failure is raised."""
    async def once() -> None:
        rid = await p.begin_transaction([], []) if in_transaction else None
        try:
            await p.reap_stale_external_app_relations("app1", transaction=rid)
        except BaseException:
            if rid:
                await p.rollback_transaction(rid)
            raise
        if rid:
            await p.commit_transaction(rid)
    reap = asyncio.create_task(once())
    try:
        await asyncio.sleep(1.0)
        await p.commit_transaction(writer_tid)
    except BaseException:      # the writer failed: nothing is left running, and the reaper's transaction is rolled back
        reap.cancel()
        with contextlib.suppress(BaseException):
            await reap
        with contextlib.suppress(Exception):
            await p.rollback_transaction(writer_tid)
        raise
    try:
        await reap
    except Exception as e:
        if not p.is_write_conflict(e):
            raise
        await once()


async def _app_edges(pg_db, org: str) -> dict[str, object]:
    rows = await pg_db.run(lambda tx: tx.fetch("SELECT from_id, doc->'isExternalUser' AS ext FROM graph.edges WHERE org_id = $1 AND coll = 'userAppRelation' AND to_id = 'app1'", org))
    return {r["from_id"]: json.loads(r["ext"]) if isinstance(r["ext"], str) else r["ext"] for r in rows}


async def test_the_reaper_in_its_caller_s_transaction_keeps_a_collaborator_granted_while_it_runs(pg_db) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com"}], "users")
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        tid = await p.begin_transaction([], [])
        await p.batch_create_edges([_perm("x2", "users", "r1", "records")], "permission", transaction=tid)
        await p.ensure_app_membership("x2", "users", "app1", is_external=True, transaction=tid)
        await _reap_while(p, tid, in_transaction=True)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


async def test_the_reaper_keeps_a_membership_made_ordinary_while_it_runs(pg_db) -> None:
    """x2's flagged membership is made an ordinary one in a transaction still open while the reaper runs: the reaper decides on the committed flag."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com"}], "users")
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        tid = await p.begin_transaction([], [])
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=False, syncState="COMPLETED", lastSyncUpdate=2)], "userAppRelation", transaction=tid)
        await _reap_while(p, tid)
        assert await _app_edges(pg_db, org) == {"x2": False}


async def test_a_reindex_grant_written_while_the_reaper_runs_ends_with_the_membership(pg_db) -> None:
    """A reindex writes x2's grant in one transaction (on_updated_record_permissions) and its membership in the next (on_external_app_users). A reaper
    that runs while the grant is uncommitted does not see it and drops the membership; the membership the reindex writes after its grant commits stays,
    and a reaper run after that keeps it."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com"}], "users")
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        tid = await p.begin_transaction([], [])
        await p.batch_create_edges([_perm("x2", "users", "r1", "records")], "permission", transaction=tid)
        await _reap_while(p, tid)
        assert await _app_edges(pg_db, org) == {}      # what a reindex that writes no membership leaves: x2 cannot reach the App
        await p.ensure_app_membership("x2", "users", "app1", is_external=True)
        await p.reap_stale_external_app_relations("app1")
        assert await _app_edges(pg_db, org) == {"x2": True}


async def test_a_person_granted_while_the_reaper_runs_is_not_deleted(pg_db) -> None:
    """A person held on the App only by its flagged membership. A writer takes the person (upsert_person_by_email, as the processor's principal lookup
    does for a grant) and grants it a record while the reaper runs. The reaper may drop the membership, never the person the committed grant comes from."""
    from app.models.entities import Person
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        pid = await p.upsert_person_by_email(Person(email="pers@example.com", org_id=org), raise_on_error=True)
        await p.ensure_app_membership(pid, "person", "app1", is_external=True)
        tid = await p.begin_transaction([], [])
        assert await p.upsert_person_by_email(Person(email="pers@example.com", org_id=org), transaction=tid, raise_on_error=True) == pid
        await p.batch_create_edges([_perm(pid, "person", "r1", "records")], "permission", transaction=tid)
        await _reap_while(p, tid)
        held = await pg_db.run(lambda tx: tx.fetch("SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND id = $2", org, pid))
    assert len(held) == 1
