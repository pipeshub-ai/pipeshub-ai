"""A Neo4j of the lane's own (``pxh-e2e3-neo4j-<suffix>``) and the production provider over it.

The container publishes Bolt on a free loopback port only: the worker (this process) reaches it, no sandbox does
(NP-4 probes its bridge address). It is removed by name at teardown, and carries ``docker_session``'s labels for a
run killed before that.
"""

from __future__ import annotations

import contextlib
import secrets
import socket
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tests.support import docker_session
from tests.unit.harness.engines.real_stack import OWNER

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

NEO4J_IMAGE = "neo4j:5.26"
LABEL = "pxh.e2e3"


@dataclass(frozen=True)
class Neo4jServer:
    client: Any  # docker.DockerClient
    name: str
    port: int
    password: str = ""

    @property
    def uri(self) -> str:
        return f"bolt://127.0.0.1:{self.port}"

    def address(self) -> str:
        """Its address on Docker's default bridge (what a sandbox would have to reach)."""
        info = self.client.api.inspect_container(self.name)
        return str(info["NetworkSettings"]["Networks"]["bridge"]["IPAddress"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_neo4j(
    suffix: str | None = None,
    *,
    name: str = "pxh-e2e3-neo4j",
    labels: dict[str, str] | None = None,
    suite: str = "e2e3",
) -> Neo4jServer:
    """``name`` and ``labels``: another lane's own container name and label namespace (default E2E3's); ``suite``:
    the ``docker_session`` suite whose reaper owns it (default the matrix lane's)."""
    import docker

    client = docker.from_env()
    suffix = suffix or uuid.uuid4().hex[:8]
    password = secrets.token_hex(12)
    server = Neo4jServer(client, f"{name}-{suffix}", free_port(), password)
    client.containers.run(
        NEO4J_IMAGE,
        name=server.name,
        detach=True,
        labels={
            **(labels if labels is not None else {**OWNER, "pipeshub.agent": "E2E3", LABEL: suffix}),
            **docker_session.labels(suite),
        },
        environment={
            "NEO4J_AUTH": f"neo4j/{password}",
            "NEO4J_server_memory_heap_max__size": "512m",
            "NEO4J_server_memory_pagecache_size": "128m",
            "NEO4J_PLUGINS": "[]",
        },
        ports={"7687/tcp": ("127.0.0.1", server.port)},
        mem_limit="1g",
    )
    try:
        _wait_bolt(server)
    except BaseException:
        stop_neo4j(server)
        raise
    return server


def _wait_bolt(server: Neo4jServer, timeout_s: float = 180.0) -> None:
    from neo4j import GraphDatabase

    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with GraphDatabase.driver(server.uri, auth=("neo4j", server.password)) as driver:
                driver.verify_connectivity()
                return
        except Exception:
            if time.monotonic() > deadline:
                logs = server.client.api.logs(server.name).decode("utf-8", "replace")[-2000:]
                raise TimeoutError(f"Neo4j {server.name} never accepted Bolt:\n{logs}") from None
            time.sleep(1.0)


def stop_neo4j(server: Neo4jServer) -> None:
    with contextlib.suppress(Exception):
        server.client.api.remove_container(server.name, force=True, v=True)
    server.client.close()


async def connect(server: Neo4jServer, logger: logging.Logger, config_service: object) -> Neo4jProvider:
    """The production provider and client, its schema ensured as a service does at start."""
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    client = Neo4jClient(uri=server.uri, username="neo4j", password=server.password, database="neo4j", logger=logger)
    if not await client.connect():
        raise ConnectionError(f"Neo4j at {server.uri} refused the connection")
    provider = Neo4jProvider(logger=logger, config_service=config_service)
    provider.client = client
    await provider.ensure_schema()
    return provider


__all__ = ["NEO4J_IMAGE", "Neo4jServer", "connect", "free_port", "start_neo4j", "stop_neo4j"]
