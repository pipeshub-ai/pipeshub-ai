"""The pgvector provider's writes against the migrated schema, through PgBouncer in transaction mode. What was written is read back
with the administrator's connection (reads through the provider come with the change that adds them).

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_writes.py -m integration

Every case runs twice: ``native`` (a delete removes rows) and ``safe`` (HNSW safe mode, forced on whichever pgvector this is: a delete is a
tombstone and the postings go at once).
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.pgvector import writes
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    OrgRequired,
    PgVectorError,
    SpaceMismatch,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
A, B = "a" * 24, "b" * 24
DIM = 8


def pid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pgvector-writes/{name}"))


def point(org: str, name: str, vrid: str = "r1", *, i: int = 0, text: str = "hello", terms: tuple[int, ...] = (3, 7), dim: int = DIM,
          conn: list[str] | None = None, model: str = "p:m:8") -> VectorPoint:
    payload: dict = {"page_content": text, "metadata": {"orgId": org, "virtualRecordId": vrid, "blockId": name, "embeddingModel": model}}
    if conn is not None:
        payload["connectorIds"] = conn
    return VectorPoint(id=pid(f"{org}/{name}"), dense_vector=[1.0 if j == i % dim else 0.0 for j in range(dim)], payload=payload,
                       sparse_vector=SparseVector(list(terms), [0.5] * len(terms)) if terms else None)


def flt(org: str, **must: str) -> FilterExpression:
    return FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org),
                                  *[FieldCondition(key=f"metadata.{k}", value=v) for k, v in must.items()]])


@pytest.fixture(params=[False, True], ids=["native", "safe"])
async def store(request, env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    if request.param:
        svc.pg_capabilities = dataclasses.replace(svc.pg_capabilities, hnsw_safe_mode=True)
    svc.env = env
    yield svc
    await svc.disconnect()


async def sql(store: PgVectorService, query: str, *args: object) -> list:
    conn = await store.env.admin()
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def live(store: PgVectorService, org: str) -> int:
    return (await sql(store, "SELECT count(*) FROM retrieval.points p WHERE org_id = $1 AND NOT EXISTS "
                             "(SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)", org))[0][0]


async def count_delta(store: PgVectorService, org: str) -> int:
    return (await sql(store, "SELECT coalesce(sum(d), 0) FROM retrieval.lexical_df_delta WHERE org_id = $1 AND term = $2", org, writes.COUNT_TERM))[0][0]


async def postings(store: PgVectorService, org: str) -> int:
    return (await sql(store, "SELECT count(*) FROM retrieval.postings WHERE org_id = $1", org))[0][0]


async def test_an_upsert_writes_the_point_its_text_its_postings_and_the_count(store) -> None:
    await store.upsert_points("records", [point(A, "p1", text="alpha"), point(A, "p2", "r2", text="beta", terms=())])
    rows = await sql(store, "SELECT p.point_id, p.group_key, p.vrid, p.block_id, p.embedding_dim, pt.page_content, pt.payload "
                            "FROM retrieval.points p JOIN retrieval.point_text pt USING (org_id, point_sk) WHERE p.org_id = $1 ORDER BY p.block_id", A)
    assert [(r["group_key"], r["vrid"], r["block_id"], r["embedding_dim"], r["page_content"]) for r in rows] == [
        ("r1", "r1", "p1", DIM, "alpha"), ("r2", "r2", "p2", DIM, "beta")]
    assert "page_content" not in rows[0]["payload"] and "orgId" in rows[0]["payload"]
    assert await postings(store, A) == 2 and await count_delta(store, A) == 1       # only p1 has terms
    space = await sql(store, "SELECT role, dim FROM retrieval.org_spaces WHERE org_id = $1", A)
    assert [(r["role"], r["dim"]) for r in space] == [("active", DIM)]                # the first write defined the org's space


async def test_writing_the_same_point_again_updates_it_in_place_and_follows_the_terms(store) -> None:
    await store.upsert_points("records", [point(A, "p1", terms=(3, 7))])
    first = (await sql(store, "SELECT point_sk, xmin::text AS x FROM retrieval.points WHERE org_id = $1", A))[0]
    await store.upsert_points("records", [point(A, "p1", terms=(3, 7))])                  # unchanged: no new row version
    same = (await sql(store, "SELECT point_sk, xmin::text AS x FROM retrieval.points WHERE org_id = $1", A))[0]
    assert (same["point_sk"], same["x"]) == (first["point_sk"], first["x"])
    await store.upsert_points("records", [point(A, "p1", text="changed", terms=(7, 9), i=3)])
    row = (await sql(store, "SELECT p.point_sk, pt.page_content FROM retrieval.points p JOIN retrieval.point_text pt USING (org_id, point_sk) "
                            "WHERE p.org_id = $1", A))[0]
    assert (row["point_sk"], row["page_content"]) == (first["point_sk"], "changed")
    assert [r["term"] for r in await sql(store, "SELECT term FROM retrieval.postings WHERE org_id = $1 ORDER BY term", A)] == [7, 9]
    assert await count_delta(store, A) == 1 and await live(store, A) == 1
    await store.upsert_points("records", [point(A, "p1", terms=())])                      # loses all its terms: leaves the count
    assert await postings(store, A) == 0 and await count_delta(store, A) == 0


async def test_every_write_needs_its_org_and_nothing_is_written_without_one(store) -> None:
    bad = point(A, "p1")
    bad.payload["metadata"]["orgId"] = ""
    with pytest.raises(OrgRequired):
        await store.upsert_points("records", [bad])
    with pytest.raises(OrgRequired):
        await store.delete_points("records", FilterExpression(must=[FieldCondition(key="metadata.virtualRecordId", value="r1")]))
    with pytest.raises(OrgRequired):
        await store.update_payload_by_ids("records", [pid("x")], {"k": 1}, org_id="")
    assert (await sql(store, "SELECT count(*) FROM retrieval.points"))[0][0] == 0


async def test_a_shared_vrid_is_deleted_for_one_org_only(store) -> None:
    await store.upsert_points("records", [point(A, "p1", "shared"), point(A, "p2", "shared", terms=(5,))])
    await store.upsert_points("records", [point(B, "p1", "shared")])
    await store.delete_points("records", flt(A, virtualRecordId="shared"))
    assert (await live(store, A), await live(store, B)) == (0, 1)
    assert (await postings(store, A), await count_delta(store, A)) == (0, 0)                # the postings and the count went with the points
    assert (await postings(store, B), await count_delta(store, B)) == (2, 1)
    tombstones = (await sql(store, "SELECT count(*) FROM retrieval.point_tombstones WHERE org_id = $1", A))[0][0]
    assert tombstones == (2 if store._safe_mode else 0)
    assert (await sql(store, "SELECT count(*) FROM retrieval.points WHERE org_id = $1", A))[0][0] == (2 if store._safe_mode else 0)


async def test_deletes_run_in_batches(store, monkeypatch) -> None:
    monkeypatch.setattr(writes, "DELETE_BATCH", 2)
    await store.upsert_points("records", [point(A, f"p{n}", terms=(n + 1,)) for n in range(5)])
    await store.delete_points("records", flt(A, virtualRecordId="r1"))
    assert await live(store, A) == 0 and await postings(store, A) == 0 and await count_delta(store, A) == 0


async def test_a_point_deleted_in_safe_mode_comes_back_when_written_again(store) -> None:
    await store.upsert_points("records", [point(A, "p1")])
    await store.delete_points("records", flt(A, virtualRecordId="r1"))
    assert await live(store, A) == 0
    await store.upsert_points("records", [point(A, "p1")])
    assert (await live(store, A), await postings(store, A), await count_delta(store, A)) == (1, 2, 1)
    assert (await sql(store, "SELECT count(*) FROM retrieval.point_tombstones WHERE org_id = $1", A))[0][0] == 0


async def test_membership_is_replaced_only_by_a_later_reader_of_the_graph(store) -> None:
    now = datetime.now(UTC)
    await store.upsert_points("records", [point(A, "p1", conn=["c1"])])
    members = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = 'r1' ORDER BY 1, 2"
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c1")]
    await store.set_payload("records", {"connectorIds": ["c2"], "recordGroupIds": ["g1"]}, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=5))
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c2"), (2, "g1")]
    await store.set_payload("records", {"connectorIds": ["old"]}, flt(A, virtualRecordId="r1"), computed_at=now)        # an earlier read loses
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c2"), (2, "g1")]
    await store.upsert_points("records", [point(A, "p1")])                                                              # indexing carries none: erases none
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c2"), (2, "g1")]


async def test_payload_updates_by_id_and_by_filter_stay_inside_the_org(store) -> None:
    await store.upsert_points("records", [point(A, "p1"), point(A, "p2")])
    await store.upsert_points("records", [point(B, "p1")])
    await store.update_payload_by_ids("records", [pid(f"{A}/p1"), pid(f"{B}/p1")], {"tag": "x"}, org_id=A)        # B's id is left alone
    await store.set_payload("records", {"flag": True}, flt(A, blockId="p2"))
    await store.overwrite_payload("records", {"only": 1}, flt(B, blockId="p1"))
    payloads = {(r["org_id"], r["block"]): r["payload"] for r in await sql(
        store, "SELECT pt.org_id, p.block_id AS block, pt.payload::text AS payload FROM retrieval.point_text pt JOIN retrieval.points p USING (org_id, point_sk)")}
    assert '"tag": "x"' in payloads[(A, "p1")] and '"tag"' not in payloads[(B, "p1")]
    assert '"flag": true' in payloads[(A, "p2")] and '"flag"' not in payloads[(A, "p1")]
    assert payloads[(B, "p1")] == '{"only": 1}'
    with pytest.raises(PgVectorError, match="may not change"):
        await store.set_payload("records", {"metadata": {"blockId": "z"}}, flt(A, blockId="p1"))


async def test_the_first_write_defines_the_orgs_space_and_another_dimension_is_refused(store) -> None:
    await store.upsert_points("records", [point(A, "p1")])
    with pytest.raises(SpaceMismatch):
        await store.upsert_points("records", [point(A, "p2", dim=4, model="p:m:4")])
    await store.upsert_points("records", [point(B, "p1", dim=4, model="p:m:4")])                    # another org, another space
    dims = {r["org_id"]: r["dim"] for r in await sql(store, "SELECT org_id, dim FROM retrieval.org_spaces")}
    assert dims == {A: DIM, B: 4}
    with pytest.raises(SpaceMismatch):
        await store.upsert_points("records", [point(B, "p3", dim=DIM)])


async def test_concurrent_writers_never_cross_orgs_and_two_writers_of_the_same_points_leave_exact_counts(store) -> None:
    async def write(org: str, n: int) -> None:
        with org_context.bind(org_context.OrgContext(org, None, "service", "t", "-")):
            await store.upsert_points("records", [point(org, f"p{j}", terms=(j + 1,)) for j in range(n)])

    await asyncio.gather(*[write(org, 20) for org in (A, B) for _ in range(3)])               # three writers of the same 20 points per org
    for org in (A, B):
        assert (await live(store, org), await postings(store, org), await count_delta(store, org)) == (20, 20, 20)
    assert (await sql(store, "SELECT count(*) FROM retrieval.postings q JOIN retrieval.points p USING (org_id, point_sk) WHERE q.org_id <> p.org_id"))[0][0] == 0
