"""The SQL helpers of the projection (``kg.edge_ends``, ``kg.coll_code``, ``kg.proj_doc``) and their Python twins in ``kg.model`` say the same."""
from __future__ import annotations

import itertools
import json
import logging

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg.model import COLL_CODE, edge_ends
from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org

pytestmark = pytest.mark.integration

COLLS = ("apps", "recordGroups", "records", "users", "groups", "roles", "teams", "organizations", "blocks")
EDGES = ("permission", "belongsTo", "nodeRelations", "inheritPermissions", "userAppRelation", "authenticatedAs", "isOfType")
DOCS = ({}, {"relationshipType": "PARENT_CHILD"}, {"relationshipType": "ATTACHMENT"}, {"relationshipType": "BOOKMARK"}, {"type": "USER"})


async def fetch(pg_db, sql: str, *args: object) -> list:
    p = PostgresGraphProvider(logging.getLogger("kg-sql"), pg_db)
    with bind(OrgContext(new_org(), None, "job", "job:kg-test", "t")):
        return await p._q(lambda tx: tx.fetch(sql, *args), None)


async def test_the_ends_of_an_edge_are_the_same_in_sql_and_in_python(pg_db) -> None:
    cases = [(c, fc, tc, d) for c, fc, tc, d in itertools.product(EDGES, COLLS, COLLS, DOCS)]
    rows = await fetch(pg_db, """SELECT x.c, x.fc, x.tc, x.i,
          coalesce((SELECT array_agg(e.coll || ':' || e.id ORDER BY e.coll, e.id) FROM kg.edge_ends(x.c, x.fc, 'a', x.tc, 'b', ($5::jsonb[])[x.i + 1]) e), '{}') AS ends
        FROM unnest($1::text[], $2::text[], $3::text[], $4::int[]) AS x(c, fc, tc, i)""",
                       [c for c, _, _, _ in cases], [fc for _, fc, _, _ in cases], [tc for _, _, tc, _ in cases], [DOCS.index(d) for _, _, _, d in cases],
                       [json.dumps(d) for d in DOCS])
    assert len(rows) == len(cases)
    for r, (c, fc, tc, d) in zip(rows, cases, strict=True):
        py = sorted(f"{k[0]}:{k[1]}" for k in edge_ends(c, (fc, "a"), (tc, "b"), d))
        assert list(r["ends"]) == py, (c, fc, tc, d)


async def test_the_collection_codes_are_the_same_in_sql_and_in_python(pg_db) -> None:
    rows = await fetch(pg_db, "SELECT c, kg.coll_code(c) AS code FROM unnest($1::text[]) AS t(c)", [*COLL_CODE, "blocks"])
    assert {r["c"]: r["code"] for r in rows} == {**COLL_CODE, "blocks": None}


async def test_the_projected_part_of_a_node_document_is_only_what_the_projection_reads(pg_db) -> None:
    doc = '{"recordName": "n", "indexingStatus": "DONE", "isDeleted": null, "accessRule": "OPEN", "extra": {"x": 1}, "orgId": "o"}'
    rows = await fetch(pg_db, "SELECT kg.proj_doc('records', $1::jsonb)::text AS d, kg.proj_doc('users', $1::jsonb)::text AS u", doc)
    assert json.loads(rows[0]["d"]) == {"recordName": "n", "accessRule": "OPEN", "orgId": "o"}      # a status and an unknown field are not projected, a null flag is absent
    assert rows[0]["u"] is None                                                                  # a principal's document is not projected at all
