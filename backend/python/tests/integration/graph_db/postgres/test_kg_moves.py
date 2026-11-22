"""A folder move's guards read from the typed graph's PARENT_CHILD links (``kg.hierarchy.pc_*``) equal the walk's.

``is_record_descendant_of``, ``get_folder_depth`` and ``get_folder_subtree_height`` decide whether a move may happen: answered wrongly, a folder moves into its own
subtree (a cycle) or past the depth limit. Random worlds of PARENT_CHILD and ATTACHMENT relations (several parents, cycles, chains) are stored, copied into kg and
promoted; every guard is asked about every record (every pair, for the descendant check) with the read switch off (the walk) and on: the same answers, and in
particular kg never answers "not a descendant" where the walk says it is one. The rest of the file breaks one thing at a time (a relation retyped, a change waiting
in the log, a parent kg does not hold, another connector, projection code of an older epoch, a seeded difference) and checks the plans.
"""
from __future__ import annotations

import json
import random
from typing import Any

import pytest

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.services.graph_db.postgres.kg import hierarchy as kg_hier

from .pg_support import new_org
from .test_kg_derivation import ctx, edge
from .test_kg_hierarchy import Tally, _scans
from .test_kg_read import _admin, _switch, flip

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
WHATS = ("pc_ancestor", "pc_depth", "pc_height")
FOLDER = FOLDER_MIME_TYPES[0]


@pytest.fixture
def tally(monkeypatch: pytest.MonkeyPatch) -> Tally:
    t = Tally()
    monkeypatch.setattr(kg_hier, "HIERARCHY", t)
    return t


async def _world(p, db, org: str, records: list[str], relations: list[tuple[str, str, str]], *, folders: set[str] = frozenset(), app: str = "app1") -> None:
    """``records`` of one connector (those in ``folders`` with a folder mime type) and ``relations`` (parent, child, relationship type) between them."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": a, "name": a, "type": "KB", "orgId": org} for a in ("app1", "app2")], "apps")
        await p.batch_upsert_nodes([{"_key": r, "recordName": r, "connectorId": app, "orgId": org, "recordType": "FILE", "origin": "UPLOAD",
                                     "mimeType": FOLDER if r in folders else "text/plain"} for r in records], "records")
        await p.batch_create_edges([edge(r, "records", app, "apps") for r in records], "belongsTo")
        if relations:
            await p.batch_create_edges([edge(a, "records", b, "records", relationshipType=t) for a, b, t in relations], "nodeRelations")
    await flip(db, org)


def _random(seed: int, n: int = 14) -> tuple[list[str], list[tuple[str, str, str]], set[str]]:
    rnd = random.Random(seed)
    records = [f"r{i:02d}" for i in range(n)]
    rels: dict[tuple[str, str], str] = {}
    for _ in range(rnd.randint(n, 3 * n)):
        a, b = rnd.sample(records, 2)
        if rnd.random() < 0.8 and a > b:          # mostly downward, so chains form; the rest close cycles
            a, b = b, a
        rels[(a, b)] = rnd.choice(["PARENT_CHILD", "PARENT_CHILD", "ATTACHMENT"])
    return records, [(a, b, t) for (a, b), t in rels.items()], {r for r in records if rnd.random() < 0.5}


async def _guards(p, org: str, records: list[str]) -> dict[str, Any]:
    with ctx(org):
        return {"anc": {(a, b): await p.is_record_descendant_of(a, b) for a in records for b in records},
                "depth": {r: await p.get_folder_depth(r) for r in records},
                "height": {r: await p.get_folder_subtree_height(r, FOLDER_MIME_TYPES) for r in records}}


async def _both(p, org: str, records: list[str]) -> tuple[dict, dict]:
    with _switch("0"):
        walk = await _guards(p, org, records)
    return walk, await _guards(p, org, records)


@pytest.mark.parametrize("seed", range(12))
async def test_random_worlds_answer_every_guard_as_the_walk_does(pg_provider, pg_db, tally, seed) -> None:
    org = new_org()
    records, rels, folders = _random(seed)
    await _world(pg_provider, pg_db, org, records, rels, folders=folders)
    walk, got = await _both(pg_provider, org, records)
    unsafe = [pair for pair, is_below in walk["anc"].items() if is_below and not got["anc"][pair]]
    assert unsafe == [], f"kg said 'not a descendant' where the walk says it is: {unsafe}"
    assert got == walk
    assert all(tally.c[(w, "kg")] for w in WHATS), dict(tally.c)          # kg answering nothing would prove nothing


# ---- one thing at a time -----------------------------------------------------------------------------------------------------------------------------------------------------------

CHAIN = ["a", "b", "c", "d", "x"]
CHAIN_RELS = [("a", "b", "PARENT_CHILD"), ("b", "c", "PARENT_CHILD"), ("c", "d", "PARENT_CHILD"), ("a", "x", "ATTACHMENT")]


async def test_an_attachment_is_not_followed_and_a_retyped_relation_is_read_again(pg_provider, pg_db, tally) -> None:
    """d is below a by PARENT_CHILD relations, x only by an attachment; once b > c is an attachment, d is no longer below a (kg's links follow the drain)."""
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS, folders={"a", "b", "c", "d"})
    walk, got = await _both(pg_provider, org, CHAIN)
    assert got == walk and got["anc"][("d", "a")] and not got["anc"][("x", "a")] and got["depth"]["d"] == 4 and got["height"]["a"] == 3
    await _admin("""UPDATE graph.edges SET doc = jsonb_set(doc, '{relationshipType}', '"ATTACHMENT"') WHERE org_id = $1 AND coll = 'nodeRelations' AND from_id = 'b'
        AND to_id = 'c'""", org)
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(pg_provider, org, CHAIN)
    assert got == walk and not got["anc"][("d", "a")] and got["depth"]["d"] == 2 and got["height"]["a"] == 1
    assert tally.c[("pc_ancestor", "kg")], dict(tally.c)


async def test_a_change_waiting_in_the_log_takes_the_walk_until_it_is_drained(pg_provider, pg_db, tally, monkeypatch) -> None:
    """A new PARENT_CHILD relation d > a (a cycle) is not in kg until the drain takes it: the connector is not served meanwhile, so the guard sees the cycle."""
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    monkeypatch.setenv("PG_KG_FLUSH", "async")
    with ctx(org):
        await pg_provider.batch_create_edges([edge("d", "records", "a", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        assert await pg_provider.is_record_descendant_of("a", "d")
    assert tally.c[("pc_ancestor", "not_served")] and not tally.c[("pc_ancestor", "kg")], dict(tally.c)


async def test_a_parent_kg_does_not_hold_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """b is removed and its relations stay: the walk still goes from c up through b's id to a; kg holds no b and hands every guard to the walk."""
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS, folders={"a", "b", "c", "d"})
    await _admin("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = 'b'", org)
    await flip(pg_db, org)
    tally.c.clear()
    rest = [r for r in CHAIN if r != "b"]
    walk, got = await _both(pg_provider, org, rest)
    assert got == walk and got["anc"][("d", "a")]
    assert tally.c[("pc_ancestor", "unheld")] and tally.c[("pc_depth", "unheld")] and tally.c[("pc_height", "unheld")], dict(tally.c)


async def test_a_parent_of_another_connector_takes_the_walk(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    with ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": "o", "recordName": "o", "connectorId": "app2", "orgId": org, "recordType": "FILE", "origin": "UPLOAD"}], "records")
        await pg_provider.batch_create_edges([edge("o", "records", "app2", "apps")], "belongsTo")
        await pg_provider.batch_create_edges([edge("o", "records", "a", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    await flip(pg_db, org)
    tally.c.clear()
    walk, got = await _both(pg_provider, org, [*CHAIN, "o"])
    assert got == walk and got["anc"][("d", "o")]
    assert tally.c[("pc_ancestor", "reach")] and tally.c[("pc_depth", "reach")], dict(tally.c)


async def test_a_connector_copied_by_older_projection_code_takes_the_walk(pg_provider, pg_db, tally) -> None:
    """Code of epoch 5 wrote no PARENT_CHILD links: a connector it verified is not served (the guard would see no parents)."""
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    await _admin("UPDATE kg.projection_state SET code_epoch = 5 WHERE org_id = $1 AND app_sk <> 0", org)
    walk, got = await _both(pg_provider, org, CHAIN)
    assert got == walk and got["anc"][("d", "a")]
    assert tally.c[("pc_ancestor", "not_served")] and not tally.c[("pc_ancestor", "kg")], dict(tally.c)


async def test_older_projection_code_cannot_write_into_kg_any_more(pg_provider, pg_db) -> None:
    """The writers that take work off the change log refuse epoch 5, so code that writes no PARENT_CHILD links cannot keep a connector current."""
    import asyncpg
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    with ctx(org), pytest.raises(asyncpg.exceptions.InvalidParameterValueError):
        await pg_db.run(lambda tx: tx.fetchval("SELECT kg.w_changes_ack($1)", 5))


async def test_a_sampled_guard_that_differs_from_the_walk_takes_the_connector_back_to_the_walk(pg_provider, pg_db, monkeypatch) -> None:
    """The safety net: the link c > d removed in kg alone; with every read sampled, kg says d is not below a, the walk says it is, the difference demotes the connector
    and the next read is the walk's."""
    from app.services.graph_db.postgres.kg import net
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    await _admin("""DELETE FROM kg.links WHERE org_id = $1 AND kind = 3 AND child_sk = (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'd')""", org)
    monkeypatch.setenv(net.SAMPLE_ENV, "1")
    with ctx(org):
        assert not await pg_provider.is_record_descendant_of("d", "a")              # kg answered, from the seeded links
        await net.settle()
        mode = await pg_db.run(lambda tx: tx.fetchval("""SELECT s.mode FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk
            WHERE s.org_id = $1 AND k.coll = 1 AND k.id = 'app1'""", org))
        assert mode == "legacy"
        assert await pg_provider.is_record_descendant_of("d", "a")                  # the walk's, from now on


async def test_with_the_switch_off_or_in_a_callers_transaction_kg_is_not_read(pg_provider, pg_db, tally) -> None:
    org = new_org()
    await _world(pg_provider, pg_db, org, CHAIN, CHAIN_RELS)
    with _switch("0"), ctx(org):
        assert await pg_provider.is_record_descendant_of("d", "a")
    with ctx(org):
        tid = await pg_provider.begin_transaction(read=[], write=["records"])
        try:
            assert await pg_provider.is_record_descendant_of("d", "a", transaction=tid)
            assert await pg_provider.get_folder_depth("d", transaction=tid) == 4
        finally:
            await pg_provider.rollback_transaction(tid)
    assert tally.c[("pc_ancestor", "off")] == 2 and tally.c[("pc_depth", "off")] == 1 and not tally.c[("pc_ancestor", "kg")], dict(tally.c)


async def test_every_guard_statement_is_answered_by_index_searches(pg_provider, pg_db) -> None:
    """EXPLAIN gate: a chain of 200 PARENT_CHILD records beside 12,000 others in the connector, never analysed and analysed: no guard statement scans a table."""
    org = new_org()
    chain = [f"c{i:03d}" for i in range(200)]
    rels = [(chain[i], chain[i + 1], "PARENT_CHILD") for i in range(len(chain) - 1)]
    await _world(pg_provider, pg_db, org, chain, rels, folders=set(chain[::7]))
    await _admin("""INSERT INTO kg.records (org_id, sk, coll, id, kind, app_sk, path, leaf)
        SELECT $1, 72000000 + g, 3, 'plan-' || g, 3, a.app_sk, a.path, true FROM generate_series(1, 12000) g,
          (SELECT app_sk, path FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'c000') a""", org)
    with ctx(org):
        row = await pg_db.run(lambda tx: tx.fetchrow("""SELECT (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'c150') AS deep,
            (SELECT sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'c000') AS top, (SELECT app_sk FROM kg.records WHERE org_id = $1 AND coll = 3 AND id = 'c000') AS app""",
            org))
    cases = {"ancestor": (kg_hier.PC_ANCESTOR, [row["deep"], row["app"], "c000"]), "depth": (kg_hier.PC_DEPTH, [row["deep"], row["app"], 100]),
             "height": (kg_hier.PC_HEIGHT, [row["top"], row["app"], 100, list(FOLDER_MIME_TYPES)])}
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

