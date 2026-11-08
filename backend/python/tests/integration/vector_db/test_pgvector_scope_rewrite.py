# ruff: noqa: F811
"""The scope rewrite against real pgvector (see `test_scope_rewrite_stores.py` for what is held): the same contract, with the fence by the time
the graph was read (`computed_at`) instead of a generation, and every call naming the organisation it runs for.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_scope_rewrite.py -m integration
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import for_org
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import make_collection_config
from tests.integration.vector_db.test_scope_rewrite_stores import _Contract
from tests.permissions.rewrite_kit import Env
from tests.permissions.stamp_world import ORG

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


class TestPgVectorRewrite(_Contract):
    store = "pgvector"

    async def make(self, svc, world, **kw) -> Env:
        """A collection is created outside any organisation (the catalog binds the system); everything else runs inside the world's."""
        name = make_collection(self.store)
        await asyncio.create_task(svc.create_collection(name, make_collection_config()), context=contextvars.Context())
        return await Env.make(world, svc, collection=name, settle_seconds=60.0, create=False, **kw)

    async def drop(self, svc, env: Env) -> None:
        env.close()
        await asyncio.create_task(svc.delete_collection(env.loaded.collection), context=contextvars.Context())

    @pytest.fixture(autouse=True)
    def _org(self) -> object:
        with for_org(ORG, job="test"):
            yield

    @pytest.fixture
    async def vector_service(self, env) -> AsyncIterator[PgVectorService]:
        port = env.start_bouncer()
        svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
        yield svc
        await svc.disconnect()
