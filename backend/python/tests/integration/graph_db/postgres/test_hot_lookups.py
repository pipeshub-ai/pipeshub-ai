"""The code graph's hot lookups (blocks by record or by connector and name, code files by path) on PostgreSQL: they are served by the vetted reads of graph_0003
(row-level security keeps a plain statement from the expression indexes), and answer exactly what the plain statements answered.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from app.core.db.uow import Tx
from app.core.org_context import OrgContext, bind

from .pg_support import OrgBound, StandInProvider, new_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

# The statements as the provider sent them before the vetted reads (a null filter also matches an absent key in the first).
OLD_FILTERS = """SELECT n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
    AND NOT EXISTS (SELECT 1 FROM unnest($3::text[], $4::text[]) f(k, v)
        WHERE CASE WHEN f.v = 'null' THEN coalesce(n.doc->f.k, 'null'::jsonb) <> 'null'::jsonb ELSE n.doc->f.k IS DISTINCT FROM f.v::jsonb END)"""
OLD_PAGE = """SELECT n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND ($3::text IS NULL OR n.id > $3)
    AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
    ORDER BY n.id OFFSET $4 LIMIT $5"""


@pytest.fixture
async def world(pg_db) -> AsyncIterator[tuple[OrgBound, OrgBound]]:
    a, b = OrgBound(StandInProvider(logging.getLogger("hot"), pg_db), new_org()), OrgBound(StandInProvider(logging.getLogger("hot"), pg_db), new_org())
    for p in (a, b):                     # the same ids and keys in both orgs
        blocks = [{"_key": f"b{i:04d}", "orgId": p.org, "recordId": "r1" if i < 1500 else "r2", "connectorId": "c1" if i % 5 else "c2",
                   "nameFolded": f"n{i % 7}", "qualifiedName": f"m::f{i}", **({"flag": None} if i % 11 == 0 else {})} for i in range(1600)]
        blocks.append({"_key": "bx", "orgId": p.org, "recordId": "r1", "connectorId": "c1"})            # no nameFolded, qualifiedName
        await p.batch_upsert_nodes(blocks, "blocks")
        await p.batch_upsert_nodes([{"_key": f"f{i}", "orgId": p.org, "filePath": f"src/p{i % 3}/f{i}.py"} for i in range(30)], "codeFiles")
    # a block of A alone, a code file of A alone
    await a.batch_upsert_nodes([{"_key": "only-a", "orgId": a.org, "recordId": "r1", "connectorId": "c1", "nameFolded": "n1"}], "blocks")
    await a.batch_upsert_nodes([{"_key": "fa", "orgId": a.org, "filePath": "src/only-a.py"}], "codeFiles")
    yield a, b
    for p in (a, b):
        with bind(OrgContext(p.org, None, "job", "job:test", "t")):
            await pg_db.run(lambda tx, o=p.org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def old(pg_db, org, sql, *args) -> list:
    with bind(OrgContext(org, None, "job", "job:test", "t")):
        return await pg_db.run(lambda tx: tx.fetch(sql, org, *args))


def keys(docs) -> list[str]:
    return sorted(d["_key"] if "_key" in d else d["id"] for d in docs)


def row_ids(rows) -> list[str]:
    return [json.loads(r["doc"])["id"] if isinstance(r["doc"], str) else r["doc"]["id"] for r in rows]


def raw_keys(rows) -> list[str]:
    return sorted(row_ids(rows))


def shapes(org) -> list[tuple[str, dict]]:
    return [
        ("blocks", {"orgId": org, "recordId": "r1", "qualifiedName": "m::f3", "connectorId": "c1"}),
        ("blocks", {"orgId": org, "recordId": "r1", "connectorId": "c1"}),                         # more than a page of 1000 blocks
        ("blocks", {"orgId": org, "recordId": "r1"}),
        ("blocks", {"orgId": org, "recordId": "r1", "flag": None}),                                # a null filter matches an absent or null field
        ("blocks", {"orgId": org, "recordId": "r1", "nameFolded": None}),
        ("blocks", {"orgId": org, "recordId": "r2", "flag": False}),
        ("blocks", {"orgId": org, "recordId": "nope", "connectorId": "c1"}),                        # a record with no blocks
        ("blocks", {"orgId": "someone-else", "recordId": "r1"}),                                    # another org's value in the filter
        ("blocks", {"orgId": org, "connectorId": "c1", "nameFolded": "n1"}),
        ("blocks", {"orgId": org, "connectorId": "c2", "nameFolded": "n3"}),
        ("blocks", {"connectorId": "c1", "nameFolded": "n1", "recordId": "r2"}),
        ("blocks", {"orgId": org, "connectorId": "c1", "nameFolded": "absent"}),
        ("codeFiles", {"orgId": org, "filePath": "src/p1/f4.py"}),
        ("codeFiles", {"orgId": org, "filePath": "src/only-a.py"}),
        ("codeFiles", {"orgId": org, "filePath": "src/none.py"}),
        ("blocks", {"orgId": org, "qualifiedName": "m::f3"}),                                       # no indexed key: the plain statement
        ("blocks", {"recordId": 5}),                                                               # not a string: the plain statement
        ("apps", {"orgId": org}),
    ]


async def test_get_nodes_by_filters_answers_what_the_plain_statement_answered(world, pg_db) -> None:
    for p in world:
        for coll, f in shapes(p.org):
            expected = await old(pg_db, p.org, OLD_FILTERS, coll, list(f), [json.dumps(v) for v in f.values()])
            got = await p.get_nodes_by_filters(coll, f, raise_on_error=True)
            assert keys(got) == raw_keys(expected), (p.org, coll, f)
    a, b = world
    # both directions, spelled out: A's own rows are found and none of them leaks into B's answer
    assert "only-a" in keys(await a.get_nodes_by_filters("blocks", {"recordId": "r1", "connectorId": "c1"}))
    assert "only-a" not in keys(await b.get_nodes_by_filters("blocks", {"recordId": "r1", "connectorId": "c1"}))
    assert await b.get_nodes_by_filters("codeFiles", {"filePath": "src/only-a.py"}) == []
    assert len(await a.get_nodes_by_filters("blocks", {"recordId": "r1"})) == 1502        # past one page of the vetted read


async def test_get_documents_paginated_answers_what_the_plain_statement_answered(world, pg_db) -> None:
    for p in world:
        for coll, f in shapes(p.org):
            for skip, limit, after in ((0, 50, None), (3, 10, None), (0, 1000, None), (0, 2000, None), (10, 20, "b0100"), (0, 5, "zzzz")):
                expected = await old(pg_db, p.org, OLD_PAGE, coll, after, skip, limit, list(f), [json.dumps(v) for v in f.values()])
                got = await p.get_documents_paginated(coll, skip=skip, limit=limit, filters=f, sort_field="_key", after_key=after, raise_on_error=True)
                assert [d["_key"] for d in got] == row_ids(expected), (p.org, coll, f, skip, limit, after)


async def test_a_hot_lookup_is_sent_to_its_vetted_read(world, monkeypatch) -> None:
    sent: list[str] = []
    real = Tx.fetch

    async def spy(self, sql, *args) -> list:
        sent.append(sql)
        return await real(self, sql, *args)
    monkeypatch.setattr(Tx, "fetch", spy)
    a, _ = world
    await a.get_nodes_by_filters("codeFiles", {"orgId": a.org, "filePath": "src/p1/f4.py"})
    await a.get_nodes_by_filters("blocks", {"orgId": a.org, "recordId": "r1", "qualifiedName": "m::f3", "connectorId": "c1"})
    await a.get_documents_paginated("blocks", limit=5, filters={"orgId": a.org, "connectorId": "c1", "nameFolded": "n1"}, sort_field="_key")
    await a.get_nodes_by_filters("blocks", {"orgId": a.org, "qualifiedName": "m::f3"})
    assert [next((n for n in ("code_files_by_path", "blocks_by_record", "blocks_by_name") if n in s), "plain") for s in sent if "graph." in s] == [
        "code_files_by_path", "blocks_by_record", "blocks_by_name", "plain"]
