"""WP91-QWEN-W (C2.41): an agent that connects its MCP servers in the background after ``session/new`` gets its first
prompt only once the Tool Gateway has witnessed it list the tools.

Qwen Code 0.26.5 over ACP answers ``session/new`` at once and discovers its MCP servers afterwards
(``startMcpDiscoveryInBackground``), so a prompt sent on the answer goes out without the gateway's tools (WP-76, on the
real binary). Nothing in ACP or its settings reports readiness; the gateway's own count of tool lists served to the
attempt does. A profile says so with ``mcp.status.connects: background``: the driver then waits, bounded by the
gateway timeout, for the count to rise; Gemini CLI (``connects: session``, the default) connects inside
``session/new`` and is checked once, as before.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask
from harness_core.testing.engines.contract import collect, open_rig
from harness_core.testing.virtual_time import run_on_virtual_time
from harness_engines.acp.profiles import AcpProfileInvalid, McpStatus, parse_profile
from harness_engines.acp.testing.profile import fake_entry, fake_profile
from tests.unit.harness_engines.acp.test_e2e3_f6_gateway_probe import (
    RUNTIME,
    STATUS,
    ProbedHarness,
)

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue
    from tests.unit.harness_engines.acp.conftest import AcpHarness

RUN = "run-wp91-qwen-witness"
BACKGROUND: dict[str, JsonValue] = {**STATUS, "status": {"api": "gateway", "runtime": RUNTIME, "connects": "background"}}


def _sent(harness: AcpHarness, method: str) -> list[dict[str, JsonValue]]:
    return [
        json.loads(json.dumps(m["params"]))
        for f in harness.fakes.values()
        for c in f.connections
        for m in c.received
        if m.get("method") == method
    ]


class LateWitness:
    """The gateway's count for one attempt: the probe's list, then the agent's own a while after its session opened
    (``rises_after`` reads of the count), or never."""

    def __init__(self, harness: AcpHarness, *, rises_after: int | None) -> None:
        self._harness = harness
        self._rises_after = rises_after
        self.reads = 0
        self.prompts_before_rise: int | None = None

    def listed(self) -> int:
        opened = bool(_sent(self._harness, "session/new") or _sent(self._harness, "session/load"))
        if opened:
            self.reads += 1
        risen = opened and self._rises_after is not None and self.reads > self._rises_after
        if risen and self.prompts_before_rise is None:
            self.prompts_before_rise = len(_sent(self._harness, "session/prompt"))
        return 1 + (1 if risen else 0)


class TestProfile:
    def test_wp91_qwen_w1_a_gateway_status_may_say_the_agent_connects_in_the_background(self) -> None:
        profile = parse_profile(fake_entry(mcp=BACKGROUND))
        assert profile.mcp_status == McpStatus(api="gateway", runtime=RUNTIME, connects="background")
        assert profile.mcp_status is not None and profile.mcp_status.waits_for_witness
        default = fake_profile(mcp=STATUS).mcp_status
        assert default is not None and default.connects == "session" and not default.waits_for_witness

    @pytest.mark.parametrize("connects", ["later", 1, None])
    def test_wp91_qwen_w1_any_other_connect_mode_is_refused(self, connects: JsonValue) -> None:
        status: dict[str, JsonValue] = {"api": "gateway", "runtime": RUNTIME, "connects": connects}
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp={**STATUS, "status": status}))

    def test_wp91_qwen_w1_only_the_gateway_status_takes_a_connect_mode(self) -> None:
        status: dict[str, JsonValue] = {"api": "opencode", "port": 4096, "connects": "background"}
        with pytest.raises(AcpProfileInvalid):
            _ = parse_profile(fake_entry(mcp={**STATUS, "status": status}))


class TestWait:
    async def test_wp91_qwen_w2_the_first_prompt_waits_until_the_gateway_saw_the_agent_list_its_tools(self) -> None:
        harness = ProbedHarness(profile=parse_profile(fake_entry(mcp=BACKGROUND)), session_options={"gateway_timeout_s": 5.0})
        rig = await open_rig(harness)
        witness = LateWitness(harness, rises_after=3)
        rig.session.witness_tool_gateway(witness)  # pyright: ignore[reportAttributeAccessIssue]
        assert isinstance((await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok")))[-1], TurnCompleted)
        assert witness.reads > 3 and witness.prompts_before_rise == 0
        assert len(_sent(harness, "session/prompt")) == 1
        await rig.session.close()

    def test_wp91_qwen_w3_an_agent_that_never_lists_them_gets_no_prompt_and_is_unhealthy(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def main() -> None:
            harness = ProbedHarness(
                profile=parse_profile(fake_entry(mcp=BACKGROUND)), session_options={"gateway_timeout_s": 0.3}
            )
            rig = await open_rig(harness)
            rig.session.witness_tool_gateway(LateWitness(harness, rises_after=None))  # pyright: ignore[reportAttributeAccessIssue]
            loop = asyncio.get_running_loop()
            started = loop.time()
            with pytest.raises(EngineUnhealthy):
                _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
            # Bounded by the gateway timeout, not by the turn.
            assert loop.time() - started < 5.0
            assert _sent(harness, "session/new") and not _sent(harness, "session/prompt")
            await rig.session.close()

        run_on_virtual_time(main)
        assert "engine.tool_gateway.unlisted" in caplog.text

    def test_wp91_qwen_w4_an_agent_that_connects_inside_session_new_is_checked_once_and_not_waited_for(self) -> None:
        """Gemini CLI connects while it opens the session and never retries: the count is read once; a wait would
        only delay the same refusal."""

        async def main() -> None:
            harness = ProbedHarness(profile=fake_profile(mcp=STATUS), session_options={"gateway_timeout_s": 30.0})
            rig = await open_rig(harness)
            witness = LateWitness(harness, rises_after=1)
            rig.session.witness_tool_gateway(witness)  # pyright: ignore[reportAttributeAccessIssue]
            loop = asyncio.get_running_loop()
            started = loop.time()
            with pytest.raises(EngineUnhealthy):
                _ = await collect(rig.session, EngineTask(turn_id_for(RUN, 0), "say ok"))
            assert loop.time() - started < 5.0
            assert witness.reads == 1
            await rig.session.close()

        run_on_virtual_time(main)
