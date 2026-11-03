"""Scope fields on every store: they ride with an upsert, a payload merge (`set_payload`) keeps or replaces them as asked, a read-back
returns them, and a point written without them carries none. Nothing reads them yet.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
"""

import inspect

import pytest

from app.services.permissions.scope_fields import SCOPE_KEYS, ScopeFields, Stamp
from app.services.vector_db.membership import STALE_SCOPE
from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    point_id,
    wait_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "ab" * 12
FIELDS = ScopeFields(Stamp.UNION, ("pg-1", "page-2"), ("page-2", "seed-3"), ("u:x",))


def _point(name: str, vrid: str, extra: dict | None = None) -> VectorPoint:
    return VectorPoint(id=point_id(name), dense_vector=make_dense([1.0]), payload={
        "page_content": name, "metadata": {"orgId": ORG, "virtualRecordId": vrid},
        "connectorIds": ["c1"], "recordGroupIds": ["g1"], "rootRecordGroupIds": [], **(extra or {})})


async def _upsert(svc, col: str, points: list[VectorPoint]) -> None:
    """OpenSearch matches a payload merge only against refreshed documents; the others have no such wait."""
    if "refresh" in inspect.signature(svc.upsert_points).parameters:
        await svc.upsert_points(col, points, refresh=True)
    else:
        await svc.upsert_points(col, points)


class _ScopeFieldsContract:
    async def _read(self, svc, col: str, *names: str) -> list[VectorPoint]:
        async def got() -> list[VectorPoint]:
            found = await svc.retrieve_points(col, [point_id(n) for n in names])
            return found if len(found) == len(names) else []
        return await wait_for(got, timeout=45.0)

    async def test_fields_ride_with_the_points_and_a_merge_replaces_them_whole(self, vector_service) -> None:
        col = make_collection(vector_service.get_service_name())
        try:
            await vector_service.create_collection(col, make_collection_config())
            await _upsert(vector_service, col, [_point("a", "v1", FIELDS.to_payload()), _point("b", "v2")])
            a, b = await self._read(vector_service, col, "a", "b")
            assert ScopeFields.from_payload(a.payload) == FIELDS
            assert not set(SCOPE_KEYS) & set(b.payload), "a point nobody stamped carries no scope key"
            flt = await vector_service.filter_collection(must={"orgId": ORG, "virtualRecordId": "v1"})
            await vector_service.set_payload(col, ScopeFields.container().to_payload(), flt, refresh=True)
            a2, b2 = await self._read(vector_service, col, "a", "b")
            assert ScopeFields.from_payload(a2.payload) == ScopeFields.container()  # the old ids are cleared, not merged
            assert not set(SCOPE_KEYS) & set(b2.payload), "a merge reaches only the points it matched"
            assert a2.payload["connectorIds"] == ["c1"]
            await vector_service.set_payload(col, STALE_SCOPE.to_payload(), flt, refresh=True)
            assert ScopeFields.from_payload((await self._read(vector_service, col, "a"))[0].payload) == STALE_SCOPE
            await vector_service.set_payload(col, {"connectorIds": ["c2"]}, flt, refresh=True)
            kept = (await self._read(vector_service, col, "a"))[0].payload
            assert kept["connectorIds"] == ["c2"] and ScopeFields.from_payload(kept) == STALE_SCOPE  # a membership-only merge keeps them
        finally:
            await vector_service.delete_collection(col)


class TestQdrantScopeFields(_ScopeFieldsContract):
    @pytest.fixture
    async def vector_service(self, qdrant_service):  # noqa: ANN201
        return qdrant_service


class TestOpenSearchScopeFields(_ScopeFieldsContract):
    @pytest.fixture
    async def vector_service(self, opensearch_service):  # noqa: ANN201
        return opensearch_service

    async def test_a_new_index_maps_the_scope_fields_as_keyword(self, opensearch_service) -> None:
        col = make_collection("opensearch")
        try:
            await opensearch_service.create_collection(col, make_collection_config())
            await _upsert(opensearch_service, col, [_point("a", "v1", FIELDS.to_payload())])
            props = (await opensearch_service.client.indices.get_mapping(index=col))[col]["mappings"]["properties"]
            assert all(props[f]["type"] == "keyword" for f in ("pathIds", "aclScope", "grantTokens", "scopeStamp"))
        finally:
            await opensearch_service.delete_collection(col)


class TestRedisScopeFields(_ScopeFieldsContract):
    @pytest.fixture
    async def vector_service(self, redis_service):  # noqa: ANN201
        return redis_service
