"""E2E3-F6 (WP-59) for an ACP agent with no MCP status API (Gemini CLI): the driver asks the tool gateway itself.

Gemini CLI 0.63.0 connects its MCP servers inside ``session/new`` and ``session/load`` and waits there for a slow
one, but one that refused is left disconnected for the life of the session and every prompt goes out without its
tools; ACP has no request that reports or reconnects it. A profile whose ``mcp.status`` is
``{"api": "gateway", "runtime": ...}`` makes the driver list the gateway's tools from inside the sandbox, over the
agent's own route (the URL and the tools placeholder it is given), with the agent's own runtime, before it opens
the session; a gateway that never lists them is an engine that never came up (``EngineUnhealthy``), no session is
opened and no prompt is sent, and the run resumes in a fresh attempt once the gateway is up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

import pytest

from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.logs import HarnessLog
from harness_core.testing.engines.contract import ENDPOINTS, collect, open_rig
from harness_core.testing.virtual_time import run_on_virtual_time
from harness_engines.acp.gateway_probe import (
    PROBE_SCRIPT,
    TOKEN_ENV,
    await_gateway_tools,
)
from harness_engines.acp.profiles import AcpProfileInvalid, McpStatus, parse_profile
from harness_engines.acp.testing.profile import fake_entry, fake_profile
from tests.unit.harness.engines.mcp_startup_lab import NEVER, TOOLS_PREFIX, LateGateway
from tests.unit.harness_engines.acp.conftest import AcpHarness

if TYPE_CHECKING:
    from harness_core.testing.sandbox.egress import FakeEgress
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

RUNTIME = "/usr/bin/node"
STATUS = {"server": "gw", "toolName": "{server}_{tool}", "status": {"api": "gateway", "runtime": RUNTIME}}
RUN = "run-e2e3-f6-gateway"


def _task(index: int) -> EngineTask:
    return EngineTask(turn_id=turn_id_for(RUN, index), prompt="say ok")


class FakeNode:
    """The agent's runtime in the fake sandbox: answers each probe with the next scripted exit code (the last one
    repeats) and records what it was run with and what the agent had done by then."""

    def __init__(self, harness: AcpHarness, codes: list[int]) -> None:
        self.harness = harness
        self.codes = codes
        self.calls: list[tuple[tuple[str, ...], dict[str, str], list[str]]] = []

    def install(self, sandbox: FakeSandboxHandle) -> None:
        sandbox.install(RUNTIME, self.program)

    async def program(self, context: FakeProcessContext) -> int:
        self.calls.append((tuple(context.argv), dict(context.env), _methods(self.harness)))
        code = self.codes[min(len(self.calls), len(self.codes)) - 1]
        await context.emit(b"ready\n" if code == 0 else b"http 503\n")
        return code


@dataclass
class ProbedHarness(AcpHarness):
    codes: list[int] = field(default_factory=lambda: [0])

    def __post_init__(self) -> None:
        super().__post_init__()
        self.node = FakeNode(self, self.codes)

    @override
    async def new_sandbox(self, egress: FakeEgress) -> FakeSandboxHandle:
        sandbox = await super().new_sandbox(egress)
        self.node.install(sandbox)
        return sandbox


def _harness(codes: list[int], *, timeout_s: float = 5.0) -> ProbedHarness:
    return ProbedHarness(profile=fake_profile(mcp=STATUS), session_options={"gateway_timeout_s": timeout_s}, codes=codes)


def _methods(harness: AcpHarness) -> list[str]:
    return [str(m.get("method")) for f in harness.fakes.values() for c in f.connections for m in c.received if "method" in m]


class TestProfile:
    def test_e2e3_f6_a_gateway_status_names_the_runtime_that_asks(self) -> None:
        assert fake_profile(mcp=STATUS).mcp_status == McpStatus(api="gateway", runtime=RUNTIME)

    @pytest.mark.parametrize(
        "status",
        [
            {"api": "gateway"},
            {"api": "gateway", "runtime": "node"},
            {"api": "gateway", "runtime": "/usr/bin/../bin/node"},
            {"api": "gateway", "runtime": RUNTIME, "port": 4096},
            {"api": "opencode", "port": 4096, "runtime": RUNTIME},
        ],
    )
    def test_e2e3_f6_a_gateway_status_outside_the_schema_is_refused(self, status: dict[str, object]) -> None:
        with pytest.raises(AcpProfileInvalid):
            parse_profile(fake_entry(mcp={"server": "tools", "status": status}))  # pyright: ignore[reportArgumentType]


class TestSession:
    async def test_e2e3_f6_the_gateway_lists_its_tools_before_session_new(self) -> None:
        harness = _harness([7, 7, 0])
        rig = await open_rig(harness)
        events = await collect(rig.session, _task(0))
        assert isinstance(events[-1], TurnCompleted)
        (new,) = [m for f in harness.fakes.values() for c in f.connections for m in c.received if m.get("method") == "session/new"]
        (server,) = json.loads(json.dumps(new["params"]))["mcpServers"]
        assert (server["name"], server["url"]) == ("gw", ENDPOINTS.tools_mcp_url)
        (authorization,) = [h["value"] for h in server["headers"] if h["name"] == "Authorization"]
        placeholder = authorization.removeprefix("Bearer ")
        assert len(harness.node.calls) == 3
        for argv, env, before in harness.node.calls:
            # Asked over the agent's own route: the URL and the placeholder ``session/new`` gives the gateway.
            assert argv[:3] == (RUNTIME, "-e", PROBE_SCRIPT)
            assert argv[3] == server["url"]
            assert env == {TOKEN_ENV: placeholder}
            assert placeholder not in " ".join(argv)
            assert "session/new" not in before and "session/prompt" not in before
        # A gateway that accepts and answers slowly is waited for as long as the gate lasts, as Gemini itself waits:
        # an ask is bounded by what is left of the gate (5 s here), not cut short and started over.
        assert 4000 < int(harness.node.calls[0][0][4]) <= 5000
        await rig.session.close()

    async def test_e2e3_f6_a_gateway_that_never_lists_its_tools_opens_no_session_and_sends_no_prompt(self) -> None:
        harness = _harness([7], timeout_s=1.2)
        rig = await open_rig(harness)
        with pytest.raises(EngineUnhealthy):
            await collect(rig.session, _task(0))
        assert len(harness.node.calls) >= 2
        assert not {"session/new", "session/load", "session/prompt"} & set(_methods(harness))
        await rig.session.close()

    async def test_e2e3_f6_resume_after_an_unhealthy_open_loads_the_session_once_the_gateway_is_up(self) -> None:
        harness = _harness([0])
        first = await open_rig(harness)
        assert isinstance((await collect(first.session, _task(0)))[-1], TurnCompleted)
        state = await first.session.capture_state()
        await first.session.close()

        # The next attempt's gateway never comes up: no session is loaded, the attempt is unhealthy.
        harness.node.codes, harness.node.calls = [7], []
        down = await open_rig(harness, restore=state, gateway=first.gateway)
        with pytest.raises(EngineUnhealthy):
            await collect(down.session, FollowUp(turn_id_for(RUN, 1), "say beta"))
        down_methods = [str(m.get("method")) for m in harness.fakes[down.sandbox.id].connections[-1].received]
        assert "session/load" not in down_methods and "session/prompt" not in down_methods
        await down.session.close()

        # The attempt after it finds the gateway up and goes on in the same agent session.
        harness.node.codes, harness.node.calls = [0], []
        up = await open_rig(harness, restore=state, gateway=first.gateway)
        events = await collect(up.session, FollowUp(turn_id_for(RUN, 1), "say beta"))
        assert isinstance(events[-1], TurnCompleted) and events[-1].final_text == "beta"
        received = [str(m.get("method")) for m in harness.fakes[up.sandbox.id].connections[-1].received]
        assert "session/load" in received and "session/new" not in received
        ((_argv, _env, before),) = harness.node.calls
        assert "session/load" not in before
        await up.session.close()


class TestProbeLoop:
    def test_e2e3_f6_the_bound_is_the_gateway_timeout_and_the_reason_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def main() -> float:
            harness = _harness([7])
            rig = await open_rig(harness)
            loop = asyncio.get_running_loop()
            started = loop.time()
            with pytest.raises(EngineUnhealthy):
                await await_gateway_tools(
                    rig.sandbox, _log(), runtime=RUNTIME, server="gw", url="http://tool-gateway:8097/mcp",
                    placeholder="pxh-tools-x", timeout_s=1.0,
                )
            waited = loop.time() - started
            await rig.session.close()
            return waited

        waited = run_on_virtual_time(main)
        assert 1.0 <= waited < 3.0
        assert "engine.tool_gateway.unconnected" in caplog.text
        assert "http 503" in caplog.text and "pxh-tools-x" not in caplog.text


def _log() -> HarnessLog:
    return HarnessLog(logging.getLogger("tests.e2e3_f6_gateway_probe"))


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="needs Node on PATH (the probe script is the agent runtime's JavaScript)")
class TestProbeScript:
    """The script itself, on the host's Node, against the E2E3-F6 lab's late gateway (MCP Streamable HTTP)."""

    async def _run(self, gateway: LateGateway, *, timeout_ms: int = 4000) -> tuple[int, str]:
        assert NODE is not None
        async with gateway.serving("127.0.0.1") as base:
            process = await asyncio.create_subprocess_exec(
                NODE, "-e", PROBE_SCRIPT, base + TOOLS_PREFIX + "/mcp", str(timeout_ms),
                env={TOKEN_ENV: "pxh-tools-probe"}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode is not None
            return process.returncode, out.decode()

    async def test_e2e3_f6_a_ready_gateway_lists_its_tools(self) -> None:
        gateway = LateGateway()
        code, out = await self._run(gateway)
        assert (code, out.strip()) == (0, "ready")
        assert gateway.mcp_methods == ["initialize", "notifications/initialized", "tools/list"]

    async def test_e2e3_f6_a_slow_initialize_within_the_bound_is_ready(self) -> None:
        code, _ = await self._run(LateGateway(late_s=1.0))
        assert code == 0

    async def test_e2e3_f6_a_refusing_gateway_is_not_ready_and_says_why(self) -> None:
        code, out = await self._run(LateGateway(refuse_s=NEVER))
        assert code != 0 and out.strip() == "http 503"

    async def test_e2e3_f6_an_initialize_past_the_bound_is_not_ready(self) -> None:
        code, out = await self._run(LateGateway(late_s=5.0), timeout_ms=500)
        assert code != 0 and out.strip() == "timeout"

    async def test_e2e3_f6_a_gateway_with_no_tools_is_not_ready(self) -> None:
        gateway = LateGateway(tools=())
        code, out = await self._run(gateway)
        assert code != 0 and out.strip() == "no tools"
