"""The pgvector provider's dense search against the migrated schema, through PgBouncer; native and forced HNSW safe mode.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_search.py -m integration
"""
from __future__ import annotations

import dataclasses
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.pgvector import dense
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionNotFoundError,
    OrgRequired,
    SpaceMismatch,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
A, B, C = "a" * 24, "b" * 24, "c" * 24
Q = [1.0, 0.0, 0.0, 0.0]


def pid(org: str, n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pgvector-search/{org}/{n}"))


def point(org: str, n: int, vrid: str | None = None, *, conn: list[str] | None = None) -> VectorPoint:
    """Point ``n`` lies ``n`` steps away from the query ``Q``: the nearer, the smaller ``n``."""
    payload: dict = {"page_content": f"text {n}", "metadata": {"orgId": org, "virtualRecordId": vrid or f"r{n}", "blockId": f"b{n}", "embeddingModel": "p:m:4"}}
    if conn is not None:
        payload["connectorIds"] = conn
    return VectorPoint(id=pid(org, n), dense_vector=[1.0, n / 10, 0.0, 0.0], payload=payload)


def lexical(org: str, n: int, terms: tuple[int, ...], *, conn: list[str] | None = None) -> VectorPoint:
    p = point(org, n, conn=conn)
    return dataclasses.replace(p, sparse_vector=SparseVector(list(terms), [1.0] * len(terms)))


def lex(org: str, *terms: int, dense: bool = False, should: dict[str, list[str]] | None = None, **kw: object) -> HybridSearchRequest:
    return dataclasses.replace(req(org, should=should, **kw), dense_query=Q if dense else None, sparse_query=SparseVector(list(terms), [1.0] * len(terms)))


def req(org: str, *, should: dict[str, list[str]] | None = None, **kw: object) -> HybridSearchRequest:
    flt = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org)], should=[FieldCondition(key=k, values=v) for k, v in (should or {}).items()])
    return HybridSearchRequest(dense_query=Q, filter=flt, **kw)  # type: ignore[arg-type]


@pytest.fixture(params=[False, True], ids=["native", "safe"])
async def store(request, env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    if request.param:
        svc.pg_capabilities = dataclasses.replace(svc.pg_capabilities, hnsw_safe_mode=True)
    svc.env = env
    yield svc
    await svc.disconnect()


async def test_the_nearest_points_come_first_with_the_scores_of_a_one_leg_fusion_and_their_payload(store) -> None:
    await store.upsert_points("records", [point(A, n) for n in (5, 1, 9, 3, 7)])
    [hits] = await store.query_nearest_points("records", [req(A, limit=3)])
    assert [h.id for h in hits] == [pid(A, 1), pid(A, 3), pid(A, 5)]
    assert [h.score for h in hits] == [pytest.approx(1 / 2), pytest.approx(1 / 3), pytest.approx(1 / 4)]
    assert hits[0].payload["page_content"] == "text 1" and hits[0].payload["metadata"]["blockId"] == "b1" and all(h.degraded is None for h in hits)
    [bare] = await store.query_nearest_points("records", [req(A, limit=10, with_payload=False)])
    assert [h.id for h in bare] == [pid(A, n) for n in (1, 3, 5, 7, 9)] and all(h.payload == {} for h in bare)
    two = await store.query_nearest_points("records", [req(A, limit=1), req(A, limit=2)])
    assert [len(r) for r in two] == [1, 2]


async def test_a_search_sees_only_its_own_org(store) -> None:
    await store.upsert_points("records", [point(A, n) for n in (1, 2)])
    await store.upsert_points("records", [point(B, 1)])
    [a] = await store.query_nearest_points("records", [req(A, limit=10)])
    [b] = await store.query_nearest_points("records", [req(B, limit=10)])
    [c] = await store.query_nearest_points("records", [req(C, limit=10)])
    assert [h.id for h in a] == [pid(A, 1), pid(A, 2)] and [h.id for h in b] == [pid(B, 1)] and c == []


async def test_membership_filters_select_and_an_empty_accessible_list_finds_nothing(store) -> None:
    await store.upsert_points("records", [point(A, 1, conn=["c1"]), point(A, 2, conn=["c2"]), point(A, 3, conn=["c1", "c2"])])
    for should, want in (({"connectorIds": ["c1"]}, {1, 3}), ({"connectorIds": ["c2"]}, {2, 3}), ({"connectorIds": ["c9"]}, set()),
                         ({"connectorIds": []}, set()), ({"connectorIds": [], "recordGroupIds": [], "rootRecordGroupIds": []}, set()),
                         ({"metadata.virtualRecordId": ["r2"]}, {2}), ({"connectorIds": ["c9"], "metadata.virtualRecordId": ["r1"]}, {1})):
        [hits] = await store.query_nearest_points("records", [req(A, should=should, limit=10)])
        assert {h.id for h in hits} == {pid(A, n) for n in want}, should
    must = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="metadata.virtualRecordId", values=["r1", "r3"])])
    [hits] = await store.query_nearest_points("records", [HybridSearchRequest(dense_query=Q, filter=must, limit=10)])
    assert [h.id for h in hits] == [pid(A, 1), pid(A, 3)]
    kind = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="metadata.blockId", value="b2")])
    [hits] = await store.query_nearest_points("records", [HybridSearchRequest(dense_query=Q, filter=kind, limit=10)])
    assert [h.id for h in hits] == [pid(A, 2)]                                                           # a condition on a point column


async def test_deleted_points_are_not_found_and_the_refusals(store) -> None:
    await store.upsert_points("records", [point(A, n) for n in (1, 2, 3)])
    await store.delete_points("records", FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="metadata.virtualRecordId", value="r1")]))
    [hits] = await store.query_nearest_points("records", [req(A, limit=10)])
    assert [h.id for h in hits] == [pid(A, 2), pid(A, 3)]
    with pytest.raises(OrgRequired):
        await store.query_nearest_points("records", [HybridSearchRequest(dense_query=Q, filter=None)])
    with pytest.raises(OrgRequired):
        await store.query_nearest_points("records", [HybridSearchRequest(dense_query=Q, filter=FilterExpression(must=[FieldCondition(key="metadata.kind", value="x")]))])
    with pytest.raises(SpaceMismatch):
        await store.query_nearest_points("records", [HybridSearchRequest(dense_query=[1.0, 0.0], filter=req(A).filter)])
    with pytest.raises(CollectionNotFoundError):
        await store.query_nearest_points("nope", [req(A)])
    with pytest.raises(ValueError, match="term id"):
        await store.query_nearest_points("records", [HybridSearchRequest(dense_query=Q, sparse_query=SparseVector([2**32], [1.0]), filter=req(A).filter)])


async def test_every_tier_answers_the_same_nearest_points(store) -> None:
    await store.upsert_points("records", [point(A, n) for n in range(1, 31)])
    [exact] = await store.query_nearest_points("records", [req(A, limit=5)])
    search = dataclasses.replace(store._cfg.search, n_e0=2, n_own=1)                                       # nothing is small enough for the exact tier any more
    store._cfg = dataclasses.replace(store._cfg, search=search)
    store._sizer = dense.Sizer(store._db, search)
    [indexed] = await store.query_nearest_points("records", [req(A, limit=5)])
    assert [h.id for h in indexed] == [h.id for h in exact] == [pid(A, n) for n in range(1, 6)]


async def test_the_quantisation_check_stores_a_verdict_for_the_orgs_space(store) -> None:
    await store.upsert_points("records", [point(A, n) for n in range(1, 61)])
    assert await store.validate_space("records", C) is None                                               # an org with no space has nothing to check
    recall = await store.validate_space("records", A)
    assert recall is not None and recall >= 0.9
    conn = await store.env.admin()
    try:
        row = await conn.fetchrow("SELECT recall, samples FROM retrieval.space_checks WHERE org_id = $1", A)
    finally:
        await conn.close()
    assert row is not None and row["recall"] == pytest.approx(recall) and row["samples"] > 0


async def test_the_lexical_leg_ranks_by_terms_stays_in_its_org_and_honours_membership_and_deletes(store) -> None:
    await store.upsert_points("records", [lexical(A, 1, (10, 11, 12), conn=["c1"]), lexical(A, 2, (10, 11), conn=["c2"]), lexical(A, 3, (10,), conn=["c1"]),
                                          lexical(A, 4, (99,), conn=["c1"])])
    await store.upsert_points("records", [lexical(B, 1, (10, 11, 12))])
    [hits] = await store.query_nearest_points("records", [lex(A, 10, 11, 12, limit=10)])
    assert [h.id for h in hits] == [pid(A, 1), pid(A, 2), pid(A, 3)]                                    # more matching terms first; the term-less point is absent
    assert [h.score for h in hits] == [pytest.approx(1 / 2), pytest.approx(1 / 3), pytest.approx(1 / 4)] and hits[0].payload["page_content"] == "text 1"
    [c1] = await store.query_nearest_points("records", [lex(A, 10, 11, 12, should={"connectorIds": ["c1"]}, limit=10)])
    assert [h.id for h in c1] == [pid(A, 1), pid(A, 3)]
    [none] = await store.query_nearest_points("records", [lex(A, 10, should={"connectorIds": []}, limit=10)])
    assert none == []
    [other] = await store.query_nearest_points("records", [lex(C, 10, limit=10)])
    assert other == []
    await store.delete_points("records", FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="metadata.virtualRecordId", value="r1")]))
    [after] = await store.query_nearest_points("records", [lex(A, 10, 11, 12, limit=10)])
    assert [h.id for h in after] == [pid(A, 2), pid(A, 3)]
    [one] = await store.query_nearest_points("records", [lex(A, 10, 11, 12, limit=1)])
    assert [h.id for h in one] == [pid(A, 2)]


async def test_a_hybrid_search_fuses_the_legs_and_a_condition_on_a_point_column_filters_the_lexical_leg(store) -> None:
    await store.upsert_points("records", [lexical(A, 1, (7,)), lexical(A, 2, (7, 8)), lexical(A, 5, (8,)), lexical(A, 9, (70,))])
    # dense order 1, 2, 5, 9; lexical order (terms 7 and 8) 2, then 1 and 5 (equal scores broken by point_sk), so 1 and 2 tie and go by id
    [hits] = await store.query_nearest_points("records", [dataclasses.replace(lex(A, 7, 8, dense=True, limit=10))])
    assert {h.id for h in hits} == {pid(A, n) for n in (1, 2, 5, 9)} and {h.id for h in hits[:2]} == {pid(A, 1), pid(A, 2)}
    assert [h.score for h in hits[:2]] == [pytest.approx(1 / 2 + 1 / 3)] * 2                              # points 1 and 2 are first in one leg, second in the other: the sum
    block = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="metadata.blockId", value="b5")])
    [only] = await store.query_nearest_points("records", [HybridSearchRequest(dense_query=None, sparse_query=SparseVector([7, 8], [1.0, 1.0]), filter=block, limit=10)])
    assert [h.id for h in only] == [pid(A, 5)]                                                         # the extra condition is applied to the lexical rows
