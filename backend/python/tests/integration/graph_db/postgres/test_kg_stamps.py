"""The access stamps of kg.records (class, flags, path): after any change the stamps equal a fresh compile of the graph, a change restamps only what it can reach, and a stamp
that reaches a connector the unit does not hold is redone with it.

The compiler itself (``permissions.classes``) is held to the oracle by its own gate; what is tested here is that kg's stamps are what that compiler says of the graph as it is
(a dry run of the whole connector, and a second organisation loaded with the same graph and projected whole), and that the incremental projection never leaves a stale stamp.
"""
from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres.kg import project as project_mod
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import NeedLocks
from app.services.graph_db.postgres.kg.stamp import StampStats, stamp, stamp_app
from app.services.graph_db.postgres.kg.writers import KgWriter

from .kg_fresh import fresh_with_stamps
from .pg_support import new_org
from .test_kg_derivation import ctx, edge, prov
from .test_kg_projection import add_edge, delete_edge, delete_node, update_node
from .test_kg_projection_races import OPS, world2

if TYPE_CHECKING:
    from app.core.db import Tx

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

STAMPS = "SELECT sk, path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf, r13 FROM kg.records WHERE org_id = $1"


@pytest.fixture(autouse=True)
def projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.setenv("PG_KG_STRICT", "1")


async def held_stamps(p, org: str) -> dict:
    """id -> (path as ids, class as (app, group, clauses with ids), flags) of every record row of the org."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        classes = {r["id"]: (ids.get(r["app_sk"]), ids.get(r["group_sk"]), sorted(sorted(("@self" if x == "@self" else ids.get(x)) for x in c) for c in json.loads(r["clauses"])))
                   for r in await tx.fetch("SELECT id, app_sk, group_sk, clauses FROM kg.scope_classes WHERE org_id = $1", org)}
        out = {}
        for r in await tx.fetch(STAMPS, org):
            path = tuple(ids.get(int.from_bytes(bytes(r["path"])[i:i + 8], "big")) for i in range(0, len(r["path"]), 8)) if r["path"] is not None else None
            out[ids[r["sk"]]] = (path, classes.get(r["class_id"]), r["self_lit"], r["seed"], r["own_grant"], r["under_deleted"], r["legacy_reason"], r["leaf"], r["r13"])
        return out
    with ctx(org):
        return await p._q(go, None)


async def held_scopes(p, org: str) -> dict:
    """id -> (kind, connector id, restricted, rule_restricted, needs_grant, deleted) of every access list of the org."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        return {ids[r["sk"]]: (r["kind"], ids[r["app_sk"]], r["restricted"], r["rule_restricted"], r["needs_grant"], r["deleted"])
                for r in await tx.fetch("SELECT * FROM kg.scopes WHERE org_id = $1", org)}
    with ctx(org):
        return await p._q(go, None)


async def dry_diffs(p, org: str, app_id: str) -> list[str]:
    """What a fresh compile of the whole connector says is different from what kg holds."""
    async def go(tx) -> list[str]:
        st = StampStats()
        after = None
        while True:
            after = await stamp_app(tx, org, app_id, after, 2, st, dry=True)
            if after is None:
                return st.diffs
    with ctx(org):
        return await p._q(go, None)


async def test_a_projected_world_has_stamps_that_equal_a_fresh_compile(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == []
    got = await held_stamps(p, org)
    assert got["f1"][1] is None and got["f1"][6] == 0 and got["f2"][6] == 0       # f1 is deleted and f2 is a placeholder in the world: never readable
    assert got["fold"][1] is not None and got["fold"][6] is None                  # a readable record has a class and no refusal
    assert got["fold"][0] == () and got["f1"][0] == ("fold",) and got["f2"][0] == ("g1",)    # the path is the primary chain of parents, root first, the record itself excluded
    assert got["f1"][7] is True and got["fold"][7] is False                       # a record is a leaf unless it has a child
    assert got["g1"][2:5] == (True, True, True)                                   # a group the team is granted on: a literal of its own, a seed, an own grant


@pytest.mark.parametrize("seed", range(60))
async def test_after_random_changes_the_stamps_equal_a_fresh_compile_and_those_of_a_whole_projection(pg_db, seed: int) -> None:
    rng = random.Random(seed)
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    for op in [rng.choice(OPS) for _ in range(rng.randint(2, 6))]:
        await op(p, org)
        if seed % 2 == 0:
            await drain_org(pg_db, org)
    await drain_org(pg_db, org)
    for app in ("app1", "app2"):
        assert await dry_diffs(p, org, app) == [], (seed, app)
    other, want = await fresh_with_stamps(p, org)
    got = await held_stamps(p, org)
    assert _norm(got, org) == _norm(want, other), seed
    assert _norm(await held_scopes(p, org), org) == _norm(await held_scopes(p, other), other), seed


def _norm(h: dict, org: str) -> dict:
    return {("ORG" if k == org else k): v for k, v in h.items() if not k.startswith("org")}


async def changed_by(p, org: str, op) -> tuple[set[str], set[str]]:
    """(the records whose stamps changed, the records the projection says it looked at) for one change, projected on its own."""
    before = await held_stamps(p, org)
    await drain_org(p._db, org)
    await op(p, org)
    seen: set[str] = set()
    real = project_mod.stamp

    async def spying(*a: object, **k: object) -> StampStats:
        st = await real(*a, **k)
        seen.update(i for _, i in st.keys)
        return st
    project_mod.stamp = spying
    try:
        await drain_org(p._db, org)
    finally:
        project_mod.stamp = real
    after = await held_stamps(p, org)
    return {k for k in after if before.get(k) != after[k]}, seen


@pytest.mark.parametrize("seed", range(40))
async def test_a_change_restamps_every_record_it_can_reach_and_none_it_cannot_change(pg_db, seed: int) -> None:
    """The records whose stamps changed are all among those the stamper looked at (nothing outside its affected set changed): the affected set is complete."""
    rng = random.Random(1000 + seed)
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    for _ in range(3):
        changed, seen = await changed_by(p, org, rng.choice(OPS))
        assert changed <= seen | {"ORG"}, (seed, sorted(changed - seen))


async def test_a_stamp_that_reaches_another_connector_asks_for_its_lock_before_it_writes_and_the_projection_takes_it(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 (app2) hangs under f2 (app1)

    async def asks(tx) -> set:
        try:
            await stamp(tx, org, {("records", "x1")}, locked={"app2"})
        except NeedLocks as e:
            return e.apps
        return set()
    with ctx(org):
        assert await pg_db.run(asks, write=True) == {"app1"}                # raised before anything was written
    await drain_org(pg_db, org)                                            # the projection locks both and goes on
    assert await dry_diffs(p, org, "app2") == []
    assert (await held_stamps(p, org))["x1"][0] == ("g1", "f2")


async def test_a_page_of_the_whole_connector_restamp_that_reaches_another_connector_asks_for_its_lock_and_the_restamp_takes_it(pg_db, monkeypatch) -> None:
    from app.services.graph_db.postgres.kg import backfill as backfill_mod

    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 (app2) hangs under f2 (app1)

    async def asks(tx) -> set:
        try:
            await stamp_app(tx, org, "app2", None, 100, locked={"app2"})
        except NeedLocks as e:
            return e.apps
        return set()
    with ctx(org):
        assert await pg_db.run(asks, write=True) == {"app1"}                # raised before the page wrote anything
    held: list[set] = []
    real = backfill_mod.lock_apps

    async def spy(tx: Tx, org_id: str, apps: set[str], **k: bool) -> None:
        held.append(set(apps))
        return await real(tx, org_id, apps, **k)
    monkeypatch.setattr(backfill_mod, "lock_apps", spy)
    await drain_org(pg_db, org)                                            # the structural rows come first, as in a backfill
    await backfill_mod.restamp_app(pg_db, org, "app2")
    assert {"app1", "app2"} in held                                         # the restamp took the other connector's lock, in the same ascending pass, before it wrote
    assert (await held_stamps(p, org))["x1"][0] == ("g1", "f2")


async def test_a_child_that_comes_in_another_connector_takes_its_parent_out_of_leaf_in_the_drain(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["f2"][7] is True
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 is in the second connector
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["f2"][7] is False and await dry_diffs(p, org, "app1") == []


async def test_the_records_of_a_deleted_connector_are_refused_as_without_an_app_and_come_back_with_it(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["fold"][6] is None
    await delete_node(p, org, "apps", "app1")
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["fold"][1:3] == (None, False) and (await held_stamps(p, org))["fold"][6] == 7       # no_app: the walk answers
    await update_node(p, org, "apps", "app1", name="Drive", type="DRIVE", orgId=org)
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["fold"][6] is None and await dry_diffs(p, org, "app1") == []


async def test_the_flags_follow_the_graph_under_a_deleted_folder_and_a_grant_of_a_records_own(pg_db) -> None:
    """Written out from the world, not from the stamper: a record under a deleted folder is under_deleted, a record that carries a grant of its own under a parent is an own grant."""
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "r5", "recordName": "R5", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([{"from_id": "fold", "from_collection": "records", "to_id": "r5", "to_collection": "records", "relationshipType": "PARENT_CHILD"}], "nodeRelations")
        await p.batch_create_edges([{"from_id": "u1", "from_collection": "users", "to_id": "r5", "to_collection": "records", "type": "USER", "role": "READER"}], "permission")
    await drain_org(pg_db, org)
    got = await held_stamps(p, org)
    assert got["r5"][0] == ("fold",) and got["r5"][4] is True and got["r5"][5] is False and got["r5"][7] is True      # path, own grant, not under a deleted node, a leaf
    assert got["fold"][7] is False and got["fold"][4] is False                                                       # the folder has a child now and no grant of its own
    await update_node(p, org, "records", "fold", isDeleted=True)
    await drain_org(pg_db, org)
    got = await held_stamps(p, org)
    assert got["fold"][6] == 0 and got["r5"][5] is True                                                               # the deleted folder is never readable; what hangs under it is under a deleted node
    await update_node(p, org, "records", "fold", isDeleted=False)
    await drain_org(pg_db, org)
    got = await held_stamps(p, org)
    assert got["fold"][6] is None and got["r5"][5] is False and await dry_diffs(p, org, "app1") == []


async def test_the_affected_set_also_follows_the_rows_kg_held_because_a_removed_edge_leaves_no_trace_in_the_graph(pg_db) -> None:
    from app.services.graph_db.postgres.kg.stamp import affected
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await delete_edge(p, org, ("records", "fold"), ("records", "f1"), "nodeRelations", relationshipType="PARENT_CHILD")         # not projected yet: kg's row of f1 still names fold

    async def reach(tx) -> set:
        return (await affected(tx, org, {("records", "fold")}, {}))[0]
    with ctx(org):
        assert ("records", "f1") in await pg_db.run(reach)


async def by_key(p, org: str) -> dict:
    """(collection, id) -> (class clauses as ids or None, own_grant, legacy_reason, leaf) of every row, so two nodes of one id stay two."""
    async def go(tx) -> dict:
        ids = {r["sk"]: r["id"] for r in await tx.fetch("SELECT sk, id FROM kg.node_keys WHERE org_id = $1", org)}
        out = {}
        for r in await tx.fetch("""SELECT k.coll, k.id, r.sk, r.own_grant, r.legacy_reason, r.leaf, r.class_id, c.clauses FROM kg.records r JOIN kg.node_keys k ON k.org_id = r.org_id AND k.sk = r.sk
                LEFT JOIN kg.scope_classes c ON c.org_id = r.org_id AND c.id = r.class_id WHERE r.org_id = $1""", org):
            clauses = None if r["clauses"] is None else sorted(sorted("@self" if x == "@self" else ids[x] for x in c) for c in json.loads(r["clauses"]))
            out[(r["coll"], r["id"])] = (clauses, r["own_grant"], r["legacy_reason"], r["leaf"])
        return out
    with ctx(org):
        return await p._q(go, None)


async def twins(p, org: str, gid: str, *, group_first: bool) -> None:
    """A record X without a grant and a record group of id ``gid`` with one, in one connector (``gid`` = "X": the same id)."""
    async def nodes() -> None:
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": "X", "recordName": "private", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
    async def group() -> None:
        await p.batch_upsert_nodes([{"_key": gid, "groupName": "G", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}], "recordGroups")
    with ctx(org):
        if group_first:
            await group()
        await nodes()
        if not group_first:
            await group()
        await p.batch_create_edges([edge("u1", "users", gid, "recordGroups", type="USER", role="READER")], "permission")
        await p.batch_create_edges([edge("u1", "users", "app1", "apps")], "userAppRelation")
        await p.batch_create_edges([edge("X", "records", "app1", "apps"), edge(gid, "recordGroups", "app1", "apps")], "belongsTo")


@pytest.mark.parametrize("group_first", [True, False])
async def test_a_record_and_a_record_group_of_one_id_are_two_nodes_with_their_own_stamps(pg_db, group_first: bool) -> None:
    from app.services.graph_db.postgres.kg.model import COLL_CODE
    p, a, b = prov(pg_db), new_org(), new_org()
    await twins(p, a, "X", group_first=group_first)         # the record and the group are both "X"
    await twins(p, b, "Xg", group_first=group_first)        # the same world with the group renamed
    await drain_org(pg_db, a)
    await drain_org(pg_db, b)
    rec, grp = COLL_CODE["records"], COLL_CODE["recordGroups"]
    ha, hb = await by_key(p, a), await by_key(p, b)
    assert ha[(rec, "X")] == hb[(rec, "X")] and ha[(grp, "X")][1:] == hb[(grp, "Xg")][1:]
    assert ha[(rec, "X")][1] is False and ha[(grp, "X")][1] is True        # only the group is granted on
    assert await dry_diffs(p, a, "app1") == [] and await dry_diffs(p, b, "app1") == []


async def test_the_verify_flags_a_stamp_that_took_the_facts_of_a_node_of_the_same_id(pg_db) -> None:
    from app.services.graph_db.postgres.kg.model import COLL_CODE
    p, org = prov(pg_db), new_org()
    await twins(p, org, "X", group_first=True)
    await drain_org(pg_db, org)
    assert await dry_diffs(p, org, "app1") == []
    async def swap(tx) -> None:       # the record takes the group's flags and class, what a reader keyed by id alone would have written
        r = await tx.fetchrow("SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = $2 AND id = 'X'", org, COLL_CODE["records"])
        g = await tx.fetchrow("SELECT r.* FROM kg.records r JOIN kg.node_keys k ON k.org_id = r.org_id AND k.sk = r.sk WHERE r.org_id = $1 AND k.coll = $2 AND k.id = 'X'", org, COLL_CODE["recordGroups"])
        await KgWriter(tx).stamps_set([{"sk": r["sk"], "coll": COLL_CODE["records"], "id": "X", "kind": 3, "path": g["path"], "class_id": g["class_id"], "self_lit": g["self_lit"], "seed": g["seed"],
                                        "own_grant": g["own_grant"], "under_deleted": g["under_deleted"], "legacy_reason": g["legacy_reason"], "leaf": g["leaf"]}])
    with ctx(org):
        await pg_db.run(swap, write=True)
    assert any(d.startswith("s|X") for d in await dry_diffs(p, org, "app1"))


async def test_a_page_boundary_between_two_nodes_of_one_id_skips_neither(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await twins(p, org, "X", group_first=False)
    await drain_org(pg_db, org)
    async def pages(tx) -> set:
        st, after = StampStats(), None
        while True:
            after = await stamp_app(tx, org, "app1", after, 1, st, dry=True)       # one node a page
            if after is None:
                return st.keys
    with ctx(org):
        assert await pg_db.run(pages) == {("records", "X"), ("recordGroups", "X")}


async def test_a_record_counts_as_a_member_of_its_app_only_through_a_membership_of_its_own_app(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "kb1", "name": "Docs", "type": "KB", "orgId": org, "createdBy": "me", "createdAtTimestamp": 1, "updatedAtTimestamp": 2},
                                    {"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": k, "recordName": k, "connectorId": "kb1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for k in ("own", "other")], "records")
        await p.batch_create_edges([edge("own", "records", "kb1", "apps"), edge("other", "records", "app2", "apps")], "belongsTo")      # "other" belongs to another app, not to its own
    await drain_org(pg_db, org)
    got = await held_stamps(p, org)
    assert got["own"][1] is not None and got["own"][1][2] == [[]]            # R12: open to the members of its own app
    assert got["other"][1] is None or got["other"][1][2] != [[]]
    assert await dry_diffs(p, org, "kb1") == []


async def test_a_record_deleted_from_the_graph_asks_for_the_lock_of_its_former_connector(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await delete_node(p, org, "records", "f1")           # a leaf of app1 whose row kg still holds

    async def asks(tx) -> set:
        try:
            await stamp(tx, org, {("records", "f1")}, locked=set())
        except NeedLocks as e:
            return e.apps
        return set()
    with ctx(org):
        assert await pg_db.run(asks, write=True) == {"app1"}


async def test_a_connector_with_a_record_of_an_over_long_id_verifies_clean_and_the_record_stays_without_a_stamp(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    long_id = "Q" * 300
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": long_id, "recordName": "long", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"},
                                    {"_key": "c", "recordName": "child", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([edge("c", "records", "app1", "apps")], "belongsTo")
    await drain_org(pg_db, org)
    assert await dry_diffs(p, org, "app1") == []
    assert long_id not in await held_stamps(p, org) and "c" in await held_stamps(p, org)      # no row for the long id: a reader finds no stamp for it


async def test_a_parent_whose_id_kg_cannot_name_never_widens_the_class_of_its_child(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    long_id = "P" * 300                                  # over 256 characters: kg holds no key for it
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "u1", "orgId": org, "email": "u@example.com"}], "users")
        await p.batch_upsert_nodes([{"_key": long_id, "recordName": "long", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "accessRule": "RESTRICTED"},
                                    {"_key": "c", "recordName": "child", "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"}], "records")
        await p.batch_create_edges([edge(long_id, "records", "c", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge("u1", "users", long_id, "records", type="USER", role="READER")], "permission")
        await p.batch_create_edges([edge("c", "records", "app1", "apps")], "belongsTo")
    await drain_org(pg_db, org)
    got = (await held_stamps(p, org))["c"]
    print("L3", got)
    assert got[1] is None or got[1][2] == []              # no class wider than "nobody"; a record without one is read by the walk


async def test_the_access_lists_are_the_records_and_groups_with_a_list_of_their_own_and_follow_the_graph(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    # g1 is granted (to a team) and inherits nothing, so it needs a grant; f2 hangs under a folder it does not inherit from (an attachment), so it restricts; f1 is granted too but is
    # deleted with nothing below it, and fold has no list of its own
    assert await held_scopes(p, org) == {"g1": (2, "app1", False, False, True, False), "f2": (3, "app1", True, False, True, False)}
    await update_node(p, org, "records", "f1", accessRule="RESTRICTED")
    await update_node(p, org, "records", "f1", isDeleted=False)
    await drain_org(pg_db, org)
    assert (await held_scopes(p, org))["f1"] == (3, "app1", True, True, True, False)       # a rule other than open or strict restricts and needs a grant
    assert await dry_diffs(p, org, "app1") == []
    await delete_node(p, org, "records", "f1")
    await delete_node(p, org, "recordGroups", "g1")
    await drain_org(pg_db, org)
    assert set(await held_scopes(p, org)) == {"f2"}                                              # the list of a node that is gone goes with it
    assert await dry_diffs(p, org, "app1") == []


async def test_a_stale_access_list_shows_in_the_dry_run_and_a_restamp_repairs_it(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)

    async def corrupt(tx: Tx) -> None:
        ids = {r["id"]: r["sk"] for r in await tx.fetch("SELECT id, sk FROM kg.node_keys WHERE org_id = $1", org)}
        await KgWriter(tx).scopes_set([(ids["g1"], 2, ids["app1"], True, False, False, False), (ids["fold"], 3, ids["app1"], True, True, True, False)])
    with ctx(org):
        await pg_db.run(corrupt, write=True)
    assert sorted(await dry_diffs(p, org, "app1")) == ["sc|fold", "sc|g1"]             # one list wrong, one that should not exist
    from app.services.graph_db.postgres.kg import backfill as backfill_mod
    await backfill_mod.restamp_app(pg_db, org, "app1")
    assert await dry_diffs(p, org, "app1") == []
    assert set(await held_scopes(p, org)) == {"g1", "f2"}


async def test_a_record_of_no_connector_that_a_connector_did_not_bring_is_r13_and_nothing_else_is(pg_db) -> None:
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "own", "recordName": "Own", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"},
                                    {"_key": "alien", "recordName": "Alien", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"},
                                    {"_key": "gone", "recordName": "Gone", "orgId": org, "recordType": "FILE", "origin": "UPLOAD", "isDeleted": True}], "records")
    await drain_org(pg_db, org)
    got = await held_stamps(p, org)
    assert [got[k][8] for k in ("own", "alien", "gone")] == [True, False, False]
    assert all(got[k][8] is False for k in ("fold", "f1", "f2", "x1", "g1"))                    # a connector's own records are never r13
    assert got["own"][1] is None and got["own"][6] == 7                                         # and it keeps its refusal, so a reader that does not know r13 walks
    await update_node(p, org, "records", "own", connectorId="app1")
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["own"][8] is False and await dry_diffs(p, org, "app1") == []
    await update_node(p, org, "records", "own", connectorId=None)
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["own"][8] is True



async def test_r13_follows_the_origin_in_the_graph_not_the_one_kg_held_before(pg_db) -> None:
    """A restamp that runs before the record's own projection has written the new origin must still use the graph's."""
    p, org = prov(pg_db), new_org()
    await world2(p, org)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "own", "recordName": "Own", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"}], "records")
    await drain_org(pg_db, org)
    assert (await held_stamps(p, org))["own"][8] is True
    await update_node(p, org, "records", "own", origin="CONNECTOR")

    async def restamp_only(tx: Tx) -> None:          # the stamper alone: kg.records still says UPLOAD
        await stamp(tx, org, {("records", "own")})
    with ctx(org):
        await pg_db.run(restamp_only, write=True)
    assert (await held_stamps(p, org))["own"][8] is False


async def test_a_restamp_page_counts_a_write_for_every_connector_it_rewrites(pg_db) -> None:
    from app.services.graph_db.postgres.kg import locks

    p, org = prov(pg_db), new_org()
    await world2(p, org)
    await drain_org(pg_db, org)
    await add_edge(p, org, ("records", "f2"), ("records", "x1"), "nodeRelations", relationshipType="PARENT_CHILD")      # x1 (app2) hangs under f2 (app1)
    await drain_org(pg_db, org)
    before = locks.written(org, "app1")

    async def page(tx) -> None:
        await stamp_app(tx, org, "app2", None, 100, locked={"app1", "app2"})
    with ctx(org):
        await pg_db.run(page, write=True)
    assert locks.written(org, "app1") > before                                # the parent of the page's child is app1's row
