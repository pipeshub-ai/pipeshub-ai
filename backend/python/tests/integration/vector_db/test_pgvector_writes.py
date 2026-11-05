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
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.pgvector import writes
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    OrgRequired,
    PgVectorError,
    PgVectorUnsupportedError,
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


MEMBERS = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = $2 ORDER BY 1, 2"


async def test_a_membership_write_that_carries_some_arrays_leaves_the_others_as_they_are(store) -> None:
    now = datetime.now(UTC)
    bulk = point(A, "p1", conn=["c1"])
    bulk.payload.update({"recordGroupIds": ["g1"], "rootRecordGroupIds": ["rg1"]})
    await store.upsert_points("records", [bulk], computed_at=now)
    await store.set_payload("records", {"connectorIds": ["c9"]}, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=5))
    assert [tuple(r) for r in await sql(store, MEMBERS, A, "r1")] == [(1, "c9"), (2, "g1"), (3, "rg1")]          # only the connector arm moved
    row = (await sql(store, "SELECT connector_ids, record_group_ids, root_record_group_ids FROM retrieval.point_groups WHERE org_id = $1", A))[0]
    assert (row[0], row[1], row[2]) == (["c9"], ["g1"], ["rg1"])
    await store.update_payload_by_ids("records", [pid(f"{A}/p1")], {"recordGroupIds": []}, org_id=A, computed_at=now + timedelta(seconds=9))
    assert [tuple(r) for r in await sql(store, MEMBERS, A, "r1")] == [(1, "c9"), (3, "rg1")]                    # an explicit empty list does empty one


async def test_two_writers_of_one_group_leave_the_members_of_the_one_that_read_the_graph_later(store) -> None:
    now = datetime.now(UTC)
    seed = await store.env.admin()
    other, first = await store.env.admin(), await store.env.admin()
    try:
        await writes.write_groups(seed, A, 1, {"r1": {"connectorIds": ["x"]}}, now, allow_empty=True)
        tx_a = first.transaction()
        await tx_a.start()
        await writes.write_groups(first, A, 1, {"r1": {"connectorIds": ["a", "b"]}}, now + timedelta(seconds=1), allow_empty=True)   # not committed yet
        tx_b = other.transaction()
        await tx_b.start()
        later = asyncio.ensure_future(writes.write_groups(other, A, 1, {"r1": {"connectorIds": ["b"]}}, now + timedelta(seconds=2), allow_empty=True))
        await asyncio.sleep(0.5)
        assert not later.done()                                                                       # it waits for the first writer's row lock
        await tx_a.commit()
        assert await asyncio.wait_for(later, 10) == 1
        await tx_b.commit()
    finally:
        for c in (seed, other, first):
            await c.close()
    assert [tuple(r) for r in await sql(store, MEMBERS, A, "r1")] == [(1, "b")]                          # not (1, a), (1, b)


async def test_an_upsert_that_carries_some_membership_arrays_leaves_the_others_as_they_are(store) -> None:
    await store.upsert_points("records", [dataclasses.replace(point(A, "p1", conn=["c1"]), payload={
        **point(A, "p1", conn=["c1"]).payload, "recordGroupIds": ["g1"], "rootRecordGroupIds": ["root1"]})])
    members = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = 'r1' ORDER BY 1, 2"
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c1"), (2, "g1"), (3, "root1")]
    await store.upsert_points("records", [point(A, "p1", conn=["c2"])], computed_at=datetime.now(UTC) + timedelta(seconds=5))     # carries connectorIds alone
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c2"), (2, "g1"), (3, "root1")]      # the other two kinds are not erased


async def test_points_of_one_group_in_one_upsert_each_contribute_the_membership_arrays_they_carry(store) -> None:
    first = point(A, "p1", conn=["c1"])
    second = dataclasses.replace(point(A, "p2"), payload={**point(A, "p2").payload, "recordGroupIds": ["g1"]})
    await store.upsert_points("records", [first, second])                                                     # one record, two points, different arrays
    members = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = 'r1' ORDER BY 1, 2"
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c1"), (2, "g1")]
    later = point(A, "p3", conn=["c9"])
    await store.upsert_points("records", [first, later], computed_at=datetime.now(UTC) + timedelta(seconds=5))   # the same key twice: the later point wins it
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c9"), (2, "g1")]


async def test_a_repeated_point_takes_its_place_at_its_last_occurrence_when_the_batch_sets_a_membership_key(store) -> None:
    await store.upsert_points("records", [point(A, "p1", conn=["c1"]), point(A, "p2", conn=["c2"]), point(A, "p1", conn=["c3"])])
    members = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = 'r1' ORDER BY 1, 2"
    assert [tuple(r) for r in await sql(store, members, A)] == [(1, "c3")]                # the last point of the batch wins the key


async def test_a_writer_that_carries_no_membership_still_holds_its_groups_rows_until_it_commits(store) -> None:
    """The maintenance job that collects the membership of groups without points takes its candidates ``FOR UPDATE SKIP LOCKED``. A writer
    that adds points to a group without carrying membership must hold that group's row too, or the job removes the membership under it."""
    await store.upsert_points("records", [point(A, "p1", conn=["c1"])])
    coll = (await sql(store, "SELECT collection_id FROM retrieval.collections WHERE name = 'records'"))[0][0]
    space = (await sql(store, "SELECT space_id FROM retrieval.org_spaces WHERE org_id = $1", A))[0][0]
    candidates = ("SELECT group_key FROM retrieval.point_groups WHERE org_id = $1 AND collection_id = $2 FOR UPDATE SKIP LOCKED")
    writer, collector = await store.env.admin(), await store.env.admin()
    try:
        tx = writer.transaction()
        await tx.start()
        await writes.upsert(writer, coll, space, writes.prepare([point(A, "p2")]), datetime.now(UTC), safe_mode=False)       # group r1, no membership keys
        async with collector.transaction():
            assert await collector.fetch(candidates, A, coll) == []                    # the collector skips the group the open writer holds
        await tx.commit()
        async with collector.transaction():
            assert [r["group_key"] for r in await collector.fetch(candidates, A, coll)] == ["r1"]       # free again once the writer has committed
    finally:
        for c in (writer, collector):
            await c.close()
    assert [tuple(r) for r in await sql(store, MEMBERS, A, "r1")] == [(1, "c1")]            # the membership is still there


async def test_a_point_written_again_after_a_safe_mode_delete_is_counted_once_whichever_way_its_terms_change(store) -> None:
    safe = dataclasses.replace(store.pg_capabilities, hnsw_safe_mode=True)
    store.pg_capabilities = safe
    await store.upsert_points("records", [point(A, "p1", terms=(3,)), point(A, "p2", terms=())])
    assert await count_delta(store, A) == 1
    await store.delete_points("records", flt(A, virtualRecordId="r1"))
    assert await count_delta(store, A) == 0
    await store.upsert_points("records", [point(A, "p1", terms=()), point(A, "p2", terms=(5,))])             # p1 loses its terms, p2 gains them
    assert (await live(store, A), await postings(store, A), await count_delta(store, A)) == (2, 1, 1)         # only p2 has terms
    await store.delete_points("records", flt(A, virtualRecordId="r1"))
    await store.upsert_points("records", [point(A, "p1", terms=(3, 4)), point(A, "p2", terms=(5,))])
    assert (await live(store, A), await postings(store, A), await count_delta(store, A)) == (2, 3, 2)


async def test_an_overwrite_replaces_the_whole_payload_text_included(store) -> None:
    await store.upsert_points("records", [point(A, "p1", text="keep me?")])
    await store.overwrite_payload("records", {"only": 1}, flt(A, blockId="p1"))
    row = (await sql(store, "SELECT page_content, payload::text AS payload FROM retrieval.point_text WHERE org_id = $1", A))[0]
    assert row["payload"] == '{"only": 1}' and row["page_content"] is None
    await store.set_payload("records", {"more": 2}, flt(A, blockId="p1"))                                      # a merge keeps what it does not carry
    await store.upsert_points("records", [point(A, "p1", text="back")])
    await store.set_payload("records", {"more": 2}, flt(A, blockId="p1"))
    assert (await sql(store, "SELECT page_content FROM retrieval.point_text WHERE org_id = $1", A))[0][0] == "back"


async def test_an_index_run_that_read_the_graph_before_the_sync_loses_to_it(store) -> None:
    now = datetime.now(UTC)
    p = point(A, "p1", conn=["old"])
    await store.upsert_points("records", [p], computed_at=now)
    await store.set_payload("records", {"connectorIds": ["synced"]}, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=30))
    await store.upsert_points("records", [point(A, "p1", conn=["stale"])], computed_at=now + timedelta(seconds=10))     # read before the sync wrote
    assert [tuple(r) for r in await sql(store, MEMBERS, A, "r1")] == [(1, "synced")]


STAMP = {"scopeStamp": "union", "pathIds": ["s1", "p1"], "aclScope": ["s1"], "scopeFieldsVersion": 5, "scopeGeneration": 3}
SCOPE_ROW = ("SELECT path_ids, acl_scope, scope_stamp, scope_version, scope_generation FROM retrieval.point_groups "
             "WHERE org_id = $1 AND group_key = 'r1'")
MEMBERS_OF_R1 = "SELECT kind, member_id FROM retrieval.group_members WHERE org_id = $1 AND group_key = 'r1' ORDER BY 1, 2"


async def test_the_scope_fields_ride_with_the_group_and_are_read_back_only_when_it_was_stamped(store) -> None:
    stamped = point(A, "p1", conn=["c1"])
    stamped.payload.update(STAMP)
    await store.upsert_points("records", [stamped, point(A, "p2")])                       # one record, two points, one carries the stamp
    row = (await sql(store, SCOPE_ROW, A))[0]
    assert (list(row[0]), list(row[1]), row[2], row[3], row[4]) == (["s1", "p1"], ["s1"], "union", 5, 3)
    assert [tuple(r) for r in await sql(store, MEMBERS_OF_R1, A)] == [(1, "c1"), (4, "p1"), (4, "s1"), (5, "s1")]
    assert "scopeStamp" not in (await sql(store, "SELECT payload FROM retrieval.point_text WHERE org_id = $1 LIMIT 1", A))[0][0]
    await store.upsert_points("records", [point(B, "q1", vrid="r9")])                     # a group nobody stamped
    with org_context.bind(org_context.OrgContext(A, None, "service", "t", "-")):
        got = await store.retrieve_points("records", [pid(f"{A}/p2")])                    # the other point of the stamped group reads it too
        assert got[0].payload["scopeStamp"] == "union" and got[0].payload["pathIds"] == ["s1", "p1"] and got[0].payload["scopeGeneration"] == 3
    with org_context.bind(org_context.OrgContext(B, None, "service", "t", "-")):
        plain = (await store.retrieve_points("records", [pid(f"{B}/q1")]))[0].payload
        assert not {"scopeStamp", "pathIds", "aclScope", "scopeFieldsVersion", "scopeGeneration"} & set(plain)    # never stamped: no keys, not "container"


async def test_a_payload_merge_replaces_the_stamp_whole_behind_the_fence_and_a_membership_write_leaves_it(store) -> None:
    now = datetime.now(UTC)
    first = point(A, "p1", conn=["c1"])
    first.payload.update(STAMP)
    await store.upsert_points("records", [first], computed_at=now)
    newer = {"scopeStamp": "container", "pathIds": [], "aclScope": [], "scopeFieldsVersion": 5, "scopeGeneration": 4}
    await store.set_payload("records", newer, flt(A, virtualRecordId="r1"), computed_at=now - timedelta(seconds=5))     # an earlier read loses
    assert (await sql(store, SCOPE_ROW, A))[0][2] == "union"
    await store.set_payload("records", newer, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=5))
    row = (await sql(store, SCOPE_ROW, A))[0]
    assert (list(row[0]), list(row[1]), row[2], row[4]) == ([], [], "container", 4)
    assert [tuple(r) for r in await sql(store, MEMBERS_OF_R1, A)] == [(1, "c1")]          # the old path and scope members are gone with the lists
    await store.set_payload("records", {"connectorIds": ["c2"]}, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=9))
    assert (await sql(store, SCOPE_ROW, A))[0][2] == "container"                           # a write that carries no stamp leaves it


async def test_an_upsert_that_carries_a_stamp_but_no_membership_leaves_an_existing_group_alone_and_a_merge_writes_it(store) -> None:
    """On purpose: an upsert with no membership never replaces a group row that exists (it must not erase what the membership sync wrote),
    so a stamp that rides with it is not written either; a payload merge states what it carries and is written."""
    now = datetime.now(UTC)
    await store.upsert_points("records", [point(A, "p1", conn=["c1"])], computed_at=now)
    stamp_only = point(A, "p2")
    stamp_only.payload.update(STAMP)
    await store.upsert_points("records", [stamp_only], computed_at=now + timedelta(seconds=5))
    assert (await sql(store, SCOPE_ROW, A))[0][2] is None
    await store.set_payload("records", STAMP, flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=9))
    assert (await sql(store, SCOPE_ROW, A))[0][2] == "union"


async def test_a_malformed_stamp_is_refused_and_nothing_of_the_write_is_stored(store) -> None:
    bad = point(A, "p1", conn=["c1"])
    bad.payload.update({**STAMP, "scopeFieldsVersion": True})
    with pytest.raises(PgVectorError, match="scopeFieldsVersion"):
        await store.upsert_points("records", [bad])
    assert await sql(store, "SELECT 1 FROM retrieval.point_groups WHERE org_id = $1", A) == []
    await store.upsert_points("records", [point(A, "p1", conn=["c1"])])
    with pytest.raises(PgVectorError, match="pathIds"):
        await store.set_payload("records", {**STAMP, "pathIds": {"a": 1}}, flt(A, virtualRecordId="r1"))
    assert (await sql(store, SCOPE_ROW, A))[0][2] is None


def scope_flt(org: str, key: str, *values: object) -> FilterExpression:
    return FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org), FieldCondition(key=key, values=list(values))])


async def test_a_filter_on_a_scope_field_matches_the_group_that_holds_it_in_scroll_update_and_delete(store) -> None:
    stamped = point(A, "p1", conn=["c1"])
    stamped.payload.update(STAMP)
    other = point(A, "p2", vrid="r2")                                                       # never stamped
    await store.upsert_points("records", [stamped, point(A, "p3"), other])
    await store.upsert_points("records", [point(B, "q1")])                                    # p3 is the same record as p1: it has no stamp of its own
    for key, hit, miss in (("scopeStamp", "union", "container"), ("scopeFieldsVersion", 5, 4), ("scopeGeneration", 3, 2),
                           ("pathIds", "p1", "zz"), ("aclScope", "s1", "zz")):
        got = await store.scroll("records", scope_flt(A, key, hit), 10)
        assert sorted(p.id for p in got.points) == sorted([pid(f"{A}/p1"), pid(f"{A}/p3")]), key      # both points of the stamped record, none of the others
        assert (await store.scroll("records", scope_flt(A, key, miss), 10)).points == [], key
    await store.set_payload("records", {"tag": "x"}, scope_flt(A, "scopeStamp", "union"))
    tagged = await sql(store, "SELECT p.block_id FROM retrieval.point_text pt JOIN retrieval.points p USING (org_id, point_sk) "
                              "WHERE pt.org_id = $1 AND pt.payload ? 'tag' ORDER BY 1", A)
    assert [r[0] for r in tagged] == ["p1", "p3"]
    await store.delete_points("records", scope_flt(A, "scopeStamp", "union"))
    assert await live(store, A) == 1 and await live(store, B) == 1                      # the unstamped record and the other org are left


async def test_a_search_does_not_take_a_scope_list_as_an_arm(store) -> None:
    await store.upsert_points("records", [point(A, "p1")])
    req = HybridSearchRequest(dense_query=[1.0] + [0.0] * (DIM - 1), filter=FilterExpression(
        must=[FieldCondition(key="metadata.orgId", value=A)], should=[FieldCondition(key="pathIds", values=["s1"])]), limit=5)
    with pytest.raises(PgVectorUnsupportedError, match="pathIds"):
        await store.query_nearest_points("records", [req])


async def test_scope_keys_left_in_a_stored_payload_are_not_read_as_the_groups_stamp(store) -> None:
    await store.upsert_points("records", [point(A, "p1")])                                 # a group nobody stamped
    await sql(store, "UPDATE retrieval.point_text SET payload = payload || '{\"scopeStamp\": \"container\", \"pathIds\": [\"old\"], \"scopeFieldsVersion\": 5}'::jsonb "
                     "WHERE org_id = $1", A)                                              # a point written before the fields moved to the group
    with org_context.bind(org_context.OrgContext(A, None, "service", "t", "-")):
        got = (await store.retrieve_points("records", [pid(f"{A}/p1")]))[0].payload
        assert not {"scopeStamp", "pathIds", "aclScope", "scopeFieldsVersion", "scopeGeneration"} & set(got)
        scrolled = (await store.scroll("records", flt(A), 10)).points[0].payload
        assert not {"scopeStamp", "pathIds", "aclScope", "scopeFieldsVersion", "scopeGeneration"} & set(scrolled)


def tagged(store: PgVectorService) -> object:
    return sql(store, "SELECT p.block_id FROM retrieval.point_text pt JOIN retrieval.points p USING (org_id, point_sk) "
                      "WHERE pt.org_id = $1 AND pt.payload ? 'tag' ORDER BY 1", A)


async def test_an_empty_scope_list_is_a_stamped_group_never_an_unstamped_one(store) -> None:
    stamped = point(A, "p1", conn=["c1"])
    stamped.payload.update({"scopeStamp": "container", "pathIds": [], "aclScope": [], "scopeFieldsVersion": 5, "scopeGeneration": 3})
    await store.upsert_points("records", [stamped, point(A, "p2", vrid="r2", conn=["c1"])])        # r2 is never stamped
    for key in ("pathIds", "aclScope"):
        none = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key=key, values_count_lte=0)])
        assert [p.id for p in (await store.scroll("records", none, 10)).points] == [pid(f"{A}/p1")], key
    gone = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="connectorIds", values=["c1"]),
                                  FieldCondition(key="pathIds", values_count_lte=0)])
    await store.delete_points("records", gone)
    assert [p.id for p in (await store.scroll("records", flt(A), 10)).points] == [pid(f"{A}/p2")]                      # the unstamped group stays


async def test_an_update_chooses_its_points_before_it_changes_what_the_filter_reads(store) -> None:
    now = datetime.now(UTC)
    first = point(A, "p1", conn=["c1"])
    first.payload.update(STAMP)
    await store.upsert_points("records", [first, point(A, "p2")], computed_at=now)         # one record, two points, stamped "union"
    container = {"scopeStamp": "container", "pathIds": [], "aclScope": [], "scopeFieldsVersion": 5, "scopeGeneration": 3}
    await store.set_payload("records", {**container, "tag": "x"}, scope_flt(A, "scopeStamp", "union"), computed_at=now + timedelta(seconds=5))
    assert (await sql(store, SCOPE_ROW, A))[0][2] == "container"
    assert [r[0] for r in await tagged(store)] == ["p1", "p2"]                             # the payload reached the points the filter named
    await store.set_payload("records", {"scopeStamp": "union", "pathIds": ["s1"], "aclScope": [], "scopeFieldsVersion": 5, "scopeGeneration": 3},
                            flt(A, virtualRecordId="r1"), computed_at=now + timedelta(seconds=9))
    await store.set_payload("records", {**container, "tag": "y"}, scope_flt(A, "pathIds", "s1"), computed_at=now + timedelta(seconds=15))
    assert list((await sql(store, SCOPE_ROW, A))[0][0]) == []                              # a filter on the list the update empties
    assert [r[0] for r in await sql(store, "SELECT p.block_id FROM retrieval.point_text pt JOIN retrieval.points p USING (org_id, point_sk) "
                                           "WHERE pt.org_id = $1 AND pt.payload ->> 'tag' = 'y' ORDER BY 1", A)] == ["p1", "p2"]
    await store.overwrite_payload("records", {"tag": "z", **container}, scope_flt(A, "scopeStamp", "container"))
    assert [r[0] for r in await sql(store, "SELECT p.block_id FROM retrieval.point_text pt JOIN retrieval.points p USING (org_id, point_sk) "
                                           "WHERE pt.org_id = $1 AND pt.payload ->> 'tag' = 'z' ORDER BY 1", A)] == ["p1", "p2"]
