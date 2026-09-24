"""Fixtures for tests against a real Redis Cluster (Phase 6, R17).

Needs the 3-master cluster from
`deployment/docker-compose/docker-compose.integration.redis-cluster.yml`:

    docker compose -f deployment/docker-compose/docker-compose.integration.redis-cluster.yml up -d
    cd backend/python && REDIS_MODE=cluster \
      REDIS_CLUSTER_ENDPOINTS=127.0.0.1:17000,127.0.0.1:17001,127.0.0.1:17002 \
      pytest tests/integration/redis_cluster -m integration

Every test in this package exercises the *real* `ClusterRedisProvider`
end-to-end against real cluster slots -- the CROSSSLOT and multi-node
behaviour `FakeClusterRedis` only approximates in the unit contract suites
under `tests/unit/services/redis/`. They skip cleanly (do not fail the
suite) when no cluster answers, so a normal unit/CI run without Docker is
unaffected.

Environment:
  REDIS_MODE                (must be "cluster" for these tests to mean anything)
  REDIS_CLUSTER_ENDPOINTS   (default: 127.0.0.1:17000,127.0.0.1:17001,127.0.0.1:17002)
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider_factory import (
    get_redis_provider,
    reset_redis_provider_registry,
)

DEFAULT_ENDPOINTS = "127.0.0.1:17000,127.0.0.1:17001,127.0.0.1:17002"


@pytest.fixture
def unique_suffix() -> str:
    """Keys/pools/groups are per-test, so reruns never inherit state left on
    the shared cluster by a previous run."""
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="session", autouse=True)
def _redis_cluster_env() -> None:
    """Point every `get_redis_provider()` call at the compose cluster unless
    the caller already set `REDIS_MODE`/`REDIS_CLUSTER_ENDPOINTS` themselves
    (e.g. a CI job pointed at a different cluster)."""
    os.environ.setdefault("REDIS_MODE", "cluster")
    os.environ.setdefault("REDIS_CLUSTER_ENDPOINTS", DEFAULT_ENDPOINTS)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_cluster_available(_redis_cluster_env: None) -> RedisConnectionConfig:
    """The resolved cluster config, or skip the module if no cluster answers."""
    pytest.importorskip("redis", reason="redis package not installed")
    config = RedisConnectionConfig.from_env()
    if not config.cluster_endpoints:
        pytest.skip("REDIS_CLUSTER_ENDPOINTS not set")

    provider = get_redis_provider(config, mode="cluster")
    try:
        reachable = await asyncio.wait_for(provider.ping(), timeout=10.0)
    except Exception as exc:
        pytest.skip(f"Redis Cluster not available at {config.cluster_endpoints} — {exc}")
    if not reachable:
        pytest.skip(f"Redis Cluster not reachable at {config.cluster_endpoints}")
    return config


@pytest.fixture(autouse=True)
def _reset_provider_registry_between_tests():
    """Each test gets its own cached provider/client set: a lease manager or
    consumer left half-initialized by a failed test must not leak a stale
    client into the next one."""
    yield
    reset_redis_provider_registry()
