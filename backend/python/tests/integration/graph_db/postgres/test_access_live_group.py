"""A membership whose group no longer exists grants nothing: a source account the user signed in as keeps the grants of its groups only while each group is a node, as
the user's own groups do. Both reads of a viewer's grants follow it: the read of every grant (``get_knowledge_hub_access_v3``) and the access check's read per node."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
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
        await reap_twice(p)
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
            await _until_waiting_on(p, pg_db, tid, reap)      # the reaper waits on the writer's lock while the grant is uncommitted
            await p.commit_transaction(tid)
            committed = True
            await reap
        finally:
            if not committed:
                await p.rollback_transaction(tid)
        kept = await pg_db.run(lambda tx: tx.fetch("SELECT from_id FROM graph.edges WHERE org_id = $1 AND coll = 'userAppRelation' AND to_id = 'app1'", org))
    assert [r["from_id"] for r in kept] == ["x2"]


async def _until_waiting_on(p, pg_db, tid: str, task: asyncio.Task) -> None:
    """Returns once another session waits on a lock ``tid`` holds; fails if none does within 10 s (a sleep would pass whether or not it waited)."""
    holder = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), tid)
    for _ in range(200):
        assert not task.done() or task.exception() is None, "the waiter failed instead of waiting"
        assert not task.done(), "it finished without waiting on the lock"
        if await pg_db.run(lambda tx: tx.fetchval("SELECT count(*) FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid))", holder)):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("nothing waited on the lock")


async def _world_x(p, org: str) -> None:
    await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
    await p.batch_upsert_nodes([{"_key": "r1", "recordName": "r1", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")


async def _reap_while(p, writer_tid: str, *, pg_db, in_transaction: bool = False) -> None:
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
        await _until_waiting_on(p, pg_db, writer_tid, reap)
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
        await _reap_while(p, tid, in_transaction=True, pg_db=pg_db)
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
        await _reap_while(p, tid, pg_db=pg_db)
        assert await _app_edges(pg_db, org) == {"x2": False}


async def test_a_person_granted_while_the_reaper_runs_is_not_deleted(pg_db) -> None:
    """A person held on the App only by its flagged membership. A writer takes the person (upsert_person_by_email, as the processor's principal lookup
    does for a grant) and grants it a record while the reaper runs. The reaper may drop the membership, never the person the committed grant comes from."""
    from app.models.entities import Person
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        pid = await p.upsert_person_by_email(Person(email="pers@example.com", org_id=org), raise_on_error=True)
        await p.ensure_app_membership(pid, "person", "app1", is_external=True)
        await p.reap_stale_external_app_relations("app1")      # the first pass marks the membership: the one below sweeps
        tid = await p.begin_transaction([], [])
        assert await p.upsert_person_by_email(Person(email="pers@example.com", org_id=org), transaction=tid, raise_on_error=True) == pid
        await p.batch_create_edges([_perm(pid, "person", "r1", "records")], "permission", transaction=tid)
        await _reap_while(p, tid, pg_db=pg_db)
        held = await pg_db.run(lambda tx: tx.fetch("SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND id = $2", org, pid))
    assert len(held) == 1


async def _x2_flagged(p, org: str) -> None:
    await _world_x(p, org)
    await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com"}], "users")
    await p.batch_create_edges([edge("x2", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
    await p.reap_stale_external_app_relations("app1")      # the reaper's first pass marks x2 (no grant yet): the next pass, a test's, sweeps it


async def reap_twice(p, app_id: str = "app1") -> int:
    """Both passes of the reaper (common.grant_locks): the first marks, the second, once every transaction open at the mark has ended, removes."""
    await p.reap_stale_external_app_relations(app_id)
    return await p.reap_stale_external_app_relations(app_id)


async def test_a_grant_through_the_store_keeps_its_holder_s_membership_from_a_reaper_that_overlaps_it(pg_db) -> None:
    """A connector's grant write alone (no ensure_app_membership after it): the store locks x2's flagged membership before it writes the grant, in
    the grant's transaction, still open while the reaper runs. The reaper waits for it and sees the grant: both commit and x2 keeps the App."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        tid = await p.begin_transaction([], [])
        await GraphTransactionStore(p, tid, explicit=True).batch_create_edges([_perm("x2", "users", "r1", "records")], "permission")
        await _reap_while(p, tid, pg_db=pg_db)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_group_s_grant_through_the_store_keeps_its_member_s_membership_from_a_reaper_that_overlaps_it(pg_db, member) -> None:
    """x2 is a flagged collaborator of app1 and a member of g, which grants nothing yet: the reaper would drop it. g's grant on r1 is written
    through the store in a transaction still open while the reaper runs. The store locks the flagged memberships of g's members on r1's App first,
    so the reaper waits for it and sees the grant: x2 keeps the App."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        await p.batch_create_edges([_perm("x2", "users", "g", member)], "permission")
        tid = await p.begin_transaction([], [])
        await GraphTransactionStore(p, tid, explicit=True).batch_create_edges([_perm("g", member, "r1", "records", "GROUP")], "permission")
        await _reap_while(p, tid, pg_db=pg_db)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


async def test_the_lock_holds_only_the_memberships_the_grants_keep(pg_db) -> None:
    """On r1's App only: x2's own membership for its grant on r1, x3's as a member of g for g's grant on r1, every app of x4's for its grant to g.
    An ordinary member, a flagged one of another App and a person with none are not held."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _world_x(p, org)
        await p.batch_upsert_nodes([{"_key": "app2", "name": "Mail", "type": "MAIL", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in ("x2", "x3", "x4", "x5")], "users")
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], "groups")
        flagged = {"isExternalUser": True, "syncState": "COMPLETED", "lastSyncUpdate": 1}
        await p.batch_create_edges([edge("x2", "users", "app1", "apps", **flagged), edge("x2", "users", "app2", "apps", **flagged),
                                    edge("x3", "users", "app1", "apps", **flagged), edge("x4", "users", "app2", "apps", **flagged),
                                    edge("x5", "users", "app1", "apps", isExternalUser=False, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
        await p.batch_create_edges([_perm("x3", "users", "g", "groups"), _perm("x5", "users", "g", "groups")], "permission")
        held = await p.lock_external_app_memberships([("users", "x2", "records", "r1"), ("groups", "g", "records", "r1"),
                                                      ("users", "x4", "groups", "g"), ("person", "nobody", "records", "r1")])
    assert held == [("users", "x2", "app1"), ("users", "x3", "app1"), ("users", "x4", "app2")]


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_group_s_new_grant_waits_for_a_reaper_deciding_on_its_app(pg_db, member) -> None:
    """A reaper of app1 deciding in a transaction still open holds the app's lock: g's new grant on r1 (a record of app1) waits for it, and is
    written once the reaper has committed."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        await p.batch_create_edges([_perm("x2", "users", "g", member)], "permission")
        hold = await p.begin_transaction([], [])
        tid = await p.begin_transaction([], [])
        try:
            await p.reap_stale_external_app_relations("app1", transaction=hold)
            write = asyncio.create_task(GraphTransactionStore(p, tid, explicit=True).replace_record_permissions(
                "r1", [_perm("g", member, "r1", "records", "GROUP")], None, inherit=False))
            await _until_waiting_on(p, pg_db, hold, write)
            await p.commit_transaction(hold)
            await write
            await p.commit_transaction(tid)
        finally:
            for t in (hold, tid):
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)


async def _reaper(p) -> tuple[asyncio.Task, int]:
    """The reaper of app1 in a transaction of its own, committed when it returns, and the backend that runs it: a wait is then known to be its."""
    rid = await p.begin_transaction([], [])
    pid = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), rid)

    async def run() -> int:
        try:
            n = await p.reap_stale_external_app_relations("app1", transaction=rid)
        except BaseException:
            with contextlib.suppress(Exception):
                await p.rollback_transaction(rid)
            raise
        await p.commit_transaction(rid)
        return n
    return asyncio.create_task(run()), pid


async def _until_blocked_or_done(pg_db, holder: int, task: asyncio.Task, *, waiter: int | None = None) -> None:
    """Returns once ``task`` finished or a session waits on a lock the backend ``holder`` holds (the backend ``waiter``, when given: the task's
    own, not another the holder blocks); fails if neither happens within 10 s."""
    for _ in range(200):
        if task.done() or await pg_db.run(lambda tx: tx.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid)) AND ($2::int IS NULL OR pid = $2)", holder, waiter)):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("it neither finished nor waited on a lock")


@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_member_added_while_a_group_s_grant_is_written_keeps_its_membership(pg_db, member) -> None:
    """T1 writes g's grant on r1 while g has no member, and stays open. T2 makes x2, a flagged collaborator of app1, a member of g and commits;
    the reaper runs; T1 commits. T1 holds app1's lock (the org's for a team's membership is T2's), so the reaper waits for T1 and reads g's grant
    with x2's membership, and x2 keeps app1 once all three are done."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        t1, t2 = await p.begin_transaction([], []), await p.begin_transaction([], [])
        open_ = [t1, t2]

        async def add() -> None:
            await GraphTransactionStore(p, t2, explicit=True).batch_create_edges([_perm("x2", "users", "g", member)], "permission")
            await p.commit_transaction(open_.pop(open_.index(t2)))
        adding = reaping = None
        try:
            pid1, pid2 = [await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t) for t in (t1, t2)]
            await GraphTransactionStore(p, t1, explicit=True).batch_create_edges([_perm("g", member, "r1", "records", "GROUP")], "permission")
            adding = asyncio.create_task(add())
            await _until_blocked_or_done(pg_db, pid1, adding)
            reaping, reaper_pid = await _reaper(p)
            await _until_blocked_or_done(pg_db, pid1, reaping, waiter=reaper_pid)      # the reaper itself waits for T1, which holds app1's lock
            assert not reaping.done(), "the reaper decided while T1's grant was open"
            await p.commit_transaction(open_.pop(open_.index(t1)))
            await adding
            await reaping
        finally:
            for task in (adding, reaping):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert set(await _app_edges(pg_db, org)) == {"x2"}



@pytest.mark.parametrize("member", ["groups", "roles", "teams"])
async def test_a_member_added_between_two_records_of_a_new_group_grant_keeps_its_membership(pg_db, member) -> None:
    """T1 writes g's new grant on r1, then on r2, in one transaction; T2 makes x2, a flagged collaborator of app1, a member of g in between; the
    reaper runs before T1 commits. The reaper waits for T1, which holds app1's lock, and reads g's grants with x2's membership: x2 keeps app1."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "r2", "recordName": "r2", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], member)
        t1, t2 = await p.begin_transaction([], []), await p.begin_transaction([], [])
        open_ = [t1, t2]
        store = GraphTransactionStore(p, t1, explicit=True)

        async def add() -> None:
            await GraphTransactionStore(p, t2, explicit=True).batch_create_edges([_perm("x2", "users", "g", member)], "permission")
            await p.commit_transaction(open_.pop(open_.index(t2)))
        adding = reaping = None
        try:
            pid1, pid2 = [await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t) for t in (t1, t2)]
            await store.replace_record_permissions("r1", [_perm("g", member, "r1", "records", "GROUP")], None, inherit=False)
            adding = asyncio.create_task(add())
            await _until_blocked_or_done(pg_db, pid1, adding)
            await store.replace_record_permissions("r2", [_perm("g", member, "r2", "records", "GROUP")], None, inherit=False)
            reaping, reaper_pid = await _reaper(p)
            await _until_blocked_or_done(pg_db, pid1, reaping, waiter=reaper_pid)
            await p.commit_transaction(open_.pop(open_.index(t1)))
            await adding
            await reaping
        finally:
            for task in (adding, reaping):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


@pytest.mark.parametrize("holder", ["users", "groups"])
async def test_a_restored_record_s_grant_locks_its_membership_though_the_edge_was_there(pg_db, holder) -> None:
    """r1 is in the trash with x2's grant on it (its own, or through g). T1 restores r1 and writes its grants again, holding app1's lock from
    the restore: the reaper waits for T1, and x2 keeps app1."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    grant = _perm("x2", "users", "r1", "records") if holder == "users" else _perm("g", "groups", "r1", "records", "GROUP")
    with ctx(org):
        await _x2_flagged(p, org)
        if holder == "groups":
            await p.batch_upsert_nodes([{"_key": "g", "name": "g", "orgId": org, "connectorId": "app1"}], "groups")
            await p.batch_create_edges([_perm("x2", "users", "g", "groups")], "permission")
        await p.batch_create_edges([grant], "permission")
        await p.soft_delete_records(["r1"], "app1", delete_source="CONNECTOR", batch_id="b-trash")
        t1 = await p.begin_transaction([], [])
        open_ = [t1]
        reaping = None
        try:
            pid1 = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t1)
            store = GraphTransactionStore(p, t1, explicit=True)
            assert await store.restore_records([{"id": "r1"}], "b-trash") == ["r1"]
            await store.replace_record_permissions("r1", [grant], None, inherit=False)
            reaping, reaper_pid = await _reaper(p)
            await _until_blocked_or_done(pg_db, pid1, reaping, waiter=reaper_pid)
            await p.commit_transaction(open_.pop())
            await reaping
        finally:
            if reaping is not None and not reaping.done():
                reaping.cancel()
                with contextlib.suppress(BaseException):
                    await reaping
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


async def test_a_grant_kept_by_a_replace_while_another_removes_it_is_locked(pg_db) -> None:
    """T0 removes x2's grant on r1 and stays open; T1 writes r1's grants with x2's. T1's delete waits for T0 and then finds the edge gone, so
    x2's grant is created, not kept: T1 locks x2's membership, the reaper waits for T1, and x2 keeps app1."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_create_edges([_perm("x2", "users", "r1", "records")], "permission")
        t0, t1 = await p.begin_transaction([], []), await p.begin_transaction([], [])
        open_ = [t0, t1]
        writing = reaping = None
        try:
            pid0, pid1 = [await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t) for t in (t0, t1)]
            await GraphTransactionStore(p, t0, explicit=True).replace_record_permissions("r1", [], None, inherit=False)
            writing = asyncio.create_task(GraphTransactionStore(p, t1, explicit=True).replace_record_permissions(
                "r1", [_perm("x2", "users", "r1", "records")], None, inherit=False))
            await _until_blocked_or_done(pg_db, pid0, writing)
            await p.commit_transaction(open_.pop(open_.index(t0)))
            await writing
            reaping, reaper_pid = await _reaper(p)
            await _until_blocked_or_done(pg_db, pid1, reaping, waiter=reaper_pid)
            await p.commit_transaction(open_.pop(open_.index(t1)))
            await reaping
        finally:
            for task in (writing, reaping):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


async def test_a_team_member_added_while_an_app_is_added_keeps_its_membership_of_it(pg_db) -> None:
    """W makes x2 a member of team t and stays open. Meanwhile app2 appears: its record r2 is granted to t, and x2 is a flagged collaborator of
    it. The reaper of app2 runs before W commits. A team's membership can keep memberships of any app, app2 included though W never named it:
    W holds the org's lock, which every reaper takes before its app's, so the reaper waits for W and reads x2's chain to r2."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "t", "name": "t", "orgId": org}], "teams")
        w = await p.begin_transaction([], [])
        reaping = None
        try:
            pid = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), w)
            await GraphTransactionStore(p, w, explicit=True).batch_create_edges([_perm("x2", "users", "t", "teams")], "permission")
            await p.batch_upsert_nodes([{"_key": "app2", "name": "Mail", "type": "MAIL", "orgId": org}], "apps")
            await p.batch_upsert_nodes([{"_key": "r2", "recordName": "r2", "connectorId": "app2", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
            await p.batch_create_edges([_perm("t", "teams", "r2", "records", "TEAM")], "permission")
            await p.batch_create_edges([edge("x2", "users", "app2", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")
            reaping = asyncio.create_task(p.reap_stale_external_app_relations("app2"))
            await _until_blocked_or_done(pg_db, pid, reaping)
            assert not reaping.done(), "the reaper of app2 decided while x2's membership of t was open"
            await p.commit_transaction(w)
            w = None
            await reaping
        finally:
            if reaping is not None and not reaping.done():
                reaping.cancel()
                with contextlib.suppress(BaseException):
                    await reaping
            if w is not None:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(w)
        kept = await pg_db.run(lambda tx: tx.fetch("SELECT from_id FROM graph.edges WHERE org_id = $1 AND coll = 'userAppRelation' AND to_id = 'app2'", org))
    assert [r["from_id"] for r in kept] == ["x2"]


async def test_writers_of_one_app_share_its_lock(pg_db) -> None:
    """Two grant writers of app1, each in a transaction still open: the lock is shared, so the second writes without waiting for the first."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "r2", "recordName": "r2", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        t1, t2 = await p.begin_transaction([], []), await p.begin_transaction([], [])
        try:
            await GraphTransactionStore(p, t1, explicit=True).batch_create_edges([_perm("x2", "users", "r1", "records")], "permission")
            await asyncio.wait_for(GraphTransactionStore(p, t2, explicit=True).batch_create_edges([_perm("x2", "users", "r2", "records")], "permission"), 2)
            await p.commit_transaction(t1)
            await p.commit_transaction(t2)
        finally:
            for t in (t1, t2):
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)


async def test_the_app_lock_queue_cycle_is_resolved_without_failing_a_writer(pg_db) -> None:
    """The one cycle the app lock could leave: W1 holds app1's lock shared, the reaper queues for it exclusively, W2 (holding a row W1 needs)
    asks for it shared behind the reaper, and W1 then waits for W2's row. PostgreSQL's deadlock check finds the cycle through the wait queue
    and lets W2 ahead of the reaper (a soft deadlock: nothing fails), so W2 writes, W1 then gets the row, and the reaper decides after both."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    r2 = {"_key": "r2", "recordName": "r2", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([r2], "records")
        w1, w2 = await p.begin_transaction([], []), await p.begin_transaction([], [])
        open_, tasks = {w1, w2}, []
        try:
            pid1, pid2 = [await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t) for t in (w1, w2)]
            await p.batch_upsert_nodes([{**r2, "recordName": "r2 by w2"}], "records", transaction=w2)          # W2 holds r2's row
            await GraphTransactionStore(p, w1, explicit=True).batch_create_edges([_perm("x2", "users", "r1", "records")], "permission")
            reaping, reaper_pid = await _reaper(p)
            tasks.append(reaping)
            await _until_blocked_or_done(pg_db, pid1, reaping, waiter=reaper_pid)                             # the reaper queues behind W1
            updating = asyncio.create_task(p.batch_upsert_nodes([{**r2, "recordName": "r2 by w1"}], "records", transaction=w1))
            tasks.append(updating)
            await _until_blocked_or_done(pg_db, pid2, updating)                                                # W1 waits for W2's row
            granting = asyncio.create_task(GraphTransactionStore(p, w2, explicit=True).batch_create_edges([_perm("x2", "users", "r2", "records")], "permission"))
            tasks.append(granting)
            for _ in range(100):      # W2's grant asks for app1's lock and queues behind the reaper before the deadlock check lets it ahead
                if await pg_db.run(lambda tx: tx.fetchval("SELECT $2 = ANY(pg_blocking_pids($1))", pid2, reaper_pid)):      # behind the reaper
                    break
                assert not granting.done(), "W2's grant went through without waiting on the app lock"
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("W2's grant never waited on the app lock")
            await asyncio.wait_for(granting, 4)          # within the lock timeout: W2 was let ahead of the reaper, not left behind it
            assert not reaping.done() and not updating.done()
            await p.commit_transaction(w2)
            open_.discard(w2)
            await asyncio.wait_for(updating, 4)
            await p.commit_transaction(w1)
            open_.discard(w1)
            await asyncio.wait_for(reaping, 4)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert set(await _app_edges(pg_db, org)) == {"x2"}


async def test_a_direct_grant_on_a_record_without_a_connector_keeps_no_membership(pg_db) -> None:
    """A record with no connectorId names no app: a user's grant on it keeps none of the user's flagged memberships (a group's, role's or team's
    membership keeps every app's, a direct grant only its record's app), so none is locked or made again for it."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "rx", "recordName": "rx", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        assert await p.lock_external_app_memberships([("users", "x2", "records", "rx")]) == []
        assert await p.lock_external_app_memberships([("users", "x2", "records", "r1")]) == [("users", "x2", "app1")]


@pytest.mark.parametrize("locked", [True, False], ids=["under_the_app_lock", "lock_free"])
async def test_a_writer_open_at_the_sweep_keeps_its_membership_only_under_the_app_lock(pg_db, monkeypatch, locked) -> None:
    """x2's membership was marked by the reaper's first pass (no grant then). A writer opens after the mark, grants x2 r1 and stays open while the
    second pass runs. Under the app lock the pass waits for the writer and reads its grant: x2 keeps app1. Without the lock the pass reads the
    committed state, where the grant is not yet, and removes the membership under the grant about to commit: the hole of a reaper-only design
    (design-6715-v2, section 1). Every transaction open at the mark has ended, so the horizon does not hold the pass back in either case."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    if not locked:
        monkeypatch.setattr(PostgresGraphProvider, "_REAPER_HOLDS_APP_LOCK", False)
    with ctx(org):
        await _x2_flagged(p, org)
        tid = await p.begin_transaction([], [])
        reaping = None
        try:
            holder = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), tid)
            await GraphTransactionStore(p, tid, explicit=True).batch_create_edges([_perm("x2", "users", "r1", "records")], "permission")
            reaping, reaper_pid = await _reaper(p)
            if locked:
                await _until_blocked_or_done(pg_db, holder, reaping, waiter=reaper_pid)
                assert not reaping.done(), "the sweep decided while the grant was open"
            else:
                await asyncio.wait_for(reaping, 10)
            await p.commit_transaction(tid)
            tid = None
            await asyncio.wait_for(reaping, 10)
        finally:
            if reaping is not None and not reaping.done():
                reaping.cancel()
                with contextlib.suppress(BaseException):
                    await reaping
            if tid is not None:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(tid)
        assert ("x2" in await _app_edges(pg_db, org)) is locked


async def test_a_grant_queued_behind_the_sweep_loses_the_membership_until_the_next_membership_step(pg_db, caplog) -> None:
    """The residual window of the two-pass reaper (design-6715-v2, section 2, option D), explained rather than silent. x2 was marked by the first
    pass and nothing wrote its membership since. The sweep removes it holding app1's lock; a grant to x2 queued behind it then commits without the
    membership, and the removal is logged with the app, the principal and the mark. The next sync's membership step makes it again. Before the
    two-pass reaper the grant's re-make covered this, at the cost measured in m6715."""
    from app.utils.logger import log_record_fields
    caplog.set_level(logging.INFO)
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        rid, tid = await p.begin_transaction([], []), await p.begin_transaction([], [])
        open_, grant = [rid, tid], None
        try:
            await p.reap_stale_external_app_relations("app1", transaction=rid)
            grant = asyncio.create_task(GraphTransactionStore(p, tid, explicit=True).batch_create_edges([_perm("x2", "users", "r1", "records")], "permission"))
            await _until_waiting_on(p, pg_db, rid, grant)
            await p.commit_transaction(open_.pop(0))
            await asyncio.wait_for(grant, 10)
            await p.commit_transaction(open_.pop(0))
        finally:
            if grant is not None and not grant.done():
                grant.cancel()
                with contextlib.suppress(BaseException):
                    await grant
            for t in open_:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(t)
        assert "x2" not in await _app_edges(pg_db, org)
        removed = [log_record_fields(r) for r in caplog.records if r.getMessage() == "graph.reaper.membership_removed"]
        assert any(f.get("connector_id") == "app1" and f.get("principal_id") == "x2" and f.get("reap_mark") for f in removed), removed
        await p.ensure_app_membership("x2", "users", "app1", is_external=True)      # the next sync's membership step
        assert await _app_edges(pg_db, org) == {"x2": True}


async def test_a_membership_written_after_the_mark_is_not_swept(pg_db) -> None:
    """x2 was marked by the first pass; its sync's membership step writes it again: the write clears the mark, so the next pass keeps it (and
    marks it again, no grant keeping it), and the one after removes it."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.ensure_app_membership("x2", "users", "app1", is_external=True)
        await p.reap_stale_external_app_relations("app1")
        assert "x2" in await _app_edges(pg_db, org)
        await p.reap_stale_external_app_relations("app1")
        assert "x2" not in await _app_edges(pg_db, org)


async def test_a_trashed_grant_s_collaborator_loses_the_app_and_its_org_wide_records_and_gets_both_back_on_restore(pg_db) -> None:
    """x2, a user of the org (it belongs to the Organization), is an external collaborator of app1 through its one grant, on r1. Its flagged
    membership is what lets app1's org-wide grant (on r-org) reach it: app membership is not inert (design-6715-v2, section 3). r1 goes to the
    trash. While the membership is kept (what keep-trashed would do) x2 still reads r-org. The reaper's two passes remove it, the trashed grant
    keeping nothing: r-org is hidden. The restore makes the membership again from the trashed grant's note: both records are readable again. x3,
    whose grant on r1 the source revoked while it was in the trash, gets nothing back."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": org, "name": "Org", "accountType": "enterprise", "isActive": True}], "organizations")
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com", "userId": "uid-x2", "isActive": True},
                                    {"_key": "x3", "orgId": org, "email": "x3@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": "r-org", "recordName": "r-org", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([edge("x2", "users", org, "organizations", entityType="ORGANIZATION", createdAtTimestamp=1)], "belongsTo")
        await p.batch_create_edges([_perm(org, "organizations", "r-org", "records", "ORG"), _perm("x2", "users", "r1", "records"),
                                    _perm("x3", "users", "r1", "records")], "permission")
        await p.batch_create_edges([edge("x3", "users", "app1", "apps", isExternalUser=True, syncState="COMPLETED", lastSyncUpdate=1)], "userAppRelation")

        async def visible() -> set[str]:
            return await p.filter_accessible_record_ids(["r-org", "r1"], "uid-x2", org)
        assert await visible() == {"r-org", "r1"}
        tid = await p.begin_transaction([], [])
        await GraphTransactionStore(p, tid, explicit=True).soft_delete_records(["r1"], "app1", delete_source="CONNECTOR", batch_id="b-trash")
        await p.commit_transaction(tid)
        assert "r-org" in await visible()      # the membership kept with the grant in the trash: r-org still reachable
        await reap_twice(p)
        assert set(await _app_edges(pg_db, org)) == set()
        assert await visible() == set()      # no org-wide record while the only grant is in the trash
        await p.delete_edge("x3", "users", "r1", "records", "permission")      # revoked at the source meanwhile
        tid = await p.begin_transaction([], [])
        assert await GraphTransactionStore(p, tid, explicit=True).restore_records([{"id": "r1"}], "b-trash") == ["r1"]
        await p.commit_transaction(tid)
        assert await _app_edges(pg_db, org) == {"x2": True}
        assert await visible() == {"r-org", "r1"}


async def test_a_team_member_reaches_the_team_s_records_of_an_app_only_through_its_app_membership(pg_db) -> None:
    """As on Neo4j and ArangoDB: a team without an edge to app1 gives its member x2 its grant on r1 only while x2 holds its own membership of app1."""
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "x2", "orgId": org, "email": "x2@example.com", "userId": "uid-x2", "isActive": True}], "users")
        await p.batch_upsert_nodes([{"_key": "t", "name": "t", "orgId": org}], "teams")
        await p.batch_create_edges([_perm("x2", "users", "t", "teams"), _perm("t", "teams", "r1", "records", "TEAM")], "permission")
        assert await p.filter_accessible_record_ids(["r1"], "uid-x2", org) == {"r1"}
        await p.delete_edge("x2", "users", "app1", "apps", "userAppRelation")
        assert await p.filter_accessible_record_ids(["r1"], "uid-x2", org) == set()


async def test_a_team_write_queued_behind_the_sweep_keeps_the_added_member_s_membership(pg_db) -> None:
    """As on Neo4j and ArangoDB: x2's membership was marked by the first pass; the sweep removes it and stays open; a team write making x2 a
    member of t (granted r1) waits for the org's lock, then makes the membership again with the lock held: x2 holds app1 when the team write
    commits, with no later ensure_app_membership."""
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    p, org = PostgresGraphProvider(logging.getLogger("live-group"), pg_db), new_org()
    with ctx(org):
        await _x2_flagged(p, org)
        await p.batch_upsert_nodes([{"_key": "t", "name": "t", "orgId": org}], "teams")
        await p.batch_create_edges([_perm("t", "teams", "r1", "records", "TEAM")], "permission")
        rid = await p.begin_transaction([], [])
        writing = None
        try:
            await p.reap_stale_external_app_relations("app1", transaction=rid)

            async def add_member(tx_store) -> None:
                assert await tx_store.batch_create_edges([_perm("x2", "users", "t", "teams")], "permission")
            writing = asyncio.create_task(GraphDataStore(logging.getLogger("live-group"), p).execute_idempotent_in_transaction(add_member, explicit=True))
            await _until_waiting_on(p, pg_db, rid, writing)
            await p.commit_transaction(rid)
            rid = None
            await asyncio.wait_for(writing, 10)
        finally:
            if writing is not None and not writing.done():
                writing.cancel()
                with contextlib.suppress(BaseException):
                    await writing
            if rid is not None:
                with contextlib.suppress(Exception):
                    await p.rollback_transaction(rid)
        assert await _app_edges(pg_db, org) == {"x2": True}
