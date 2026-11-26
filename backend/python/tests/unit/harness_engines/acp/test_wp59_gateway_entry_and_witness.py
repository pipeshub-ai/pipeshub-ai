"""WP-59 round 2 in the ACP driver, with the scripted ACP peer.

- One declaration of the gateway (WS-05): Gemini CLI's ACP mapping of an ``mcpServers`` entry keeps only its URL
  and headers and replaces the settings file's entry of that name, so the run's tool timeout and ``trust: false``
  were lost. A profile with ``mcp.declare: config`` sends no server in ``session/new`` and ``session/load``; its
  rendered config is the one declaration.
- The Tool Gateway as witness (E2E3-F6): the probe proves the gateway lists its tools a moment before the session
  opens; the gateway's own count proves the agent listed them while it opened. A session that opened with the
  count unchanged is ``EngineUnhealthy`` before any prompt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.ports import WitnessesToolGateway
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.testing.engines.contract import collect, open_rig
from harness_engines.acp.profiles import AcpProfileInvalid, parse_profile
from harness_engines.acp.testing.fake_agent import CONFIG_ENV
from harness_engines.acp.testing.profile import fake_entry, fake_profile
from tests.unit.harness_engines.acp.test_e2e3_f6_gateway_probe import (
    STATUS,
    ProbedHarness,
)

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from tests.unit.harness_engines.acp.conftest import AcpHarness

RUN = "run-wp59-witness"
GATEWAY_CONFIG: dict[str, JsonValue] = {
    "env": "FAKE_ACP_MCP",
    "template": {"mcpServers": {"${mcp_server}": {"httpUrl": "${tools_mcp_url}", "timeout": "${tool_timeout_ms}", "trust": False}}},
}
FAKE_CONFIG: dict[str, JsonValue] = {"env": CONFIG_ENV, "template": {"model": "${model}", "permission": {}}, "toolRules": ["permission"]}


def _declared(**mcp: JsonValue) -> dict[str, JsonValue]:
    return fake_entry(mcp={**STATUS, **mcp}, configs=[FAKE_CONFIG, GATEWAY_CONFIG])


def _sent(harness: AcpHarness, method: str) -> list[dict[str, JsonValue]]:
    return [
        json.loads(json.dumps(m["params"]))
        for f in harness.fakes.values()
        for c in f.connections
        for m in c.received
        if m.get("method") == method
    ]


class CountingWitness:
    """The Tool Gateway's count for one attempt: rises by one when the agent opens a session, if it gets through."""

    def __init__(self, harness: AcpHarness, *, agent_lists: bool) -> None:
        self._harness = harness
        self._agent_lists = agent_lists

    def listed(self) -> int:
        probe = 1
        opened = len(_sent(self._harness, "session/new")) + len(_sent(self._harness, "session/load"))
        return probe + (opened if self._agent_lists else 0)


class TestDeclare:
    def test_wp59_config_is_accepted_only_with_a_config_that_names_the_gateway(self) -> None:
        assert parse_profile(_declared(declare="config")).mcp_declare == "config"
        assert fake_profile().mcp_declare == "session"
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp={**STATUS, "declare": "config"}))
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(_declared(declare="both"))

    async def test_wp59_a_config_declared_gateway_is_sent_in_no_session_new_or_load(self) -> None:
        harness = ProbedHarness(profile=parse_profile(_declared(declare="config")))
        first = await open_rig(harness)
        assert isinstance((await collect(first.session, EngineTask(turn_id_for(RUN, 0), "say ok")))[-1], TurnCompleted)
        state = await first.session.capture_state()
        await first.session.close()
        second = await open_rig(harness, restore=state, gateway=first.gateway)
        assert isinstance((await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta")))[-1], TurnCompleted)
        await second.session.close()
        ((new,), (load,)) = (_sent(harness, "session/new"), _sent(harness, "session/load"))
        assert new["mcpServers"] == [] and load["mcpServers"] == []
        # The one declaration is the rendered config, with the run's tool timeout and trust off.
        rendered = json.loads(first.launch.env["FAKE_ACP_MCP"])["mcpServers"]["gw"]
        assert rendered["trust"] is False and rendered["timeout"] >= 150_000

    async def test_wp59_a_session_declared_gateway_is_still_sent(self) -> None:
        harness = ProbedHarness(profile=fake_profile(mcp=STATUS))
        rig = await open_rig(harness)
        _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
        await rig.session.close()
        ((new,),) = (_sent(harness, "session/new"),)
        assert [s["name"] for s in new["mcpServers"]] == ["gw"]  # pyright: ignore[reportIndexIssue, reportArgumentType, reportCallIssue, reportUnknownVariableType]


class TestWitness:
    def test_e2e3_f6_the_acp_session_takes_the_gateways_witness(self) -> None:
        from harness_engines.acp.session import AcpSession

        assert issubclass(AcpSession, WitnessesToolGateway)

    async def test_e2e3_f6_a_session_the_agent_opened_without_listing_the_tools_is_unhealthy(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness = ProbedHarness(profile=fake_profile(mcp=STATUS))
        rig = await open_rig(harness)
        rig.session.witness_tool_gateway(CountingWitness(harness, agent_lists=False))  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(EngineUnhealthy):
            _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
        assert _sent(harness, "session/new") and not _sent(harness, "session/prompt")
        assert "engine.tool_gateway.unlisted" in caplog.text
        await rig.session.close()

    async def test_e2e3_f6_a_session_the_agent_listed_the_tools_in_goes_on(self) -> None:
        harness = ProbedHarness(profile=fake_profile(mcp=STATUS))
        rig = await open_rig(harness)
        rig.session.witness_tool_gateway(CountingWitness(harness, agent_lists=True))  # pyright: ignore[reportAttributeAccessIssue]
        assert isinstance((await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok")))[-1], TurnCompleted)
        await rig.session.close()

    async def test_e2e3_f6_resume_is_witnessed_the_same_way(self) -> None:
        harness = ProbedHarness(profile=fake_profile(mcp=STATUS))
        first = await open_rig(harness)
        _ = await collect(first.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
        state = await first.session.capture_state()
        await first.session.close()
        second = await open_rig(harness, restore=state, gateway=first.gateway)
        second.session.witness_tool_gateway(CountingWitness(harness, agent_lists=False))  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(EngineUnhealthy):
            _ = await collect(second.session, FollowUp(turn_id_for(RUN, 1), "say beta"))
        assert _sent(harness, "session/load") and len(_sent(harness, "session/prompt")) == 1
        await second.session.close()
