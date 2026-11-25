"""An ArangoDB of the lane's own (``pxh-e2e4-arango-<suffix>``) and the production provider over it (E2E4 AR).

The knowledge cells' second graph backend: the same org seeded through ``IGraphDBProvider`` on
``ArangoHTTPProvider``, whose collections carry the strict schemas ``ensure_schema`` applies. The container publishes
HTTP on a free loopback port only: the worker (this process) reaches it, no sandbox does (NP-4 probes its bridge
address). It is removed by name at teardown; it carries ``docker_session``'s labels whatever else it is labelled
with, so a run killed before teardown leaves it to the session finalizer and the suite's stale reaper (BUGS-A).
"""

from __future__ import annotations

import contextlib
import secrets
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tests.support import docker_session
from tests.unit.harness.engines.matrix.graph import free_port
from tests.unit.harness.engines.real_stack import OWNER

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

ARANGO_IMAGE = "arangodb:3.12.4"
LABEL = "pxh.e2e4"
DATABASE = "pxh_e2e4"


@dataclass(frozen=True)
class ArangoServer:
    client: Any  # docker.DockerClient
    name: str
    port: int
    password: str = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def address(self) -> str:
        """Its address on Docker's default bridge (what a sandbox would have to reach)."""
        info = self.client.api.inspect_container(self.name)
        return str(info["NetworkSettings"]["Networks"]["bridge"]["IPAddress"])


def start_arango(
    suffix: str | None = None,
    *,
    name: str = "pxh-e2e4-arango",
    labels: dict[str, str] | None = None,
    suite: str = "e2e3",
) -> ArangoServer:
    """``name`` and ``labels``: another lane's own container name and label namespace (default E2E4's); ``suite``:
    the ``docker_session`` suite whose reaper owns it (default the matrix lane's)."""
    import docker

    client = docker.from_env()
    suffix = suffix or uuid.uuid4().hex[:8]
    password = secrets.token_hex(12)
    server = ArangoServer(client, f"{name}-{suffix}", free_port(), password)
    client.containers.run(
        ARANGO_IMAGE,
        name=server.name,
        detach=True,
        labels={
            **(labels if labels is not None else {**OWNER, "pipeshub.agent": "E2E4", LABEL: suffix}),
            **docker_session.labels(suite),
        },
        # ArangoDB sizes its caches from the host's memory, not the container's limit: told nothing, it was
        # OOM-killed at 1 GiB after the seed (its client then reads every failed query as "no access").
        environment={"ARANGO_ROOT_PASSWORD": password, "ARANGODB_OVERRIDE_DETECTED_TOTAL_MEMORY": "1G"},
        ports={"8529/tcp": ("127.0.0.1", server.port)},
        mem_limit="2g",
    )
    try:
        _wait_http(server)
    except BaseException:
        stop_arango(server)
        raise
    return server


def _wait_http(server: ArangoServer, timeout_s: float = 180.0) -> None:
    import base64

    auth = base64.b64encode(f"root:{server.password}".encode()).decode()
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            request = urllib.request.Request(f"{server.url}/_api/version", headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        if time.monotonic() > deadline:
            logs = server.client.api.logs(server.name).decode("utf-8", "replace")[-2000:]
            raise TimeoutError(f"ArangoDB {server.name} never answered:\n{logs}")
        time.sleep(1.0)


def stop_arango(server: ArangoServer) -> None:
    with contextlib.suppress(Exception):
        server.client.api.remove_container(server.name, force=True, v=True)
    server.client.close()


class _ArangoConfig:
    """Only the connection document the provider reads at ``connect``; the matrix's config service follows."""

    def __init__(self, server: ArangoServer) -> None:
        self._document = {"url": server.url, "username": "root", "password": server.password, "db": DATABASE}

    async def get_config(self, *_: object, **__: object) -> dict[str, str]:
        return dict(self._document)


async def connect(server: ArangoServer, logger: logging.Logger) -> ArangoHTTPProvider:
    """The production provider, its strict collection schemas and indexes ensured as a service does at start."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider = ArangoHTTPProvider(logger, _ArangoConfig(server))  # type: ignore[arg-type]
    if not await provider.connect():
        raise ConnectionError(f"ArangoDB at {server.url} refused the connection")
    await provider.ensure_schema()
    return provider


async def disconnect(provider: Any) -> None:  # noqa: ANN401
    close = getattr(provider, "disconnect", None)
    if close is not None:
        with contextlib.suppress(Exception):
            await close()


__all__ = ["ARANGO_IMAGE", "ArangoServer", "connect", "disconnect", "start_arango", "stop_arango"]
