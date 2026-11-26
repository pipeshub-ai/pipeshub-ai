"""E2E3-F6 in the ACP driver: where the profile names the agent's MCP status API (``mcp.status``), the session holds
the tool gateway before its first prompt, and a gateway that never connects is an engine that never came up."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted
from harness_core.engines.types import EngineTask
from harness_core.testing.engines.contract import collect, open_rig
from harness_engines.acp.testing.profile import fake_profile
from tests.unit.harness_engines.acp.conftest import AcpHarness

if TYPE_CHECKING:
    from harness_core.logs import HarnessLog
    from harness_engines.opencode_api.http import OpencodeHttp

STATUS = {"server": "tools", "toolName": "{server}_{tool}", "status": {"api": "opencode", "port": 4096}}
TASK = EngineTask(turn_id="trn-1", prompt="say ok")


def _methods(harness: AcpHarness) -> list[str]:
    (fake,) = harness.fakes.values()
    return [str(m.get("method")) for c in fake.connections for m in c.received if "method" in m]


async def test_e2e3_f6_the_gateway_is_connected_after_session_new_and_before_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Round 10: the gateway is asked about under the name the profile gives it, not opencode's own default.
    harness = AcpHarness(profile=fake_profile(mcp={**STATUS, "server": "gw"}), session_options={"gateway_timeout_s": 7.0})
    asked: list[tuple[int, str, float, list[str]]] = []

    async def gate(http: OpencodeHttp, log: HarnessLog, *, server: str, timeout_s: float) -> None:
        asked.append((int(http._base.rsplit(":", 1)[1]), server, timeout_s, _methods(harness)))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr("harness_engines.acp.session.await_tool_gateway", gate)
    rig = await open_rig(harness)
    events = await collect(rig.session, TASK)
    assert isinstance(events[-1], TurnCompleted)
    ((port, server, timeout_s, before),) = asked
    assert (port, server, timeout_s) == (4096, "gw", 7.0)
    assert "session/new" in before and "session/prompt" not in before
    await rig.session.close()


async def test_e2e3_f6_a_gateway_that_never_connects_sends_no_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = AcpHarness(profile=fake_profile(mcp=STATUS))

    async def never(http: OpencodeHttp, log: HarnessLog, *, server: str, timeout_s: float) -> None:
        raise EngineUnhealthy("opencode did not connect the tool gateway")

    monkeypatch.setattr("harness_engines.acp.session.await_tool_gateway", never)
    rig = await open_rig(harness)
    with pytest.raises(EngineUnhealthy):
        await collect(rig.session, TASK)
    assert "session/prompt" not in _methods(harness)
    await rig.session.close()


async def test_e2e3_f6_a_profile_without_a_status_api_is_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = AcpHarness()
    assert harness.profile.mcp_status is None

    async def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("asked an agent that has no status API")

    monkeypatch.setattr("harness_engines.acp.session.await_tool_gateway", unexpected)
    rig = await open_rig(harness)
    assert isinstance((await collect(rig.session, TASK))[-1], TurnCompleted)
    await rig.session.close()
