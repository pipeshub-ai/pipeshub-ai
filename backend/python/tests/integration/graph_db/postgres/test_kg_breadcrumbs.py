"""The knowledge hub's trail (breadcrumbs) read from the typed graph (``kg.hierarchy.trail``) equals the walk's.

Every world of the permission corpus and of the generated worlds is stored, copied into kg and promoted; then the trail of every node is read with the read switch off (the walk)
and on, and so are the breadcrumbs a viewer gets. The rest of the file breaks one thing at a time (a second parent, a second group, a node kg does not hold, a missing path, another
connector, a connector not served) and holds the answer to the walk's, and checks the plans of the statements.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.services.graph_db.postgres.kg import hierarchy as kg_hier
from tests.permissions import gen
from tests.permissions.scenario import load_all

from .pg_support import new_org
from .test_conformance import CHUNK
from .test_kg_derivation import ctx, edge
from .test_kg_hierarchy import HierLayer, Tally, _scans, _tree, _types_all
from .test_kg_read import _admin, _bound, _hex, _switch, flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
WORLDS = 8


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> Tally:
    t = Tally()
    monkeypatch.setattr(kg_hier, "HIERARCHY", t)
    return t


@pytest.fixture
async def layer(pg_provider, pg_db) -> AsyncIterator[HierLayer]:
    lay = HierLayer(pg_provider, pg_db)
    yield lay
    await lay.release(list(lay._prefix))


async def _trail(p, org: str, nid: str) -> list[dict[str, Any]]:
    with _bound(org):
        return await p._kh_trail(org, nid, None)


async def _both(org: str, call) -> tuple[Any, Any]:
    with _switch("0"):
        walk = await call()
    with _switch(None):
        got = await call()
    return walk, got


async def _compare(p, layer, world, tag: str) -> list[str]:
    ns = await layer._ns(world)
    org = _hex(ns.org)
    viewer = next((ns.id(u) for u in world.users()[:1]), None)
    diffs: list[str] = []
    for n in world.nodes:
        nid = ns.id(n.id)
        walk, got = await _both(org, lambda nid=nid: _trail(p, org, nid))
        if walk != got:
            diffs.append(f"{tag} trail {n.id}: walk {walk} kg {got}")
        if viewer is not None:
            async def crumbs(nid=nid) -> list[dict[str, Any]]:
                with _bound(org):
                    return await p.get_knowledge_hub_breadcrumbs(nid, viewer, org)
            walk, got = await _both(org, crumbs)
            if walk != got:
                diffs.append(f"{tag} breadcrumbs {n.id}: walk {walk} kg {got}")
    return diffs


async def _gate(layer, p, worlds: list, tag: str) -> list[str]:
    await layer.preload(worlds)
    diffs: list[str] = []
    for i, w in enumerate(worlds):
        diffs += await _compare(p, layer, w, f"{tag}{i}")
    await layer.release(worlds)
    return diffs


async def test_the_corpus_trail_from_kg_equals_the_walk(layer, pg_provider, tally) -> None:
    worlds = [s.final_world() for s in load_all()]
    diffs: list[str] = []
    for start in range(0, len(worlds), CHUNK):
        diffs += await _gate(layer, pg_provider, worlds[start:start + CHUNK], f"corpus{start}x")
    assert diffs == [], "\n".join(diffs[:20])
    assert tally.c[("breadcrumbs", "kg")], dict(tally.c)      # kg answering nothing would prove nothing


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_trail_from_kg_equals_the_walk(layer, pg_provider, tally, kind, monkeypatch) -> None:
    from tests.permissions.gen import adversarial
    monkeypatch.setattr(adversarial, "RULES", [r for r in adversarial.RULES if r != "INHERITED"])      # as test_kg_hierarchy: a rule no connector writes, never served
    diffs = await _gate(layer, pg_provider, gen.generate(kind, gen.seeds()[0], WORLDS), f"{kind}x")
    assert diffs == [], "\n".join(diffs[:20])
    assert tally.c[("breadcrumbs", "kg")], dict(tally.c)


# ---- one thing at a time -----------------------------------------------------------------------------------------------------------------------------------------------------------

def _names(trail: list[dict[str, Any]]) -> list[str]:
    return [s["name"] for s in trail]


async def test_a_record_s_trail_goes_up_through_its_parents_groups_and_app(pg_provider, pg_db, tally) -> None:
    """``_tree``: app1 > g1 > g2 (groups), f in g1, a under f in g2, b under a; c has two record parents (f and a), d none (only g2)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    for nid, want in (("b", ["b", "a", "f", "g1", "Drive"]), ("d", ["d", "g2", "g1", "Drive"]), ("g2", ["g2", "g1", "Drive"]), ("app1", ["Drive"])):
        walk, got = await _both(org, lambda nid=nid: _trail(pg_provider, org, nid))
        assert got == walk and _names(got) == want, (nid, walk, got)
    assert tally.c[("breadcrumbs", "kg")] == 4, dict(tally.c)
    assert next(s for s in got if s["name"] == "Drive")["nodeType"] == "app"


async def test_a_second_record_parent_or_group_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """b gains a second record parent, 0x, written after a (so kg's primary parent of b stays a): the walk takes the lowest id (0x), kg would take a, so the walk answers.
    The same for c (two parents) and for a record in two groups."""
    org = new_org()
    await _tree(pg_provider, pg_db, org, extra=[{"_key": "m", "recordName": "m", "connectorId": "app1", "orgId": org, "recordType": "FILE"}])
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": "0x", "recordName": "0x", "connectorId": "app1", "orgId": org, "recordType": "FILE"}], "records")
        await pg_provider.batch_create_edges([edge("0x", "records", "b", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await pg_provider.batch_create_edges([edge("m", "records", "g1", "recordGroups"), edge("m", "records", "g2", "recordGroups"),
                                              edge("0x", "records", "g2", "recordGroups")], "belongsTo")
    await _types_all(org)
    await flip(pg_db, org)
    tally.c.clear()
    got = {}
    for nid in ("b", "c", "m"):
        walk, got[nid] = await _both(org, lambda nid=nid: _trail(pg_provider, org, nid))
        assert got[nid] == walk, (nid, walk, got[nid])
    assert _names(got["b"]) == ["b", "0x", "g2", "g1", "Drive"] and _names(got["m"]) == ["m", "g1", "Drive"]
    assert tally.c[("breadcrumbs", "ambiguous")] == 3 and not tally.c[("breadcrumbs", "kg")], dict(tally.c)


async def test_a_parent_kg_does_not_hold_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """a is removed and its edges stay: the walk goes from b up to a's id, finds no node there and ends; kg holds no a, sees the relation into b from a node it does not hold and
    hands the read to the walk."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = 'a'", org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(org, lambda: _trail(pg_provider, org, "b"))
    assert got == walk and _names(got) == ["b"]
    assert tally.c[("breadcrumbs", "unheld")] and not tally.c[("breadcrumbs", "kg")], dict(tally.c)


async def test_a_row_with_no_path_of_another_connector_or_not_served_takes_the_walk(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    walk, _ = await _both(org, lambda: _trail(pg_provider, org, "b"))
    await _admin("UPDATE kg.records SET path = NULL WHERE org_id = $1 AND id = 'a' AND coll = 3", org)
    tally.c.clear()
    assert (await _both(org, lambda: _trail(pg_provider, org, "b")))[1] == walk
    assert tally.c[("breadcrumbs", "reach")] and not tally.c[("breadcrumbs", "kg")], dict(tally.c)

    await _admin("""UPDATE kg.records SET path = (SELECT p.path || int8send(p.sk) FROM kg.records p WHERE p.org_id = $1 AND p.id = 'f' AND p.coll = 3),
        app_sk = (SELECT sk FROM kg.node_keys WHERE org_id = $1 AND coll = 1 AND id = 'app2') WHERE org_id = $1 AND id = 'a' AND coll = 3""", org)
    tally.c.clear()
    assert (await _both(org, lambda: _trail(pg_provider, org, "b")))[1] == walk
    assert tally.c[("breadcrumbs", "reach")] and not tally.c[("breadcrumbs", "kg")], dict(tally.c)

    await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", org)
    tally.c.clear()
    assert (await _both(org, lambda: _trail(pg_provider, org, "b")))[1] == walk
    assert tally.c[("breadcrumbs", "not_served")] and not tally.c[("breadcrumbs", "kg")], dict(tally.c)


async def test_an_id_shared_by_a_record_and_a_group_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """A record whose id is g1's: the walk steps from f to the id g1 and reads the record of that id (records first); kg steps to the group, sees the record and hands it over."""
    org = new_org()
    await _tree(pg_provider, pg_db, org, extra=[{"_key": "g1", "recordName": "a record named like g1", "connectorId": "app1", "orgId": org, "recordType": "FILE"}])
    await _types_all(org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(org, lambda: _trail(pg_provider, org, "f"))
    assert got == walk and "a record named like g1" in _names(got)
    assert not tally.c[("breadcrumbs", "kg")], dict(tally.c)


async def test_with_the_switch_off_or_in_a_callers_transaction_kg_is_not_read(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    with _switch("0"):
        await _trail(pg_provider, org, "b")
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            await pg_provider._kh_trail(org, "b", tid)
        finally:
            await pg_provider.rollback_transaction(tid)
    assert tally.c[("breadcrumbs", "off")] == 2 and not tally.c[("breadcrumbs", "kg")], dict(tally.c)


async def test_a_sampled_trail_that_differs_from_the_walk_takes_the_connector_back_to_the_walk(pg_provider, pg_db, monkeypatch) -> None:
    """The safety net: b moved under f in kg alone; with every read sampled, kg's trail is read again by both in one snapshot, the difference demotes the connector, and the next
    read is the walk's."""
    from app.services.graph_db.postgres.kg import net
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("UPDATE kg.records SET parent_sk = (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'f') WHERE org_id = $1 AND coll = 3 AND id = 'b'", org)
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    assert _names(await _trail(pg_provider, org, "b")) == ["b", "f", "g1", "Drive"]          # kg answered, from the seeded structure
    with ctx(org):
        await net.settle()
        mode = await pg_db.run(lambda tx: tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = 'app1'""", org))
    assert mode == "legacy"
    assert _names(await _trail(pg_provider, org, "b")) == ["b", "a", "f", "g1", "Drive"]     # the walk's, from now on


async def test_every_trail_statement_is_answered_by_index_searches(pg_provider, pg_db) -> None:
    """EXPLAIN gate: a group of 12,000 members and 12,000 other records, never analysed and then analysed: no trail statement scans a kg or graph table whole (the trail reads
    only the edges up from a node, never a group's members, and each step's document by its own key, never the organisation's nodes)."""
    org = new_org()
    await _tree(pg_provider, pg_db, org)
    await _admin("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id) SELECT $1, 'belongsTo', 'records', 'm-' || g, 'recordGroups', 'g2'
        FROM generate_series(1, 12000) g""", org)
    with ctx(org):
        row = await pg_db.run(lambda tx: tx.fetchrow("SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 2 AND id = 'g2'", org))
    await _admin("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'm-' || g, jsonb_build_object('id', 'm-' || g, 'recordName', 'm' || g)
        FROM generate_series(1, 12000) g""", org)
    cases = {"node": (kg_hier.TRAIL_NODE, [row["sk"]]), "app": (kg_hier.TRAIL_APP, ["recordGroups", "g1"]),
             "documents": (kg_hier.HUB_NODES, [["b", "a", "f", "g2", "g1", "app1", "missing"]])}
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE kg.records; ANALYZE kg.links; ANALYZE kg.node_keys; ANALYZE graph.nodes; ANALYZE graph.edges")
        for name, (sql, args) in cases.items():
            async def go(tx, sql=sql, args=args) -> tuple[list[str], int]:
                await tx.fetchval(kg_hier.PROFILE)
                ran = await tx.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, org, *args)
                plan = (json.loads(ran) if isinstance(ran, str) else ran)[0]["Plan"]
                return _scans(plan), max(_rows(plan))
            with ctx(org):
                bad, most = await pg_db.run(go, custom_plan=True)
            if bad:
                async def text(tx, sql=sql, args=args) -> str:
                    await tx.fetchval(kg_hier.PROFILE)
                    return "\n".join(r[0] for r in await tx.fetch("EXPLAIN " + sql, org, *args))
                with ctx(org):
                    print(await pg_db.run(text, custom_plan=True))
            assert not bad, f"{state} {name}: {bad}"
            assert most < 100, f"{state} {name}: a node read {most} rows"          # not the group's 12,000 members


def _rows(node: dict[str, Any]) -> list[int]:
    return [node.get("Actual Rows", 0) * max(1, node.get("Actual Loops", 1)), *(r for c in node.get("Plans", []) for r in _rows(c))]
