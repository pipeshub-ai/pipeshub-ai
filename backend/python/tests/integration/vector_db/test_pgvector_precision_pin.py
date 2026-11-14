"""The weight-precision pin and its tag filters on pgvector (one space per organisation, counts bound to the org).

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_precision_pin.py -m integration
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.integration.vector_db.precision_pin_scenario import (
    run_legacy_scenario,
    run_pin_scenario,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
A, B = "a" * 24, "b" * 24


@pytest.fixture
async def store(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    yield svc
    await svc.disconnect()


async def test_the_pin_is_claimed_refused_released_and_the_tags_filter(store) -> None:
    assert store.get_capabilities().spaces_per_org is True
    with org_context.for_org(A, job="test"):
        await run_pin_scenario(store, "records", A)


async def test_an_index_with_untagged_points_is_fp32(store) -> None:
    with org_context.for_org(B, job="test"):
        await run_legacy_scenario(store, "records", B)
