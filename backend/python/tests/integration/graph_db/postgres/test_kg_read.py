"""The access check answered from the typed graph (``kg.read``, defaults on) equals the walk and the oracle, and takes the walk wherever it may not answer.

Every world of the permission corpus and of the generated worlds is stored in an org of its own, its connectors are copied into kg, verified and promoted to ``enforce``, and then every
viewer x node pair is asked with the switch on and with it off: 0 over-admits and 0 under-admits against the walk, and against the oracle. The rest of the file breaks one thing at a
time (the switch, the connector's state, a stamp, a literal, the database) and requires the walk's answer, and that nothing of another org is ever returned.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

import asyncpg
import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg import backfill as bf
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kg.drainer import drain_org
from tests.permissions import gen
from tests.permissions.graph import Namespace
from tests.permissions.runner import ConformanceRunner, targets
from tests.permissions.scenario import load_all

from .test_conformance import CHUNK, PgLayer, _describe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


def _hex(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()[:24]


def _bound(org: str):  # noqa: ANN202
    return bind(OrgContext(org, None, "job", "job:test", "t"))


@pytest.fixture(autouse=True)
def _projection_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)           # the defaults: the projection and the read are on
    monkeypatch.delenv("PG_KG_FLUSH", raising=False)
    monkeypatch.delenv("PG_KG_READ", raising=False)


@contextlib.contextmanager
def _switch(value: str | None):  # noqa: ANN202
    old = os.environ.get("PG_KG_READ")
    if value is None:
        os.environ.pop("PG_KG_READ", None)
    else:
        os.environ["PG_KG_READ"] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PG_KG_READ", None)
        else:
            os.environ["PG_KG_READ"] = old


async def flip(db, org: str) -> tuple[int, int]:
    """Copy every connector of the org into kg, verify it and promote it to enforce. Returns (promoted, connectors). A world with a value the projection refuses (an access rule that is
    not one of the three) stays on the walk, which is itself a case the check must answer equally."""
    async def apps(tx) -> list[str]:
        return [r["id"] for r in await tx.fetch("SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' ORDER BY id", org)]
    with _bound(org):
        ids = await db.run(apps)
    done = 0
    try:
        await drain_org(db, org)
        for a in ids:
            if (await bf.backfill(db, org, a, page=50)).flipped:
                with contextlib.suppress(bf.Refused):
                    await bf.promote(db, org, a)
                    done += 1
    except asyncpg.exceptions.InvalidParameterValueError:
        pass
    return done, len(ids)


KG_TABLES = ("records", "links", "scope_members", "principal_closure", "scope_classes", "class_literals", "scopes", "restamp_queue", "graph_changes", "projection_state", "node_keys")


class KgLayer(PgLayer):
    """``PgLayer`` whose worlds are copied into kg and promoted after they are stored."""

    def __init__(self, provider, db) -> None:
        super().__init__(provider, db)
        self.promoted = Counter()

    async def _wipe(self, org: str) -> None:
        await super()._wipe(org)
        for t in KG_TABLES:      # what an earlier run of the same world left
            await _admin(f"DELETE FROM kg.{t} WHERE org_id = $1", org)

    async def preload(self, worlds) -> None:
        new = [w for w in dict.fromkeys(worlds) if w not in self._prefix]
        await super().preload(new)
        for w in new:
            done, total = await flip(self._db, _hex(Namespace(self._prefix[w]).org))
            self.promoted.update(promoted=done, connectors=total)


@pytest.fixture
async def layer(pg_provider, pg_db) -> AsyncIterator[KgLayer]:
    lay = KgLayer(pg_provider, pg_db)
    yield lay
    await lay.release(list(lay._prefix))


@pytest.fixture
def decided(monkeypatch: pytest.MonkeyPatch) -> Counter:
    """What the reader decided itself (admitted, denied) and what it left to the walk, over the test."""
    seen: Counter = Counter()
    orig = kg_read.decide

    async def spy(*a: object, **k: object) -> kg_read.Decision:
        d = await orig(*a, **k)
        seen.update(admit=len(d.admit), denied=d.denied, walk=len(d.walk))
        return d
    monkeypatch.setattr(kg_read, "decide", spy)
    return seen


async def _ask(prov, org: str, uid: str, ids: list[str], access: dict, switch: str | None) -> dict[str, dict]:
    with _switch(switch), _bound(org):
        return {r["id"]: r for r in await prov._kh_v3_accessible_rows(uid, org, ids, [], access=access, transaction=None)}


async def _gate(layer: KgLayer, prov, worlds: list, tag: str, decided: Counter, *, need_kg: bool = True) -> None:
    """``need_kg`` False: the worlds may hold a value the projection refuses (the adversarial generator's unknown access rule), so a chunk may be answered by the walk alone."""
    await layer.preload(worlds)
    runner = ConformanceRunner(layer)
    over: list[str] = []
    under: list[str] = []
    rows: list[str] = []
    pairs = 0
    for i, w in enumerate(worlds):
        ns = await layer._ns(w)
        org = _hex(ns.org)
        ids = [ns.id(n) for n in targets(w)]
        for viewer in w.users():
            uid = ns.id(viewer)
            with _bound(org):
                access = await prov.get_knowledge_hub_access_v3(uid, org)
            walk = await _ask(prov, org, uid, ids, access, "0")
            got = await _ask(prov, org, uid, ids, access, None)
            pairs += len(ids)
            over += [f"{tag}{i} {viewer} {x}" for x in sorted(set(got) - set(walk))]
            under += [f"{tag}{i} {viewer} {x}" for x in sorted(set(walk) - set(got))]
            rows += [f"{tag}{i} {viewer} {x}" for x in sorted(set(got) & set(walk)) if got[x] != walk[x]]
        report = await runner.check_world(w, f"{tag}{i}")            # the oracle, answered with the switch on
        assert not report.over_admits, f"{tag}{i}: over-admit against the oracle\n{_describe(report)}"
        assert not report.findings, f"{tag}{i}\n{_describe(report)}"
    if os.environ.get("KG_READ_REPORT"):
        with open(os.environ["KG_READ_REPORT"], "a") as f:
            f.write(f"{tag}: worlds {len(worlds)} pairs {pairs} promoted {dict(layer.promoted)} kg {dict(decided)} over {len(over)} under {len(under)} rows {len(rows)}\n")
    assert over == [], over[:10]
    assert under == [], under[:10]
    assert rows == [], rows[:10]
    assert pairs > 0
    assert not need_kg or (decided["admit"] > 0 and decided["denied"] > 0), f"kg decided too little to prove anything: {dict(decided)}"


@pytest.mark.parametrize("tier", ["rules", "golden"])
async def test_the_corpus_answered_from_kg_equals_the_walk_and_the_oracle(layer, pg_provider, decided, tier) -> None:
    worlds = [s.final_world() for s in load_all() if ("rules" in s.tags) == (tier == "rules")]
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await _gate(layer, pg_provider, chunk, f"{tier}{start}x", decided)
        await layer.release(chunk)


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_answered_from_kg_equal_the_walk_and_the_oracle(layer, pg_provider, decided, kind) -> None:
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await _gate(layer, pg_provider, chunk, f"{kind}{seed}x{start}x", decided, need_kg=kind == "proptest")
            await layer.release(chunk)


# ---- targeted cases -----------------------------------------------------------------------------------------------------------------------

class Case:
    """A world in kg where one viewer is admitted, by a class with a literal, to a node ``x``; ``lit`` is that literal's sk and ``lit_kind`` its kind."""
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


async def _admin(sql: str, *args) -> None:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute(sql, *args)
    finally:
        await con.close()


async def _decide(db, org: str, ids: list[str], access: dict) -> kg_read.Decision:
    async def go(tx) -> kg_read.Decision:
        await tx.fetchval(kg_read.PROFILE)
        return await kg_read.decide(tx, org, ids, access)
    with _bound(org):
        return await db.run(go, isolation="repeatable_read", custom_plan=True)


async def _case(layer: KgLayer, prov, db, *, literal_kind: int | None = None) -> Case:
    """The first (viewer, node) of the generated worlds that kg admits; with ``literal_kind``, one whose class names a literal of that kind."""
    for seed in (20261030, 7, 11):
        worlds = gen.generate("proptest", seed, 30)
        await layer.preload(worlds)
        for w in worlds:
            ns = await layer._ns(w)
            org = _hex(ns.org)
            ids = [ns.id(n) for n in targets(w)]
            for viewer in w.users():
                uid = ns.id(viewer)
                with _bound(org):
                    access = await prov.get_knowledge_hub_access_v3(uid, org)
                d = await _decide(db, org, ids, access)
                for x in sorted(d.admit):
                    async def info(tx, x=x, org=org) -> tuple[Any, dict]:
                        r = await tx.fetchrow("SELECT r.sk, r.class_id, r.kind, r.app_sk FROM kg.records r WHERE r.org_id = $1 AND r.coll = ANY('{1,2,3}'::smallint[]) AND r.id = $2", org, x)
                        c = await tx.fetchval("SELECT clauses FROM kg.scope_classes WHERE org_id = $1 AND id = $2", org, r["class_id"]) if r["class_id"] else None
                        lits = sorted({v for cl in (json.loads(c) if isinstance(c, str) else c or []) for v in cl if v != kg_read.SELF})
                        kinds = {k["sk"]: k["kind"] for k in await tx.fetch("SELECT sk, kind FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])", org, lits)}
                        return r, kinds
                    with _bound(org):
                        r, kinds = await db.run(info)
                    for lit, kind in kinds.items():
                        if literal_kind is None or kind == literal_kind:
                            return Case(world=w, ns=ns, org=org, uid=uid, ids=ids, access=access, x=x, x_sk=r["sk"], app_sk=r["app_sk"], lit=lit, lit_kind=kind)
    raise AssertionError("no generated world has a viewer admitted by a class with such a literal")


async def _same_as_walk(prov, c: Case) -> None:
    assert await _ask(prov, c.org, c.uid, c.ids, c.access, None) == await _ask(prov, c.org, c.uid, c.ids, c.access, "0")


@pytest.mark.parametrize("value", [None, "", "1"])
async def test_the_read_is_on_by_default_and_with_one(layer, pg_provider, pg_db, monkeypatch, value) -> None:
    c = await _case(layer, pg_provider, pg_db)
    calls = Counter()
    orig = kg_read.decide

    async def spy(*a: object, **k: object) -> kg_read.Decision:
        calls["decide"] += 1
        return await orig(*a, **k)
    monkeypatch.setattr(kg_read, "decide", spy)
    with _switch(value):
        assert kg_read.enabled() is True
        got = await _ask(pg_provider, c.org, c.uid, c.ids, c.access, value)
    assert calls["decide"] == 1
    assert got == await _ask(pg_provider, c.org, c.uid, c.ids, c.access, "0")


async def test_with_the_switch_at_zero_nothing_reads_kg(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)

    async def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("kg was read with PG_KG_READ=0")
    monkeypatch.setattr(kg_read, "decide", boom)
    assert kg_read.enabled() is True
    with _switch("0"):
        assert kg_read.enabled() is False
        assert await _ask(pg_provider, c.org, c.uid, c.ids, c.access, "0")


async def test_with_the_projection_off_the_connectors_go_back_to_the_walk(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)
    assert c.x in (await _decide(pg_db, c.org, c.ids, c.access)).admit
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    rec = next(n for n in c.world.nodes if n.kind == "record")
    with _bound(c.org):
        await pg_provider.batch_upsert_nodes([{"id": c.ns.id(rec.id), "recordName": "renamed"}], "records")      # the write takes back its log rows: kg cannot tell it is behind
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert not d.admit and d.denied == 0 and set(d.walk) == set(c.ids)
    await _same_as_walk(pg_provider, c)


async def test_a_connector_that_is_not_enforced_or_not_current_takes_the_walk(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)
    assert (await _decide(pg_db, c.org, c.ids, c.access)).admit
    # shadow: kg is only compared with the walk, never read
    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", c.org)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert not d.admit and d.denied == 0 and set(d.walk) == set(c.ids)
    await _same_as_walk(pg_provider, c)
    # legacy: the way back
    await _admin("UPDATE kg.projection_state SET mode = 'legacy' WHERE org_id = $1 AND app_sk <> 0", c.org)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert not d.admit and d.denied == 0 and set(d.walk) == set(c.ids)
    await _same_as_walk(pg_provider, c)
    # a projection written by another code version
    await _admin("UPDATE kg.projection_state SET mode = 'enforce', code_epoch = code_epoch + 1 WHERE org_id = $1 AND app_sk <> 0", c.org)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert not d.admit and d.denied == 0
    await _same_as_walk(pg_provider, c)


async def test_a_connector_with_a_change_waiting_takes_the_walk_until_it_is_drained(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)
    before = await _decide(pg_db, c.org, c.ids, c.access)
    answered = set(c.ids) - set(before.walk)
    assert c.x in answered
    monkeypatch.setenv("PG_KG_FLUSH", "async")                         # the next write only logs: its connector is not current until it is drained
    local = dict(zip(c.ids, targets(c.world), strict=True))[c.x]
    app = c.world.node(local).connector or local
    rec = next((n for n in c.world.nodes if n.kind == "record" and n.connector == app), None)
    assert rec is not None, "the connector of x has no record to change"
    with _bound(c.org):
        await pg_provider.batch_upsert_nodes([{"id": c.ns.id(rec.id), "recordName": "renamed"}], "records")
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert c.x in d.walk and c.x not in d.admit            # the connector of x waits for its change; another connector of the org is still answered
    await _same_as_walk(pg_provider, c)
    await drain_org(pg_db, c.org)
    assert c.x in (await _decide(pg_db, c.org, c.ids, c.access)).admit


@pytest.mark.parametrize(("column", "value", "reason"), [("fence_job", "99999", "hidden"), ("path", "NULL", "no_path"), ("r13", "NULL", "r13"), ("r13", "true", "r13"),
                                                          ("placeholder", "true", "placeholder"), ("class_id", "NULL", "legacy")])
async def test_a_node_that_is_hidden_unstamped_or_refused_takes_the_walk_and_the_rest_does_not(layer, pg_provider, pg_db, column, value, reason) -> None:
    c = await _case(layer, pg_provider, pg_db)
    base = await _decide(pg_db, c.org, c.ids, c.access)
    others = set(base.admit) - {c.x}
    await _admin(f"UPDATE kg.records SET {column} = {value} WHERE org_id = $1 AND sk = $2", c.org, c.x_sk)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert c.x in d.walk and c.x not in d.admit, reason
    assert others <= d.admit                                            # nothing else is lost
    await _same_as_walk(pg_provider, c)


async def test_a_literal_kg_cannot_vouch_for_never_admits_and_the_node_takes_the_walk(layer, pg_provider, pg_db, monkeypatch) -> None:
    """The class of ``x`` names a record ``lit`` the viewer holds a grant on. If ``lit``'s ``leaf`` is unknown (NULL) or says it is a leaf, or ``lit`` has lost its last child in kg while
    its flag still says it has one (a stale False), the class is not trusted: ``x`` is left to the walk instead of being admitted (or denied) from it."""
    c = await _case(layer, pg_provider, pg_db, literal_kind=3)
    assert c.x in (await _decide(pg_db, c.org, c.ids, c.access)).admit
    for leaf in ("NULL", "true"):
        await _admin(f"UPDATE kg.records SET leaf = {leaf} WHERE org_id = $1 AND sk = $2", c.org, c.lit)
        d = await _decide(pg_db, c.org, c.ids, c.access)
        assert c.x in d.walk and c.x not in d.admit, leaf
        await _same_as_walk(pg_provider, c)
        await _admin("UPDATE kg.records SET leaf = false WHERE org_id = $1 AND sk = $2", c.org, c.lit)
    # a stale False: the record has no child in kg any more but its flag was not updated
    await _admin("UPDATE kg.records SET parent_sk = NULL WHERE org_id = $1 AND parent_sk = $2", c.org, c.lit)
    await _admin("DELETE FROM kg.links WHERE org_id = $1 AND parent_sk = $2 AND kind = 1", c.org, c.lit)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert c.x in d.walk and c.x not in d.admit
    await _same_as_walk(pg_provider, c)
    # without the child check the flag alone admits (what the earlier read did): the check is what stops it
    monkeypatch.setattr(kg_read, "LITERALS", kg_read.LITERALS.replace("(r.kind <> 3 OR coalesce(", "(true OR coalesce("))
    assert c.x in (await _decide(pg_db, c.org, c.ids, c.access)).admit


async def test_a_class_row_that_is_missing_takes_the_walk(layer, pg_provider, pg_db) -> None:
    c = await _case(layer, pg_provider, pg_db)

    async def go(tx) -> int:
        return await tx.fetchval("SELECT class_id FROM kg.records WHERE org_id = $1 AND sk = $2", c.org, c.x_sk)
    with _bound(c.org):
        cid = await pg_db.run(go)
    await _admin("DELETE FROM kg.scope_classes WHERE org_id = $1 AND id = $2", c.org, cid)
    d = await _decide(pg_db, c.org, c.ids, c.access)
    assert c.x in d.walk and c.x not in d.admit
    await _same_as_walk(pg_provider, c)


async def test_an_explicit_transaction_the_system_role_and_a_failing_read_take_the_walk(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)
    want = await _ask(pg_provider, c.org, c.uid, c.ids, c.access, "0")
    assert want

    async def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("kg must not be read here")
    monkeypatch.setattr(kg_read, "decide", boom)
    with _bound(c.org):
        tid = await pg_provider.begin_transaction([], [])
        try:
            got = {r["id"]: r for r in await pg_provider._kh_v3_accessible_rows(c.uid, c.org, c.ids, [], access=c.access, transaction=tid)}
        finally:
            await pg_provider.rollback_transaction(tid)
    assert got == want

    async def fail(*_a: object, **_k: object) -> None:
        raise RuntimeError("kg is down")
    monkeypatch.setattr(kg_read, "decide", fail)
    assert await _ask(pg_provider, c.org, c.uid, c.ids, c.access, None) == want       # fails closed to the walk, not to nothing


async def test_a_blocked_or_slow_read_hands_the_call_to_the_walk_at_once(layer, pg_provider, pg_db, monkeypatch) -> None:
    c = await _case(layer, pg_provider, pg_db)
    want = await _ask(pg_provider, c.org, c.uid, c.ids, c.access, "0")
    holder = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    lock = holder.transaction()
    await lock.start()
    try:
        await holder.execute("LOCK TABLE kg.records IN ACCESS EXCLUSIVE MODE")      # a rewrite or a reindex
        t0 = time.perf_counter()
        got = await _ask(pg_provider, c.org, c.uid, c.ids, c.access, None)
        waited = time.perf_counter() - t0
    finally:
        await lock.rollback()
        await holder.close()
    assert got == want
    assert waited < 1.5, f"the read waited {waited:.1f} s on the lock"
    # a read that runs past the statement limit
    monkeypatch.setattr(kg_read, "PROFILE", kg_read.PROFILE.replace("'2s'", "'100ms'"))
    monkeypatch.setattr(kg_read, "CLASSES", kg_read.CLASSES.replace("WHERE org_id = $1", "WHERE (SELECT pg_sleep(2)) IS NOT NULL AND org_id = $1"))
    t0 = time.perf_counter()
    got = await _ask(pg_provider, c.org, c.uid, c.ids, c.access, None)
    assert got == want
    assert time.perf_counter() - t0 < 1.5


async def test_a_read_never_returns_another_orgs_rows(layer, pg_provider, pg_db) -> None:
    """The org is the session's: an org asked for that is not the session's reads nothing, and kg's own rows are only those of the session's org (row-level security)."""
    c = await _case(layer, pg_provider, pg_db)
    other = _hex("another-org-of-the-test")
    assert other != c.org
    with _bound(other):
        assert await pg_provider._kh_v3_accessible_rows(c.uid, c.org, c.ids, [], access=c.access, transaction=None) == []      # asked for an org that is not the session's
        got = await pg_provider._kh_v3_accessible_rows(c.uid, other, c.ids, [], access=c.access, transaction=None)              # the session's org holds none of these ids
        assert got == []

        async def go(tx) -> tuple[kg_read.Decision, list]:
            d = await kg_read.decide(tx, other, c.ids, c.access)
            seen = await tx.fetch(kg_read.BY_ID, c.org, c.ids)                   # another org's org id, under this session
            return d, seen
        d, seen = await pg_db.run(go)
    assert not d.admit and d.denied == 0 and set(d.walk) == set(c.ids)
    assert list(seen) == []


N = 12_000


async def test_every_statement_of_the_reader_is_answered_by_index_searches(layer, pg_provider, pg_db) -> None:
    """EXPLAIN gate: on an org of 12,000 records, as the reader's unit runs the statements (custom plans, bitmap scans off), never analysed and then analysed, each is bounded by its
    inputs: no sequential or bitmap heap scan, and the index conditions are the org and the key asked. Run on every PostgreSQL version of the suite."""
    c = await _case(layer, pg_provider, pg_db, literal_kind=3)
    await _admin("INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, leaf) SELECT $1, 50000000 + g, 3, 'plans-r' || g, 3, $2, true FROM generate_series(1, $3) g", c.org, c.app_sk, N)
    await _admin("INSERT INTO kg.links (org_id, parent_sk, kind, child_sk) SELECT $1, 50000000 + g, 1, 60000000 + g FROM generate_series(1, $2) g", c.org, N)
    ids = [*c.ids, *(f"plans-r{j}" for j in range(1, N, 240))]
    sks = [c.lit, c.x_sk, *(50000000 + j for j in range(1, N, 240))]
    wants = {
        "by id": (kg_read.BY_ID, [ids], ("records_id", "Index Cond: ((org_id = ", "id = q.id", "coll = 1")),
        "classes": (kg_read.CLASSES, [[1, 2, 3]], ("scope_classes_pkey", "Index Cond: ((org_id = ", "id = q.id")),
        "literals": (kg_read.LITERALS, [sks], ("records_pkey", "Index Cond: ((org_id = ", "sk = q.sk", "records_parent", "links_pkey")),
    }
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE kg.node_keys; ANALYZE kg.scope_classes; ANALYZE kg.links")
        for name, (sql, args, must) in wants.items():
            async def go(tx, sql=sql, args=args) -> str:
                await tx.fetchval(kg_read.PROFILE)
                return "\n".join(r[0] for r in await tx.fetch("EXPLAIN (COSTS OFF) " + sql, c.org, *args))
            with _bound(c.org):
                plan = await pg_db.run(go, custom_plan=True)
            if "Seq Scan" in plan or "Bitmap Heap Scan" in plan or any(t not in plan for t in must):
                pytest.fail(f"{state} {name}: a scan the ids do not bound, or an index condition missing ({must})\n{plan}")
