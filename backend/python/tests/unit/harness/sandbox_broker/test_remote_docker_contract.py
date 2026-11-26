"""RB-2: SB-1..SB-21 through the HTTP API, with the Docker broker behind it (integration lane)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.remote.client import RemoteSandboxBroker
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.testing.sandbox import SandboxBrokerContract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


@pytest.fixture
async def broker(docker_broker: DockerSandboxBroker) -> AsyncIterator[RemoteSandboxBroker]:
    server = TestServer(create_broker_app(docker_broker, StaticBearerAuth("t")))
    await server.start_server()
    client = await RemoteSandboxBroker.open(str(server.make_url("")), StaticBearerAuth("t"))
    yield client
    await client.aclose()
    await server.close()


class TestRemoteOverDocker(SandboxBrokerContract):
    pass
