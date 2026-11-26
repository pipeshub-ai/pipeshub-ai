"""MR-1..MR-12 on the in-memory runner and on ``SandboxMCPRunner`` over the fake broker (P5.10).

On the fake broker the server's secret reaches its listed host through the fake egress proxy, which opens the
sealed route and puts the secret where the server sent its placeholder (OWD-1, WP-06): ``received_secret`` asks the
host what key arrived.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, override

import pytest

from harness_core.ids import RandomIds
from harness_core.sandbox.mcp_runner import (
    InMemoryMCPRunner,
    RunnerEgressHost,
    SandboxMCPRunner,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.egress import FakeEgressProxy
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_core.testing.sandbox.mcp_echo_server import SECRET_HEADER
from harness_core.testing.sandbox.mcp_runner import (
    ECHO_COMMAND,
    SECRET_NAME,
    LineRpc,
    MCPRunnerContract,
    MCPRunnerContractEnv,
    digest,
    fake_bridge,
    serve_echo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.testing.sandbox.egress import EgressRequest

LISTED = "api.mr-listed.test"
UPSTREAM = "http://mr-listed-upstream.internal"


class ClockEnv(MCPRunnerContractEnv):
    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock

    @override
    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def mcp_env(clock: ManualClock) -> ClockEnv:
    return ClockEnv(clock)


class TestInMemoryRunner(MCPRunnerContract):
    @pytest.fixture
    async def mcp_runner(self, clock: ManualClock, mcp_env: ClockEnv) -> AsyncIterator[InMemoryMCPRunner]:
        runner = InMemoryMCPRunner({ECHO_COMMAND: serve_echo}, clock=clock, ids=RandomIds(), config=mcp_env.config)
        yield runner
        _ = await runner.stop_all()


class ProxiedEnv(ClockEnv):
    egress = (RunnerEgressHost(LISTED, (80,), upstream=UPSTREAM),)

    @override
    async def received_secret(self, rpc: LineRpc) -> str:
        reply = await rpc.call("send_secret", {"url": f"http://{LISTED}/whoami", "name": SECRET_NAME})
        status, _, body = reply.partition(" ")
        assert status == "200", reply
        return str(json.loads(body)["keyDigest"])


class TestSandboxRunnerOnFakeBroker(MCPRunnerContract):
    @pytest.fixture
    def mcp_env(self, clock: ManualClock) -> ProxiedEnv:
        return ProxiedEnv(clock)

    @pytest.fixture
    async def mcp_runner(self, clock: ManualClock, mcp_env: ProxiedEnv) -> AsyncIterator[SandboxMCPRunner]:
        proxy = FakeEgressProxy(clock=clock, gateways={})

        async def whoami(request: EgressRequest) -> dict[str, str]:
            return {"keyDigest": digest(request.headers.get(SECRET_HEADER.lower(), ""))}

        proxy.route_upstream(UPSTREAM, whoami)
        broker = FakeSandboxBroker(
            clock=clock, programs={**FAKE_PROGRAMS, "python3": fake_bridge()}, egress_proxy=proxy
        )
        runner = SandboxMCPRunner(broker, clock=clock, ids=RandomIds(), config=mcp_env.config, sealer=proxy.sealer)
        yield runner
        _ = await runner.stop_all()
        assert broker.sandboxes == {}
