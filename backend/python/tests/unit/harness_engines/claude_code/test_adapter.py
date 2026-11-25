"""CA-1..CA-3 (E9) and the session over a fake ``claude`` that replays a recorded 2.1.289 transcript.

The real binary runs in ``tests/unit/harness/engines/test_real_claude_code_conformance.py``; here a fake
program in the fake broker's sandbox stands in for it, so the adapter's checks, the protocol it speaks and
the state it saves are pinned without Docker.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.errors import (
    EngineBinaryMismatch,
    EngineSessionClosed,
    EngineUnhealthy,
    ToolNameTooLong,
)
from harness_core.engines.events import TurnCompleted
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineTask,
    FollowUp,
    GatewayEndpoints,
)
from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.sandbox.tar import CONFIG_MODE
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_engines.claude_code.adapter import (
    MAX_TOOL_NAME,
    ClaudeCodeAdapter,
    reported_version,
)
from harness_engines.claude_code.launch import (
    BASE_PROMPT,
    MCP_SERVER,
    SKILLS_ENV,
    SKILLS_LINK,
    TRANSCRIPT_DIR,
    exposed_tool_name,
)
from harness_engines.claude_code.state import decode_state
from harness_engines.release import release_allowlist

if TYPE_CHECKING:
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

CLAUDE = next(r.version for r in release_allowlist() if r.version.kind == "claude_code")
FIXTURES = Path(__file__).parent / "fixtures"
SESSION = "11111111-2222-4333-8444-555555555555"
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway:8097/v1",
    model_api=ModelApi.ANTHROPIC_MESSAGES,
    tools_mcp_url="http://tool-gateway:8097/mcp",
    token_placeholder="pxh-model-placeholder",
    tools_token_placeholder="pxh-tools-placeholder",
)


@dataclass
class FakeClaude:
    """The pinned ``claude`` as far as the adapter can tell: ``--version``, then stream-json replaying a transcript
    recorded from 2.1.289."""

    transcript: str = "text"
    version: str = CLAUDE.version
    digest: str = CLAUDE.binary_digest
    received: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    argv: list[list[str]] = field(default_factory=list[list[str]])
    # What ``mcp_status`` says of the tool gateway.
    gateway: str = "connected"

    async def claude(self, context: FakeProcessContext) -> int:
        self.argv.append(context.argv)
        if "--version" in context.argv:
            await context.stdout.put(f"{self.version} (Claude Code)\n".encode())
            return 0
        buffer = b""
        while chunk := await context.read_stdin():
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in (json.loads(raw) for raw in lines if raw.strip()):
                self.received.append(line)
                await self._answer(context, line)
        return 0

    async def _answer(self, context: FakeProcessContext, message: dict[str, Any]) -> None:
        if message["type"] == "control_request":
            # The tool gateway is connected at once: the adapter waits for it before any turn (E2E3-F6).
            body = {"mcpServers": [{"name": MCP_SERVER, "status": self.gateway}]}
            answer = body if message["request"]["subtype"] == "mcp_status" else {}
            response = {"subtype": "success", "request_id": message["request_id"], "response": answer}
            await context.stdout.put(json.dumps({"type": "control_response", "response": response}).encode() + b"\n")
            return
        lines = (FIXTURES / f"{self.transcript}.jsonl").read_text().splitlines()
        replay = [json.loads(line) for line in lines if json.loads(line).get("type") != "control_response"]
        for item in replay:
            if "session_id" in item:
                item["session_id"] = SESSION
            await context.stdout.put(json.dumps(item).encode() + b"\n")
            if item.get("type") == "result":
                break
        context.write_file(f"{TRANSCRIPT_DIR}/{SESSION}.jsonl", json.dumps(message).encode() + b"\n")


async def _sandbox(fake: FakeClaude) -> FakeSandboxHandle:
    broker = FakeSandboxBroker(clock=ManualClock(), modes=frozenset(RuntimeMode), programs=dict(FAKE_PROGRAMS))
    spec = SandboxSpec(
        image_digest="claude@sha256:" + "0" * 64,
        mode=RuntimeMode.DOCKER_HARDENED,
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
    )
    handle = await broker.acquire(spec, lease=LeaseRef(holder="e9", fence=1), idempotency_key="e9")
    handle.install("claude", fake.claude)
    handle.install("sha256sum", sha256sum_program(lambda _path: fake.digest))
    return handle


def _launch(skills_dir: str = "/skills") -> Any:  # noqa: ANN401
    profile = EngineProfile(engine=CLAUDE, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    return ClaudeCodeAdapter().render_launch(profile, ENDPOINTS, skills_dir)


async def _events(session: Any, task: Any) -> list[Any]:  # noqa: ANN401
    return await asyncio.wait_for(_drain(session, task), 10)


async def _drain(session: Any, task: Any) -> list[Any]:  # noqa: ANN401
    return [event async for event in session.run_turn(task)]


def test_ca1_capabilities() -> None:
    capabilities = ClaudeCodeAdapter().capabilities()
    assert (capabilities.can_steer, capabilities.native_skills, capabilities.sub_agents) == (True, True, True)
    assert capabilities.model_apis == frozenset({ModelApi.ANTHROPIC_MESSAGES})
    assert (capabilities.max_tools, capabilities.drive, capabilities.max_tool_name) == (None, "stdio", MAX_TOOL_NAME)


@pytest.mark.parametrize(
    ("printed", "version"),
    [("2.1.289 (Claude Code)\n", "2.1.289"), ("2.2.0-beta.1 (Claude Code)", "2.2.0-beta.1"), ("claude 2.1.289", "")],
)
def test_the_binary_reports_its_version(printed: str, version: str) -> None:
    assert reported_version(printed) == version


@pytest.mark.parametrize(("change", "value"), [("digest", "sha256:" + "f" * 64), ("version", "2.1.290")])
async def test_ca2_a_binary_that_is_not_the_pin_never_starts(change: str, value: str, caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeClaude(**{change: value})
    sandbox = await _sandbox(fake)
    with pytest.raises(EngineBinaryMismatch) as refused:
        await ClaudeCodeAdapter().open(sandbox, _launch(), None)
    assert refused.value.reason_code == "binary_mismatch"
    assert all("--version" in argv for argv in fake.argv), "the engine was started"
    assert "engine.binary.mismatch" in caplog.text


@pytest.mark.parametrize("code", [137, -9])
async def test_h1_eg6_a_binary_killed_as_it_starts_is_a_crash_not_a_mismatch(code: int) -> None:
    from harness_core.engines.errors import EngineCrashed

    fake = FakeClaude()

    async def killed(context: FakeProcessContext) -> int:
        fake.argv.append(context.argv)
        return code

    sandbox = await _sandbox(fake)
    sandbox.install("claude", killed)
    with pytest.raises(EngineCrashed) as gone:
        await ClaudeCodeAdapter().open(sandbox, _launch(), None)
    assert gone.value.reason_code == "engine_crashed"
    assert all("--version" in argv for argv in fake.argv), "the engine was started"


def test_h1_eg6_the_end_of_claude_codes_stream_is_a_crash() -> None:
    from harness_core.engines.errors import EngineCrashed
    from harness_engines.claude_code.stream import ClaudeStreamEnded

    assert issubclass(ClaudeStreamEnded, EngineCrashed)


def test_ca3_a_granted_tool_name_the_providers_cannot_carry_is_refused_at_the_grant() -> None:
    capabilities = ClaudeCodeAdapter().capabilities()
    assert MAX_TOOL_NAME == 52 and len(exposed_tool_name("t" * 52)) == 64
    EndpointConformanceGateway(endpoints=ENDPOINTS, grant=(GrantedTool("t" * 52),), capabilities=capabilities)
    with pytest.raises(ToolNameTooLong) as refused:
        EndpointConformanceGateway(endpoints=ENDPOINTS, grant=(GrantedTool("t" * 53),), capabilities=capabilities)
    assert (refused.value.reason_code, refused.value.longest, refused.value.max_length) == ("tool_name_too_long", 53, 52)


async def test_a_turn_initializes_with_the_instructions_maps_events_and_saves_the_transcript() -> None:
    fake = FakeClaude()
    sandbox = await _sandbox(fake)
    session = await ClaudeCodeAdapter().open(sandbox, _launch(), None, correlation={"runId": "r"})
    assert (await session.health()).engine_version == CLAUDE.version
    assert sandbox.state.files[SKILLS_LINK].data == b"/skills"
    events = await _events(session, EngineTask(turn_id="trn_1", prompt="say hello", instructions="House style."))
    assert isinstance(events[-1], TurnCompleted) and events[-1].final_text == "hello there"
    status, initialize, user = fake.received
    assert status["request"] == {"subtype": "mcp_status"}, "the tool gateway is connected before any turn (E2E3-F6)"
    assert initialize["request"] == {"subtype": "initialize", "appendSystemPrompt": f"{BASE_PROMPT}\n\nHouse style."}
    assert user["message"] == {"role": "user", "content": "say hello"}
    assert session.session_id == SESSION
    state = await session.capture_state()
    assert decode_state(state, CLAUDE).session_id == SESSION
    # A later turn's instructions travel with its message; the process is initialized once.
    await _events(session, EngineTask(turn_id="trn_2", prompt="again", instructions="Later rule."))
    assert [m["type"] for m in fake.received] == ["control_request", "control_request", "user", "user"]
    assert fake.received[-1]["message"]["content"] == "Later rule.\n\nagain"
    await session.close()
    await session.close()
    assert not (await session.health()).alive
    with pytest.raises(EngineSessionClosed):
        await _events(session, FollowUp(turn_id="trn_3", text="x"))


async def test_a_restored_session_is_resumed_by_its_id_in_a_fresh_sandbox() -> None:
    first = FakeClaude()
    session = await ClaudeCodeAdapter().open(await _sandbox(first), _launch(), None)
    await _events(session, EngineTask(turn_id="trn_1", prompt="say alpha"))
    state = await session.capture_state()
    await session.close()
    second = FakeClaude()
    sandbox = await _sandbox(second)
    resumed = await ClaudeCodeAdapter().open(sandbox, _launch(), state)
    assert sandbox.state.files[f"{TRANSCRIPT_DIR}/{SESSION}.jsonl"].data == decode_state(state, CLAUDE).transcript
    await _events(resumed, FollowUp(turn_id="trn_2", text="say beta"))
    started = next(argv for argv in second.argv if "--version" not in argv)
    assert started[-2:] == ["--resume", SESSION]
    assert [m.get("request", {}).get("subtype", m["type"]) for m in second.received] == ["mcp_status", "initialize", "user"]
    assert resumed.session_id == SESSION
    await resumed.close()


async def test_e2e3_f6_claude_code_whose_tool_gateway_never_connects_never_starts_a_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeClaude(gateway="failed")
    sandbox = await _sandbox(fake)
    with pytest.raises(EngineUnhealthy):
        await ClaudeCodeAdapter(gateway_timeout_s=0.3).open(sandbox, _launch(), None)
    subtypes = {m["request"]["subtype"] for m in fake.received}
    assert subtypes == {"mcp_status", "mcp_reconnect"}, "no turn and no initialize: the engine never came up"
    assert "engine.session.closed" in caplog.text, "the process is stopped"


async def test_claude_code_raises_no_ask_for_the_controller_to_answer() -> None:
    session = await ClaudeCodeAdapter().open(await _sandbox(FakeClaude()), _launch(""), None)
    with pytest.raises(EngineSessionClosed):
        await session.answer("ask-1", "approved")
    await session.close()


async def test_the_configuration_and_the_skills_link_are_written_read_only_by_the_controller() -> None:
    """SEC1 EC-7: what Claude Code obeys, and the link to its skills, are not the sandbox user's to change."""
    fake = FakeClaude()
    sandbox = await _sandbox(fake)
    launch = _launch()
    session = await ClaudeCodeAdapter().open(sandbox, launch, None)
    files = sandbox.state.files
    assert all(files[path].mode == CONFIG_MODE for path in launch.files)
    link = files[SKILLS_LINK]
    assert link.is_symlink and link.data.decode() == launch.env[SKILLS_ENV]
    await session.close()


async def test_fixp2_b2_a_resumed_sessions_first_turn_carries_the_runs_instructions() -> None:
    """FIX-P2-B2: a run that resumes the conversation's Claude Code session (its second request) never gave the model
    its own instructions: Claude Code 2.1.294 keeps the resumed session's system prompt, so the memories accepted
    since the session started (rendered into the run's instructions) were missing from every model request (S3
    memory-basics-15m, 2026-10-22). On a resumed session, the run's instructions travel with its first turn."""
    first = FakeClaude()
    session = await ClaudeCodeAdapter().open(await _sandbox(first), _launch(), None)
    await _events(session, EngineTask(turn_id="trn_1", prompt="say alpha", instructions="House style."))
    state = await session.capture_state()
    await session.close()
    second = FakeClaude()
    resumed = await ClaudeCodeAdapter().open(await _sandbox(second), _launch(), state)
    instructions = "House style.\n\n<remembered>x</remembered>"
    await _events(resumed, EngineTask(turn_id="trn_2", prompt="say beta", instructions=instructions))
    _, initialize, user = second.received
    assert initialize["request"]["appendSystemPrompt"].endswith("<remembered>x</remembered>")
    assert user["message"]["content"] == f"{instructions}\n\nsay beta"
    # The next turn of the same process does not repeat them.
    await _events(resumed, EngineTask(turn_id="trn_3", prompt="again", instructions=instructions))
    assert second.received[-1]["message"]["content"] == "again"
    await resumed.close()
