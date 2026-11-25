"""E2E3-F6 on Claude Code, opencode and the ACP driver (opencode and Gemini CLI): the first model request waits for a
late tool gateway.

Codex 0.160 sends a turn's first model request without a late MCP server's tools unless the server is ``required``
(``harness_engines/codex``). Here each other engine runs on its pinned binary, in its release image under the Docker
broker, behind the egress proxy, against a scripted model and a tool gateway that is late (``mcp_startup_lab``): it
answers ``initialize`` after 3 s or 10 s, refuses (503, what the proxy answers while nothing listens behind it) for
the first 3 s, or never comes up. Every turn that starts carries the gateway's tool in its first model request; a
gateway that never comes up is an engine that never came up (``EngineUnhealthy``), and no model request is made.

The controls pin the vendor behaviour the fix rests on: without the driver's readiness gate, Claude Code 2.1.294,
opencode 1.18.34 and opencode's own ACP server each send the first request without the gateway's tools when the
gateway refused at start (all three already wait for a gateway that accepts and answers slowly). The gate asks each
engine's own status API: Claude Code's ``mcp_status`` control request, opencode's ``GET /mcp`` (natively, and on the
ACP server's own port, ``mcp.status`` in its profile).

Gemini CLI 0.63.0 over ACP (WP-59) waits inside ``session/new`` for a gateway that answers late, but one that refused
is never retried and the prompt goes out without its tools; it has no status API and reports nothing. Its profile's
``mcp.status`` is ``gateway``: the driver lists the gateway's tools from inside the sandbox, with Gemini's own Node,
before it opens the session (``harness_engines.acp.gateway_probe``).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.events import TurnCompleted, turn_id_for
from harness_core.engines.ports import WitnessesToolGateway
from harness_core.engines.types import (
    SEALED_DIR,
    EngineMode,
    EngineProfile,
    EngineTask,
    FollowUp,
    GatewayEndpoints,
)
from harness_core.gateway.types import RunAttemptBinding
from harness_core.sandbox.tar import put_files
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.engines.contract import SKILLS_DIR
from harness_core.workspace.sync.archive import Owner
from tests.unit.harness.engines.lane_releases import lane_acp_profile, lane_release
from tests.unit.harness.engines.mcp_startup_lab import (
    MODEL_PREFIX,
    NEVER,
    PROBE_CLIENT,
    TOOL,
    TOOLS_PREFIX,
    LateGateway,
)
from tests.unit.harness.engines.real_stack import OWNER, engine_broker, lane_available
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.types import EngineLaunch, EngineVersion
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
LEASE = LeaseRef("worker-e2e3f6", 1)
ATTEMPT = "att-e2e3f6"
# Short enough that the never-up case ends the test quickly, past every late gateway here.
GATE_S = 20.0
TURN_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Engine:
    name: str
    version: EngineVersion
    image: str
    model_api: ModelApi
    adapter: Callable[[float], Any]
    exposed: Callable[[str], str]
    memory_mb: int = 1024


def _claude_code() -> Engine:
    from harness_engines.claude_code.adapter import ClaudeCodeAdapter
    from harness_engines.claude_code.launch import exposed_tool_name

    release = lane_release("claude_code")
    return Engine(
        "claude_code", release.version, release.image, ModelApi.ANTHROPIC_MESSAGES,
        lambda gate_s: ClaudeCodeAdapter(gateway_timeout_s=gate_s), exposed_tool_name,
    )


def _opencode() -> Engine:
    from harness_engines.opencode.adapter import OpencodeAdapter
    from harness_engines.opencode.launch import exposed_tool_name

    release = lane_release("opencode")
    return Engine(
        "opencode", release.version, release.image, ModelApi.CHAT_COMPLETIONS,
        lambda gate_s: OpencodeAdapter(gateway_timeout_s=gate_s), exposed_tool_name,
    )


def _acp(agent: str, *, memory_mb: int = 1024, **changes: Any) -> Engine:  # noqa: ANN401
    import dataclasses

    from harness_engines.acp.adapter import acp_adapter

    profile = dataclasses.replace(lane_acp_profile(agent), **changes)
    return Engine(
        profile.kind, profile.engine, profile.image, profile.model_api,
        lambda gate_s: acp_adapter(profile, gateway_timeout_s=gate_s), profile.exposed_tool_name, memory_mb,
    )


ENGINES: dict[str, Callable[[], Engine]] = {
    "claude_code": _claude_code,
    "opencode": _opencode,
    "acp-opencode": lambda: _acp("opencode"),
    # Gemini CLI's Node bundle needs more than the others (as in its conformance lane).
    "acp-gemini": lambda: _acp("gemini", memory_mb=1536),
}


@pytest.fixture(params=sorted(ENGINES))
def engine(request: pytest.FixtureRequest) -> Engine:
    return ENGINES[request.param]()


@pytest.fixture
async def broker(stack: RealStack, engine: Engine) -> AsyncIterator[DockerSandboxBroker]:
    available, reason = lane_available(engine.image)
    if not available:
        pytest.skip(reason)
    namespace = f"io.pipeshub.e2e3f6.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e2e3f6-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=engine.image)
    try:
        yield created
    finally:
        await created.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()


async def _one_turn(
    engine: Engine,
    broker: DockerSandboxBroker,
    stack: RealStack,
    gateway: LateGateway,
    *,
    gate_s: float = GATE_S,
    restore: EngineState | None = None,
    states: list[EngineState] | None = None,
    launch_edit: Callable[[EngineLaunch], EngineLaunch] | None = None,
    witness: bool = False,
    stop_after_s: float | None = None,
    repo_files: Mapping[str, bytes] | None = None,
) -> list[EngineEvent]:
    """Opens the engine in a fresh sandbox (from ``restore``, as a resumed attempt does) and runs one turn; the
    engine's state after it is appended to ``states``; the sandbox is released whatever happens.

    ``launch_edit`` changes the rendered launch (a short tool timeout); ``witness`` hands the session the lab
    gateway's tool-list count, as the controller hands it the Tool Gateway's; ``stop_after_s`` interrupts a turn
    still running then (a control that would otherwise wait for the engine's own 10-minute default)."""
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    binding = RunAttemptBinding("tenant", "scope", run_id, None, ATTEMPT, 1, "", "")
    # The proxy swaps each placeholder for the token Node issues for the sandbox's run and attempt.
    stack.models.bind(run_id, ATTEMPT, binding)
    async with gateway.serving(stack.host_ip) as base:
        placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
        spec = SandboxSpec(
            image_digest=engine.image,
            mode=RuntimeMode.DOCKER_HARDENED,
            resources=Resources(cpus=1.0, memory_mb=engine.memory_mb, pids=256, scratch_mb=256),
            egress=EgressPolicy(
                destinations=(
                    EgressDestination("model-gateway", (8097,), upstream=base + MODEL_PREFIX),
                    EgressDestination("tool-gateway", (8097,), upstream=base + TOOLS_PREFIX),
                ),
                token_routes=(
                    TokenRoute(placeholders[0], "model-gateway", "gateway:model"),
                    TokenRoute(placeholders[1], "tool-gateway", "gateway:tools"),
                ),
            ),
            mounts=(
                MountSpec("/workspace"),
                MountSpec("/state"),
                skills_mount(SKILLS_DIR),
                # Root's and read-only, seeded from outside: what an engine obeys above a repository (WP-59).
                MountSpec(SEALED_DIR, read_only=True),
            ),
            labels={
                **OWNER,
                "pipeshub.agent": "E2E3-F6",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": ATTEMPT,
            },
        )
        handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"e2e3f6-{uuid.uuid4().hex}")
        try:
            adapter = engine.adapter(gate_s)
            endpoints = GatewayEndpoints(
                model_base_url=MODEL_URL,
                model_api=engine.model_api,
                tools_mcp_url=TOOLS_URL,
                token_placeholder=placeholders[0],
                tools_token_placeholder=placeholders[1],
            )
            profile = EngineProfile(engine=engine.version, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
            launch = adapter.render_launch(profile, endpoints, SKILLS_DIR)
            if launch_edit is not None:
                launch = launch_edit(launch)
            if repo_files:
                # What a cloned repository brings: the sandbox user's files in the workspace.
                await put_files(handle, {f"/workspace/{p}": c for p, c in repo_files.items()}, owner=Owner())
            session = await adapter.open(handle, launch, restore)
            if witness:
                assert isinstance(session, WitnessesToolGateway)
                session.witness_tool_gateway(gateway)
            try:
                task: EngineTask | FollowUp = (
                    EngineTask(turn_id=turn_id_for("run-e2e3f6", 0), prompt="search the knowledge base for x")
                    if restore is None
                    else FollowUp(turn_id_for("run-e2e3f6", 1), "search the knowledge base for y")
                )

                async def drain() -> list[EngineEvent]:
                    return [event async for event in session.run_turn(task)]

                if stop_after_s is None:
                    events = await asyncio.wait_for(drain(), TURN_TIMEOUT_S)
                else:
                    running = asyncio.get_running_loop().create_task(drain())
                    _ = await asyncio.wait({running}, timeout=stop_after_s)
                    if not running.done():
                        await session.interrupt()
                    events = await asyncio.wait_for(running, TURN_TIMEOUT_S)
                if states is not None:
                    states.append(await session.capture_state())
                return events
            finally:
                await session.close()
        finally:
            await broker.release(handle)


LATE = {
    "initialize_after_3s": LateGateway(late_s=3.0),
    "initialize_after_10s": LateGateway(late_s=10.0),
    "refused_for_3s": LateGateway(refuse_s=3.0),
}


@pytest.mark.parametrize("late", sorted(LATE))
async def test_e2e3_f6_a_late_tool_gateway_still_gives_the_first_model_request_its_tools(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack, late: str
) -> None:
    gateway = LateGateway(late_s=LATE[late].late_s, refuse_s=LATE[late].refuse_s)
    events = await _one_turn(engine, broker, stack, gateway)
    assert isinstance(events[-1], TurnCompleted), events[-1]
    assert engine.exposed(TOOL) in gateway.first_request_tools(), gateway.requested_tools


async def test_e2e3_f6_a_tool_gateway_that_never_comes_up_is_an_engine_that_never_came_up(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack
) -> None:
    gateway = LateGateway(refuse_s=NEVER)
    with pytest.raises(EngineUnhealthy):
        await _one_turn(engine, broker, stack, gateway, gate_s=8.0)
    assert gateway.model_requests == 0, "no turn may start without the gateway's tools"


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
async def test_e2e3_f6_wp59_gemini_resumes_in_its_own_session_after_an_attempt_whose_gateway_never_came_up(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack, caplog: pytest.LogCaptureFixture
) -> None:
    """The attempt after an unhealthy one (the controller releases it and resumes) loads Gemini's session from the
    state the last good attempt left, once its gateway lists its tools, and the turn carries them and the earlier
    turn."""
    states: list[EngineState] = []
    _ = await _one_turn(engine, broker, stack, LateGateway(), states=states)
    (state,) = states
    down = LateGateway(refuse_s=NEVER)
    with pytest.raises(EngineUnhealthy):
        _ = await _one_turn(engine, broker, stack, down, gate_s=8.0, restore=state)
    assert down.model_requests == 0
    late = LateGateway(refuse_s=3.0)
    events = await _one_turn(engine, broker, stack, late, restore=state)
    assert isinstance(events[-1], TurnCompleted), events[-1]
    assert engine.exposed(TOOL) in late.first_request_tools(), late.requested_tools
    assert "search the knowledge base for x" in late.request_bodies[0]
    assert "engine.session.load_refused" not in caplog.text


# WP-59: one declaration of Gemini's gateway, in its system settings (round 3: the layer a repository's workspace
# settings cannot override), proven by what Gemini enforces.
GEMINI_SETTINGS = f"{SEALED_DIR}/gemini/settings.json"
# Short, so a hung call is seen cut within the test; the engine's own default would be 10 minutes.
SHORT_TOOL_TIMEOUT_MS = 4000
HUNG_S = 60.0


def _short_tool_timeout(launch: EngineLaunch) -> EngineLaunch:
    import dataclasses
    import json

    settings = json.loads(launch.files[GEMINI_SETTINGS])
    (server,) = settings["mcpServers"].values()
    assert server["trust"] is False and server["timeout"] >= 150_000
    server["timeout"] = SHORT_TOOL_TIMEOUT_MS
    return dataclasses.replace(launch, files={**launch.files, GEMINI_SETTINGS: json.dumps(settings).encode()})


def _record_permissions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every permission ask Gemini sent the driver over ACP, as the driver classified it."""
    from harness_engines.acp import session as acp_session

    seen: list[tuple[str, str]] = []
    real = acp_session.classify

    def recording(policy: Any, call: Any) -> Any:  # noqa: ANN401
        verdict = real(policy, call)
        seen.append(verdict)
        return verdict

    monkeypatch.setattr(acp_session, "classify", recording)
    return seen


def _assert_cut_at_the_run_timeout(gateway: LateGateway) -> None:
    assert gateway.tool_calls_at, ("the model's call never reached the gateway", gateway.mcp_methods)
    assert len(gateway.model_at) >= 2, "Gemini never answered the model after the hung call"
    waited = gateway.model_at[1] - gateway.tool_calls_at[0]
    assert SHORT_TOOL_TIMEOUT_MS / 1000 - 0.5 <= waited < HUNG_S / 4, waited
    assert "functionResponse" in gateway.request_bodies[1]


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
@pytest.mark.parametrize("resumed", [False, True], ids=["session_new", "session_load"])
async def test_wp59_gemini_cuts_a_hung_gateway_tool_at_the_runs_tool_timeout_and_asks_the_driver_nothing(
    engine: Engine,
    broker: DockerSandboxBroker,
    stack: RealStack,
    monkeypatch: pytest.MonkeyPatch,
    resumed: bool,
) -> None:
    """The rendered settings declare the gateway (no ``mcpServers`` in ``session/new`` or ``session/load``): a tool
    call that hangs is cut at the run's tool timeout, not at Gemini's 10-minute default.

    Pinned with it: Gemini sends no ``session/request_permission`` for a gateway tool, ``trust: false`` or not. The
    image floor's ``mcp.allowed: ["tools"]`` is also an ALLOW policy rule for ``mcp_tools_*`` in Gemini CLI 0.63.0
    ("Settings (MCP Allowed)"), and the driver would allow a gateway tool anyway: the run's approvals are the Tool
    Gateway's. A release that starts asking shows up here."""
    restore = None
    if resumed:
        states: list[EngineState] = []
        _ = await _one_turn(engine, broker, stack, LateGateway(), states=states)
        (restore,) = states
    asks = _record_permissions(monkeypatch)
    gateway = LateGateway(gemini_calls=engine.exposed(TOOL), tool_hang_s=HUNG_S)
    events = await _one_turn(engine, broker, stack, gateway, restore=restore, launch_edit=_short_tool_timeout, witness=True)
    assert isinstance(events[-1], TurnCompleted), events[-1]
    _assert_cut_at_the_run_timeout(gateway)
    assert ("allow", "gateway_tool") not in asks, asks


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
async def test_wp59_control_a_gateway_sent_in_session_new_loses_the_runs_tool_timeout(
    broker: DockerSandboxBroker, stack: RealStack, engine: Engine
) -> None:
    """Pins why the entry is not sent over ACP: Gemini CLI 0.63.0 maps it to the URL and headers alone and lets it
    replace the settings entry, so the same hung call is still waiting long past the run's tool timeout."""
    sent = _acp("gemini", memory_mb=1536, mcp_declare="session")
    gateway = LateGateway(gemini_calls=sent.exposed(TOOL), tool_hang_s=HUNG_S)
    stop_s = 5 * SHORT_TOOL_TIMEOUT_MS / 1000
    events = await _one_turn(sent, broker, stack, gateway, launch_edit=_short_tool_timeout, stop_after_s=stop_s)
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "interrupted", events[-1]
    assert gateway.tool_calls_at, gateway.mcp_methods
    assert len(gateway.model_at) == 1, "the call was cut, so the entry's timeout did reach Gemini"


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
async def test_e2e3_f6_wp59_gemini_whose_own_connect_is_refused_after_the_probe_is_unhealthy(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack
) -> None:
    """The probe is served the tool list, then the gateway refuses everything: Gemini opens its session without the
    tools and says nothing; the gateway's count did not move, so no prompt is sent."""
    gateway = LateGateway(refuse_after_probe=True)
    with pytest.raises(EngineUnhealthy):
        _ = await _one_turn(engine, broker, stack, gateway, witness=True)
    assert PROBE_CLIENT and gateway.model_requests == 0


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
async def test_e2e3_f6_wp59_control_without_the_witness_that_session_runs_without_the_tools(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack
) -> None:
    gateway = LateGateway(refuse_after_probe=True)
    events = await _one_turn(engine, broker, stack, gateway)
    assert isinstance(events[-1], TurnCompleted)
    assert engine.exposed(TOOL) not in gateway.first_request_tools(), gateway.requested_tools


# WP-59 round 3: what a repository's own ``.gemini/settings.json`` tries with the gateway's entry. Gemini CLI merges
# a trusted workspace's settings over the user's, one MCP server name at a time; only the system layer is above it.
REPO_TOOLS: dict[str, dict[str, Any]] = {
    "different_url": {"httpUrl": "http://tool-gateway:8097/elsewhere", "headers": {"Authorization": "Bearer $HARNESS_TOOLS_TOKEN"}},
    "longer_timeout": {
        "httpUrl": TOOLS_URL,
        "headers": {"Authorization": "Bearer $HARNESS_TOOLS_TOKEN"},
        "timeout": 600_000,
        "trust": True,
    },
    "extra_headers": {
        "httpUrl": TOOLS_URL,
        "headers": {"Authorization": "Bearer $HARNESS_TOOLS_TOKEN", "X-Repo-Header": "from-the-repository"},
    },
}


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
@pytest.mark.parametrize("resumed", [False, True], ids=["session_new", "session_load"])
@pytest.mark.parametrize("attempt", sorted(REPO_TOOLS))
async def test_wp59_a_repositorys_gemini_settings_never_touch_the_gateway_entry(
    engine: Engine,
    broker: DockerSandboxBroker,
    stack: RealStack,
    attempt: str,
    resumed: bool,
) -> None:
    """The repository's ``tools`` entry loses to PipesHub's, which sits in Gemini's system settings: the run reaches
    only ``/mcp`` with no header of the repository's, a hung tool is cut at the run's timeout, and the gateway's
    witness sees Gemini list the tools."""
    import json

    repo = {".gemini/settings.json": json.dumps({"mcpServers": {"tools": REPO_TOOLS[attempt]}}).encode()}
    restore = None
    if resumed:
        states: list[EngineState] = []
        _ = await _one_turn(engine, broker, stack, LateGateway(), states=states, repo_files=repo, witness=True)
        (restore,) = states
    gateway = LateGateway(gemini_calls=engine.exposed(TOOL), tool_hang_s=HUNG_S)
    events = await _one_turn(
        engine, broker, stack, gateway, restore=restore, launch_edit=_short_tool_timeout, witness=True, repo_files=repo
    )
    assert isinstance(events[-1], TurnCompleted), events[-1]
    _assert_cut_at_the_run_timeout(gateway)
    assert {path for path, _ in gateway.mcp_requests} == {"mcp"}, gateway.mcp_requests
    assert not any("x-repo-header" in headers for _, headers in gateway.mcp_requests)


@pytest.mark.parametrize("engine", ["acp-gemini"], indirect=True)
async def test_wp59_control_the_same_repository_entry_wins_over_user_settings(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack
) -> None:
    """Pins why the entry is in the system layer: declared as user settings (round 2), the repository's ``tools``
    entry replaces it and Gemini goes where the repository says."""
    import dataclasses
    import json

    user_settings = "/state/acp/gemini/.gemini/settings.json"

    def as_user_settings(launch: EngineLaunch) -> EngineLaunch:
        system = json.loads(launch.files[GEMINI_SETTINGS])
        user = json.loads(launch.files[user_settings])
        user["mcpServers"] = system.pop("mcpServers")
        files = {**launch.files, user_settings: json.dumps(user).encode(), GEMINI_SETTINGS: json.dumps(system).encode()}
        return dataclasses.replace(launch, files=files)

    repo = {".gemini/settings.json": json.dumps({"mcpServers": {"tools": REPO_TOOLS["different_url"]}}).encode()}
    gateway = LateGateway()
    events = await _one_turn(engine, broker, stack, gateway, launch_edit=as_user_settings, repo_files=repo)
    assert isinstance(events[-1], TurnCompleted)
    assert "elsewhere" in {path for path, _ in gateway.mcp_requests}, gateway.mcp_requests


# Where each adapter's readiness gate lives; the control replaces it with nothing.
GATES: dict[str, str] = {
    "acp-gemini": "harness_engines.acp.session.await_gateway_tools",
    "acp-opencode": "harness_engines.acp.session.await_tool_gateway",
    "claude_code": "harness_engines.claude_code.adapter.await_tool_gateway",
    "opencode": "harness_engines.opencode.adapter.await_tool_gateway",
}


@pytest.mark.parametrize("engine", sorted(GATES), indirect=True)
async def test_e2e3_f6_control_without_the_gate_a_refused_gateway_leaves_the_first_request_without_tools(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ungated(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(GATES[engine.name], ungated)
    gateway = LateGateway(refuse_s=3.0)
    events = await _one_turn(engine, broker, stack, gateway)
    assert isinstance(events[-1], TurnCompleted)
    assert engine.exposed(TOOL) not in gateway.first_request_tools(), gateway.requested_tools

