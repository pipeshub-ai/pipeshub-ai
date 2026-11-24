"""``get_accessible_virtual_record_ids`` against the per-record access check, on random worlds, both ways.

The oracle takes every indexed, live record of the apps the user passes the gate for and asks the batch access check (``_kh_v3_accessible_rows``)
about each, with no walk and no shortcut. The search scope must return exactly what that admits: a record it leaves out is an under-admit, one it
adds is an over-admit. The worlds mix an app that opens everything, record-level connectors (access rules, inheritance, grants to the user, a group
and the organization, groups that declare their own scope), a collection, an app the user has no gate for, deleted, internal and placeholder
records, and another organization's records under the same ids.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

import app.services.graph_db.postgres.access as access_module
import app.services.graph_db.postgres.permissions as permissions_module
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg import restamp
from app.services.graph_db.postgres.kg.drainer import drain_org

from .test_access import World, _org
from .test_access import w as w
from .test_grants import in_org
from .test_kg_read import flip
from .test_kg_stamps import dry_diffs

pytestmark = pytest.mark.integration

WIDE, LVL, LVL2, SHUT, KB, KB2 = "wide", "lvl", "lvl2", "shut", "kb", "kb2"


def pick(rng: random.Random, weights: dict[Any, int]) -> Any:  # noqa: ANN401
    return rng.choices(list(weights), list(weights.values()))[0]


async def build(w: World, rng: random.Random, org: str, *, n: int) -> dict[str, dict]:
    """A random world in ``org``; the records it wrote, by id."""
    p = w.p
    edges: dict[str, list[dict]] = {}

    def edge(coll: str, f: tuple[str, str], t: tuple[str, str], **doc: object) -> None:
        edges.setdefault(coll, []).append({"from_collection": f[0], "from_id": f[1], "to_collection": t[0], "to_id": t[1], **doc})

    def child(parent: tuple[str, str], kid: tuple[str, str], *, inherits: bool = True, rel: str = "PARENT_CHILD") -> None:
        edge("nodeRelations", parent, kid, relationshipType=rel)
        if inherits:
            edge("inheritPermissions", kid, parent)

    await w.nodes("users", {"_key": "u1", "userId": "uid-u1"}, {"_key": "u2", "userId": "uid-u2"}, {"_key": "u3", "userId": "uid-u3"})
    await w.nodes("groups", {"_key": "g1"})
    await w.nodes("organizations", {"_key": "o1"})
    await w.nodes("apps", {"_key": WIDE, "type": "CONNECTOR", "permissionModel": "APP_LEVEL"}, {"_key": LVL, "type": "CONNECTOR"},
                  {"_key": LVL2, "type": "CONNECTOR", "isActive": False}, {"_key": SHUT, "type": "CONNECTOR", "permissionModel": "APP_LEVEL"},
                  {"_key": KB, "type": "KB"}, {"_key": KB2, "type": "KB"})
    for app in (WIDE, LVL, LVL2, KB):
        edge("permission", ("users", "u1"), ("apps", app), type="USER", role="READER" if app != KB else "WRITER")
    edge("permission", ("users", "u2"), ("apps", LVL), type="USER", role="READER")
    edge("permission", ("users", "u2"), ("apps", WIDE), type="USER", role="READER")
    edge("permission", ("users", "u2"), ("groups", "g1"), type="USER", role="READER")
    edge("belongsTo", ("users", "u2"), ("organizations", "o1"), entityType="ORGANIZATION")
    edge("permission", ("users", "u3"), ("apps", KB2), type="USER", role="READER")        # a role a collection does not count
    recs: dict[str, dict] = {}
    for app in (WIDE, LVL, LVL2, SHUT, KB, KB2):
        parents: list[tuple[str, str]] = [("apps", app)]
        kb = app in (KB, KB2)
        for j in range(0 if kb else rng.randint(3, 6)):      # record groups, nested or not, some declaring their own scope
            gid = f"{app}-g{j}"
            declared = not kb and rng.random() < 0.2
            await w.nodes("recordGroups", {"_key": gid, "connectorId": app, "isDeleted": rng.random() < 0.08,
                                           **({"permissionModel": "RECORD_GROUP_LEVEL"} if declared else {}), **({"accessRule": pick(rng, {"RESTRICTED": 1, "STRICT": 1})} if rng.random() < 0.2 else {})})
            child(rng.choice(parents), ("recordGroups", gid), inherits=rng.random() < 0.8)
            parents.append(("recordGroups", gid))
            if rng.random() < 0.3:
                edge("permission", ("users", rng.choice(["u1", "u2"])), ("recordGroups", gid), type="USER", role="READER")
            if rng.random() < 0.15:
                edge("permission", ("groups", "g1"), ("recordGroups", gid), type="GROUP", role="READER")
        for i in range(n if not kb else n // 2):
            rid = f"{app}-r{i}"
            doc = {"_key": rid, "connectorId": app, "recordName": rid, "virtualRecordId": f"v-{app}-{i // 2 if rng.random() < 0.2 else i}",
                   "indexingStatus": pick(rng, {"COMPLETED": 9, "QUEUED": 1}), "origin": "UPLOAD" if kb else "CONNECTOR", "recordType": "FILE",
                   "sourceCreatedAtTimestamp": rng.randint(1, 100), "isDeleted": rng.random() < 0.08, "isInternal": rng.random() < 0.05,
                   "isPlaceholder": rng.random() < 0.04, "accessRule": pick(rng, {None: 6, "OPEN": 3, "RESTRICTED": 2, "STRICT": 2})}
            if doc["accessRule"] is None:
                del doc["accessRule"]
            if rng.random() < 0.1:
                doc["indexingStatus"], doc["hasIndexedCopy"] = "QUEUED", True
            await w.nodes("records", doc)
            recs[rid] = doc
            if kb:
                if rng.random() < 0.9:
                    edge("belongsTo", ("records", rid), ("apps", app))
                continue
            above = rng.choice(parents)
            child(above, ("records", rid), inherits=rng.random() < 0.8, rel=pick(rng, {"PARENT_CHILD": 4, "ATTACHMENT": 1}))
            if rng.random() < 0.3:
                parents.append(("records", rid))
            if rng.random() < 0.15:
                edge("belongsTo", ("records", rid), ("recordGroups", rng.choice(parents)[1]) if False else ("recordGroups", f"{app}-g0"))
            roll = rng.random()
            if roll < 0.08:
                edge("permission", ("users", rng.choice(["u1", "u2"])), ("records", rid), type="USER", role="READER")
            elif roll < 0.12:
                edge("permission", ("groups", "g1"), ("records", rid), type="GROUP", role="READER")
            elif roll < 0.15:
                edge("permission", ("organizations", "o1"), ("records", rid), type=pick(rng, {"ORG": 1, "DOMAIN": 1}), role="READER")
    for coll, es in edges.items():
        await p.batch_create_edges(es, coll)
    return recs


ODD = [None, False, 0, "", [], {}, "0", "false", 1, True, "yes", 0.0]
ODDP = [None, False, 0, "0", "false", 1, True, "yes", "t"]


async def extras(w: World, rng: random.Random, recs: dict[str, dict]) -> None:
    """More shapes: app members with no hierarchy, a member of another app, odd flag values, records with two parents, parents of another connector, long chains."""
    p = w.p
    es = {"nodeRelations": [], "inheritPermissions": [], "belongsTo": [], "permission": []}

    def e(c: str, f: tuple[str, str], t: tuple[str, str], **d: object) -> None:
        es.setdefault(c, []).append({"from_collection": f[0], "from_id": f[1], "to_collection": t[0], "to_id": t[1], **d})

    def mk(rid: str, app: str, **kw: object) -> dict:
        d = {"_key": rid, "connectorId": app, "recordName": rid, "virtualRecordId": "v-" + rid, "indexingStatus": "COMPLETED",
             "origin": "CONNECTOR", "recordType": "FILE", "sourceCreatedAtTimestamp": 50, "isDeleted": False, "isInternal": False, "isPlaceholder": False, **kw}
        for k in [k for k, v in d.items() if v is None and k in ("isInternal", "isPlaceholder")]:
            del d[k]
        recs[rid] = d
        return d
    docs = []
    # wide app: members with no hierarchy, odd flags, member of another app only
    for i, v in enumerate(ODD):
        for fld in ("isInternal", "isPlaceholder"):
            if fld == "isPlaceholder" and (isinstance(v, float) or v not in ODDP):
                continue
            d = mk(f"wm-{fld}-{i}", WIDE, **{fld: v})
            docs.append(d)
            e("belongsTo", ("records", d["_key"]), ("apps", WIDE))
    d = mk("wm-other", WIDE)
    docs.append(d)
    e("belongsTo", ("records", "wm-other"), ("apps", KB))
    d = mk("wm-none", WIDE)
    docs.append(d)
    # cross-connector parents
    for i in range(6):
        b = rng.choice([WIDE, LVL, SHUT, LVL2])
        pg = f"{rng.choice([WIDE, LVL, SHUT])}-g0"
        d = mk(f"x-{i}", b)
        docs.append(d)
        e("nodeRelations", ("recordGroups", pg), ("records", d["_key"]), relationshipType="PARENT_CHILD")
        if rng.random() < .7:
            e("inheritPermissions", ("records", d["_key"]), ("recordGroups", pg))
    # DAG: two parents
    for app in (WIDE, LVL):
        for i in range(8):
            d = mk(f"dag-{app}-{i}", app, accessRule=rng.choice(["OPEN", "RESTRICTED", "STRICT"]))
            docs.append(d)
            for pg in rng.sample([f"{app}-g0", f"{app}-g1", f"{app}-g2"], 2):
                e("nodeRelations", ("recordGroups", pg), ("records", d["_key"]), relationshipType="PARENT_CHILD")
                if rng.random() < .6:
                    e("inheritPermissions", ("records", d["_key"]), ("recordGroups", pg))
    # chain of 55 under LVL and WIDE
    for app in (WIDE, LVL):
        prev = ("apps", app)
        for i in range(55):
            d = mk(f"chain-{app}-{i}", app)
            docs.append(d)
            e("nodeRelations", prev, ("records", d["_key"]), relationshipType="PARENT_CHILD")
            e("inheritPermissions", ("records", d["_key"]), prev)
            prev = ("records", d["_key"])
    await p.batch_upsert_nodes([{"orgId": w.org, **d} for d in docs], "records")
    for c, batch in es.items():
        if batch:
            await p.batch_create_edges(batch, c)


async def oracle(w: World, uid: str, recs: dict[str, dict], keep: Any = lambda _d: True) -> dict[str, str]:  # noqa: ANN401
    """The per-record access check over every eligible record, no narrowing."""
    p = w.p
    user = await p.get_user_by_user_id(uid, raise_on_error=True)
    if not user:
        return {}
    access = await p.get_knowledge_hub_access_v3(user["id"], w.org)
    gated = set(access["gated_app_ids"])
    ids = sorted(i for i, d in recs.items() if d["connectorId"] in gated and d.get("virtualRecordId") and not d["isDeleted"] and keep(d)
                 and (d["indexingStatus"] == "COMPLETED" or d.get("hasIndexedCopy")))
    ok: set[str] = set()
    for k in range(0, len(ids), 500):
        rows = await p._kh_v3_accessible_rows(user["id"], w.org, ids[k:k + 500], [], access=access, transaction=None)
        ok |= {r["id"] for r in rows if not r.get("isInternal") and not r.get("isPlaceholder")}
    out: dict[str, str] = {}
    for i in ids:
        if i in ok:
            out.setdefault(recs[i]["virtualRecordId"], i)
    return out


async def check(w: World, pg_db, seed: int, *, n: int, users: tuple[str, ...], promote: bool = False) -> int:
    """Returns how many connectors kg serves (``promote``: every connector copied, verified and promoted first)."""
    rng = random.Random(seed)
    recs = await build(w, rng, w.org, n=n)
    await extras(w, rng, recs)
    promoted = (await flip(pg_db, w.org))[0] if promote else 0
    other = _org()
    try:
        with in_org(other):       # another organization's records under the same ids, open to nobody here
            await build(World(w.p, other), random.Random(seed + 100), other, n=15)
        lo = {"source_created_after_ms": 40, "source_created_before_ms": 70}
        for uid in users:
            want = await oracle(w, uid, recs)
            got = await w.p.get_accessible_virtual_record_ids(uid, w.org)
            assert got == want, (uid, sorted(set(got.items()) ^ set(want.items()))[:6])
            if uid == "uid-u1":      # the world opens some of what is eligible to this user and shuts some
                user = await w.p.get_user_by_user_id(uid, raise_on_error=True)
                gated = set((await w.p.get_knowledge_hub_access_v3(user["id"], w.org))["gated_app_ids"])
                eligible = {d["virtualRecordId"] for d in recs.values() if d["connectorId"] in gated and not d["isDeleted"]
                            and (d["indexingStatus"] == "COMPLETED" or d.get("hasIndexedCopy"))}
                assert 20 < len(want) < len(eligible) - 5, (len(want), len(eligible))
            want_t = await oracle(w, uid, recs, lambda d: 40 <= d["sourceCreatedAtTimestamp"] <= 70)      # a time window narrows the same answer
            assert await w.p.get_accessible_virtual_record_ids(uid, w.org, time_range=lo) == want_t, uid
            want_x = await oracle(w, uid, recs, lambda d: d["connectorId"] != LVL)
            assert await w.p.get_accessible_virtual_record_ids(uid, w.org, exclude_app_ids=frozenset({LVL})) == want_x, ("excluded", uid)
    finally:
        with in_org(other):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", other), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)
    return promoted


@pytest.mark.parametrize("chunk", [1, 100000])      # 1: every app is read as a whole, in sets; 100000: only the per-record check
@pytest.mark.parametrize("seed", range(12))
async def test_the_search_scope_is_exactly_what_the_per_record_check_admits(w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int, chunk: int) -> None:
    monkeypatch.setattr(permissions_module, "_CHECK_CHUNK", chunk)
    await check(w, pg_db, seed, n=40, users=("uid-u1", "uid-u2", "uid-u3", "uid-nobody"))


async def test_the_search_scope_of_apps_of_more_than_a_chunk_of_records_is_exactly_what_the_per_record_check_admits(w: World, pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    whole: list[str] = []
    real = access_module.records_below_app

    async def spy(tx: object, org: str, app_id: str, granted: set[str], *, opens: bool) -> set[str]:
        whole.append(app_id)
        return await real(tx, org, app_id, granted, opens=opens)
    monkeypatch.setattr(access_module, "records_below_app", spy)
    await check(w, pg_db, 3, n=2800, users=("uid-u1", "uid-u2"))
    assert WIDE in whole      # the unfiltered searches read the app whole (more than a chunk of the candidates, at least half of its records), as the real threshold says


@pytest.mark.timeout(300)      # copying, verifying and promoting every connector of the world first
@pytest.mark.parametrize("seed", range(8))
async def test_the_search_scope_of_connectors_kg_serves_is_read_from_their_classes_and_is_what_the_per_record_check_admits(
        w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """Once kg serves the record-level connectors, their candidates are the records their classes admit and the rows kg does not decide, never a walk of the graph."""
    walked: list[str] = []
    real = access_module.reachable_records

    async def spy(tx: object, org: str, access: dict, app_ids: list[str]) -> tuple:
        walked.extend(app_ids)
        return await real(tx, org, access, app_ids)
    monkeypatch.setattr(access_module, "reachable_records", spy)
    promoted = await check(w, pg_db, seed, n=40, users=("uid-u1", "uid-u2", "uid-u3", "uid-nobody"), promote=True)
    assert promoted, "the world was not promoted: the test proves nothing"
    assert LVL not in walked and LVL2 not in walked, walked


async def walk_scopes(w: World, recs: dict[str, dict], users: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """Each user's oracle with kg switched off: the graph's own answer."""
    monkeypatch.setenv("PG_KG_READ", "0")
    try:
        return {uid: await oracle(w, uid, recs) for uid in users}
    finally:
        monkeypatch.delenv("PG_KG_READ")


async def served(pg_db, org: str) -> set[str]:
    async def go(tx: object) -> set[str]:
        return {a for a in (WIDE, LVL, LVL2, SHUT, KB, KB2) if await kg_read.serves(tx, org, a)}
    return await pg_db.run(go)


MEMBERS = ("uid-u1", "uid-u2", "uid-u3", "uid-nobody")


async def widest_group(pg_db, org: str, app: str) -> tuple[str, str | None]:
    """The live record group of ``app`` with the most records below it (hierarchy children and members, to any depth), and its access rule."""
    async def go(tx: object) -> list:
        return await tx.fetch("""WITH RECURSIVE d(g, id) AS (
              SELECT n.id, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'recordGroups' AND n.doc->>'connectorId' = $2 AND NOT coalesce((n.doc->>'isDeleted')::boolean, false)
              UNION SELECT d.g, x.id FROM d CROSS JOIN LATERAL (
                SELECT e.to_id AS id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_id = d.id
                UNION ALL SELECT e.from_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.to_id = d.id AND e.from_coll = 'records') x)
            SELECT d.g, count(*) AS n, min(n.doc->>'accessRule') AS rule FROM d JOIN graph.nodes n ON n.org_id = $1 AND n.coll = 'recordGroups' AND n.id = d.g
            GROUP BY d.g ORDER BY n DESC, d.g LIMIT 1""", org, app)
    r = (await pg_db.run(go))[0]
    return r["g"], r["rule"]


@pytest.mark.timeout(300)
@pytest.mark.parametrize("seed", range(4))
async def test_a_membership_added_and_removed_while_kg_serves_never_restamps_a_connector_whole_and_ends_with_the_walks_answer(
        w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """A membership is a closure row, and no stamp reads who links to a connector. With a restamp threshold of 3: u3, who only belongs to a collection, joins and leaves a
    group that is granted records, and kg keeps serving every connector it served with nothing queued; u1, who also holds grants on records, does too, and what is queued is
    at most the rows its grants reach, never a connector whole. After each change every user's scope is the graph's."""
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    recs = await build(w, random.Random(seed), w.org, n=40)
    await extras(w, random.Random(seed), recs)
    assert (await flip(pg_db, w.org))[0], "the world was not promoted: the test proves nothing"
    before = await served(pg_db, w.org)
    assert LVL in before
    for user in ("u3", "u1"):
        for change in (w.p.batch_create_edges, w.p.batch_delete_edges):
            await change([{"from_collection": "users", "from_id": user, "to_collection": "groups", "to_id": "g1", "type": "USER", "role": "READER"}], "permission")
            await drain_org(pg_db, w.org)
            queued = await pg_db.run(lambda tx: restamp.pending(tx, w.org))
            assert "app" not in {j.kind for j in queued}, (user, change.__name__)
            if user == "u3":
                assert queued == [] and await served(pg_db, w.org) == before, change.__name__
            await restamp.run_jobs(pg_db, w.org, "w1")
            want = await walk_scopes(w, recs, MEMBERS, monkeypatch)
            for uid in MEMBERS:
                assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], (user, change.__name__, uid)


@pytest.mark.timeout(300)
@pytest.mark.parametrize("seed", range(2))
async def test_a_change_of_an_app_waiting_behind_a_membership_of_one_of_its_members_is_still_restamped(
        w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """u3 joins a group, then the collection u3 belongs to stops being a collection. The drain takes one row a page: the membership's projection brings the app in (u3
    links to it) without restamping it, so it must not count the app's own change, waiting behind it, as done. That change is restamped, and every scope is the graph's."""
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    recs = await build(w, random.Random(seed), w.org, n=40)
    await extras(w, random.Random(seed), recs)
    assert (await flip(pg_db, w.org))[0], "the world was not promoted: the test proves nothing"
    await w.p.batch_create_edges([{"from_collection": "users", "from_id": "u3", "to_collection": "groups", "to_id": "g1", "type": "USER", "role": "READER"}], "permission")
    await w.nodes("apps", {"_key": KB2, "type": "CONNECTOR"})
    await drain_org(pg_db, w.org, page=1)
    await restamp.run_jobs(pg_db, w.org, "w1")
    want = await walk_scopes(w, recs, MEMBERS, monkeypatch)
    for uid in MEMBERS:
        assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], uid
    assert await dry_diffs(w.p, w.org, KB2) == []


@pytest.mark.timeout(300)
@pytest.mark.parametrize("seed", range(4))
async def test_a_read_while_a_restamp_is_under_way_never_admits_what_the_graph_denies(w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """Changes that do outdate stamps (a record group narrowed, a connector that opened everything to its members closed) and a membership taken away land together; the queued restamp runs one small
    batch at a time, and after every batch each user's scope is exactly the graph's: what the restamp has not reached yet is never answered from its outdated stamp."""
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    recs = await build(w, random.Random(seed), w.org, n=40)
    await extras(w, random.Random(seed), recs)
    assert (await flip(pg_db, w.org))[0], "the world was not promoted: the test proves nothing"
    await w.nodes("recordGroups", {"_key": f"{LVL}-g0", "accessRule": "RESTRICTED"})
    await w.nodes("apps", {"_key": WIDE, "permissionModel": None})          # it opened everything to its members; now only grants do
    await w.p.batch_delete_edges([{"from_collection": "users", "from_id": "u2", "to_collection": "groups", "to_id": "g1", "type": "USER"}], "permission")
    await drain_org(pg_db, w.org)
    want = await walk_scopes(w, recs, MEMBERS, monkeypatch)
    steps = 0
    while job := await restamp.claim(pg_db, w.org, "w1"):
        done = False
        while not done:
            for uid in MEMBERS:
                got = await w.p.get_accessible_virtual_record_ids(uid, w.org)
                assert set(got.items()) <= set(want[uid].items()), (steps, uid, sorted(set(got.items()) - set(want[uid].items()))[:6])
            done, job = await restamp.run_batch(pg_db, w.org, job, "w1", batch=2)
            steps += 1
    assert steps > 2, steps
    assert await pg_db.run(lambda tx: restamp.pending(tx, w.org)) == []
    for uid in MEMBERS:
        assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], uid


async def grants_on(pg_db, org: str, node: str) -> list[dict]:
    """The permission edges into the record group ``node``, as the provider deletes them."""
    async def go(tx: object) -> list:
        return await tx.fetch("""SELECT from_coll, from_id, doc->>'type' AS type FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND to_coll = 'recordGroups' AND to_id = $2""",
                              org, node)
    return [{"from_collection": r["from_coll"], "from_id": r["from_id"], "to_collection": "recordGroups", "to_id": node, "type": r["type"]} for r in await pg_db.run(go)]


@pytest.mark.timeout(300)
@pytest.mark.parametrize("change", ["unshare_last", "narrow"])
@pytest.mark.parametrize("seed", range(4))
async def test_a_connector_whose_rows_a_restamp_job_hides_stays_served_and_answers_exactly_what_the_graph_answers(
        w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int, change: str) -> None:
    """Every grant on a record group taken away (it is no longer granted), or the group narrowed, reaches more records than the restamp threshold of 3: a rows job. kg keeps serving
    every connector it served while the job runs one small batch at a time; the rows it has not restamped yet are the walk's, the rest are their stamps', and after every batch
    each user's search scope and per-record answers are exactly the graph's."""
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    recs = await build(w, random.Random(seed), w.org, n=40)
    await extras(w, random.Random(seed), recs)
    group, rule = await widest_group(pg_db, w.org, LVL)
    await w.p.batch_create_edges([{"from_collection": "groups", "from_id": "g1", "to_collection": "recordGroups", "to_id": group, "type": "GROUP", "role": "READER"}], "permission")
    assert (await flip(pg_db, w.org))[0], "the world was not promoted: the test proves nothing"
    before = await served(pg_db, w.org)
    assert LVL in before
    if change == "unshare_last":
        await w.p.batch_delete_edges(await grants_on(pg_db, w.org, group), "permission")
    else:
        await w.nodes("recordGroups", {"_key": group, "accessRule": "RESTRICTED" if rule != "RESTRICTED" else "OPEN"})
    await drain_org(pg_db, w.org)
    queued = await pg_db.run(lambda tx: restamp.pending(tx, w.org))
    assert queued and {j.kind for j in queued} == {"rows"}, queued
    want = await walk_scopes(w, recs, MEMBERS, monkeypatch)
    steps = 0
    while job := await restamp.claim(pg_db, w.org, "w1"):
        done = False
        while not done:
            assert await served(pg_db, w.org) == before, steps
            for uid in MEMBERS:
                assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], (steps, uid)
                assert await oracle(w, uid, recs) == want[uid], (steps, uid)
            done, job = await restamp.run_batch(pg_db, w.org, job, "w1", batch=2)
            steps += 1
    assert steps > 2, steps
    assert await pg_db.run(lambda tx: restamp.pending(tx, w.org)) == [] and await served(pg_db, w.org) == before
    for uid in MEMBERS:
        assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], uid


@pytest.mark.timeout(300)
@pytest.mark.parametrize("seed", range(4))
async def test_a_share_on_a_record_group_that_stays_granted_hides_nothing_and_answers_exactly_what_the_graph_answers(
        w: World, pg_db, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """A grant on a record group that is already granted (a share) changes no stamp below it: with a restamp threshold of 3 nothing is hidden or queued, kg keeps serving every
    connector it served, and each user's search scope and per-record answers are the graph's, before the share is taken back and after."""
    monkeypatch.setenv("PG_KG_INLINE_MAX", "3")
    recs = await build(w, random.Random(seed), w.org, n=40)
    await extras(w, random.Random(seed), recs)
    group, _ = await widest_group(pg_db, w.org, LVL)
    await w.p.batch_create_edges([{"from_collection": "groups", "from_id": "g1", "to_collection": "recordGroups", "to_id": group, "type": "GROUP", "role": "READER"}], "permission")
    assert (await flip(pg_db, w.org))[0], "the world was not promoted: the test proves nothing"
    before = await served(pg_db, w.org)
    assert LVL in before
    u3 = [{"from_collection": "users", "from_id": "u3", "to_collection": "recordGroups", "to_id": group, "type": "USER", "role": "READER"}]
    for change in (w.p.batch_create_edges, w.p.batch_delete_edges):
        await change(u3, "permission")
        await drain_org(pg_db, w.org)
        assert await pg_db.run(lambda tx: restamp.pending(tx, w.org)) == [], change.__name__
        assert await served(pg_db, w.org) == before, change.__name__
        want = await walk_scopes(w, recs, MEMBERS, monkeypatch)
        for uid in MEMBERS:
            assert await w.p.get_accessible_virtual_record_ids(uid, w.org) == want[uid], (change.__name__, uid)
            assert await oracle(w, uid, recs) == want[uid], (change.__name__, uid)
