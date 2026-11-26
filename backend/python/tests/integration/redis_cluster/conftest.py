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
under `tests/unit/services/redis/`. They skip when REDIS_CLUSTER_ENDPOINTS is
unset, so a normal unit/CI run without Docker is unaffected; they never dial a
default address.

Environment:
  REDIS_MODE                (set to "cluster" here when unset)
  REDIS_CLUSTER_ENDPOINTS   (required; the compose cluster is the value above)
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
from tests.support import it_backends


@pytest.fixture
def unique_suffix() -> str:
    """Keys/pools/groups are per-test, so reruns never inherit state left on
    the shared cluster by a previous run."""
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="session", autouse=True)
def _redis_cluster_env() -> None:
    """Run every `get_redis_provider()` call in cluster mode, against the
    cluster REDIS_CLUSTER_ENDPOINTS names; skip when it names none."""
    it_backends.endpoint("REDIS_CLUSTER_ENDPOINTS")
    os.environ.setdefault("REDIS_MODE", "cluster")


def _unavailable(reason: str) -> None:
    """Skip locally; fail under CI, where a missing cluster must never read as a pass (the job starts one)."""
    if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
        pytest.fail(f"CI is set but the Redis Cluster is unusable: {reason}")
    pytest.skip(reason)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_cluster_available(_redis_cluster_env: None) -> RedisConnectionConfig:
    """The resolved cluster config, or skip the module if no cluster answers."""
    pytest.importorskip("redis", reason="redis package not installed")
    config = RedisConnectionConfig.from_env()
    if not config.cluster_endpoints:
        _unavailable("REDIS_CLUSTER_ENDPOINTS not set")

    provider = get_redis_provider(config, mode="cluster")
    try:
        reachable = await asyncio.wait_for(provider.ping(), timeout=10.0)
    except Exception as exc:
        _unavailable(f"Redis Cluster not available at {config.cluster_endpoints} — {exc}")
    if not reachable:
        _unavailable(f"Redis Cluster not reachable at {config.cluster_endpoints}")
    return config


@pytest.fixture(autouse=True)
def _reset_provider_registry_between_tests():
    """Each test gets its own cached provider/client set: a lease manager or
    consumer left half-initialized by a failed test must not leak a stale
    client into the next one."""
    yield
    reset_redis_provider_registry()
