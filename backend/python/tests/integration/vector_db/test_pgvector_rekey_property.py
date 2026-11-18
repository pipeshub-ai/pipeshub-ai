"""The policy-C re-key property on pgvector (PostgreSQL with pgvector through PgBouncer), one collection ('records') used in turn."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.modules.transformers import vectorstore as vs_mod
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401
from tests.integration.vector_db.helpers import DIM
from tests.support.index_world import ORG
from tests.support.rekey_property import SEED, one_trial

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
TRIALS = 12


@pytest.fixture
async def svc(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    s = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4,
                                                    placement="none"))
    with org_context.for_org(ORG, job="test"):
        yield s
    await s.disconnect()


@pytest.mark.parametrize("trial", range(TRIALS))
async def test_property_rekey_under_policy_c_on_pgvector(svc, monkeypatch, trial) -> None:
    monkeypatch.setattr(vs_mod._token_encoder, "_cached", None, raising=False)
    rng = random.Random(SEED * 1000 + 500 + trial)

    async def clear() -> None:
        await svc.delete_points("records", await svc.filter_collection(must={"orgId": ORG}))

    await clear()
    try:
        await one_trial(svc, "records", "records", DIM, rng, monkeypatch, unmovable=False, clear=clear)
    finally:
        await clear()
