# ruff: noqa: F811, ANN401
"""The scope rewrite against real pgvector (see `test_scope_rewrite_stores.py` for what is held): the same contract, with the fence by the time
the graph was read (`computed_at`) instead of a generation, and every call naming the organisation it runs for.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_scope_rewrite.py -m integration
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from app.core.org_context import for_org
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from app.services.vector_db.scope_rewrite import RewriteIncomplete
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import make_collection_config
from tests.integration.vector_db.test_scope_rewrite_stores import _Contract
from tests.permissions.model import Mutation
from tests.permissions.rewrite_kit import Env, Log
from tests.permissions.scenario import WorldStore
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


async def ahead_of_the_database_clock(svc: PgVectorService, seconds: float) -> None:
    """Every group row of the organisation was computed `seconds` after the database's clock now: a writer whose clock runs ahead wrote it."""
    db, _ = svc._assert_connected()

    async def body(tx: Any) -> None:
        await tx.execute("/* pipeshub:test */ UPDATE retrieval.point_groups SET computed_at = clock_timestamp() + make_interval(secs => $2) WHERE org_id = $1",
                         ORG, seconds)

    await db.run(body, write=True, idempotent=True, custom_plan=True)


class TestPgVectorClock:
    """The engine measures pgvector's fence in the database's clock; what can still refuse it is a row computed after that clock."""

    store = "pgvector"
    make = TestPgVectorRewrite.make
    drop = TestPgVectorRewrite.drop

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

    async def test_the_fence_time_is_the_databases_clock(self, vector_service: PgVectorService) -> None:
        assert abs((await vector_service.fence_time() - datetime.now(UTC)).total_seconds()) < 5

    async def test_a_row_computed_after_the_database_clock_keeps_the_root_pending_and_says_so_until_the_clock_passes(
            self, vector_service: PgVectorService, caplog: pytest.LogCaptureFixture) -> None:
        env = await self.make(vector_service, WorldStore().world("golden/confluence_hr"))
        try:
            await env.change((Mutation("moved", "page:Handbook", "space:ALL", "space:ALL"),))
            await ahead_of_the_database_clock(vector_service, 300)
            with caplog.at_level(logging.WARNING), pytest.raises(RewriteIncomplete):
                await env.job.run_connector(ORG, "w:app")
            assert env.pending() and any("refused or lost the write" in r.getMessage() for r in caplog.records)
            seen = Log()
            await env.audit(seen)
            assert not seen.leaks and not seen.pending_admits
            await ahead_of_the_database_clock(vector_service, -1)      # the clock has passed it (or the row was written again)
            await env.settle()
            assert not await env.problems()
        finally:
            await self.drop(vector_service, env)

    async def test_the_stale_marker_a_row_ahead_refuses_fails_the_run_and_does_not_pass_for_written(self, vector_service: PgVectorService) -> None:
        env = await self.make(vector_service, WorldStore().world("golden/confluence_hr"))
        try:
            await ahead_of_the_database_clock(vector_service, 300)
            with pytest.raises(RewriteIncomplete, match="stale marker did not land"):
                await env.job._mark_stale(ORG, "w:app", [env.vrids()["page:Handbook"]], await env.job._epoch("w:app"))
        finally:
            await self.drop(vector_service, env)

    async def test_the_back_fills_own_stale_marker_a_row_ahead_refuses_fails_the_pass_instead_of_ending_it_current(
            self, vector_service: PgVectorService, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        from app.services.vector_db import scope_backfill
        from app.services.vector_db.membership import STALE_SCOPE

        async def stale(*a: Any, **k: Any) -> Any:
            return STALE_SCOPE      # every stamp is "cannot be computed": the pass writes the marker for all

        env = await self.make(vector_service, WorldStore().world("golden/confluence_hr"))
        monkeypatch.setattr(scope_backfill, "scope_fields_for_state", stale)      # (after the world is stamped: it starts clean)
        monkeypatch.setattr(scope_backfill, "MARKER_DELAY_SECONDS", 0.0)
        try:
            await ahead_of_the_database_clock(vector_service, 300)
            with caplog.at_level(logging.ERROR):
                failed = await env.backfill.run_connector(ORG, "w:app", restart=True)
            assert failed.state == "failed" and "could not be marked stale" in caplog.text, failed.line()
            assert "w:app" not in await env.current()
            await ahead_of_the_database_clock(vector_service, -1)
            assert (await env.backfill.run_connector(ORG, "w:app", restart=True)).state == "done"
            seen = Log()
            await env.audit(seen)
            assert not seen.leaks and not seen.pending_admits
        finally:
            await self.drop(vector_service, env)
