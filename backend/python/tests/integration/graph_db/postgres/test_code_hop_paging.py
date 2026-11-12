"""A page of a code-graph hop read in cursor order: the same rows and the same pages as the statement it replaced, for every direction, type set, limit and shape of key (mixed case and
punctuation, parallel edges, edges of both collections, types named by the document or by the key, self links), and a page of a node of any degree reads about a page of its edges."""
from __future__ import annotations

import logging
import os
import re
from collections import Counter

import asyncpg
import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres import codegraph as cg
from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# The statement before this change: the page is the rows after the cursor in the order of a text built from the row, and every matching edge of the anchors is read and sorted.
LEGACY = """SELECT e.anchor, e.coll, e.key, e.dir, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) AS rel, e.doc,
    concat_ws(chr(1), e.anchor, e.dir, e.coll, e.key, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')), e.edge_coll, e.ekey) COLLATE "C" AS page_key FROM (
    SELECT e.from_id AS anchor, e.to_coll AS coll, e.to_id AS key, 'outbound' AS dir, e.ekey, e.doc, e.coll AS edge_coll FROM graph.edges e
      WHERE $7::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = ANY($4::text[])
    UNION ALL
    SELECT e.to_id, e.from_coll, e.from_id, 'inbound', e.ekey, e.doc, e.coll FROM graph.edges e
      WHERE $8::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = ANY($4::text[])
        AND NOT ($9::bool AND e.from_coll = e.to_coll AND e.from_id = e.to_id)) e
    WHERE coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) = ANY($5::text[])
      AND concat_ws(chr(1), e.anchor, e.dir, e.coll, e.key, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')), e.edge_coll, e.ekey) COLLATE "C" > $10::text
    ORDER BY page_key LIMIT $6"""
IDS = ["B1", "a2", "_x", "a-1", "A_1", "a1", "b10", "b1", "b2", "10", "9", "Zed", "zed", "é1", "a.b", "a b"]
TYPES = ["CALLS", "IMPORTS_FROM", "calls", "INHERITS"]


def _bound(org: str):  # noqa: ANN202
    return bind(OrgContext(org, None, "job", "job:test", "t"))


async def _admin(sql: str, *args: object) -> None:
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        await con.execute(sql, *args)
    finally:
        await con.close()


async def _seed(org: str) -> None:
    """Edges between the keys above: every ordered pair in some type, a second type on some pairs, a key that names the type with a document that does not, the other edge collection,
    and links of a key to itself."""
    values: list[tuple] = []
    for i, a in enumerate(IDS):
        for j, b in enumerate(IDS):
            if (i * 7 + j * 3) % 5 == 0:
                continue
            t = TYPES[(i + j) % len(TYPES)]
            values.append(("nodeRelations", "blocks", a, "blocks", b, t, f'{{"relationshipType": "{t}", "line": {i + j}}}'))
            if (i + j) % 4 == 0:
                values.append(("nodeRelations", "blocks", a, "blocks", b, "IMPORTS_FROM", '{"relationshipType": "IMPORTS_FROM"}'))       # a parallel edge of another type
            if (i * j) % 6 == 1:
                values.append(("recordLinks", "blocks", a, "blocks", b, "CALLS", '{"relationshipType": "CALLS"}'))                       # the other edge collection
            if (i + 2 * j) % 9 == 0:
                values.append(("nodeRelations", "blocks", a, "blocks", b, "CALLS", "{}"))                                                # the type is the key's alone (a foreign key's name would be)
            if (i + j) % 11 == 0:
                values.append(("nodeRelations", "blocks", a, "blocks", b, "fk", '{"relationshipType": "CALLS"}'))                       # the document names the type, the key another
        values.append(("nodeRelations", "blocks", a, "blocks", a, "CALLS", '{"relationshipType": "CALLS"}'))                           # a link of a key to itself
    for i in range(0, len(values), 500):
        await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, v.c, v.fc, v.f, v.tc, v.t, v.k, v.d::jsonb "
                     "FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[], $7::text[], $8::text[]) AS v(c, fc, f, tc, t, k, d) ON CONFLICT DO NOTHING",
                     org, *([r[n] for r in values[i:i + 500]] for n in range(7)))


async def _walk(db, org: str, sql_new: bool, anchors: list[str], types: list[str], direction: str, limit: int, *, cursors: bool = False) -> list[list[tuple]]:
    """Every page of the hop, from the first to the last, as (anchor, dir, collection, key, type) rows, with the cursor of each if asked (the old statement's cursors are another format)."""
    out, after = [], ""
    out_f, in_f, both = direction in ("outbound", "any"), direction in ("inbound", "any"), direction == "any"
    for _ in range(2000):
        async def go(tx, after=after) -> list:
            if sql_new:
                return await cg._hop_page(tx, org, "blocks", anchors, types, limit, out=out_f, inn=in_f, both=both, after=after)
            return list(await tx.fetch(LEGACY, org, ["nodeRelations", "recordLinks"], "blocks", anchors, types, limit, out_f, in_f, both, after))
        with _bound(org):
            rows = await db.run(go)
        out.append([(r["anchor"], r["dir"], r["coll"], r["key"], r["rel"], *((r["page_key"],) if cursors else ())) for r in rows])
        if len(rows) < limit:
            return out
        after = rows[-1]["page_key"]
    raise AssertionError("a walk that never ends")


async def test_every_page_is_the_page_the_old_statement_returned(pg_db) -> None:
    org = new_org()
    await _seed(org)
    shapes = [(IDS, ["CALLS"], "outbound"), (IDS, ["CALLS", "IMPORTS_FROM"], "inbound"), (IDS, [*TYPES, "fk"], "any"), (["a1", "B1", "_x"], ["CALLS", "calls"], "any"),
              (["a1"], ["CALLS"], "inbound"), (["a1"], ["CALLS", "fk"], "outbound"), (["nothing"], ["CALLS"], "any"), ([*IDS, "a1"], ["IMPORTS_FROM"], "any")]
    pages = 0
    for anchors, types, direction in shapes:
        for limit in (1, 2, 3, 7, 50, 1000):
            old = await _walk(pg_db, org, False, anchors, types, direction, limit)
            new = await _walk(pg_db, org, True, anchors, types, direction, limit)
            assert new == old, (anchors, types, direction, limit)
            pages += len(old)
    assert pages > 200


async def test_the_pages_are_the_whole_order_of_the_tuple_byte_wise(pg_db) -> None:
    org = new_org()
    await _seed(org)
    pages = await _walk(pg_db, org, True, IDS, ["CALLS", "IMPORTS_FROM", "calls", "INHERITS"], "any", 5, cursors=True)
    keys = [tuple(f.encode("utf-8") for f in cg._decode_cursor(row[5])) for page in pages for row in page]
    assert len(keys) > 100 and keys == sorted(keys) and len(set(keys)) == len(keys)     # strictly increasing, nothing twice


async def test_a_cursor_in_the_middle_of_a_page_resumes_after_exactly_that_row(pg_db) -> None:
    org = new_org()
    await _seed(org)
    whole = [r for page in await _walk(pg_db, org, True, IDS, TYPES, "any", 1000, cursors=True) for r in page]
    for i in (0, 1, len(whole) // 2, len(whole) - 2):
        async def go(tx, after=whole[i][5]) -> list:
            return await cg._hop_page(tx, org, "blocks", IDS, TYPES, 3, out=True, inn=True, both=True, after=after)
        with _bound(org):
            got = [(r["anchor"], r["dir"], r["coll"], r["key"], r["rel"], r["page_key"]) for r in await pg_db.run(go)]
        assert got == whole[i + 1:i + 4]


async def test_another_orgs_edges_are_never_returned(pg_db) -> None:
    a, b = new_org(), new_org()
    await _seed(a)
    await _seed(b)
    await _admin("DELETE FROM graph.edges WHERE org_id = $1", a)
    assert [r for page in await _walk(pg_db, a, True, IDS, TYPES, "any", 50) for r in page] == []
    assert len([r for page in await _walk(pg_db, b, True, IDS, TYPES, "any", 50) for r in page]) > 100


async def test_a_cursor_that_cannot_be_split_resumes_as_the_whole_key_always_did(pg_db) -> None:
    """An id that holds the separator makes a cursor of more than seven parts, and any other text is no cursor of ours: the page is the rows after it in the order of the whole key, as before."""
    org = new_org()
    await _seed(org)
    await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'blocks', 'a1', 'blocks', v, 'CALLS', "
                 "'{\"relationshipType\": \"CALLS\"}'::jsonb FROM unnest(ARRAY[E'p\\x01q', E'p\\x01r']) v", org)
    p = PostgresGraphProvider(logging.getLogger("hop"), pg_db)
    for after in ("a1\x01outbound\x01blocks", "a1\x01outbound\x01blocks\x01p\x01q\x01CALLS\x01nodeRelations\x01CALLS"):
        async def old(tx, after=after) -> list:
            return list(await tx.fetch(LEGACY, org, ["nodeRelations", "recordLinks"], "blocks", ["a1"], ["CALLS"], 6, True, False, False, after))
        with _bound(org):
            want = [(r["anchor"], r["dir"], r["coll"], r["key"], r["page_key"]) for r in await pg_db.run(old)]
            got = await p.get_neighbors_for_nodes_by_relationship_types(["a1"], "blocks", ["CALLS"], "outbound", limit=6, after=after, raise_on_error=True)
        assert want and [(r["anchorKey"], r["direction"], r["collection"], r["key"], r["cursor"]) for r in got] == [(w[0], w[1], w[2], w[3], w[4]) for w in want], after


async def test_the_provider_pages_through_the_same_rows_as_the_statement(pg_db) -> None:
    org = new_org()
    await _seed(org)
    p = PostgresGraphProvider(logging.getLogger("hop"), pg_db)
    got, after = [], ""
    for _ in range(500):
        with _bound(org):
            rows = await p.get_neighbors_for_nodes_by_relationship_types(["a1", "B1"], "blocks", ["CALLS"], "any", limit=4, after=after)
        got += [(r["anchorKey"], r["direction"], r["collection"], r["key"], r["relationshipType"]) for r in rows]
        if len(rows) < 4:
            break
        after = rows[-1]["cursor"]
    old = [r for page in await _walk(pg_db, org, False, ["a1", "B1"], ["CALLS"], "any", 4) for r in page]
    assert got == [(r[0], r[1], r[2], r[3], r[4]) for r in old] and len(got) > 4


async def test_a_page_of_a_node_of_any_degree_reads_about_a_page_of_its_edges(pg_db) -> None:
    """A hub with 120,000 callers and as many edges elsewhere: the first page and a page far into it, never analysed and then analysed, each read by the order's index and not a sort of every edge."""
    org = new_org()
    await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'blocks', 'c' || lpad(g::text, 6, '0'), 'blocks', 'hub', 'CALLS', "
                 "jsonb_build_object('relationshipType', 'CALLS') FROM generate_series(1, 120000) g", org)
    await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'blocks', 'x' || g, 'blocks', 'y' || ((g * 7) % 20000), 'CALLS', "
                 "jsonb_build_object('relationshipType', 'CALLS') FROM generate_series(1, 120000) g", org)
    far = cg._cursor({"anchor": "hub", "dir": "inbound", "coll": "blocks", "key": "c090000", "rel": "CALLS", "edge_coll": "nodeRelations", "ekey": "CALLS"})
    for state in ("fresh", "analysed"):
        if state == "analysed":
            await _admin("ANALYZE graph.edges")
        for after in ("", far):
            async def go(tx, after=after) -> str:
                args = cg._hop_args(org, "blocks", ["hub"], ["CALLS"], 200, out=False, inn=True, both=False, after=after)
                await tx.fetchval(cg._HOP_PROFILE)
                return "\n".join(r[0] for r in await tx.fetch("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + cg._HOP_PAGE_SQL, *args))
            with _bound(org):
                plan = await pg_db.run(go)
            assert "edges_by_neighbour_in" in plan and "Seq Scan on" not in plan and "Bitmap Heap Scan" not in plan, (state, after, plan)
            root = next(line for line in plan.splitlines() if line.strip().startswith("Buffers:"))               # the whole statement's, hit and read (a cold cache reads)
            touched = sum(int(part.split("=")[1]) for part in root.split() if part.startswith(("hit=", "read=")))
            assert touched < 2500, (state, after, touched, plan)                                                         # a page's rows, not the 120,000 of the hub
            if after:
                assert re.search(r"Index Cond: .*ROW\(", plan), plan                                                  # the cursor is a bound of the index scan


async def test_the_page_is_planned_for_its_arguments_in_every_run_of_a_unit(pg_db) -> None:
    """Eight pages in the one unit (one connection, so the statement's counters add up): a prepared statement may take its generic plan from the sixth run, which reads every edge of a hub whatever the cursor."""
    org = new_org()
    await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, 'nodeRelations', 'blocks', 'c' || lpad(g::text, 6, '0'), 'blocks', 'hub', 'CALLS', "
                 "jsonb_build_object('relationshipType', 'CALLS') FROM generate_series(1, 20000) g", org)
    p = PostgresGraphProvider(logging.getLogger("hop"), pg_db)
    with _bound(org):
        t = await p.begin_transaction([], [])
    try:
        with _bound(org):
            await p._txn(t).run(lambda tx: tx.fetchval("SELECT set_config('plan_cache_mode', 'force_generic_plan', true)"))      # the mode the unit would be in once a prepared statement went generic
        after = ""
        for _ in range(8):
            with _bound(org):
                rows = await p.get_neighbors_for_nodes_by_relationship_types(["hub"], "blocks", ["CALLS"], "inbound", limit=50, after=after, transaction=t)
            assert len(rows) == 50
            after = rows[-1]["cursor"]
        with _bound(org):
            counts = await p._txn(t).run(lambda tx: tx.fetch("SELECT generic_plans, custom_plans FROM pg_prepared_statements "
                                                             "WHERE statement LIKE '%pipeshub:graph.get_neighbors_for_nodes_by_relationship_types%' AND statement NOT LIKE '%pg_prepared_statements%' AND statement LIKE '%UNION ALL%'"))
        assert len(counts) == 1 and counts[0]["generic_plans"] == 0 and counts[0]["custom_plans"] == 8, [dict(c) for c in counts]
    finally:
        with _bound(org):
            await p.rollback_transaction(t)


SEPS = ["p", "p\x01A", "p\x01", "p\x01\x01", "q", "q\x01x", "p\\1", "é\x01", "p\x01A\x01", "a\\"]


async def _seed_separators(org: str) -> list[tuple]:
    """Edges between ids that hold the separator and the escape (some are prefixes of others) in some types and both edge collections; returns the rows a hop of all of them reads, as (anchor, dir, coll, key, type)."""
    values = []
    for i, a in enumerate(SEPS):
        for j, b in enumerate(SEPS):
            if (i + 2 * j) % 3 == 0:
                values.append(("nodeRelations", a, b, "CALLS", '{"relationshipType": "CALLS"}'))
            if (i * j) % 4 == 1:
                values.append(("recordLinks", a, b, "CALLS", '{"relationshipType": "CALLS"}'))        # the same neighbour and type in the other edge collection
            if (i + j) % 5 == 0:
                values.append(("nodeRelations", a, b, "IMPORTS_FROM", '{"relationshipType": "IMPORTS_FROM"}'))
    await _admin("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) SELECT $1, v.c, 'blocks', v.f, 'blocks', v.t, v.k, v.d::jsonb "
                 "FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[]) AS v(c, f, t, k, d)", org, *([r[n] for r in values] for n in range(5)))
    rows = []
    for _, a, b, k, _d in values:
        t = k
        rows.append((a, "outbound", "blocks", b, t))
        if a != b:
            rows.append((b, "inbound", "blocks", a, t))
    return rows


@pytest.mark.parametrize("limit", [1, 2, 7])
async def test_a_whole_walk_over_ids_that_hold_the_separator_returns_every_row_once_and_ends(pg_db, limit: int) -> None:
    org = new_org()
    want = await _seed_separators(org)
    p = PostgresGraphProvider(logging.getLogger("hop"), pg_db)
    got, after = [], ""
    for _ in range(len(want) + 3):
        with _bound(org):
            rows = await p.get_neighbors_for_nodes_by_relationship_types(SEPS, "blocks", ["CALLS", "IMPORTS_FROM"], "any", limit=limit, after=after, raise_on_error=True)
        got += [(r["anchorKey"], r["direction"], r["collection"], r["key"], r["relationshipType"]) for r in rows]
        if len(rows) < limit:
            break
        after = rows[-1]["cursor"]
    else:
        raise AssertionError("a walk that never ends")
    assert len(want) > 50 and Counter(got) == Counter(want) and len(got) == len(want), (len(got), len(want))


async def test_a_walk_that_began_with_an_old_cursor_ends_in_the_order_it_began_in(pg_db) -> None:
    """A cursor of the whole-key order (a walk started before the ordered page): the walk goes on in that order, returns old cursors, and ends with every row after the cursor once."""
    org = new_org()
    await _seed_separators(org)
    p = PostgresGraphProvider(logging.getLogger("hop"), pg_db)

    async def old_walk(after: str, limit: int) -> list[tuple]:
        out = []
        for _ in range(500):
            async def go(tx, after=after) -> list:
                return list(await tx.fetch(LEGACY, org, ["nodeRelations", "recordLinks"], "blocks", SEPS, ["CALLS", "IMPORTS_FROM"], limit, True, True, True, after))
            with _bound(org):
                rows = await pg_db.run(go)
            out += [(r["anchor"], r["dir"], r["coll"], r["key"], r["page_key"]) for r in rows]
            if len(rows) < limit:
                return out
            after = rows[-1]["page_key"]
        raise AssertionError("never ends")
    whole = await old_walk("", 5)
    assert len(whole) > 50
    for start in (3, 20, len(whole) - 4):                     # a walk that was at this row when the new code came in
        got, after = [], whole[start][4]
        for _ in range(500):
            with _bound(org):
                rows = await p.get_neighbors_for_nodes_by_relationship_types(SEPS, "blocks", ["CALLS", "IMPORTS_FROM"], "any", limit=4, after=after, raise_on_error=True)
            got += [(r["anchorKey"], r["direction"], r["collection"], r["key"], r["cursor"]) for r in rows]
            if len(rows) < 4:
                break
            after = rows[-1]["cursor"]
        else:
            raise AssertionError("never ends")
        assert got == whole[start + 1:], start
