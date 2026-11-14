"""A record's descendants, a group's records and the levels under a parent read from the typed graph (``kg.hierarchy``) equal the walk's.

Every world of the permission corpus and of the generated worlds is stored, given file types, copied into kg and promoted; then ``get_records_by_parent_record``,
``get_records_by_record_group`` and ``get_node_depths_batch`` are asked about every node with the read switch off (the walk) and on: the same answers, in the same order. The rest of
the file breaks one thing at a time (a second parent, an organisation, a missing path, another connector, the connector's state) and holds the answer to the walk's, and checks the
plans of the statements.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.services.graph_db.postgres.kg import drainer
from app.services.graph_db.postgres.kg import hierarchy as kg_hier
from tests.permissions import gen
from tests.permissions.graph import Namespace
from tests.permissions.scenario import load_all

from .pg_support import new_org
from .test_conformance import CHUNK, PgLayer
from .test_kg_derivation import ctx, edge
from .test_kg_read import KgLayer, _admin, _bound, _hex, _switch, flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

WORLDS = 8           # generated worlds per generator
TYPES = """INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT org_id, 'files', 'f-' || id, jsonb_build_object('id', 'f-' || id, 'isFile', abs(hashtext(id)) % 5 <> 0)
    FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND abs(hashtext(id)) % 7 <> 0;
  INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT org_id, 'isOfType', 'records', substr(id, 3), 'files', id
    FROM graph.nodes WHERE org_id = $1 AND coll = 'files'"""


class Tally:
    def __init__(self) -> None:
        self.c: Counter = Counter()

    def inc(self, *labels: str, value: int = 1) -> None:
        self.c[labels] += value


@pytest.fixture(autouse=True)
def _ids_only(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reads are compared by the ids they select (what kg changes); the typed records built from them are the walk's code either way."""
    async def page(ids: list[str], _transaction: str | None) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=i) for i in ids]
    monkeypatch.setattr(pg_provider, "_records_page", page)


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> Tally:
    t = Tally()
    monkeypatch.setattr(kg_hier, "HIERARCHY", t)
    return t


async def _types(org: str) -> None:
    """A file type for most records (some not a file, some with no type: the group listing leaves both out), before the copy."""
    for sql in TYPES.split(";"):
        await _admin(sql, org)


class HierLayer(KgLayer):
    async def preload(self, worlds) -> None:
        new = [w for w in dict.fromkeys(worlds) if w not in self._prefix]
        await PgLayer.preload(self, new)
        for w in new:
            org = _hex(Namespace(self._prefix[w]).org)
            await _types(org)
            done, total = await flip(self._db, org)
            self.promoted.update(promoted=done, connectors=total)


@pytest.fixture
async def layer(pg_provider, pg_db) -> AsyncIterator[HierLayer]:
    lay = HierLayer(pg_provider, pg_db)
    yield lay
    await lay.release(list(lay._prefix))


async def _both(org: str, call) -> tuple[Any, Any]:
    with _switch("0"), _bound(org):
        walk = await call()
    with _switch(None), _bound(org):
        got = await call()
    return walk, got


def _ids(records: list) -> list[str]:
    return [r.id for r in records]


async def _compare(prov, layer: HierLayer, world, tag: str) -> list[str]:
    """Every read of every node, walk against kg; the differences."""
    ns = await layer._ns(world)
    org = _hex(ns.org)
    diffs: list[str] = []
    records = [n for n in world.nodes if n.kind == "record"]
    viewers = [None, *(ns.id(u) for u in world.users()[:1])]

    async def same(what: str, call) -> None:
        walk, got = await _both(org, call)
        if walk != got:
            diffs.append(f"{tag} {what}: walk {walk} kg {got}")
    for n in [n for n in world.nodes if n.kind != "app" and n.connector]:
        nid, cid = ns.id(n.id), ns.id(n.connector)
        for depth in (-1, 1):
            for user in viewers if depth == -1 else [None]:
                if n.kind == "record":
                    await same(f"by_parent {n.id} {depth} {user}", lambda nid=nid, cid=cid, d=depth, u=user: _p(prov.get_records_by_parent_record(nid, cid, org, d, u)))
                else:
                    await same(f"by_group {n.id} {depth} {user}", lambda nid=nid, cid=cid, d=depth, u=user: _p(prov.get_records_by_record_group(nid, cid, org, d, u)))
        await same(f"page {n.id}", lambda nid=nid, cid=cid: _p(prov.get_records_by_parent_record(nid, cid, org, -1, None, limit=2, offset=1)))
    for n in world.nodes:
        ptype = {"app": "app", "group": "recordGroup", "record": None}[n.kind]
        for depth in (3,):
            ids = [ns.id(r.id) for r in records if r.connector == (n.id if n.kind == "app" else n.connector)]      # the records of the parent's connector, as a listing asks
            await same(f"depths {n.id} {depth}", lambda nid=ns.id(n.id), t=ptype, d=depth, ids=ids: prov.get_node_depths_batch(nid, ids, d, t))
    return diffs


async def _p(coro) -> list[str]:
    return _ids(await coro)


def _served(tally: Tally, what: str) -> int:
    return tally.c[(what, "kg")]


async def _gate(layer: HierLayer, prov, worlds: list, tag: str) -> list[str]:
    await layer.preload(worlds)
    diffs: list[str] = []
    for i, w in enumerate(worlds):
        diffs += await _compare(prov, layer, w, f"{tag}{i}")
    await layer.release(worlds)
    return diffs


async def test_the_corpus_read_from_kg_equals_the_walk(layer, pg_provider, tally) -> None:
    worlds = [s.final_world() for s in load_all()]
    diffs: list[str] = []
    for start in range(0, len(worlds), CHUNK):
        diffs += await _gate(layer, pg_provider, worlds[start:start + CHUNK], f"corpus{start}x")
    assert diffs == [], "\n".join(diffs[:20])
    assert all(_served(tally, w) for w in ("by_parent", "by_group", "depths")), dict(tally.c)      # one side answering nothing would prove nothing


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_read_from_kg_equal_the_walk(layer, pg_provider, tally, kind, monkeypatch) -> None:
    # The projection refuses the INHERITED access rule only the adversarial generator writes (no connector does): such a world is never served by kg and would compare the walk
    # with itself. It is generated without it, as the knowledge-hub conformance suite does.
    from tests.permissions.gen import adversarial
    monkeypatch.setattr(adversarial, "RULES", [r for r in adversarial.RULES if r != "INHERITED"])
    diffs = await _gate(layer, pg_provider, gen.generate(kind, gen.seeds()[0], WORLDS), f"{kind}x")
    assert diffs == [], "\n".join(diffs[:20])
    assert _served(tally, "by_parent") and _served(tally, "depths"), dict(tally.c)


# ---- one thing at a time -----------------------------------------------------------------------------------------------------------------------------------------------------------

async def _tree(p, db, org: str, *, extra: list[dict] | None = None, links: list[dict] | None = None) -> None:
    """app1 > g1 (group) holding f, g2 below g1 holding a, b, c and d; f > a > b (records), f > c, and c is a's child too (a second parent); every record a file."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE", "orgId": org}, {"_key": "app2", "name": "Other", "type": "DRIVE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": g, "groupName": g, "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"} for g in ("g1", "g2")],
                                   "recordGroups")
        await p.batch_upsert_nodes([{"_key": r, "recordName": r, "connectorId": "app1", "orgId": org, "recordType": "FILE", "origin": "CONNECTOR"} for r in "fabcd"]
                                   + (extra or []), "records")
        await p.batch_create_edges([edge("g1", "recordGroups", "app1", "apps"), edge("g2", "recordGroups", "g1", "recordGroups"),
                                    edge("f", "records", "g1", "recordGroups"),
                                    *(edge(r, "records", "g2", "recordGroups") for r in "abcd")], "belongsTo")
        await p.batch_create_edges([edge(a, "records", b, "records", relationshipType="PARENT_CHILD") for a, b in (("f", "a"), ("a", "b"), ("f", "c"), ("a", "c"))]
                                   + (links or []), "nodeRelations")
    await _types_all(org)
    await flip(db, org)


async def _types_all(org: str) -> None:
    await _admin("UPDATE graph.nodes SET doc = jsonb_set(doc, '{isFile}', 'true') WHERE org_id = $1 AND coll = 'files'", org)
    await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT org_id, 'files', 'f-' || id, jsonb_build_object('id', 'f-' || id, 'isFile', true) FROM graph.nodes n WHERE org_id = $1 AND coll = 'records'
        ON CONFLICT DO NOTHING""", org)
    await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT org_id, 'isOfType', 'records', id, 'files', 'f-' || id FROM graph.nodes
        WHERE org_id = $1 AND coll = 'records' ON CONFLICT DO NOTHING""", org)


async def _reads(p, org: str) -> dict[str, Any]:
    with ctx(org):
        return {"below_f": _ids(await p.get_records_by_parent_record("f", "app1", org, -1)), "below_a1": _ids(await p.get_records_by_parent_record("a", "app1", org, 1)),
                "group": _ids(await p.get_records_by_record_group("g1", "app1", org, -1)), "group0": _ids(await p.get_records_by_record_group("g1", "app1", org, 0)),
                "depth_app": await p.get_node_depths_batch("app1", list("fabcd"), 5, "app"), "depth_g1": await p.get_node_depths_batch("g1", list("fabcd"), 5, "recordGroup"),
                "depth_f": await p.get_node_depths_batch("f", list("fabcd"), 5, None)}


async def _walk_and_kg(p, org: str) -> tuple[dict, dict]:
    with _switch("0"):
        walk = await _reads(p, org)
    return walk, await _reads(p, org)


WHATS = ("by_parent", "by_group", "depths")


async def test_a_second_parent_is_followed_and_served(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    walk, got = await _walk_and_kg(pg_provider, org)
    assert got == walk
    assert got["below_a1"] == ["a", "b", "c"] and got["below_f"] == ["a", "b", "c", "f"] and got["group"] == ["a", "b", "c", "d", "f"]
    assert got["depth_f"] == {"a": 1, "b": 2, "c": 1} and got["depth_g1"] == {"f": 1, "a": 2, "b": 3, "c": 2}
    assert all(_served(tally, w) for w in WHATS), dict(tally.c)


async def test_a_record_of_no_organisation_is_listed_and_one_of_another_is_not_as_by_the_walk(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org, extra=[{"_key": "x", "recordName": "x", "connectorId": "app1", "orgId": new_org(), "recordType": "FILE"},
                                                 {"_key": "y", "recordName": "y", "connectorId": "app1", "recordType": "FILE"}],
                links=[edge("f", "records", k, "records", relationshipType="ATTACHMENT") for k in "xy"])
    with ctx(org):
        await pg_provider.batch_create_edges([edge(k, "records", "g1", "recordGroups") for k in "xy"], "belongsTo")
    await flip(pg_db, org)
    walk, got = await _walk_and_kg(pg_provider, org)
    assert got == walk and "y" in got["below_f"] and "x" not in got["below_f"] and "y" in got["group"]
    assert _served(tally, "by_parent") and _served(tally, "by_group"), dict(tally.c)


async def test_a_row_with_no_path_or_of_another_connector_or_a_connector_not_served_takes_the_walk(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    walk, _ = await _walk_and_kg(pg_provider, org)
    await _admin("UPDATE kg.records SET path = NULL WHERE org_id = $1 AND id = 'b'", org)
    tally.c.clear()
    assert (await _walk_and_kg(pg_provider, org))[1] == walk
    assert tally.c[("by_parent", "reach")] and tally.c[("depths", "reach")] and not _served(tally, "by_parent"), dict(tally.c)

    await _admin("""UPDATE kg.records SET path = (SELECT p.path || int8send(p.sk) FROM kg.records p WHERE p.org_id = $1 AND p.id = 'a' AND p.coll = 3)
        WHERE org_id = $1 AND id = 'b' AND coll = 3""", org)
    await _admin("UPDATE kg.records SET app_sk = (SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app2') WHERE org_id = $1 AND id = 'b' AND coll = 3", org)
    tally.c.clear()
    assert (await _walk_and_kg(pg_provider, org))[1] == walk
    assert tally.c[("by_parent", "reach")] and not _served(tally, "by_parent"), dict(tally.c)

    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    tally.c.clear()
    assert (await _walk_and_kg(pg_provider, org))[1] == walk
    assert tally.c[("by_parent", "not_served")] and tally.c[("depths", "not_served")] and not any(_served(tally, w) for w in WHATS), dict(tally.c)


async def test_with_the_switch_off_or_in_a_callers_transaction_kg_is_not_read(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with _switch("0"), ctx(org):
        await pg_provider.get_records_by_parent_record("f", "app1", org, -1)
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            await pg_provider.get_records_by_parent_record("f", "app1", org, -1, transaction=tid)
        finally:
            await pg_provider.rollback_transaction(tid)
    assert tally.c[("by_parent", "off")] == 2 and not _served(tally, "by_parent"), dict(tally.c)


async def test_a_change_waiting_in_the_log_takes_the_walk_until_it_is_drained(pg_provider, pg_db, tally, monkeypatch) -> None:
    """A child added after the copy is not in kg yet: the connector is not served until the drain has taken it, so the listing never misses it."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    monkeypatch.setenv("PG_KG_FLUSH", "async")       # the change stays in the log for the drain
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": "e", "recordName": "e", "connectorId": "app1", "orgId": org, "recordType": "FILE"}], "records")
        await pg_provider.batch_create_edges([edge("b", "records", "e", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        got = _ids(await pg_provider.get_records_by_parent_record("f", "app1", org, -1))
    assert "e" in got and tally.c[("by_parent", "not_served")], dict(tally.c)


async def test_a_record_kg_cannot_hold_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """A record whose id is longer than kg takes is not in kg: the walk lists it below a, in g2 and as the way down to z; kg sees a relation to a record it does not hold and hands
    each read to the walk, so the answers stay the walk's (also once the change log no longer holds the rows that name it)."""
    org, long = new_org(), "L" * 300
    await _tree(pg_provider, pg_db, org, extra=[{"_key": k, "recordName": k[:9], "connectorId": "app1", "orgId": org, "recordType": "FILE"} for k in (long, "z")],
                links=[edge("a", "records", long, "records", relationshipType="PARENT_CHILD"), edge(long, "records", "z", "records", relationshipType="PARENT_CHILD")])
    with ctx(org):
        await pg_provider.batch_create_edges([edge(long, "records", "g2", "recordGroups")], "belongsTo")
    await _types_all(org)
    await drainer.purge_quarantined(pg_db, org)      # the change log rows that name the long id cannot be projected; an operator drops them, and the connector is served again
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _walk_and_kg(pg_provider, org)
    assert got == walk and long in got["below_f"] and "z" in got["below_f"] and long in got["group"]
    assert await _both(org, lambda: pg_provider.get_node_depths_batch("f", ["z"], 5, None)) == ({"z": 3}, {"z": 3})
    assert tally.c[("by_parent", "unheld")] and tally.c[("by_group_nest", "unheld")] and tally.c[("depths", "unheld")], dict(tally.c)


async def test_an_edge_left_after_its_record_was_removed_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """a is removed and its edges stay: the walk still goes from f through a to b; kg holds no a (and b has no parent there), sees f's relation to a record it does not hold and
    hands the read to the walk."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = 'a'", org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _walk_and_kg(pg_provider, org)
    assert got == walk and "b" in got["below_f"] and got["depth_f"].get("b") == 2
    assert not _served(tally, "by_parent") and not _served(tally, "depths"), dict(tally.c)


async def test_a_group_kg_cannot_hold_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """A record group whose id is longer than kg takes is not in kg, so its member m has no group there: the walk gives m level 1 under the App (a member of one of its live
    groups); kg sees m's belongs-to edge to a group it does not hold and hands the read to the walk."""
    org, long = new_org(), "G" * 300
    await _tree(pg_provider, pg_db, org, extra=[{"_key": "m", "recordName": "m", "connectorId": "app1", "orgId": org, "recordType": "FILE"}])
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": long, "groupName": "long", "connectorId": "app1", "orgId": org, "accessRule": "OPEN", "permissionModel": "RECORD_LEVEL"}],
                                             "recordGroups")
        await pg_provider.batch_create_edges([edge(long, "recordGroups", "app1", "apps"), edge("m", "records", long, "recordGroups")], "belongsTo")
    await drainer.purge_quarantined(pg_db, org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(org, lambda: pg_provider.get_node_depths_batch("app1", ["m", "f"], 3, "app"))
    assert got == walk == {"m": 1, "f": 1}
    assert tally.c[("depths", "unheld")] and not _served(tally, "depths"), dict(tally.c)


async def test_an_asked_record_that_is_gone_while_its_edge_stays_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """b is removed and the edge from a to it stays: the walk still gives b level 1 under a (it follows the edge, whatever is at its end); kg holds no b and hands the read to
    the walk."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = 'b'", org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(org, lambda: pg_provider.get_node_depths_batch("a", ["b"], 3, None))
    assert got == walk == {"b": 1}
    assert tally.c[("depths", "not_projected")] and not _served(tally, "depths"), dict(tally.c)

@pytest.mark.parametrize("what", ["by_parent", "depths", "by_group"])
async def test_a_sampled_read_that_differs_from_the_walk_takes_the_connector_back_to_the_walk(pg_provider, pg_db, monkeypatch, what) -> None:
    """The safety net: with every read sampled, a read kg answers is read again by kg and by the walk in one snapshot; a seeded difference in kg's structure (b moved under f in kg
    alone, and dropped from g2 there) demotes the connector, and the next read is the walk's."""
    from app.services.graph_db.postgres.kg import net
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("""UPDATE kg.records SET parent_sk = (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'f'), group_sk = NULL
        WHERE org_id = $1 AND coll = 3 AND id = 'b'""", org)
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    with ctx(org):
        if what == "by_parent":
            got = _ids(await pg_provider.get_records_by_parent_record("a", "app1", org, 1))
            assert got == ["a", "c"]                                       # kg answered, from the seeded structure
        elif what == "depths":
            assert "b" not in await pg_provider.get_node_depths_batch("a", ["b", "c"], 3, None)
        else:
            assert "b" not in _ids(await pg_provider.get_records_by_record_group("g1", "app1", org, -1))
        await net.settle()
    async def state(tx) -> str:
        return await tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = 'app1'""", org)
    with ctx(org):
        mode = await pg_db.run(state)
        assert mode == "legacy"
        assert _ids(await pg_provider.get_records_by_parent_record("a", "app1", org, 1)) == ["a", "b", "c"]      # the walk's, from now on


# ---- plans -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

N = 12_000


async def test_every_statement_is_answered_by_index_searches(pg_provider, pg_db) -> None:
    """EXPLAIN gate: on a connector of 12,000 records (a chain under f, a wide group), never analysed and then analysed, no statement scans a kg or graph table whole."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("""INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, parent_sk, group_sk, path, leaf)
        SELECT $1, 70000000 + g, 3, 'plan-' || g, 3, a.app_sk, a.sk, g1.sk, a.path, true FROM generate_series(1, $2) g,
          (SELECT sk, app_sk, path FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'f') a, (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 2 AND id = 'g2') g1""", org, N)
    with ctx(org):
        row = await pg_db.run(lambda tx: tx.fetchrow("""SELECT (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'a') AS a,
            (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 2 AND id = 'g1') AS g1, (SELECT app_sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'a') AS app""", org))
    cases = {
        "below": (kg_hier.BELOW, [row["a"], 100, row["app"], "app1", org, None, None, None, 0, None]),
        "nest": (kg_hier.NEST, ["g1", org, 100]),
        "group records": (kg_hier.GROUP_RECORDS, [[row["g1"]], "app1", org, None, None, None, row["app"], 0, 10]),
        "up": (kg_hier.UP, [["b", "c", "plan-7"], 9]),
        "group members": (kg_hier.GROUP_MEMBERS, [[row["a"]], row["g1"]]),
        "app members": (kg_hier.APP_MEMBERS, [[row["a"]], row["app"], "app1"]),
    }
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE kg.links; ANALYZE kg.node_keys; ANALYZE graph.nodes; ANALYZE graph.edges")
        for name, (sql, args) in cases.items():
            async def go(tx, sql=sql, args=args) -> list[str]:
                await tx.fetchval(kg_hier.PROFILE)
                ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, org, *args)
                return _scans((json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"])
            with ctx(org):
                bad = await pg_db.run(go, custom_plan=True)
            assert not bad, f"{state} {name}: {bad}"


def _scans(node: dict[str, Any]) -> list[str]:
    """What in a plan reads a table by more than its key: a sequential or bitmap scan, or an index scan whose condition bounds nothing but the organisation (and the collection)."""
    rel, kind = node.get("Relation Name"), node["Node Type"]
    out = []
    if rel and kind in ("Seq Scan", "Bitmap Heap Scan"):
        out.append(f"{kind} on {rel}")
    elif rel and kind in ("Index Scan", "Index Only Scan"):
        cond = re.sub(r"(org_id|coll) = [^)]*", "", node.get("Index Cond", ""))
        if not re.search(r"\b(sk|id|parent_sk|group_sk|child_sk|from_id|to_id)\b|ROW\(", cond):
            out.append(f"{node.get('Index Name')} on {rel} bounded by {node.get('Index Cond')}")
    for child in node.get("Plans", []):
        out += _scans(child)
    return out
