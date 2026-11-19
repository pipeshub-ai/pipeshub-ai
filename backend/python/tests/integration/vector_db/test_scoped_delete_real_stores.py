"""A write scoped to a subset of a virtual record's points stays inside it on every vector store (one world for every store).

Qdrant, OpenSearch and Redis here; pgvector in test_pgvector_scoped_delete.py (it needs its own PostgreSQL).
Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_scoped_delete_real_stores.py -m integration --timeout=300
"""
from __future__ import annotations

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from tests.integration.vector_db.conftest import make_collection
from tests.support import scoped_points_world as world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300)]


class _ScopedDeleteContract:
    async def test_a_scoped_delete_removes_only_its_scope(self, vector_service) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_scoped")
        try:
            await world.fresh(vector_service, col)
            await world.store_over(vector_service).delete_embeddings(world.V, col, org_id=world.ORG, scope=world.FIRST)
            assert await world.census(vector_service, col) == {"first": 0, "second": 3, "other": 3}
        finally:
            await vector_service.delete_collection(col)

    async def test_a_scoped_delete_by_block_removes_only_that_block_of_its_scope(self, vector_service) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_scopedb")
        try:
            await world.fresh(vector_service, col)
            await world.store_over(vector_service).delete_blocks_by_ids({"b0"}, world.V, col, org_id=world.ORG, scope=world.SECOND)
            assert await world.census(vector_service, col) == {"first": 3, "second": 2, "other": 3}
        finally:
            await vector_service.delete_collection(col)

    async def test_a_whole_record_delete_leaves_other_records_alone(self, vector_service) -> None:
        col = make_collection(f"{vector_service.get_service_name()}_whole")
        try:
            await world.fresh(vector_service, col)
            await world.store_over(vector_service).delete_embeddings(world.OTHER, col, org_id=world.ORG, scope=world.WHOLE)
            assert await world.census(vector_service, col) == {"first": 3, "second": 3, "other": 0}
        finally:
            await vector_service.delete_collection(col)

    async def test_the_default_store_answer_is_that_every_field_is_filterable(self, vector_service) -> None:
        flt = await vector_service.filter_collection(must={"orgId": world.ORG, world.GROUP_FIELD: "one"})
        assert await vector_service.unindexed_filter_fields("any", flt) == []


class TestQdrantScopedDelete(_ScopedDeleteContract):
    @pytest.fixture
    async def vector_service(self, qdrant_service) -> object:
        return qdrant_service


class TestOpenSearchScopedDelete(_ScopedDeleteContract):
    @pytest.fixture
    async def vector_service(self, opensearch_service) -> object:
        return opensearch_service


class TestRedisScopedDelete(_ScopedDeleteContract):
    @pytest.fixture
    async def vector_service(self, redis_service) -> object:
        return redis_service

    async def test_the_default_store_answer_is_that_every_field_is_filterable(self, vector_service) -> None:
        """Redis filters only on the fields of its index: this one reads that index, so the answer follows what was indexed."""
        col = make_collection("redis_fields")
        try:
            await world.fresh(vector_service, col)
            flt = await vector_service.filter_collection(must={"orgId": world.ORG, world.GROUP_FIELD: "one"})
            assert await vector_service.unindexed_filter_fields(col, flt) == []
        finally:
            await vector_service.delete_collection(col)

    async def test_a_scoped_delete_by_block_removes_only_that_block_of_its_scope(self, vector_service) -> None:
        """Redis filters only on the fields of its index. On an index that lacks the block id (one created before it was added), a
        delete that must stay inside a scope and also names blocks would widen to every block, so it is refused and removes nothing."""
        col = make_collection("redis_blockid")
        try:
            await world.fresh(vector_service, col, index_block=False)
            with pytest.raises(VectorStoreError):
                await world.store_over(vector_service).delete_blocks_by_ids({"b0"}, world.V, col, org_id=world.ORG, scope=world.SECOND)
            assert await world.census(vector_service, col) == world.FULL
        finally:
            await vector_service.delete_collection(col)

    async def test_a_scoped_delete_on_an_index_without_the_scope_field_is_refused_and_removes_nothing(self, vector_service) -> None:
        col = make_collection("redis_blind")
        try:
            await world.fresh(vector_service, col, index_group=False)
            flt = await vector_service.filter_collection(must={"orgId": world.ORG, **world.FIRST.must()})
            assert await vector_service.unindexed_filter_fields(col, flt) == [world.GROUP_PATH]
            with pytest.raises(VectorStoreError):
                await world.store_over(vector_service).delete_embeddings(world.V, col, org_id=world.ORG, scope=world.FIRST)
            # The index cannot tell the scopes apart, so the census would widen too: count the record as a whole.
            whole = await vector_service.filter_collection(must={"orgId": world.ORG, "virtualRecordId": world.V})
            assert await vector_service.count_points(col, whole) == 6, "a refused delete removes nothing"
        finally:
            await vector_service.delete_collection(col)
