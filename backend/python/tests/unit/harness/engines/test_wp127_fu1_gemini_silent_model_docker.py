"""WP127-FU-1 on the real Gemini CLI binary: a model that falls silent mid-answer for longer than the egress proxy's
silence cut does not cut the stream, because the Model Gateway's Gemini wire writes its keepalive (``data: {}``,
ADR-GW-01 D1) and Gemini CLI's own ``@google/genai`` reader takes it.

The lane is RX-GEM's (``test_real_gemini_acp_conformance.py``): Gemini CLI 0.63.0's ACP mode in its image under the
Docker broker, reaching E3's real ``ModelGateway`` only through the real egress proxy. Scaled down so it runs in
seconds: the proxy cuts an upstream silent for 3 s (``UPSTREAM_READ_TIMEOUT_S``, 300 s in production), the scripted
model pauses 8 s after its first chunk, and the gateway beats every second (15 s in production, well inside 300).
The control case turns the beat off past the cut and shows the same pause is cut, so the green case proves the
keepalive, not a lenient rig. Skipped without Docker and the image (``deployment/sandbox-images/acp-gemini/build.sh``).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.events import MessageCompleted, TextDelta, TurnCompleted
from harness_core.testing.engines.contract import (
    DEFAULT_GRANT,
    EngineAdapterContract,
    collect,
)

# The module, not its class: a ``Test*`` name imported here would be collected and run here again.
from tests.unit.harness.engines import test_real_gemini_acp_conformance as rxgem
from tests.unit.harness.engines.real_stack import (
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.events import EngineEvent
    from harness_core.testing.engines.contract import Rig
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

SILENCE_CUT_S = 3.0
PAUSE_S = 8.0
ANSWER = "The whole answer arrived after the model paused."


class _GeminiLane:
    """RX-GEM's rig, borrowed rather than inherited so its contract cases are not collected here again."""

    watching_phone_home = False
    planted_workspace = None
    watched = EngineAdapterContract.watched
    seed_workspace = EngineAdapterContract.seed_workspace
    _gateway = rxgem.TestAcpGeminiOnTheRealBinary._gateway  # pyright: ignore[reportPrivateUsage]
    _sandbox = rxgem.TestAcpGeminiOnTheRealBinary._sandbox  # pyright: ignore[reportPrivateUsage]
    open_rig = rxgem.TestAcpGeminiOnTheRealBinary.open_rig

    def __init__(self, stack: RealStack, broker: DockerSandboxBroker, models: ModelGatewayLane) -> None:
        self._stack, self._broker, self._models = stack, broker, models
        self._opened: list[Any] = []


@pytest.fixture(scope="module")
def silent_stack() -> Iterator[RealStack]:
    available, reason = lane_available(rxgem.IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/acp-gemini/build.sh)")
    stack = start_stack(upstreams=lambda _stack: {"EGRESS_TEST_UPSTREAM_READ_TIMEOUT_S": str(SILENCE_CUT_S)})
    yield stack
    stop_stack(stack)


@pytest.fixture
async def stack(silent_stack: RealStack) -> AsyncIterator[RealStack]:
    async with silent_stack.serving() as serving:
        yield serving


@pytest.fixture
async def broker(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.gemsil.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-gems-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=rxgem.IMAGE)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


@contextlib.asynccontextmanager
async def _paused_turn(
    stack: RealStack, broker: DockerSandboxBroker, *, heartbeat_s: float
) -> AsyncIterator[tuple[list[EngineEvent], str, Rig]]:
    async with ModelGatewayLane(stack, _free_port(stack.host_ip), heartbeat_s=heartbeat_s).serving() as models:
        lane = _GeminiLane(stack, broker, models)
        gateway = lane._gateway(DEFAULT_GRANT)  # pyright: ignore[reportPrivateUsage]
        key = stack.models.register(gateway)
        stack.models.pauses[key] = PAUSE_S
        harness = rxgem.RealAcpGemini()
        harness.issued = stack.models.issued
        rig = await lane.open_rig(harness, profile=None, grant=DEFAULT_GRANT, gateway=gateway, restore=None)
        try:
            try:
                events = await collect(rig.session, rxgem._turn(f"say {ANSWER}"), timeout_s=120.0)
            except TimeoutError:
                events = []
            yield events, lane._opened[0].handle.id, rig  # pyright: ignore[reportPrivateUsage]
        finally:
            await rig.session.close()
            await broker.release(lane._opened[0].handle)  # pyright: ignore[reportPrivateUsage]


def _said(events: list[EngineEvent]) -> str:
    completed = [e.text for e in events if isinstance(e, MessageCompleted)]
    return completed[-1] if completed else "".join(e.text for e in events if isinstance(e, TextDelta))


def _cut(stack: RealStack, sandbox_id: str) -> list[str]:
    return [
        line
        for line in stack.proxy_logs().splitlines()
        if "egress.upstream_silent" in line and f"sandboxId={sandbox_id}" in line
    ]


async def test_wp127_fu1_gemini_cli_reads_a_whole_answer_through_a_model_silent_past_the_egress_cut(
    stack: RealStack, broker: DockerSandboxBroker
) -> None:
    async with _paused_turn(stack, broker, heartbeat_s=1.0) as (events, sandbox_id, _rig):
        assert events and isinstance(events[-1], TurnCompleted), events[-3:]
        assert events[-1].reason == "completed"
        assert ANSWER in _said(events)
        await asyncio.sleep(0.5)
        assert _cut(stack, sandbox_id) == []


async def test_wp127_fu1_control_without_a_beat_inside_the_cut_the_same_pause_is_cut(
    stack: RealStack, broker: DockerSandboxBroker
) -> None:
    async with _paused_turn(stack, broker, heartbeat_s=PAUSE_S * 4) as (events, sandbox_id, _rig):
        await asyncio.sleep(0.5)
        assert _cut(stack, sandbox_id), "without a beat inside the cut the egress should cut the silent upstream"
