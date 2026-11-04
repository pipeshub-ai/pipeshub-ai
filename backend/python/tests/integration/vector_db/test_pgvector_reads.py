"""The pgvector provider's scroll and retrieve against the migrated schema, through PgBouncer; native and forced HNSW safe mode.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_reads.py -m integration
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.services.vector_db.models import FieldCondition, FilterExpression, VectorPoint
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionNotFoundError,
    OrgMismatch,
    OrgRequired,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
A, B, C = "a" * 24, "b" * 24, "c" * 24
DIM = 4


def pid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pgvector-reads/{name}"))


def point(org: str, name: str, vrid: str = "r1", *, conn: list[str] | None = None, groups: list[str] | None = None, **meta: str) -> VectorPoint:
    payload: dict = {"page_content": f"text {name}", "metadata": {"orgId": org, "virtualRecordId": vrid, "blockId": name, "embeddingModel": "p:m:4", **meta}}
    if conn is not None:
        payload["connectorIds"], payload["recordGroupIds"] = conn, groups or []
    return VectorPoint(id=pid(f"{org}/{name}"), dense_vector=[1.0, 0.0, 0.0, 0.0], payload=payload)


def flt(org: str, *, should: dict[str, list[str]] | None = None, **must: str) -> FilterExpression:
    return FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org), *[FieldCondition(key=f"metadata.{k}", value=v) for k, v in must.items()]],
                            should=[FieldCondition(key=k, values=v) for k, v in (should or {}).items()])


@pytest.fixture(params=[False, True], ids=["native", "safe"])
async def store(request, env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    if request.param:
        svc.pg_capabilities = dataclasses.replace(svc.pg_capabilities, hnsw_safe_mode=True)
    yield svc
    await svc.disconnect()


async def scroll_all(store: PgVectorService, expr: FilterExpression, limit: int = 3, **kw: object) -> list[VectorPoint]:
    out, offset = [], None
    while True:
        page = await store.scroll("records", expr, limit, offset, **kw)
        out += page.points
        if page.next_offset is None:
            return out
        offset = page.next_offset


async def test_scroll_pages_through_every_point_once_with_the_payload_that_was_written(store) -> None:
    await store.upsert_points("records", [point(A, f"p{n}", conn=["c1"], groups=["g1"]) for n in range(7)])
    await store.upsert_points("records", [point(B, "other")])
    got = await scroll_all(store, flt(A))
    assert sorted(p.payload["metadata"]["blockId"] for p in got) == [f"p{n}" for n in range(7)] and len({p.id for p in got}) == 7
    p0 = next(p for p in got if p.payload["metadata"]["blockId"] == "p0")
    assert p0.id == pid(f"{A}/p0") and p0.payload["page_content"] == "text p0"
    assert (p0.payload["connectorIds"], p0.payload["recordGroupIds"], p0.payload["rootRecordGroupIds"]) == (["c1"], ["g1"], [])
    only = await scroll_all(store, flt(A), with_payload=["metadata.blockId", "connectorIds"])
    assert all(set(p.payload) == {"metadata", "connectorIds"} and set(p.payload["metadata"]) == {"blockId"} for p in only)
    assert [p.payload["metadata"]["blockId"] for p in await scroll_all(store, flt(B))] == ["other"]            # the other org's points are not here


async def test_a_page_that_ends_exactly_has_no_next_offset_and_an_offset_belongs_to_its_org(store) -> None:
    await store.upsert_points("records", [point(A, f"p{n}") for n in range(4)])
    await store.upsert_points("records", [point(B, "x")])
    page = await store.scroll("records", flt(A), 4)
    assert len(page.points) == 4 and page.next_offset is None
    page = await store.scroll("records", flt(A), 2)
    assert len(page.points) == 2 and page.next_offset
    with pytest.raises(OrgMismatch):
        await store.scroll("records", flt(B), 2, page.next_offset)
    with pytest.raises(ValueError, match="offset"):
        await store.scroll("records", flt(A), 2, "garbage")
    forged = base64.urlsafe_b64encode(hashlib.sha256(A.encode()).digest()[:8] + (2**64 - 1).to_bytes(8, "big")).decode()
    with pytest.raises(ValueError, match="offset"):                                            # the right organisation, a number no bigint holds
        await store.scroll("records", flt(A), 2, forged)
    with pytest.raises(ValueError, match="limit"):
        await store.scroll("records", flt(A), 10**9)
    full = await store.scroll("records", flt(A), 1, None, [])                                  # an empty list of paths is the whole payload, as on Qdrant
    assert full.points[0].payload["page_content"].startswith("text") and full.points[0].payload["metadata"]["orgId"] == A


async def test_filters_select_by_column_by_membership_and_an_empty_list_selects_nothing(store) -> None:
    await store.upsert_points("records", [point(A, "p1", "r1", conn=["c1"]), point(A, "p2", "r2", conn=["c2"]), point(A, "p3", "r3", conn=["c1", "c2"])])   # membership is per record
    assert {p.payload["metadata"]["blockId"] for p in await scroll_all(store, flt(A, virtualRecordId="r2"))} == {"p2"}
    by_conn = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="connectorIds", values=["c1"])])
    assert {p.payload["metadata"]["blockId"] for p in await scroll_all(store, by_conn)} == {"p1", "p3"}
    nobody = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A), FieldCondition(key="connectorIds", values=[])])
    assert await scroll_all(store, nobody) == []                                                         # an empty accessible list fails closed
    nobody_id = flt(A, virtualRecordId="no-such-record")
    assert await scroll_all(store, nobody_id) == []


async def test_empty_lists_given_to_filter_collection_are_dropped_as_on_every_provider(store) -> None:
    """The shared builder drops an empty list: callers short-circuit an empty accessible set before they search (and graph-check every hit)."""
    await store.upsert_points("records", [point(A, "p1", "r1", conn=["c1"]), point(A, "p2", "r2", conn=["c2"])])
    built = await store.filter_collection(must={"orgId": A}, should={"connectorIds": [], "recordGroupIds": []})
    assert {p.payload["metadata"]["blockId"] for p in await scroll_all(store, built)} == {"p1", "p2"}      # no arm left: the whole organisation


async def test_deleted_points_are_invisible_and_an_org_without_points_or_a_collection_is_handled(store) -> None:
    await store.upsert_points("records", [point(A, "p1"), point(A, "p2", "r2")])
    await store.delete_points("records", flt(A, virtualRecordId="r1"))
    assert [p.payload["metadata"]["blockId"] for p in await scroll_all(store, flt(A))] == ["p2"]          # tombstoned in safe mode, gone otherwise
    assert (await store.scroll("records", flt(C), 10)).points == []                                       # an org that never wrote
    with pytest.raises(CollectionNotFoundError):
        await store.scroll("nope", flt(A), 10)
    with pytest.raises(OrgRequired):
        await store.scroll("records", FilterExpression(must=[FieldCondition(key="metadata.virtualRecordId", value="r2")]), 10)


async def test_retrieve_answers_for_the_requests_org_only_in_the_order_asked(store) -> None:
    await store.upsert_points("records", [point(A, f"p{n}") for n in range(3)])
    await store.upsert_points("records", [point(B, "p0")])
    ids = [pid(f"{A}/p2"), pid(f"{A}/p0"), pid(f"{B}/p0"), "not-a-uuid", str(uuid.uuid4())]
    with pytest.raises(OrgRequired):
        await store.retrieve_points("records", ids)
    with org_context.bind(org_context.OrgContext(A, None, "service", "t", "-")):
        got = await store.retrieve_points("records", ids)
        assert [p.id for p in got] == [pid(f"{A}/p2"), pid(f"{A}/p0")]                                    # B's id and the unknown ones are left out
        assert got[0].payload["metadata"]["blockId"] == "p2" and got[0].payload["page_content"] == "text p2"
        await store.delete_points("records", flt(A, blockId="p2"))
        assert [p.id for p in await store.retrieve_points("records", ids)] == [pid(f"{A}/p0")]
        with pytest.raises(CollectionNotFoundError):
            await store.retrieve_points("nope", ids)
    with org_context.bind(org_context.OrgContext(C, None, "service", "t", "-")):
        assert await store.retrieve_points("records", ids) == []


async def test_a_real_time_read_does_not_trust_a_cached_miss_of_the_orgs_space(store, env) -> None:  # noqa: F811
    """The first point of an org written by another service must be visible at once to retrieve, scroll and the collection info, even
    though this service cached "no space" a moment before (the space list is cached for ``catalog_ttl`` seconds)."""
    with org_context.bind(org_context.OrgContext(C, None, "service", "t", "-")):
        assert await store.retrieve_points("records", [pid(f"{C}/first")]) == []                       # caches the org's empty space list
        assert (await store.scroll("records", flt(C), 10)).points == []
        assert (await store.get_collection_info("records")).dense_dimension is None
        port = env.start_bouncer()
        other = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=2, placement="none"))
        try:
            await other.upsert_points("records", [point(C, "first")])
        finally:
            await other.disconnect()
        assert [p.id for p in await store.retrieve_points("records", [pid(f"{C}/first")])] == [pid(f"{C}/first")]
        assert [p.id for p in (await store.scroll("records", flt(C), 10)).points] == [pid(f"{C}/first")]
        info = await store.get_collection_info("records")
        assert (info.dense_dimension, info.points_count) == (DIM, 1)
