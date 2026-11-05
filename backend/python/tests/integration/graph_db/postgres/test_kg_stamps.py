"""The access stamps of kg.records (class, flags, path): after any change the stamps equal a fresh compile of the graph, a change restamps only what it can reach, and a stamp
that reaches a connector the unit does not hold is redone with it.

The compiler itself (``permissions.classes``) is held to the oracle by its own gate; what is tested here is that kg's stamps are what that compiler says of the graph as it is
(a dry run of the whole connector, and a second organisation loaded with the same graph and projected whole), and that the incremental projection never leaves a stale stamp.
"""
from __future__ import annotations

import json
import random

import pytest

from app.services.graph_db.postgres.kg import project as project_mod
from app.services.graph_db.postgres.kg.drainer import drain_org
from app.services.graph_db.postgres.kg.locks import NeedLocks
from app.services.graph_db.postgres.kg.stamp import StampStats, stamp, stamp_app

from .kg_fresh import fresh_with_stamps
from .pg_support import new_org
from .test_kg_derivation import ctx, prov
from .test_kg_projection import add_edge, delete_edge, delete_node, update_node
from .test_kg_projection_races import OPS, world2

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

STAMPS = "SELECT sk, path, class_id, self_lit, seed, own_grant, under_deleted, legacy_reason, leaf FROM kg.records WHERE org_id = $1"


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
            out[ids[r["sk"]]] = (path, classes.get(r["class_id"]), r["self_lit"], r["seed"], r["own_grant"], r["under_deleted"], r["legacy_reason"], r["leaf"])
        return out
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
