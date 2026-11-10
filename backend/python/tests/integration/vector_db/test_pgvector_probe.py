"""pgvector provider start-up against the migrated kernel: capabilities, health, one shared runtime, a missing extension.

Run like test_pgvector_store.py (KERNEL_TEST_PG_BINS, KERNEL_TEST_PGBOUNCER).
"""
from __future__ import annotations

import pytest

from app.services.vector_db.models import HealthStatus
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_capabilities_and_health(env) -> None:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("query", port), placement="none"))
    try:
        caps = svc.pg_capabilities
        conn = await env.admin()
        want = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        await conn.close()
        assert caps.pg_major >= 17 and caps.halfvec and caps.pgvector_version == want
        health = await svc.health_check()
        assert health.status == HealthStatus.HEALTHY and health.server_version == f"pgvector {want}"
    finally:
        await svc.disconnect()


async def test_two_providers_share_one_runtime(env) -> None:  # noqa: F811
    port = env.start_bouncer()
    cfg = PgVectorConfig(dsn=env.dsn("query", port), placement="none")
    a, b = await PgVectorService.create(cfg), await PgVectorService.create(cfg)
    try:
        assert a.get_service_client() is b.get_service_client()
    finally:
        await a.disconnect()
        await b.disconnect()
