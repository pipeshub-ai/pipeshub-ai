"""The real-binary conformance lane for an ACP agent on the model gateway's chat-completions wire (C2.41).

``AcpChatLane`` is ``EngineAdapterContract`` driving one shipped ACP profile in its own image, under the Docker broker
(gVisor or runc, ``lane_mode``), reaching the gateways only through the P5.4 proxy with its two placeholders: E3's real ``ModelGateway`` on
``/v1/chat/completions``, whose real ``OpenAITransport`` calls the conformance gateway's scripted model as the org's
provider (no live model), and E2's ``RunToolEndpoint`` over MCP Streamable HTTP. A module subclasses it with its
profile; its fixtures skip without Docker or the image. The Gemini lane (``test_real_gemini_acp_conformance.py``) is
the same shape on Gemini's own wire.

The profile is read unsigned here: the lane tests the profile and the driver; the signature is the release's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

import pytest

from harness_core.access.types import ModelApi, PinnedRoute, RunBudget, route_digest
from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.events import (
    EngineError,
    ToolCallSeen,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.ports import WitnessesToolGateway
from harness_core.engines.types import (
    SEALED_DIR,
    EngineMode,
    EngineTask,
    GatewayEndpoints,
)
from harness_core.interactions.ports import RunBinding
from harness_core.sandbox.tar import tar_stream
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    ImageResources,
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
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.acp.adapter import AcpAdapter, acp_adapter
from harness_engines.acp.launch import UNSUPPORTED
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.engines.real_stack import (
    OWNER,
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
    from harness_core.testing.engines.gateway import ConformanceGateway, GrantedTool
    from harness_engines.acp.profiles import AcpAgentProfile
    from tests.unit.harness.engines.real_stack import RealStack

MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
CHAT_PATH = "/v1/chat/completions"


@dataclass
class RealAcpAgent:
    """``EngineConformanceHarness`` for one ACP profile; sandboxes come from the broker."""

    profile: AcpAgentProfile
    adapter: AcpAdapter = field(init=False)
    version: EngineVersion = field(init=False)
    issued: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self.adapter = acp_adapter(self.profile)
        self.version = self.profile.engine

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset() if tool in UNSUPPORTED else frozenset(self.profile.native_tools.get(tool, ()))

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({self.profile.plan_tool}) if self.profile.plan_tool else frozenset()

    def exposed_name(self, granted: str) -> str:
        return self.profile.exposed_tool_name(granted)

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


def _endpoints(model_placeholder: str, tools_placeholder: str) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.CHAT_COMPLETIONS,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
    )


def _turn(prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for("run-conformance", 0), prompt=prompt)


class AcpChatLane(EngineAdapterContract):
    """The shared suite plus the launch facts every chat-wire ACP profile must show on its real binary."""

    PROFILE: ClassVar[AcpAgentProfile]
    BUILD_SCRIPT: ClassVar[str]
    # Names the model must never see in plan mode (editing, shell, sub-agents, web) and some it must.
    PLAN_HIDDEN: ClassVar[frozenset[str]]
    PLAN_SHOWN: ClassVar[frozenset[str]]
    # What the agent may ask of the model gateway: chat completions, and for an agent that reads the gateway's model
    # list (Goose: the context limit of the model it runs) ``GET /v1/models``, which the gateway serves.
    MODEL_REQUESTS: ClassVar[frozenset[tuple[str, str]]] = frozenset({("POST", CHAT_PATH)})
    # Whether the agent passes the gateway's budget code on exactly; one that names only a budget stop of its own
    # (Goose) ends the turn with a budget code all the same.
    EXACT_BUDGET_CODE: ClassVar[bool] = True
    memory_mb: ClassVar[int] = 1536
    turn_timeout_s = 180.0
    lists_skills = True
    runs_commands = True

    @pytest.fixture
    def acp_image(self) -> str:
        available, reason = lane_available(self.PROFILE.image)
        if not available:
            pytest.skip(f"{reason} ({self.BUILD_SCRIPT})")
        return self.PROFILE.image

    @pytest.fixture
    async def model_gateway(self, stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
        async with ModelGatewayLane(stack, _free_port(stack.host_ip)).serving() as lane:
            yield lane

    @pytest.fixture
    async def acp_broker(self, stack: RealStack, acp_image: str) -> AsyncIterator[DockerSandboxBroker]:
        namespace = f"io.pipeshub.acp{self.PROFILE.id}.{uuid.uuid4().hex[:8]}"
        prefix = f"pxh-acp{self.PROFILE.id[:3]}-" + namespace.rsplit(".", 1)[1]
        created, provider = await engine_broker(stack, namespace, prefix, image=acp_image)
        yield created
        await created.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()

    @pytest.fixture
    def engine_harness(self, stack: RealStack, acp_image: str) -> RealAcpAgent:
        harness = RealAcpAgent(self.PROFILE)
        harness.issued = stack.models.issued
        return harness

    @pytest.fixture
    async def rigs(self, acp_broker: DockerSandboxBroker) -> AsyncIterator[list[Rig]]:
        opened: list[Rig] = []
        self._opened: list[Opened] = []
        yield opened
        for rig in opened:
            await rig.session.close()
        for item in self._opened:
            await acp_broker.release(item.handle)

    @pytest.fixture(autouse=True)
    def _lane(self, stack: RealStack, acp_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane) -> None:
        self._stack = stack
        self._broker = acp_broker
        self._models = model_gateway

    def make_gateway(self, grant: Sequence[GrantedTool]) -> ConformanceGateway | None:
        return self._gateway(grant)

    def _gateway(self, grant: Sequence[GrantedTool], *, budget: RunBudget | None = None) -> EndpointConformanceGateway:
        route = PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")
        gateway = EndpointConformanceGateway(
            endpoints=_endpoints("x" * 16, "y" * 16),
            grant=grant,
            # The engine's capabilities, as the run's endpoint is built with them (``controller.capabilities_for``).
            capabilities=acp_adapter(self.PROFILE).capabilities(),
            binding=RunBinding("tenant", "scope", f"run-{uuid.uuid4().hex[:8]}"),
            routes_digest=route_digest((route,)),
        )
        self._models.pin(gateway, route, budget=budget)
        return gateway

    async def _sandbox(
        self, gateway: EndpointConformanceGateway
    ) -> tuple[DockerSandboxHandle, tuple[str, str], RecordingEndpoint]:
        stack = self._stack
        binding = gateway.claims.binding
        run_id, attempt_id = binding.run_id, getattr(binding, "attempt_id", "")
        stack.models.bind(run_id, attempt_id, binding)
        endpoint = RecordingEndpoint(gateway)
        tools_upstream = await stack.mount(endpoint, binding)
        placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
        spec = SandboxSpec(
            image_digest=self.PROFILE.image,
            mode=lane_mode(),
            # The release's own memory floor over the lane's size, as production's controller applies it.
            resources=ImageResources(min_memory_mb=lane_release(self.PROFILE.kind).min_memory_mb).apply(
                Resources(cpus=1.0, memory_mb=self.memory_mb, pids=256, scratch_mb=256)
            ),
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
                MountSpec(SEALED_DIR, read_only=True),
            ),
            labels={
                **OWNER,
                "pipeshub.agent": "ACP",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": attempt_id,
            },
        )
        handle = await self._broker.acquire(spec, lease=LeaseRef(f"worker-acp-{self.PROFILE.id}", 1), idempotency_key=f"acp-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        return handle, placeholders, endpoint

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealAcpAgent
        *,
        profile: EngineProfile | None,
        grant: Sequence[GrantedTool],
        gateway: ConformanceGateway | None,
        restore: EngineState | None,
    ) -> Rig:
        gw = cast("EndpointConformanceGateway", gateway or self.make_gateway(grant))
        handle, placeholders, endpoint = await self._sandbox(gw)
        launch = harness.adapter.render_launch(profile or make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
        try:
            session = await harness.adapter.open(handle, launch, restore, correlation={"runId": "conformance"})
        except BaseException:
            await self._broker.release(handle)
            raise
        # The Tool Gateway's witness, as the controller hands it (R30ENG-FU-3: without it Goose's lane passed while
        # every durable job of it ended engine_unhealthy).
        if isinstance(session, WitnessesToolGateway):
            session.witness_tool_gateway(endpoint)
        rig = Rig(sandbox=handle, gateway=gw, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, gw.binding.run_id, placeholders))
        return rig

    def assert_token_via_proxy_only(self, rig: Rig, harness: Any) -> None:  # noqa: ANN401
        """What reached the gateways is the token Node issued for this sandbox's attempt, never a placeholder; the
        model gateway saw only chat-completions calls; the proxy refused nothing, so the agent tried no other host
        (no provider of its own, telemetry or update check)."""
        opened = next(o for o in self._opened if o.rig is rig)
        stack, models = self._stack, self._models
        issued = {f"Bearer {t}" for t in stack.models.issued}
        model = [r.authorization for r in models.requests]
        tools = stack.gateway.authorizations
        assert model and tools, "the agent reached neither gateway"
        assert set(model) <= issued
        assert set(tools) <= issued
        for placeholder in opened.placeholders:
            assert all(placeholder not in value for value in (*model, *tools))
        paths = {(r.method, r.path) for r in models.requests}
        assert ("POST", CHAT_PATH) in paths and paths <= self.MODEL_REQUESTS, sorted(paths)
        assert models.refused == []
        assert stack.refusals_for(opened.handle.id) == []
        assert stack.models.paths == []

    async def assert_refused_before_start(self, harness: Any, state: EngineState) -> None:  # noqa: ANN401
        gateway = cast("EndpointConformanceGateway", self.make_gateway(()))
        handle, placeholders, _ = await self._sandbox(gateway)
        recording = _RecordingSandbox(handle)
        try:
            launch = harness.adapter.render_launch(make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
            with pytest.raises(EngineStateIncompatible):
                await harness.adapter.open(recording, launch, state)
            assert recording.execs == []
        finally:
            await self._broker.release(handle)

    def assert_pin_unchanged(self, rig: Rig) -> None:
        bodies = [r.body for r in self._models.requests if r.method == "POST"]
        assert bodies and all(body.get("model") == "engine-model" for body in bodies), [body.get("model") for body in bodies]

    async def test_c2_41_the_agent_is_driven_over_acp_says_who_it_is_and_calls_the_gateway(
        self, engine_harness: RealAcpAgent, rigs: list[Rig]
    ) -> None:
        """C2.41: the pinned binary answers ``initialize`` as itself, reaches the tool gateway (its tools are in its very
        first model call) and calls a granted tool through it."""
        rig = await self._open(engine_harness, rigs)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        assert (await rig.session.health()).engine_version == self.PROFILE.agent_version
        assert rig.launch.argv == (self.PROFILE.binary, *self.PROFILE.argv)
        first = set(rig.gateway.model_calls[0].tools)
        assert {self.PROFILE.exposed_tool_name(t.name) for t in DEFAULT_GRANT} <= first, sorted(first)
        assert [c.name for c in rig.gateway.tool_calls] == ["search"]
        assert [e.tool for e in events if isinstance(e, ToolCallSeen) and e.status == "completed"] == ["search"]

    async def test_c2_41_plan_mode_shows_no_editing_or_shell_tool(self, engine_harness: RealAcpAgent, rigs: list[Rig]) -> None:
        rig = await self._open(engine_harness, rigs, profile=make_profile(engine_harness, mode=EngineMode.PLAN))
        await self.collect(rig.session, _turn("say hi"))
        tools = set(rig.gateway.model_calls[0].tools)
        assert not tools & self.PLAN_HIDDEN, sorted(tools)
        assert self.PLAN_SHOWN <= tools, sorted(tools)

    async def test_c2_41_the_model_gateways_budget_refusal_fails_the_turn_with_its_code(
        self, engine_harness: RealAcpAgent, rigs: list[Rig]
    ) -> None:
        """The first call spends the run's one-token budget; E3 refuses the next with 402 and tags its message with
        its code (``[gateway:<code>]``); the turn fails with the gateway's code, so the controller stops for the budget."""
        gateway = self._gateway(DEFAULT_GRANT, budget=RunBudget(max_cost_usd=None, max_tokens=1))
        rig = await self._open(engine_harness, rigs, gateway=gateway)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        ended = events[-1]
        assert isinstance(ended, TurnCompleted) and ended.reason == "failed"
        assert self._models.refused, "the gateway refused nothing"
        assert {(path, status) for path, status, _ in self._models.refused} == {(CHAT_PATH, 402)}
        reasons = {reason for _, _, reason in self._models.refused}
        assert reasons <= {"budget_exhausted", "budget_insufficient"}
        codes = [e.code for e in events if isinstance(e, EngineError)]
        if self.EXACT_BUDGET_CODE:
            assert codes == [next(iter(reasons))], (codes, reasons)
        else:
            assert len(codes) == 1 and codes[0] in {"budget_exhausted", "budget_insufficient"}, (codes, reasons)
