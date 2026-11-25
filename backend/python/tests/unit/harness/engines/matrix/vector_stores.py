"""A real vector store of the lane's own (``pxh-kn10-<kind>-<suffix>``) and the production provider over it (C3.25, KN-10).

The knowledge cell's vector backends: OpenSearch and Redis, each built by ``VectorDBProviderFactory`` from
``VECTOR_DB_TYPE`` and the connection document a ``ConfigurationService`` holds, as a deployment builds it. The org's
blocks go in through the indexer's registry and point builders (``seed_vectors``). The container publishes on a free
loopback port only: the worker (this process) reaches it, no sandbox does (``MatrixLane.forbidden`` lists its bridge
address). It carries ``docker_session``'s labels, so a run killed before teardown leaves it to the session finalizer.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from tests.support import docker_session
from tests.unit.harness.engines.matrix.graph import free_port
from tests.unit.harness.engines.matrix.mcp import SUITE
from tests.unit.harness.engines.matrix.org import seed_vectors
from tests.unit.harness.engines.matrix.services import MatrixServices
from tests.unit.harness.engines.real_stack import OWNER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.config.configuration_service import ConfigurationService
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from tests.unit.harness.engines.matrix.lane import MatrixLane


@dataclass(frozen=True)
class Backend:
    image: str
    container_port: int
    environment: dict[str, str]
    command: list[str] | None
    # The connection document the provider reads, given the published port.
    node: str
    document: dict[str, Any]
    mem_limit: str


# Redis's query engine refuses anything below 8.4 (FT.HYBRID); OpenSearch as tests/integration/compose/vector-db.yml.
BACKENDS: dict[str, Backend] = {
    "opensearch": Backend(
        "opensearchproject/opensearch:2.19.0", 9200,
        {"discovery.type": "single-node", "OPENSEARCH_JAVA_OPTS": "-Xms512m -Xmx512m",
         "DISABLE_SECURITY_PLUGIN": "true", "DISABLE_INSTALL_DEMO_CONFIG": "true"},
        None, "/services/opensearch",
        {"host": "127.0.0.1", "username": "", "password": "", "useSsl": False, "authType": "none", "timeout": 60},
        "1536m",
    ),
    "redis": Backend(
        "redis:8.4", 6379, {}, ["redis-server", "--save", "", "--appendonly", "no"], "/services/redis-vector",
        {"host": "127.0.0.1", "db": 0, "timeout": 60}, "512m",
    ),
}


@dataclass(frozen=True)
class VectorServer:
    client: Any  # docker.DockerClient
    kind: str
    name: str
    port: int

    def address(self) -> str:
        """Its address on Docker's default bridge (what a sandbox would have to reach)."""
        info = self.client.api.inspect_container(self.name)
        return str(info["NetworkSettings"]["Networks"]["bridge"]["IPAddress"])


def start(kind: str) -> VectorServer:
    import docker

    backend = BACKENDS[kind]
    client = docker.from_env()
    server = VectorServer(client, kind, f"pxh-kn10-{kind}-{uuid.uuid4().hex[:8]}", free_port())
    client.containers.run(
        backend.image, backend.command, name=server.name, detach=True,
        labels={**OWNER, "pipeshub.agent": "KN10", **docker_session.labels(SUITE)},
        environment=backend.environment, ports={f"{backend.container_port}/tcp": ("127.0.0.1", server.port)},
        mem_limit=backend.mem_limit,
    )
    return server


def stop(server: VectorServer) -> None:
    with contextlib.suppress(Exception):
        server.client.api.remove_container(server.name, force=True, v=True)
    server.client.close()


@contextlib.contextmanager
def _secret_key() -> Iterator[None]:
    with patch.dict(os.environ, {"SECRET_KEY": os.environ.get("SECRET_KEY") or "kn10-config-secret"}):
        yield


def connection_config(server: VectorServer) -> ConfigurationService:
    """A ConfigurationService over an in-memory key-value store: the provider checks it is one before it reads."""
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.in_memory_store import InMemoryKeyValueStore

    with patch.object(ConfigurationService, "_start_watch"), _secret_key():
        return ConfigurationService(logging.getLogger("kn10.config"), InMemoryKeyValueStore(logging.getLogger("kn10.kv")))


async def connect(server: VectorServer, timeout_s: float = 180.0) -> tuple[IVectorDBService, ConfigurationService]:
    """The provider ``VectorDBProviderFactory`` builds for ``VECTOR_DB_TYPE=<kind>``, once the server answers."""
    from app.services.vector_db.models import HealthStatus
    from app.services.vector_db.vector_db_provider_factory import (
        VectorDBProviderFactory,
    )

    backend = BACKENDS[server.kind]
    config = connection_config(server)
    with _secret_key():
        assert await config.set_config(backend.node, {**backend.document, "port": server.port})
    logger = logging.getLogger(f"kn10.{server.kind}")
    deadline = time.monotonic() + timeout_s
    last: object = None
    while time.monotonic() < deadline:
        with patch.dict(os.environ, {"VECTOR_DB_TYPE": server.kind, "VECTOR_REDIS_MODE": "standalone"}):
            try:
                provider = await VectorDBProviderFactory.create_provider(logger, config)  # type: ignore[arg-type]
                health = await provider.health_check()
                if health.status != HealthStatus.UNHEALTHY:
                    return provider, config
                last = health.message
                await provider.disconnect()
            except Exception as exc:
                last = exc
        await asyncio.sleep(1.0)
    logs = server.client.api.logs(server.name).decode("utf-8", "replace")[-2000:]
    raise TimeoutError(f"{server.kind} {server.name} never became healthy ({last!r}):\n{logs}")


@contextlib.asynccontextmanager
async def on_vector_store(matrix: MatrixLane, kind: str) -> AsyncIterator[MatrixLane]:
    """The same lane, graph, org and services over a real ``kind`` vector store instead of the contract fake."""
    server = await asyncio.to_thread(start, kind)
    provider: IVectorDBService | None = None
    config: ConfigurationService | None = None
    try:
        provider, config = await connect(server)
        base = matrix.services
        services = MatrixServices(base.org, base.graph, base.config, base.agent, provider)
        await seed_vectors(base.org, services)
        yield dataclasses.replace(matrix, services=services, runs=[], vector_store=(server.address(), BACKENDS[kind].container_port))
    finally:
        if provider is not None:
            with contextlib.suppress(Exception):
                await provider.disconnect()
        if config is not None:
            with contextlib.suppress(Exception):
                await config.close()
        await asyncio.to_thread(stop, server)


__all__ = ["BACKENDS", "on_vector_store"]
