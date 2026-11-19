"""A write scoped to a subset of a virtual record's points stays inside it on pgvector (the same world as the other stores).

The scope's field is a payload key here, not a promoted column: every scoped filter also names the record's
``virtualRecordId`` (a promoted, indexed column), so a scoped delete reads one record's rows and never scans the org.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_scoped_delete.py -m integration
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.org_context import for_org
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.support import scoped_points_world as world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


@pytest.fixture
async def store(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    yield svc
    await svc.disconnect()


async def test_a_scoped_delete_removes_only_its_scope(store) -> None:
    with for_org(world.ORG, job="scoped-delete-test"):
        await world.seed(store, "records")
        assert await world.census(store, "records") == world.FULL
        await world.store_over(store).delete_embeddings(world.V, "records", org_id=world.ORG, scope=world.FIRST)
        assert await world.census(store, "records") == {"first": 0, "second": 3, "other": 3}


async def test_a_scoped_delete_by_block_removes_only_that_block_of_its_scope(store) -> None:
    with for_org(world.ORG, job="scoped-delete-test"):
        await world.seed(store, "records")
        await world.store_over(store).delete_blocks_by_ids({"b0"}, world.V, "records", org_id=world.ORG, scope=world.SECOND)
        assert await world.census(store, "records") == {"first": 3, "second": 2, "other": 3}


async def test_a_whole_record_delete_leaves_other_records_alone(store) -> None:
    with for_org(world.ORG, job="scoped-delete-test"):
        await world.seed(store, "records")
        await world.store_over(store).delete_embeddings(world.OTHER, "records", org_id=world.ORG, scope=world.WHOLE)
        assert await world.census(store, "records") == {"first": 3, "second": 3, "other": 0}
