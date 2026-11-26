"""SR-16 (WP-06, OWD-1, ``-m docker``): a real PostgreSQL behind the egress proxy's database route.

PostgreSQL 16 signs in with SCRAM-SHA-256. The client (asyncpg, as a sandbox's psql would) connects to the proxy
with a user name of its own and no password; the proxy signs in with the sealed credential, as the route's user.
The database's port is published on loopback only, so the lane needs no Docker network of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

IMAGE = "postgres:16-alpine"
OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "WP-06"}
USER, PASSWORD, DATABASE = "app_reader", "Sr16-real-pass-0123456789", "analytics"
SEALER = CredentialSealer(b"sr16-deployment-key")
PH = "pxh-ph-database-sr16-0123456789"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def postgres() -> Iterator[int]:
    docker = pytest.importorskip("docker")
    try:
        client = docker.from_env()
        client.ping()
        client.images.get(IMAGE)
    except Exception as exc:
        pytest.skip(f"needs Docker and {IMAGE}: {type(exc).__name__}")
    port = _free_port()
    container = client.containers.run(
        IMAGE,
        name=f"pxh-wp06-pg-{uuid.uuid4().hex[:8]}",
        environment={"POSTGRES_USER": USER, "POSTGRES_PASSWORD": PASSWORD, "POSTGRES_DB": DATABASE},
        ports={"5432/tcp": ("127.0.0.1", port)},
        labels=OWNER,
        detach=True,
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            code, _ = container.exec_run(["pg_isready", "-U", USER, "-d", DATABASE, "-h", "127.0.0.1"])
            if code == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("postgres did not become ready")
        yield port
    finally:
        with contextlib.suppress(Exception):
            container.remove(force=True)
        client.close()


async def test_sr16_a_real_postgres_signs_in_through_the_proxy_and_the_client_holds_no_password(postgres: int) -> None:
    asyncpg = pytest.importorskip("asyncpg")
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(),
    )
    sealed = SEALER.seal(PH, json.dumps({"user": USER, "password": PASSWORD}))
    policy = EgressPolicy(
        destinations=(
            EgressDestination("pxh-database", (5432,), upstream=f"postgres://{USER}@127.0.0.1:{postgres}/?sslmode=prefer"),
        ),
        token_routes=(TokenRoute(PH, "pxh-database", sealed, CredentialInjection.POSTGRES),),
    )
    await proxy.bindings.register(EgressBinding("sbx-sr16", "127.0.0.2", "127.0.0.1", policy, {"orgId": "o1"}))
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="sr16")
    _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1", database_port=0)
    try:
        assert server.database_port is not None
        connection = await asyncio.wait_for(
            asyncpg.connect(host="127.0.0.2", port=server.database_port, user="sandbox", database=DATABASE),
            30,
        )
        try:
            assert await connection.fetchval("select current_user") == USER
            assert await connection.fetchval("select 40 + 2") == 42
        finally:
            await connection.close()
        refused = EgressBinding("sbx-sr16", "127.0.0.2", "127.0.0.1", EgressPolicy(
            destinations=policy.destinations,
            token_routes=(TokenRoute(PH, "pxh-database", SEALER.seal(PH, json.dumps({"user": USER, "password": "wrong"})), CredentialInjection.POSTGRES),),
        ))
        await proxy.bindings.register(refused)
        with pytest.raises(asyncpg.InvalidPasswordError):
            _ = await asyncpg.connect(host="127.0.0.2", port=server.database_port, user="sandbox", database=DATABASE)
    finally:
        await server.stop()
