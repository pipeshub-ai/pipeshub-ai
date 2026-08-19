"""
Shared fixtures for vector DB integration tests.

These tests require running Docker services:
  docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d

Run integration tests explicitly:
  pytest tests/integration/vector_db/ -m integration --timeout=120

Each provider has a dedicated integration module; shared scenarios live in
``test_contract_integration.py``.

Environment variables used:
  REDIS_VECTOR_HOST     (default: localhost)
  REDIS_VECTOR_PORT     (default: 6399)
  OPENSEARCH_HOST       (default: localhost)
  OPENSEARCH_PORT       (default: 9299)
  QDRANT_HOST           (default: localhost)
  QDRANT_PORT           (default: 6334)
"""

import os
import pytest

from app.services.vector_db.models import HealthStatus


# ---------------------------------------------------------------------------
# Helper: unique collection names per test run
# ---------------------------------------------------------------------------

def make_collection(prefix: str = "test") -> str:
    """Generate a unique-ish test collection name."""
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _connect_or_skip(svc, label: str, host: str, port: int) -> None:
    """Connect and verify the server is reachable; skip the module otherwise.

    Provider ``connect()`` may only load config (lazy client). A follow-up
    health check is what actually proves the Docker service is up.
    """
    try:
        await svc.connect()
        health = await svc.health_check()
    except Exception as exc:
        pytest.skip(f"{label} not available at {host}:{port} — {exc}")
    if health.status == HealthStatus.UNHEALTHY:
        pytest.skip(f"{label} not available at {host}:{port} — {health.message}")


# ---------------------------------------------------------------------------
# Redis provider fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
async def redis_service():
    """Connect to Redis vector service; skip if unavailable."""
    pytest.importorskip("redis", reason="redis package not installed")
    from app.services.vector_db.redis.config import RedisVectorConfig
    from app.services.vector_db.redis.redis_vector import RedisVectorService

    host = os.environ.get("REDIS_VECTOR_HOST", "localhost")
    port = int(os.environ.get("REDIS_VECTOR_PORT", "6399"))
    config = RedisVectorConfig(host=host, port=port)
    svc = RedisVectorService(config)
    await _connect_or_skip(svc, "Redis", host, port)
    yield svc
    await svc.disconnect()


# ---------------------------------------------------------------------------
# OpenSearch provider fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
async def opensearch_service():
    """Connect to OpenSearch vector service; skip if unavailable."""
    pytest.importorskip("opensearchpy", reason="opensearch-py package not installed")
    from app.services.vector_db.opensearch.config import OpenSearchConfig
    from app.services.vector_db.opensearch.opensearch import OpenSearchService

    host = os.environ.get("OPENSEARCH_HOST", "localhost")
    port = int(os.environ.get("OPENSEARCH_PORT", "9299"))
    config = OpenSearchConfig(
        host=host,
        port=port,
        username="admin",
        password="admin",
        use_ssl=False,
        verify_certs=False,
    )
    svc = OpenSearchService(config)
    await _connect_or_skip(svc, "OpenSearch", host, port)
    yield svc
    await svc.disconnect()


# ---------------------------------------------------------------------------
# Qdrant provider fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
async def qdrant_service():
    """Connect to Qdrant vector service; skip if unavailable."""
    pytest.importorskip("qdrant_client", reason="qdrant_client package not installed")
    from app.services.vector_db.qdrant.config import QdrantConfig
    from app.services.vector_db.qdrant.qdrant import QdrantService

    host = os.environ.get("QDRANT_HOST", "localhost")
    port = int(os.environ.get("QDRANT_PORT", "6334"))
    config = QdrantConfig(host=host, port=port)
    svc = QdrantService(config)
    await _connect_or_skip(svc, "Qdrant", host, port)
    yield svc
    await svc.disconnect()
