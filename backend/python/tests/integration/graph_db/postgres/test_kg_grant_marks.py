"""The placement marks of grants (``kg.grant_marks``) and the chain-tops a browse finds from them.

The marks follow every change their rows read (a grant on the parent added and removed, the parent's rule, deletion and connector, a move, a second group), in the unit that
makes it; a mark its rows no longer give fails the verify; an older copy is filled in place. A browse that takes the viewer's grants from the marks lists, on every node and
for every viewer, the same rows and arrows as one that hands the walk every grant (the walk's own input), and hands the walk only the grants that can be chain-tops."""
from __future__ import annotations

from typing import Any

import pytest

from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import grant_marks
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.verify import verify_app

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_read import _admin, flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
FOLDER = "application/vnd.folder"
VIEWERS = ("u1", "u2", "u3")


def _rec(org: str, key: str, *, folder: bool = False, **extra: Any) -> dict:  # noqa: ANN401
    return {"_key": key, "recordName": key, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "externalRecordId": f"x-{key}",
            "mimeType": FOLDER if folder else "text/plain", **extra}


async def _world(p, db, org: str) -> None:
    """app1 (a drive) with groups g1 (open to team1) and g2 (closed); in g1 folder f1 with a, b; in g2 folder f2 with c, d and folder f3 with e. u1 and u2 are in team1.
    u1 holds f2 and c (a folder shared whole), e (a file shared alone) and a (inside g1, which u1 may open anyway); u2 holds c alone; u3 holds g2's d and the group g2."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_upsert_nodes([{"_key": g, "groupName": g, "connectorId": "app1", "orgId": org} for g in ("g1", "g2")], "recordGroups")
        await p.batch_upsert_nodes([_rec(org, "f1", folder=True), _rec(org, "f2", folder=True), _rec(org, "f3", folder=True),
                                    *(_rec(org, k) for k in "abcde")], "records")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in VIEWERS], "users")
        await p.batch_upsert_nodes([{"_key": "team1", "orgId": org}], "teams")
        await p.batch_create_edges([edge("app1", "apps", "g1", "recordGroups", relationshipType="PARENT_CHILD"),
                                    edge("app1", "apps", "g2", "recordGroups", relationshipType="PARENT_CHILD"),
                                    edge("g1", "recordGroups", "f1", "records", relationshipType="PARENT_CHILD"),
                                    edge("g2", "recordGroups", "f2", "records", relationshipType="PARENT_CHILD"),
                                    edge("g2", "recordGroups", "f3", "records", relationshipType="PARENT_CHILD"),
                                    *(edge(f, "records", k, "records", relationshipType="PARENT_CHILD") for f, k in (("f1", "a"), ("f1", "b"), ("f2", "c"), ("f2", "d"), ("f3", "e")))],
                                   "nodeRelations")
        await p.batch_create_edges([*(edge(k, "records", "g1" if k in ("f1", "a", "b") else "g2", "recordGroups") for k in ("f1", "f2", "f3", *"abcde")),
                                    *(edge(g, "recordGroups", "app1", "apps") for g in ("g1", "g2")),
                                    *(edge(u, "users", org, "organizations") for u in VIEWERS)], "belongsTo")
        await p.batch_create_edges([*(edge(k, "records", f, "records") for f, k in (("f1", "a"), ("f1", "b"), ("f2", "c"), ("f2", "d"), ("f3", "e")))], "inheritPermissions")
        await p.batch_create_edges([edge("u1", "users", "team1", "teams", type="USER"), edge("u2", "users", "team1", "teams", type="USER"),
                                    edge("team1", "teams", "g1", "recordGroups", type="TEAM", role="READER"),
                                    *(edge("u1", "users", k, "records", type="USER", role="READER") for k in ("f2", "c", "e", "a")),
                                    edge("u2", "users", "c", "records", type="USER", role="READER"),
                                    edge("u3", "users", "d", "records", type="USER", role="READER"),
                                    edge("u3", "users", "g2", "recordGroups", type="USER", role="READER")], "permission")
        await p.batch_create_edges([edge(u, "users", "app1", "apps") for u in VIEWERS], "userAppRelation")
    assert (await flip(db, org))[0] == 1


async def _rows(db, org: str) -> dict[tuple[str, str], tuple[bool, str | None, bool, str | None]]:
    """Every grant of app1 by (principal, node) id: its marks and what its rows give."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        app = await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        rows = await tx.fetch(grant_marks.BY_APP, org, app, 0, 0, "", 10_000)
        return {(ids[r["principal_sk"]], ids[r["node_sk"]]): (r["parent_held"], ids.get(r["node_group"]), r["want_held"], ids.get(r["want_group"])) for r in rows}
    with ctx(org):
        return await db.run(go)


async def _marks(db, org: str) -> dict[tuple[str, str], tuple[bool, str | None]]:
    rows = await _rows(db, org)
    assert [k for k, v in rows.items() if v[:2] != v[2:]] == [], "every mark is what its rows give"
    return {k: v[:2] for k, v in rows.items()}


async def _flag(org: str, key: str, deleted: str) -> None:
    """The record's isDeleted, as a connector's soft delete writes it (the provider's upsert leaves the flag to the delete paths)."""
    await _admin(f"UPDATE graph.nodes SET doc = doc || '{{\"isDeleted\": {deleted}}}'::jsonb WHERE org_id = $1 AND coll = 'records' AND id = $2", org, key)


async def _change(p, db, org: str, fn) -> None:
    with ctx(org):
        await fn()
    await drain_org(db, org)


async def test_marks_follow_every_change_their_rows_read(pg_provider, pg_db) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    m = await _marks(pg_db, org)
    assert m[("u1", "c")] == (True, "g2")              # u1 holds c's folder f2 (OPEN, live): c is no chain-top of theirs
    assert m[("u2", "c")] == (False, "g2") and m[("u1", "e")] == (False, "g2") and m[("u1", "f2")] == (False, "g2") and m[("u1", "a")] == (False, "g1")
    assert m[("u3", "g2")] == (False, None)            # a group: no group of its own to leave it out by

    await _change(p, pg_db, org, lambda: p.batch_delete_edges([edge("u1", "users", "f2", "records", type="USER")], "permission"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")      # the parent's grant went: c may be a chain-top again
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("u1", "users", "f2", "records", type="USER", role="READER")], "permission"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "f2", folder=True, accessRule="RESTRICTED")], "records"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")      # a parent its grant alone does not open
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "f2", folder=True, accessRule="OPEN")], "records"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")
    await _change(p, pg_db, org, lambda: _flag(org, "f2", "true"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")      # a deleted parent opens nothing
    await _change(p, pg_db, org, lambda: _flag(org, "f2", "false"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "f2", folder=True, connectorId="app2")], "records"))
    assert (await _marks(pg_db, org)).get(("u1", "c"), (False, None))[0] is False      # a parent of another connector
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "f2", folder=True)], "records"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")

    async def move() -> None:      # c leaves its folder for g2's top
        await p.batch_delete_edges([edge("f2", "records", "c", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("g2", "recordGroups", "c", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await _change(p, pg_db, org, move)
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("f2", "records", "c", "records", relationshipType="ATTACHMENT")], "nodeRelations"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")       # a second hierarchy parent (a link) that the same principal holds
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("c", "records", "g1", "recordGroups")], "belongsTo"))
    assert (await _marks(pg_db, org))[("u2", "c")] == (False, None)      # in two groups: no one group to leave it out by
    with ctx(org):
        assert (await p._q(lambda tx: verify_app(tx, org, "app1"), None)).ok


async def test_a_mark_its_rows_no_longer_give_fails_the_verify_and_one_not_written_yet_does_not(pg_provider, pg_db) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    keys = "SELECT sk FROM kg.node_keys WHERE org_id = $1 AND id = $2"
    await _admin(f"UPDATE kg.scope_members SET parent_held = false WHERE org_id = $1 AND node_sk = ({keys.replace('$2', chr(39) + 'c' + chr(39))})", org)
    with ctx(org):
        assert (await p._q(lambda tx: verify_app(tx, org, "app1"), None)).ok      # late: it leaves nothing out
    await _admin(f"UPDATE kg.scope_members SET parent_held = true WHERE org_id = $1 AND node_sk = ({keys.replace('$2', chr(39) + 'e' + chr(39))})", org)
    with ctx(org):
        res = await p._q(lambda tx: verify_app(tx, org, "app1"), None)
    assert not res.ok and any(c.startswith("m|") for c in res.changed)
    await _admin(f"UPDATE kg.scope_members SET parent_held = false, node_group = ({keys.replace('$2', chr(39) + 'g1' + chr(39))}) WHERE org_id = $1 AND node_sk = ({keys.replace('$2', chr(39) + 'e' + chr(39))})", org)
    with ctx(org):
        res = await p._q(lambda tx: verify_app(tx, org, "app1"), None)
    assert not res.ok and any(c.startswith("m|") for c in res.changed)      # a group the node is not in


async def test_an_older_copy_is_filled_in_place_and_served_again(pg_provider, pg_db) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    want = await _marks(pg_db, org)
    await _admin("UPDATE kg.scope_members SET parent_held = false, node_group = NULL WHERE org_id = $1", org)
    await _admin("ALTER TABLE kg.projection_state NO FORCE ROW LEVEL SECURITY")
    try:
        await _admin(f"UPDATE kg.projection_state SET code_epoch = {bf.MARKS_FROM} WHERE org_id = $1", org)
    finally:
        await _admin("ALTER TABLE kg.projection_state FORCE ROW LEVEL SECURITY")
    mode, more = await bf.fill_grant_marks(pg_db, org, "app1", page=2)
    assert (mode, more) == ("enforce", None)
    assert await _marks(pg_db, org) == want

    async def epoch(tx) -> int:
        return await tx.fetchval("SELECT s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk WHERE s.org_id = $1 AND k.id = 'app1'", org)
    with ctx(org):
        assert await pg_db.run(epoch) == bf.LISTING_FROM          # the knowledge-base listing's fill takes it on from there (test_kg_kb_root)


async def _pages(p, org: str, user: str) -> dict[str, Any]:
    """Every browse of the viewer (the App, each group, each folder, the flat listing) with each row's id and arrow."""
    with ctx(org):
        acc = await p.get_knowledge_hub_access_v3(user, org)
        kw = {"grantee_ids": acc["grantee_ids"], "gated_app_ids": acc["gated_app_ids"], "grants_by_connector": acc["by_connector"], "user_key": user}
        out = {}
        for start, typ in (("app1", "app"), ("g1", "recordGroup"), ("g2", "recordGroup"), ("f1", "record"), ("f2", "record"), ("f3", "record")):
            for side in (False, True):
                page = await p.get_knowledge_hub_connector_page_v3("app1" if typ == "app" else "", org, limit=50, flatten=False, start_id=start, start_type=typ,
                                                                   filters={"only_containers": True} if side else {}, **kw)
                out[f"{start} {side}"] = sorted((r["id"], r["hasChildren"]) for r in page["rows"])
        page = await p.get_knowledge_hub_connector_page_v3("app1", org, limit=50, **kw)
        out["flat"] = sorted((r["id"], r["hasChildren"]) for r in page["rows"])
    return out


async def test_a_browse_from_the_marked_grants_lists_what_one_from_every_grant_lists(pg_provider, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    given: list[list[str]] = []
    real = p._kh_v3_chain_top_groups

    async def spy(app_id, org_id, granted_ids, only_group, transaction) -> list:
        given.append(sorted(granted_ids))
        return await real(app_id, org_id, granted_ids, only_group, transaction)
    monkeypatch.setattr(p, "_kh_v3_chain_top_groups", spy)
    marked = {u: await _pages(p, org, u) for u in VIEWERS}
    at_root = given[0]
    real_top = kh_listing.KhListingMixin._kh_top_grants

    async def every(self, org_, app_id, user_key, granted, transaction) -> kh_listing.TopGrants:
        return kh_listing.TopGrants(granted, None, {}, None)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_top_grants", every)
    assert {u: await _pages(p, org, u) for u in VIEWERS} == marked
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_top_grants", real_top)
    # u1's root: a (in g1, which u1 may open) and c (its folder f2 is u1's) are not handed to the walk; f2 and e are, and are listed there (and g1, team1's, of no group)
    assert at_root == ["e", "f2", "g1"]
    assert ("e", False) in marked["u1"]["app1 False"] and ("f2", True) in marked["u1"]["app1 False"]
    assert not any(i in ("a", "c") for i, _ in marked["u1"]["app1 False"])
