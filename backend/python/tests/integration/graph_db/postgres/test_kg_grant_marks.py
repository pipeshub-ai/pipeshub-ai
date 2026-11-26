"""The placement marks of grants (``kg.grant_marks``) and the chain-tops a browse finds from them.

The marks follow every change their rows read (a grant on the parent added and removed, the parent's rule, deletion and connector, a move, a second group), in the unit that
makes it; a mark its rows no longer give fails the verify; an older copy is filled in place. A browse that takes the viewer's grants from the marks lists, on every node and
for every viewer, the same rows and arrows as one that hands the walk every grant (the walk's own input), and hands the walk only the grants that can be chain-tops."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.graph_db.postgres import kh_listing
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import grant_marks
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.model import CODE_EPOCH
from app.services.graph_db.postgres.kg.reader import seeks
from app.services.graph_db.postgres.kg.verify import verify_app
from tests.support import plan_gate

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_first_drain import RESTORE as DRAIN_RESTORE
from .test_kg_first_drain import _not_seeks, no_stats  # noqa: F401  (no_stats: the fixture)
from .test_kg_first_drain import _world as _drain_world
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


async def _rows(db, org: str) -> dict[tuple[str, str], tuple[bool, str | None, str | None, bool, str | None, str | None]]:
    """Every grant of app1 by (principal, node) id: its marks (parent_held, node_group, node_parent) and what its rows give."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        app = await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        rows = await tx.fetch(grant_marks.BY_APP, org, app, 0, 0, "", 10_000)
        return {(ids[r["principal_sk"]], ids[r["node_sk"]]): (r["parent_held"], ids.get(r["node_group"]), ids.get(r["node_parent"]), r["want_held"], ids.get(r["want_group"]),
                                                             ids.get(r["want_parent"])) for r in rows}
    with ctx(org):
        return await db.run(go)


async def _marks(db, org: str) -> dict[tuple[str, str], tuple[bool, str | None]]:
    """parent_held and node_group of every grant (``_parents``: node_parent), each what its rows give."""
    rows = await _rows(db, org)
    assert [k for k, v in rows.items() if v[:3] != v[3:]] == [], "every mark is what its rows give"
    return {k: v[:2] for k, v in rows.items()}


async def _parents(db, org: str) -> dict[tuple[str, str], str | None]:
    rows = await _rows(db, org)
    assert [k for k, v in rows.items() if v[:3] != v[3:]] == [], "every mark is what its rows give"
    return {k: v[2] for k, v in rows.items()}


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
    par = await _parents(pg_db, org)
    assert par[("u1", "c")] == "f2" and par[("u1", "e")] == "f3" and par[("u1", "f2")] == "g2" and par[("u3", "g2")] is None      # a group's grant reads its rows

    await _change(p, pg_db, org, lambda: p.batch_delete_edges([edge("u1", "users", "f2", "records", type="USER")], "permission"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")      # the parent's grant went: c may be a chain-top again
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("u1", "users", "f2", "records", type="USER", role="READER")], "permission"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "c", accessRule="RESTRICTED")], "records"))
    assert (await _parents(pg_db, org))[("u2", "c")] is None             # a record that is not OPEN is no chain-top: its rows say so
    await _change(p, pg_db, org, lambda: p.batch_upsert_nodes([_rec(org, "c", accessRule="OPEN")], "records"))
    assert (await _parents(pg_db, org))[("u2", "c")] == "f2"
    await _change(p, pg_db, org, lambda: _flag(org, "c", "true"))
    assert (await _parents(pg_db, org))[("u2", "c")] is None             # nor is a deleted one
    await _change(p, pg_db, org, lambda: _flag(org, "c", "false"))
    assert (await _parents(pg_db, org))[("u2", "c")] == "f2"
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
    assert (await _parents(pg_db, org))[("u2", "c")] == "g2"             # its parent moved with it
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("f2", "records", "c", "records", relationshipType="ATTACHMENT")], "nodeRelations"))
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")       # a second hierarchy parent (a link) that the same principal holds
    assert (await _parents(pg_db, org))[("u2", "c")] is None             # two hierarchy parents: the browse reads its rows
    await _change(p, pg_db, org, lambda: p.batch_create_edges([edge("c", "records", "g1", "recordGroups")], "belongsTo"))
    assert (await _marks(pg_db, org))[("u2", "c")] == (False, None)      # in two groups: no one group to leave it out by
    with ctx(org):
        assert (await p._q(lambda tx: verify_app(tx, org, "app1"), None)).ok


@pytest.mark.parametrize("edges_too", [True, False])
async def test_a_parent_removed_from_the_graph_clears_the_mark_of_its_childs_grants(pg_provider, pg_db, edges_too) -> None:
    """The node of c's folder f2 goes from the graph, with its relations or without them (a relation left behind): c's row is re-derived in the same unit (a relation's delete
    names its child, and a node that went names the children its relations still reach), so the grant on c is marked from what kg holds after the unit, f2's row gone."""
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    assert (await _marks(pg_db, org))[("u1", "c")] == (True, "g2")
    if edges_too:
        await _change(p, pg_db, org, lambda: p.delete_nodes(["f2"], "records"))
    else:
        await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = 'f2'", org)
        await drain_org(pg_db, org)
    assert (await _marks(pg_db, org))[("u1", "c")] == (False, "g2")


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
    await _admin(f"UPDATE kg.scope_members SET node_group = ({keys.replace('$2', chr(39) + 'g2' + chr(39))}), node_parent = NULL WHERE org_id = $1 AND node_sk = ({keys.replace('$2', chr(39) + 'e' + chr(39))})", org)
    with ctx(org):
        assert (await p._q(lambda tx: verify_app(tx, org, "app1"), None)).ok      # node_parent not written yet: the browse reads the rows
    await _admin(f"UPDATE kg.scope_members SET node_parent = ({keys.replace('$2', chr(39) + 'f2' + chr(39))}) WHERE org_id = $1 AND node_sk = ({keys.replace('$2', chr(39) + 'e' + chr(39))})", org)
    with ctx(org):
        res = await p._q(lambda tx: verify_app(tx, org, "app1"), None)
    assert not res.ok and any(c.startswith("m|") for c in res.changed)      # a parent the node does not have


async def test_an_older_copy_is_filled_in_place_and_served_again(pg_provider, pg_db) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    want = await _marks(pg_db, org)
    await _admin("UPDATE kg.scope_members SET parent_held = false, node_group = NULL, node_parent = NULL WHERE org_id = $1", org)
    await _admin(f"UPDATE kg.projection_state SET code_epoch = {bf.MARKS_FROM} WHERE org_id = $1", org)
    mode, more = await bf.fill_grant_marks(pg_db, org, "app1", page=2)
    assert (mode, more) == ("enforce", None)
    assert await _marks(pg_db, org) == want

    async def epoch(tx) -> int:
        return await tx.fetchval("SELECT s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk WHERE s.org_id = $1 AND k.id = 'app1'", org)
    with ctx(org):
        assert await pg_db.run(epoch) == bf.LISTING_FROM          # the knowledge-base listing's fill takes it on from there (test_kg_kb_root)


async def test_a_copy_of_epoch_12_has_its_parents_filled_in_place_and_is_served_again(pg_provider, pg_db) -> None:
    """kg_0027: a copy finished by epoch 12 code holds node_parent NULL on every grant (what reads the node's rows); the bootstrap fills it from kg's rows and relabels it."""
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    want = await _parents(pg_db, org)
    assert any(want.values())
    await _admin("UPDATE kg.scope_members SET node_parent = NULL WHERE org_id = $1", org)
    await _admin(f"UPDATE kg.projection_state SET code_epoch = {bf.PARENTS_FROM} WHERE org_id = $1", org)
    assert await bf.fill_grant_marks(pg_db, org, "app1") == (None, None)      # not a copy of epoch 10: the earlier fill leaves it alone
    assert await bf.fill_grant_parents(pg_db, org, "app1", page=2) == ("enforce", None)
    assert await _parents(pg_db, org) == want

    async def epoch(tx) -> int:
        return await tx.fetchval("SELECT s.code_epoch FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk WHERE s.org_id = $1 AND k.id = 'app1'", org)
    with ctx(org):
        assert await pg_db.run(epoch) == CODE_EPOCH


def _shown(page: dict[str, Any]) -> tuple:
    """What a page shows: each row's id and arrow, and its counts."""
    return (sorted((r["id"], r["hasChildren"]) for r in page["rows"]), page.get("total"), page.get("totalCapped"), page.get("nRecord"), page.get("nFolder"), page.get("nGroup"))


async def _pages(p, org: str, user: str) -> dict[str, Any]:
    """Every browse of the viewer (the App, each group, each folder, the flat listing) with each row's id and arrow, and its counts."""
    with ctx(org):
        acc = await p.get_knowledge_hub_access_v3(user, org)
        kw = {"grantee_ids": acc["grantee_ids"], "gated_app_ids": acc["gated_app_ids"], "grants_by_connector": acc["by_connector"], "user_key": user}
        out = {}
        for start, typ in (("app1", "app"), ("g1", "recordGroup"), ("g2", "recordGroup"), ("f1", "record"), ("f2", "record"), ("f3", "record")):
            for side in (False, True):
                page = await p.get_knowledge_hub_connector_page_v3("app1" if typ == "app" else "", org, limit=50, flatten=False, start_id=start, start_type=typ,
                                                                   filters={"only_containers": True} if side else {}, **kw)
                out[f"{start} {side}"] = _shown(page)
        out["flat"] = _shown(await p.get_knowledge_hub_connector_page_v3("app1", org, limit=50, **kw))
    return out


async def test_a_browse_from_the_marked_grants_lists_what_one_from_every_grant_lists(pg_provider, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org = pg_provider, new_org()
    await _world(p, pg_db, org)
    given: list[list[str]] = []
    real = p._kh_root_tops

    async def spy(org_, org_id, app_id, top, groups, access, transaction) -> list:
        given.append(await top.ids(groups))
        return await real(org_, org_id, app_id, top, groups, access, transaction)
    monkeypatch.setattr(p, "_kh_root_tops", spy)
    marked = {u: await _pages(p, org, u) for u in VIEWERS}
    at_root = given[0]
    real_top = kh_listing.KhListingMixin._kh_top_grants

    async def every(self, org_, app_id, user_key, granted, transaction) -> kh_listing.TopGrants:
        return kh_listing.TopGrants(granted, None, {}, None)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_top_grants", every)
    assert {u: await _pages(p, org, u) for u in VIEWERS} == marked
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_top_grants", real_top)
    # u1's root: a (in g1, which u1 may open) and c (its folder f2 is u1's) are not read; f2 and e are, and are listed there (and g1, team1's, of no group)
    assert at_root == ["e", "f2", "g1"]
    assert ("e", False) in marked["u1"]["app1 False"][0] and ("f2", True) in marked["u1"]["app1 False"][0]
    assert not any(i in ("a", "c") for i, _ in marked["u1"]["app1 False"][0])
    assert marked["u1"]["app1 False"][1] == len(marked["u1"]["app1 False"][0])      # the total counts the chain-tops listed


async def _root_world(p, db, org: str) -> None:
    """The cases the root's chain-top read decides from the marks or from the rows: in g2 (closed) folders f2 and f4 and a folder fh of gh (a group that hides its children);
    u1 holds, each alone: e and n in f2 (n of no group), c in f2 and linked from f4 (two parents), w in f2 and also in g1 (open to team1, u1's), x right under the App, z
    under ga (a group that carries access only), h in fh, gone (deleted) and shut (RESTRICTED) in f2, and a in f1 of g1. u2 holds the group g2 and e; u3 the folder f2."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": org, "name": "Org"}], "organizations")
        await p.batch_upsert_nodes([{"_key": "g1", "groupName": "g1", "connectorId": "app1", "orgId": org}, {"_key": "g2", "groupName": "g2", "connectorId": "app1", "orgId": org},
                                    {"_key": "gh", "groupName": "gh", "connectorId": "app1", "orgId": org, "hideChildren": True},
                                    {"_key": "ga", "groupName": "ga", "connectorId": "app1", "orgId": org, "accessOnly": True}], "recordGroups")
        await p.batch_upsert_nodes([*(_rec(org, f, folder=True) for f in ("f1", "f2", "f4", "fh")), *(_rec(org, k) for k in ("a", "c", "e", "n", "w", "x", "z", "h")),
                                    _rec(org, "gone"), _rec(org, "shut", accessRule="RESTRICTED")], "records")
        tree = [("app1", "apps", "g1", "recordGroups"), ("app1", "apps", "g2", "recordGroups"), ("app1", "apps", "gh", "recordGroups"), ("app1", "apps", "x", "records"),
                ("g1", "recordGroups", "f1", "records"), ("g2", "recordGroups", "f2", "records"), ("g2", "recordGroups", "f4", "records"), ("gh", "recordGroups", "fh", "records"),
                ("ga", "recordGroups", "z", "records"), ("f1", "records", "a", "records"), ("fh", "records", "h", "records"),
                *(("f2", "records", k, "records") for k in ("c", "e", "n", "w", "gone", "shut"))]
        await p.batch_create_edges([*(edge(*t, relationshipType="PARENT_CHILD") for t in tree), edge("f4", "records", "c", "records", relationshipType="ATTACHMENT")], "nodeRelations")
        groups = {"f1": "g1", "a": "g1", "fh": "gh", "h": "gh", **dict.fromkeys(("f2", "f4", "c", "e", "w", "x", "z", "gone", "shut"), "g2")}
        await p.batch_create_edges([*(edge(k, "records", g, "recordGroups") for k, g in groups.items()), edge("w", "records", "g1", "recordGroups"),
                                    *(edge(g, "recordGroups", "app1", "apps") for g in ("g1", "g2", "gh", "ga")), *(edge(u, "users", org, "organizations") for u in VIEWERS)],
                                   "belongsTo")
        await p.batch_upsert_nodes([{"_key": u, "orgId": org, "email": f"{u}@example.com"} for u in VIEWERS], "users")
        await p.batch_upsert_nodes([{"_key": "team1", "orgId": org}], "teams")
        await p.batch_create_edges([edge("u1", "users", "team1", "teams", type="USER"), edge("team1", "teams", "g1", "recordGroups", type="TEAM", role="READER"),
                                    *(edge("u1", "users", k, "records", type="USER", role="READER") for k in ("a", "c", "e", "n", "w", "x", "z", "h", "gone", "shut")),
                                    edge("u2", "users", "g2", "recordGroups", type="USER", role="READER"), edge("u2", "users", "e", "records", type="USER", role="READER"),
                                    edge("u3", "users", "f2", "records", type="USER", role="READER")], "permission")
        await p.batch_create_edges([edge(u, "users", "app1", "apps") for u in VIEWERS], "userAppRelation")
    assert (await flip(db, org))[0] == 1
    await _change(p, db, org, lambda: _flag(org, "gone", "true"))


async def _roots(p, org: str, user: str) -> dict[str, Any]:
    """The viewer's root and its sidebar, each row's id and arrow, and its counts."""
    with ctx(org):
        acc = await p.get_knowledge_hub_access_v3(user, org)
        kw = {"grantee_ids": acc["grantee_ids"], "gated_app_ids": acc["gated_app_ids"], "grants_by_connector": acc["by_connector"], "user_key": user}
        return {side: _shown(await p.get_knowledge_hub_connector_page_v3("app1", org, limit=50, flatten=False, start_id="app1", start_type="app",
                                                                         filters={"only_containers": True} if side else {}, **kw)) for side in (False, True)}


async def test_the_root_read_from_the_marks_and_the_rows_lists_what_the_walk_lists(pg_provider, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """The root's chain-tops from the grants' marks (node_parent set), from the rows (the marks cleared) and from the walk over every grant are the same, row for row, with
    the same arrows and counts, for every viewer; and the marks are what lets most of them skip the rows."""
    p, org = pg_provider, new_org()
    await _root_world(p, pg_db, org)
    par = await _parents(pg_db, org)
    assert par[("u1", "e")] == "f2" and par[("u1", "z")] == "ga" and par[("u1", "x")] == "app1"
    assert par[("u1", "c")] is None and par[("u1", "w")] is None and par[("u1", "n")] is None and par[("u1", "gone")] is None and par[("u1", "shut")] is None
    marked = {u: await _roots(p, org, u) for u in VIEWERS}
    # u1: e, z (its parent carries access only), c (neither parent open) and n (of no group) at the root; not a (g1 opens), w (in g1 too), x (the App's own child, listed
    # as one), h (hidden), gone or shut; and g1 by the walk from the App
    assert sorted(i for i, _ in marked["u1"][False][0]) == ["c", "e", "g1", "n", "x", "z"]
    assert sorted(i for i, _ in marked["u2"][False][0]) == ["g2"] and sorted(i for i, _ in marked["u3"][False][0]) == ["f2"]
    await _admin("UPDATE kg.scope_members SET node_parent = NULL WHERE org_id = $1", org)
    assert {u: await _roots(p, org, u) for u in VIEWERS} == marked
    await drain_org(pg_db, org)

    async def every(self, org_, app_id, user_key, granted, transaction) -> kh_listing.TopGrants:
        return kh_listing.TopGrants(granted, None, {}, None)
    monkeypatch.setattr(kh_listing.KhListingMixin, "_kh_top_grants", every)
    assert {u: await _roots(p, org, u) for u in VIEWERS} == marked


@pytest.mark.pg_table_lock           # clears kg's statistics, which every other test's plans read: runs alone
async def test_the_roots_chain_top_reads_are_seeks_with_no_statistics_and_analysed(pg_provider, pg_db, no_stats) -> None:  # noqa: F811 (the fixture)
    """EXPLAIN gate, as the first drain's (test_kg_first_drain): on its world (5,000 files, each held alone by u2), kg's statistics cleared and then analysed, the root's read of the
    rows behind the grants whose node_parent is not set (``ROOT_CHAIN``, under reader.seeks as the browse runs it) reads kg.records and kg.links only by their key's index, bounded
    by the org and the key, a folder's rows at most per probe; and the viewer's candidate grants (``CANDIDATES``, as kg.hierarchy profiles it) are one range of the chain-top
    index per principal."""
    p, org = pg_provider, new_org()
    await _drain_world(p, org)
    assert (await flip(pg_db, org))[0] == 1

    async def keys(tx) -> tuple[int, list[int]]:
        app = await tx.fetchval("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app1'", org)
        return app, [r["sk"] for r in await tx.fetch("SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND NOT folder ORDER BY sk", org)][::25]
    with ctx(org):
        app, sks = await pg_db.run(keys)
    for state in ("no statistics", "analysed"):
        await (no_stats() if state == "no statistics" else _admin(DRAIN_RESTORE))

        async def go(tx) -> tuple[int, dict, dict]:
            async with seeks(tx):
                n = len(await tx.fetch(grant_marks.ROOT_CHAIN, org, sks, app))
                chain = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + grant_marks.ROOT_CHAIN, org, sks, app)
            await tx.fetchval(kg_read.PROFILE)
            cands = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + grant_marks.CANDIDATES, org, "u2", app)
            return n, *((json.loads(x) if isinstance(x, str) else x)[0] for x in (chain, cands))
        with ctx(org):
            n, chain, cands = await pg_db.run(go, custom_plan=True)
        assert n > 0, f"{state}: the read found nothing, so the gate judged no probe"
        assert not (bad := _not_seeks(chain)), f"{state} root chain: not a seek per key: {bad}"
        members = [b for node in plan_gate.scans(cands, ("scope_members",)) for b in plan_gate.bounds(node)]
        assert members and all(i == "scope_members_tops" and "org_id = " in c and "principal_sk >= " in c and "app_sk = " in c for i, c in members), f"{state} candidates: {members}"
