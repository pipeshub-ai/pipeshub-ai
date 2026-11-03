"""
Shared integration contract tests for all vector DB providers.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_contract_integration.py -m integration --timeout=120
"""

import pytest

from app.services.vector_db.models import HybridSearchRequest, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    DIM,
    make_collection_config,
    make_dense,
    point_id,
    sample_points,
    wait_for,
)

# loop_scope matches the module-scoped provider fixtures in conftest.
# Without it each test gets its own loop and the shared client raises
# "Event loop is closed" on first use.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# OpenSearch indexes are created with a 30s refresh interval, so a write can
# take that long to become searchable; the other backends answer at once.
VISIBILITY_TIMEOUT = 45.0


async def _is(read, expected) -> bool:
    return await read() == expected


class _VectorDBContractTests:
    """Provider-agnostic contract. Subclasses inject `vector_service` from a
    module-scoped fixture (qdrant_service / opensearch_service / redis_service)
    so pytest-asyncio never has to `getfixturevalue` a module fixture from a
    running test loop.
    """

    async def test_health_check_passes(self, vector_service):
        from app.services.vector_db.models import HealthStatus

        health = await vector_service.health_check()
        assert health.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

    async def test_upsert_and_dense_query(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(provider)
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            points = sample_points("org-contract")
            await vector_service.upsert_points(col, points)
            flt = await vector_service.filter_collection(must={"orgId": "org-contract"})

            async def query() -> list:
                return (await vector_service.query_nearest_points(
                    col,
                    [HybridSearchRequest(dense_query=make_dense([1.0]), filter=flt, limit=3)],
                ))[0]

            results = await wait_for(query, timeout=VISIBILITY_TIMEOUT)
            assert len(results) >= 1
        finally:
            await vector_service.delete_collection(col)

    async def test_delete_points(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_del")
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            points = sample_points("org-del")
            await vector_service.upsert_points(col, points)
            flt = await vector_service.filter_collection(must={"orgId": "org-del"})

            async def points_count_is(n: int) -> bool:
                return (await vector_service.get_collection_info(col)).points_count == n

            # Deleting before the points are searchable would match nothing on
            # OpenSearch and the zero count below would prove nothing.
            await wait_for(lambda: points_count_is(len(points)), timeout=VISIBILITY_TIMEOUT)
            await vector_service.delete_points(col, flt)
            await wait_for(lambda: points_count_is(0), timeout=VISIBILITY_TIMEOUT)
        finally:
            await vector_service.delete_collection(col)

    async def test_a_write_for_one_org_leaves_the_other_orgs_points_alone(self, vector_service) -> None:
        """The same virtual record id under two orgs: ids, filters and deletes of org A never reach org B."""
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_orgs")
        try:
            await vector_service.create_collection(col, make_collection_config())
            a_pts, b_pts = sample_points("org-a"), sample_points("org-b")
            for pt in b_pts:
                pt.id = point_id("b-" + str(pt.id))
            await vector_service.upsert_points(col, a_pts + b_pts)
            ids = [p.id for p in a_pts + b_pts]

            async def count() -> int:
                return (await vector_service.get_collection_info(col)).points_count

            await wait_for(lambda: _is(count, 6), timeout=VISIBILITY_TIMEOUT)

            async def members(org: str) -> list:
                got = await vector_service.retrieve_points(col, ids)
                return [p.payload.get("connectorIds") for p in got if p.payload["metadata"]["orgId"] == org]

            await vector_service.update_payload_by_ids(col, ids, {"connectorIds": ["c1"]}, org_id="org-a")
            await wait_for(lambda: _is(lambda: members("org-a"), [["c1"]] * 3), timeout=VISIBILITY_TIMEOUT)
            assert all(v in (None, [], ()) for v in await members("org-b"))

            flt = await vector_service.filter_collection(must={"orgId": "org-a", "virtualRecordId": "r1"})
            await vector_service.set_payload(col, {"connectorIds": ["c2"]}, flt, refresh=True)
            await vector_service.delete_points(col, await vector_service.filter_collection(must={"orgId": "org-a"}))

            assert await vector_service.retrieve_points(col, [p.id for p in a_pts]) == []
            assert len(await members("org-b")) == 3
        finally:
            await vector_service.delete_collection(col)

    async def test_a_scroll_can_exclude_orgs_to_find_the_next_owner(self, vector_service) -> None:
        """The orphan sweep names the owners of a record from its points: a read that excludes the orgs found."""
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_owners")
        try:
            await vector_service.create_collection(col, make_collection_config())
            a_pts, b_pts = sample_points("org-a"), sample_points("org-b")
            for pt in b_pts:
                pt.id = point_id("b-" + str(pt.id))
            await vector_service.upsert_points(col, a_pts + b_pts)

            async def owner_after(excluded: list[str]) -> list[str]:
                flt = await vector_service.filter_collection(
                    must={"virtualRecordId": "r1"}, **({"must_not": {"orgId": excluded}} if excluded else {}),
                )
                page = await vector_service.scroll(col, flt, limit=5, with_payload=["metadata.orgId"])
                return sorted({p.payload["metadata"]["orgId"] for p in page.points})

            async def both_seen() -> bool:
                return await owner_after([]) == ["org-a", "org-b"]

            await wait_for(both_seen, timeout=VISIBILITY_TIMEOUT)
            assert await owner_after(["org-a"]) == ["org-b"]
            assert await owner_after(["org-a", "org-b"]) == []
        finally:
            await vector_service.delete_collection(col)

    async def test_delete_removes_points_rewritten_just_before(self, vector_service) -> None:
        """A point rewritten right before its delete must still go on every backend."""
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_rewrite_del")
        try:
            await vector_service.create_collection(col, make_collection_config())
            points = sample_points("org-del")
            await vector_service.upsert_points(col, points)

            async def seen() -> bool:
                return (await vector_service.get_collection_info(col)).points_count == len(points)

            await wait_for(seen, timeout=VISIBILITY_TIMEOUT)
            await vector_service.upsert_points(col, points)
            await vector_service.delete_points(col, await vector_service.filter_collection(must={"orgId": "org-del"}))

            assert await vector_service.retrieve_points(col, [p.id for p in points]) == []
        finally:
            await vector_service.delete_collection(col)

    async def test_scroll_pagination(self, vector_service):
        provider = vector_service.get_service_name()
        col = make_collection(f"{provider}_scroll")
        cfg = make_collection_config()
        try:
            await vector_service.create_collection(col, cfg)
            many = [
                VectorPoint(
                    id=point_id(f"pt-{i}"),
                    # Never all zeros: cosine is undefined for a zero vector
                    # and OpenSearch rejects the document.
                    dense_vector=make_dense([float(i % DIM) + 1.0]),
                    payload={
                        "page_content": f"chunk {i}",
                        "metadata": {"orgId": "org-scroll", "virtualRecordId": "vr-scroll"},
                    },
                )
                for i in range(12)
            ]
            await vector_service.upsert_points(col, many)
            flt = await vector_service.filter_collection(must={"orgId": "org-scroll"})

            async def scroll_all_ids() -> set | None:
                collected = []
                offset = None
                while True:
                    page = await vector_service.scroll(col, flt, limit=5, offset=offset)
                    collected.extend(page.points)
                    if not page.next_offset:
                        break
                    offset = page.next_offset
                ids = {p.id for p in collected}
                return ids if len(ids) == 12 else None

            assert len(await wait_for(scroll_all_ids, timeout=VISIBILITY_TIMEOUT)) == 12
        finally:
            await vector_service.delete_collection(col)


class TestQdrantContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, qdrant_service):
        return qdrant_service


class TestOpenSearchContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, opensearch_service):
        return opensearch_service


class TestRedisContract(_VectorDBContractTests):
    @pytest.fixture
    async def vector_service(self, redis_service):
        return redis_service
