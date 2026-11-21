"""RX-CC1..RX-CC7 (E9): ``EngineAdapterContract`` on the real Claude Code binary behind the real gateways.

Claude Code 2.1.289 runs in ``pxh-claude-code`` (built locally from the admin-installed binary, never
published) under the Docker broker (gVisor or runc, ``lane_mode``), reaches the gateways only through the P5.4 proxy with its two
placeholders, and the proxy injects gateway tokens minted as Node mints them. The tool side is E2's
``RunToolEndpoint`` served by ``ToolGatewayApp`` over MCP Streamable HTTP; the model side is E3's
``ModelGateway`` on E3c's ``/v1/messages``, whose real ``OpenAITransport`` calls the conformance gateway's
scripted model as the org's provider. Approvals use the conformance gateway's fake ``InteractionBridge``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ModelApi, PinnedRoute, RunBudget, route_digest
from harness_core.engines.controller import BUDGET_STOP_CODES
from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.events import EngineError, TurnCompleted, turn_id_for
from harness_core.engines.native_policy import POLICY_CHECK_TOOL, NativeCallPolicy
from harness_core.engines.types import (
    EngineTask,
    FollowUp,
    GatewayEndpoints,
    SubAgentPolicy,
)
from harness_core.gateway.messages_wire import ACCEPTED_BETAS
from harness_core.interactions.ports import RunBinding
from harness_core.sandbox.tar import tar_stream
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    SandboxSpec,
    TokenRoute,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.engines.contract import (
    DEFAULT_GRANT,
    SKILL,
    SKILLS_DIR,
    EngineAdapterContract,
    Rig,
    make_profile,
)
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_core.testing.sandbox.contract import run
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.claude_code.launch import NATIVE_TOOLS, exposed_tool_name
from harness_engines.pipeshub import POLICY_HOOK_PATH, pipeshub_tool_folder
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.engines.real_stack import (
    OWNER,
    PROVIDER_KEY,
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    lane_mode,
)
from tests.unit.harness.engines.test_real_conformance import (
    RecordingEndpoint,
    _RecordingSandbox,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.engines.events import EngineState
    from harness_core.engines.policy import NativeTool
    from harness_core.engines.types import EngineProfile, EngineVersion
    from harness_core.testing.engines.gateway import ConformanceGateway
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

IMAGE: str = lane_release("claude_code").image
MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
LEASE = LeaseRef("worker-e9", 1)
# What Claude Code may send the model gateway: the Messages route and its token count.
# Past Codex's cap (E8 RX-C4).
LARGE_GRANT = 2049
MESSAGES_PATHS = frozenset({("POST", "/v1/messages"), ("POST", "/v1/messages/count_tokens")})


def _pinned() -> EngineVersion:
    return lane_release("claude_code").version


@dataclass
class RealClaudeCode:
    """``EngineConformanceHarness`` for the real binary; sandboxes come from the broker."""

    adapter: ClaudeCodeAdapter = field(default_factory=ClaudeCodeAdapter)
    version: EngineVersion = field(default_factory=_pinned)
    issued: list[str] = field(default_factory=list[str])

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset(NATIVE_TOOLS.get(tool, ()))

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({"TodoWrite"})

    def exposed_name(self, granted: str) -> str:
        return exposed_tool_name(granted)

    async def new_sandbox(self, egress: FakeEgress) -> Any:  # noqa: ANN401
        raise NotImplementedError("the real lane opens sandboxes through the broker")

    def secrets(self) -> frozenset[str]:
        return frozenset(self.issued)


@dataclass
class Opened:
    rig: Rig
    handle: DockerSandboxHandle
    run_id: str
    placeholders: tuple[str, str]


@pytest.fixture(scope="module")
def claude_code_image() -> str:
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/claude-code/build.sh --binary PATH)")
    return IMAGE


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _free_port(stack.host_ip)).serving() as lane:
        yield lane


@pytest.fixture
async def claude_broker(stack: RealStack, claude_code_image: str) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.e9real.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e9r-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=claude_code_image)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def endpoints(model_placeholder: str, tools_placeholder: str, *, policy_hook: str | None = None) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.ANTHROPIC_MESSAGES,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
        policy_hook=policy_hook,
    )


class TestClaudeCodeOnTheRealBinary(EngineAdapterContract):
    turn_timeout_s = 180.0
    lists_skills = True
    runs_commands = True
    auto_compacts = True

    @pytest.fixture
    def engine_harness(self, stack: RealStack, claude_code_image: str) -> RealClaudeCode:
        harness = RealClaudeCode()
        harness.issued = stack.models.issued
        return harness

    @pytest.fixture
    async def rigs(self, claude_broker: DockerSandboxBroker) -> AsyncIterator[list[Rig]]:
        opened: list[Rig] = []
        self._opened: list[Opened] = []
        yield opened
        for rig in opened:
            await rig.session.close()
        for item in self._opened:
            await claude_broker.release(item.handle)

    @pytest.fixture(autouse=True)
    def _lane(self, stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane) -> None:
        self._stack = stack
        self._broker = claude_broker
        self._models = model_gateway

    def make_gateway(self, grant: Sequence[GrantedTool]) -> ConformanceGateway | None:
        return self._gateway(grant)

    def _gateway(
        self, grant: Sequence[GrantedTool], *, budget: RunBudget | None = None, native: NativeCallPolicy | None = None
    ) -> EndpointConformanceGateway:
        # One run, one route: the model gateway routes each run to its own scripted model.
        route = PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")
        gateway = EndpointConformanceGateway(
            endpoints=endpoints("x" * 16, "y" * 16),
            grant=grant,
            binding=RunBinding("tenant", "scope", f"run-{uuid.uuid4().hex[:8]}"),
            routes_digest=route_digest((route,)),
            native=native,
        )
        self._models.pin(gateway, route, budget=budget)
        return gateway

    async def _sandbox(
        self, gateway: EndpointConformanceGateway, *, folder: bool = False
    ) -> tuple[DockerSandboxHandle, tuple[str, str]]:
        """``folder``: the turn's read-only ``/pipeshub/session``, seeded as the controller seeds it (with the hook's
        program)."""
        stack = self._stack
        binding = gateway.claims.binding
        run_id, attempt_id = binding.run_id, getattr(binding, "attempt_id", "")
        stack.models.bind(run_id, attempt_id, binding)
        tools_upstream = await stack.mount(RecordingEndpoint(gateway), binding)
        placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
        spec = SandboxSpec(
            image_digest=IMAGE,
            mode=lane_mode(),
            resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
            egress=EgressPolicy(
                destinations=(
                    EgressDestination("model-gateway", (8097,), upstream=self._models.upstream),
                    EgressDestination("tool-gateway", (8097,), upstream=tools_upstream),
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
                *((pipeshub_tool_folder().mount(),) if folder else ()),
            ),
            labels={
                **OWNER,
                "pipeshub.agent": "E9",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": attempt_id,
            },
        )
        handle = await self._broker.acquire(spec, lease=LEASE, idempotency_key=f"e9-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        if folder:
            await pipeshub_tool_folder().write(handle, gateway.endpoint.granted_tools(), endpoints(*placeholders))
        return handle, placeholders

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealClaudeCode
        *,
        profile: EngineProfile | None,
        grant: Sequence[GrantedTool],
        gateway: ConformanceGateway | None,
        restore: EngineState | None,
    ) -> Rig:
        gw = cast("EndpointConformanceGateway", gateway or self.make_gateway(grant))
        handle, placeholders = await self._sandbox(gw)
        launch = self.watched(
            harness.adapter.render_launch(profile or make_profile(harness), endpoints(*placeholders), SKILLS_DIR)
        )
        try:
            session = await harness.adapter.open(handle, launch, restore, correlation={"runId": "conformance"})
        except BaseException:
            await self._broker.release(handle)
            raise
        rig = Rig(sandbox=handle, gateway=gw, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, gw.binding.run_id, placeholders))
        return rig

    def phone_home_attempts(self, rig: Rig) -> list[str]:
        """CF-17: what the proxy refused from this sandbox for a destination other than the gateways."""
        opened = next(o for o in self._opened if o.rig is rig)
        return self._stack.outside_attempts(opened.handle.id)

    def assert_token_via_proxy_only(self, rig: Rig, harness: Any) -> None:  # noqa: ANN401
        """RX-CC2: what reached the gateways is the token Node issued for this sandbox's attempt, never a
        placeholder; the model gateway saw only Messages calls; the proxy refused nothing."""
        opened = next(o for o in self._opened if o.rig is rig)
        stack, models = self._stack, self._models
        issued = {f"Bearer {t}" for t in stack.models.issued}
        model = [r.authorization for r in models.requests if r.method == "POST"]
        tools = stack.gateway.authorizations
        assert model and tools, "the engine reached neither gateway"
        assert set(model) <= issued
        assert set(tools) <= issued
        for placeholder in opened.placeholders:
            assert all(placeholder not in value for value in (*model, *tools))
        assert {(r.method, r.path) for r in models.requests} <= MESSAGES_PATHS
        assert ("POST", "/v1/messages") in {(r.method, r.path) for r in models.requests}
        assert models.refused == []
        # FB-2: the process's one preconnect (an unauthenticated HEAD /api/hello) is refused by the proxy for
        # carrying no placeholder; it never reaches the model gateway, and Claude Code ignores its failure.
        refusals = stack.refusals_for(opened.handle.id)
        assert len(refusals) <= 1 and all("reason=placeholder_missing" in line for line in refusals), refusals
        assert ("HEAD", "/api/hello") not in {(r.method, r.path) for r in models.requests}
        assert stack.models.paths == []
        assert {a for calls in stack.models.authorizations.values() for a in calls} <= {f"Bearer {PROVIDER_KEY}"}

    def assert_pin_unchanged(self, rig: Rig) -> None:
        posts = [r for r in self._models.requests if (r.method, r.path) == ("POST", "/v1/messages")]
        assert posts
        assert {r.body.get("model") for r in posts} == {"engine-model"}

    async def assert_refused_before_start(self, harness: Any, state: EngineState) -> None:  # noqa: ANN401
        gateway = cast("EndpointConformanceGateway", self.make_gateway(()))
        handle, placeholders = await self._sandbox(gateway)
        recording = _RecordingSandbox(handle)
        try:
            launch = harness.adapter.render_launch(make_profile(harness), endpoints(*placeholders), SKILLS_DIR)
            with pytest.raises(EngineStateIncompatible):
                await harness.adapter.open(recording, launch, state)
            assert recording.execs == []
        finally:
            await self._broker.release(handle)

    async def test_rxcc3_every_beta_the_pinned_version_sends_is_served(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        """E3c FB-6: Claude Code does not fall back on a refused beta, so every beta of the pinned version
        must be in the gateway's allowlist; the explicit permission mode sends no ``safeguards``."""
        rig = await self._open(engine_harness, rigs)
        await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        posts = [r for r in self._models.requests if (r.method, r.path) == ("POST", "/v1/messages")]
        assert posts
        sent = {beta.strip() for r in posts for beta in r.betas.split(",") if beta.strip()}
        assert sent, "Claude Code sent no beta: the recording is wrong"
        assert sent <= ACCEPTED_BETAS, sorted(sent - ACCEPTED_BETAS)
        for request in posts:
            assert "safeguards" not in request.body
            assert request.body.get("model") == "engine-model"
        assert self._models.refused == []

    async def test_rxcc4_a_large_grant_reaches_the_model_whole(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        """Codex drops its MCP server past 2,048 tools (E8 RX-C4); 2.1.289 has no such cap, and with tool search
        off nothing is deferred: every granted tool is in the model's list (why ``max_tools`` is ``None``)."""
        assert engine_harness.adapter.capabilities().max_tools is None
        grant = tuple(GrantedTool(f"tool{i:04d}") for i in range(LARGE_GRANT))
        rig = await self._open(engine_harness, rigs, grant=grant)
        await self.collect(rig.session, _turn("say hi"))
        visible = set(rig.gateway.model_calls[0].tools)
        assert {exposed_tool_name(t.name) for t in grant} <= visible

    async def test_rxcc5_our_skill_is_listed_and_no_bundled_one(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs)
        await self.collect(rig.session, _turn("say hi"))
        seen = "\n".join(m.content for m in rig.gateway.model_calls[0].messages) + rig.gateway.model_calls[0].system
        assert "Use when asked for a report." in seen
        for bundled in ("deep-research", "dataviz", "plugin-authoring", "claude-api", "update-config"):
            assert bundled not in seen

    async def test_rxcc6_the_model_gateways_budget_refusal_ends_the_turn_as_a_budget_stop(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        """The first call spends the run's one-token budget; E3 refuses the next with 402 and
        ``x-should-retry: false`` (E3c), which Claude Code does not retry: one ``EngineError`` with the ledger's own
        code (FB-3), a code the controller stops on."""
        gateway = self._gateway(DEFAULT_GRANT, budget=RunBudget(max_cost_usd=None, max_tokens=1))
        rig = await self._open(engine_harness, rigs, gateway=gateway)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        ended = events[-1]
        assert isinstance(ended, TurnCompleted) and ended.reason == "failed"
        ((path, status, reason),) = self._models.refused
        assert (path, status) == ("/v1/messages", 402)
        assert reason in BUDGET_STOP_CODES
        assert [e.code for e in events if isinstance(e, EngineError)] == [reason]

    async def test_rxcc7_the_longest_tool_name_claude_code_admits_is_called_through_the_gateway(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        name = "t" * engine_harness.adapter.capabilities().max_tool_name  # type: ignore[operator]
        rig = await self._open(engine_harness, rigs, grant=(GrantedTool(name),))
        events = await self.collect(rig.session, _turn(f'tool {name} {{"q": 1}}\nsay ok'))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        assert sum(rig.gateway.executions.values()) == 1
        assert self._models.refused == []


    async def test_rxcc9_a_helpers_work_ends_inside_its_turn(self, engine_harness: RealClaudeCode, rigs: list[Rig]) -> None:
        """Found while building: a background helper ends its turn early and starts another one when it
        finishes, which the next turn would read as its own. Helpers run in the foreground: the next turn
        answers for itself."""
        profile = make_profile(engine_harness, sub_agents=SubAgentPolicy(enabled=True))
        rig = await self._open(engine_harness, rigs, profile=profile)
        first = await self.collect(rig.session, _turn("spawn say deep\nsay top"))
        assert isinstance(first[-1], TurnCompleted) and first[-1].final_text.strip() == "top"
        assert [type(e).__name__ for e in first].count("SubAgentFinished") == 1
        calls = len(rig.gateway.model_calls)
        second = await self.collect(rig.session, FollowUp(turn_id_for("run-conformance", 1), "say after"))
        assert isinstance(second[-1], TurnCompleted) and second[-1].final_text.strip() == "after"
        assert len(rig.gateway.model_calls) == calls + 1


    async def test_ga14_c20_26_the_managed_hook_still_refuses_after_the_agent_tries_to_disable_it(
        self, engine_harness: RealClaudeCode, rigs: list[Rig]
    ) -> None:
        """GA-14 on the real binary: the agent first tries to delete and overwrite the hook's settings and program
        (its shell runs, the build policy allows commands, and the files are root's and read-only), then writes a
        file outside the workspace. Claude Code runs the controller's ``PreToolUse`` hook from its ``--settings``
        file; the hook asks the Tool Gateway (``ph policy check`` from the read-only session folder, through the
        egress proxy) and the gateway refuses the write: the file is never written and the model is told why."""
        gateway = self._gateway(DEFAULT_GRANT, native=NativeCallPolicy.for_profile(make_profile(engine_harness)))
        handle, placeholders = await self._sandbox(gateway, folder=True)
        launch = engine_harness.adapter.render_launch(
            make_profile(engine_harness), endpoints(*placeholders, policy_hook=POLICY_HOOK_PATH), SKILLS_DIR
        )
        try:
            session = await engine_harness.adapter.open(handle, launch, None, correlation={"runId": "ga14"})
        except BaseException:
            await self._broker.release(handle)
            raise
        rig = Rig(sandbox=handle, gateway=gateway, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, gateway.binding.run_id, placeholders))
        rigs.append(rig)
        attack = (
            "rm -f /state/claude/harness-settings.json /pipeshub/session/ph.py; "
            "echo '{}' > /state/claude/harness-settings.json; echo 'exit 0' > /pipeshub/session/ph.py; true"
        )
        events = await self.collect(session, _turn(f"shell {attack}\nwrite /tmp/ga14.txt hello\nsay done"))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        checks = [c for c in gateway.tool_calls if c.name == POLICY_CHECK_TOOL]
        assert len(checks) == 2, gateway.tool_calls
        out, _, code = await run(
            await handle.exec(["sh", "-c", "test -e /tmp/ga14.txt || echo absent; cat /state/claude/harness-settings.json"], env={}),
            timeout_s=30,
        )
        assert code == 0 and out.decode().startswith("absent\n"), out
        assert out.decode().split("\n", 1)[1].encode() == launch.files["/state/claude/harness-settings.json"]
        seen = "\n".join(m.content for call in gateway.model_calls for m in call.messages)
        assert "/tmp/ga14.txt is outside the workspace" in seen


def _turn(prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for("run-conformance", 0), prompt=prompt)
