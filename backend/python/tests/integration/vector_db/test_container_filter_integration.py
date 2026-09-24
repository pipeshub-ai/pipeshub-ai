"""Container-scoped permission filtering, against a real vector store.

Everything else about this filter can be unit-tested, but not the thing that
matters most: whether a provider actually *enforces* `must` AND at-least-one
`should`. That is server-side semantics. A provider that treated the should
clauses as scoring hints — which is exactly OpenSearch's default when a `must`
clause is present — would return every document in the org, and every unit test
would still pass because the generated query looks right.

So the load-bearing assertion here is the negative one: a point matching `orgId`
but none of the container clauses must not come back.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_container_filter_integration.py -m integration --timeout=120
"""

import pytest

from app.services.vector_db.models import HybridSearchRequest, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    DIM,
    make_collection_config,
    make_dense,
    point_id,
)

# `loop_scope="module"` is not optional here: the service fixtures in conftest
# are module-scoped and their clients bind to the loop they were created on.
# Without it, Redis fails with "attached to a different loop".
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-container"

# `point_id` is the suite's one Qdrant-safe id scheme, shared with the other
# modules in this directory. These tests additionally assert on the readable
# name, so the mapping is kept to translate the ids back.
_NAME_BY_ID: dict[str, str] = {}


def _id(name: str) -> str:
    pid = point_id(name)
    _NAME_BY_ID[pid] = name
    return pid


def _name(pid: object) -> str:
    return _NAME_BY_ID.get(str(pid), str(pid))


def _point(pid: str, vrid: str, *, connector_ids=(), record_group_ids=()) -> VectorPoint:
    """A point carrying the membership arrays the container filter reads.

    These are top-level payload siblings of `metadata`, not nested inside it —
    the distinction `canonical_filter_key` exists to preserve.
    """
    return VectorPoint(
        id=pid,
        dense_vector=make_dense([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        payload={
            "page_content": f"content for {pid}",
            "metadata": {"orgId": ORG, "virtualRecordId": vrid},
            "connectorIds": list(connector_ids),
            "recordGroupIds": list(record_group_ids),
        },
    )


class _ContainerFilterTests:
    """Subclasses inject `vector_service` from a module-scoped provider fixture."""

    #: What this backend needs for a write to be visible to the next read.
    #: Only OpenSearch takes a `refresh` argument today; the others index
    #: synchronously and would reject the keyword.
    upsert_kwargs: dict = {}

    async def _upsert(self, vector_service, col, points) -> None:
        await vector_service.upsert_points(col, points, **self.upsert_kwargs)

    async def _seeded(self, vector_service, col):
        await vector_service.create_collection(col, make_collection_config())
        await self._upsert(
            vector_service,
            col,
            [
                _point(_id("by-connector"), "v-conn", connector_ids=["conn-A"]),
                _point(_id("by-group"), "v-group", record_group_ids=["rg-A"]),
                _point(_id("by-direct"), "v-direct"),
                # Same org, no container the user reaches. This is the point the
                # filter must exclude, and the only one that proves `should` is
                # enforced rather than merely scored.
                _point(_id("unreachable"), "v-none", connector_ids=["conn-Z"],
                       record_group_ids=["rg-Z"]),
            ],
        )

    async def _search(self, vector_service, col, flt, limit=10):
        results = await vector_service.query_nearest_points(
            col,
            [HybridSearchRequest(dense_query=make_dense([1.0]), filter=flt, limit=limit)],
        )
        return {_name(p.id) for p in results[0]}

    async def test_should_clauses_are_enforced_not_merely_scored(self, vector_service):
        col = make_collection(vector_service.get_service_name())
        try:
            await self._seeded(vector_service, col)
            flt = await vector_service.filter_collection(
                must={"orgId": ORG},
                should={
                    "connectorIds": ["conn-A"],
                    "recordGroupIds": ["rg-A"],
                    "virtualRecordId": ["v-direct"],
                },
            )
            found = await self._search(vector_service, col, flt)
            assert "unreachable" not in found, (
                "a point matching orgId but no container clause came back — "
                "this provider is treating `should` as score-only"
            )
            assert found == {"by-connector", "by-group", "by-direct"}
        finally:
            await vector_service.delete_collection(col)

    async def test_each_clause_admits_on_its_own(self, vector_service):
        """One populated bucket must still exclude everything else, which is the
        day-one shape while most groups are unclassified."""
        col = make_collection(vector_service.get_service_name())
        try:
            await self._seeded(vector_service, col)
            flt = await vector_service.filter_collection(
                must={"orgId": ORG}, should={"recordGroupIds": ["rg-A"]}
            )
            assert await self._search(vector_service, col, flt) == {"by-group"}
        finally:
            await vector_service.delete_collection(col)

    async def test_tool_ids_narrow_within_the_containers(self, vector_service):
        """The tool path adds `virtualRecordId` to `must`. It narrows; the
        containers in `should` still authorise."""
        col = make_collection(vector_service.get_service_name())
        try:
            await self._seeded(vector_service, col)
            flt = await vector_service.filter_collection(
                must={"orgId": ORG, "virtualRecordId": ["v-conn"]},
                should={"connectorIds": ["conn-A"], "recordGroupIds": ["rg-A"]},
            )
            assert await self._search(vector_service, col, flt) == {"by-connector"}
        finally:
            await vector_service.delete_collection(col)

    async def test_a_tool_id_outside_the_containers_is_denied(self, vector_service):
        """Narrowing must not become authorising: a tool may name a document the
        user cannot reach, and the container clauses have to still say no."""
        col = make_collection(vector_service.get_service_name())
        try:
            await self._seeded(vector_service, col)
            flt = await vector_service.filter_collection(
                must={"orgId": ORG, "virtualRecordId": ["v-none"]},
                should={"connectorIds": ["conn-A"], "recordGroupIds": ["rg-A"]},
            )
            assert await self._search(vector_service, col, flt) == set()
        finally:
            await vector_service.delete_collection(col)

    async def test_array_membership_matches_on_any_element(self, vector_service):
        """`connectorIds` is an array — a point belongs if any element matches,
        which is what makes a deduplicated record reachable through either of
        the connectors that carry it."""
        col = make_collection(vector_service.get_service_name())
        try:
            await vector_service.create_collection(col, make_collection_config())
            await self._upsert(
                vector_service,
                col,
                [_point(_id("multi"), "v-multi", connector_ids=["conn-A", "conn-B"])],
            )
            flt = await vector_service.filter_collection(
                must={"orgId": ORG}, should={"connectorIds": ["conn-B"]}
            )
            assert await self._search(vector_service, col, flt) == {"multi"}
        finally:
            await vector_service.delete_collection(col)


class TestQdrantContainerFilter(_ContainerFilterTests):
    @pytest.fixture
    async def vector_service(self, qdrant_service):
        return qdrant_service


class TestOpenSearchContainerFilter(_ContainerFilterTests):
    # The index is created with refresh_interval=30s, so a write is not
    # searchable on return unless the caller asks for it.
    upsert_kwargs = {"refresh": True}

    @pytest.fixture
    async def vector_service(self, opensearch_service):
        return opensearch_service


class TestRedisContainerFilter(_ContainerFilterTests):
    @pytest.fixture
    async def vector_service(self, redis_service):
        return redis_service
