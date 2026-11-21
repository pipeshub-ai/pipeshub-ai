"""RX-ACP (ACP-driver): ``EngineAdapterContract`` on a real ACP agent behind the real gateways.

The ACP driver runs the shipped ``acp-opencode`` profile (``harness_engines/acp_agents.json``): opencode 1.18.34's
own ACP server (``opencode acp``) in its release image, under the Docker broker (gVisor or runc, ``lane_mode``), reaching the gateways only
through the P5.4 proxy with its two placeholders. The model side is E3's real ``ModelGateway`` on its
chat-completions wire, whose real ``OpenAITransport`` calls the conformance gateway's scripted model as the org's
provider; the tool side is E2's ``RunToolEndpoint`` over MCP Streamable HTTP, given to the agent in
``session/new`` ``mcpServers``. Skipped without Docker and the opencode tools image.

The profile is read unsigned here: the lane tests the profile and the driver; the signature is the release's.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ModelApi, PinnedRoute, RunBudget, route_digest
from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.events import EngineError, TurnCompleted, turn_id_for
from harness_core.engines.types import EngineMode, EngineTask, GatewayEndpoints
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
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.acp.adapter import AcpAdapter, acp_adapter
from harness_engines.acp.launch import UNSUPPORTED
from tests.unit.harness.engines.lane_releases import lane_acp_profile
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
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
LEASE = LeaseRef("worker-acp", 1)


# The shipped profile on its kind's lane release: a candidate under the CC1 gate when one is named.
PROFILE = lane_acp_profile("opencode")
IMAGE = PROFILE.image


@dataclass
class RealAcpOpencode:
    """``EngineConformanceHarness`` for opencode's ACP server; sandboxes come from the broker."""

    adapter: AcpAdapter = field(default_factory=lambda: acp_adapter(PROFILE))
    version: EngineVersion = field(default_factory=lambda: PROFILE.engine)
    issued: list[str] = field(default_factory=list[str])

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset() if tool in UNSUPPORTED else frozenset(PROFILE.native_tools.get(tool, ()))

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({PROFILE.plan_tool}) if PROFILE.plan_tool else frozenset()

    def exposed_name(self, granted: str) -> str:
        return PROFILE.exposed_tool_name(granted)

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
def acp_image() -> str:
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/opencode/build.sh)")
    return IMAGE


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _free_port(stack.host_ip)).serving() as lane:
        yield lane


@pytest.fixture
async def acp_broker(stack: RealStack, acp_image: str) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.acpreal.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-acpr-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=acp_image)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def _endpoints(model_placeholder: str, tools_placeholder: str) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.CHAT_COMPLETIONS,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
    )


class TestAcpOpencodeOnTheRealBinary(EngineAdapterContract):
    turn_timeout_s = 120.0
    lists_skills = True
    runs_commands = True


    @pytest.fixture
    def engine_harness(self, stack: RealStack, acp_image: str) -> RealAcpOpencode:
        harness = RealAcpOpencode()
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
            binding=RunBinding("tenant", "scope", f"run-{uuid.uuid4().hex[:8]}"),
            routes_digest=route_digest((route,)),
        )
        self._models.pin(gateway, route, budget=budget)
        return gateway

    async def _sandbox(self, gateway: EndpointConformanceGateway) -> tuple[DockerSandboxHandle, tuple[str, str]]:
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
            mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount(SKILLS_DIR)),
            labels={
                **OWNER,
                "pipeshub.agent": "ACP",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": attempt_id,
            },
        )
        handle = await self._broker.acquire(spec, lease=LEASE, idempotency_key=f"acp-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        return handle, placeholders

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealAcpOpencode
        *,
        profile: EngineProfile | None,
        grant: Sequence[GrantedTool],
        gateway: ConformanceGateway | None,
        restore: EngineState | None,
    ) -> Rig:
        gw = cast("EndpointConformanceGateway", gateway or self.make_gateway(grant))
        handle, placeholders = await self._sandbox(gw)
        launch = self.watched(
            harness.adapter.render_launch(profile or make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
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
        """What reached the gateways is the token Node issued for this sandbox's attempt, never a placeholder; the
        model gateway saw only chat-completions calls; the proxy refused nothing."""
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
        assert {(r.method, r.path) for r in models.requests} == {("POST", "/v1/chat/completions")}
        assert models.refused == []
        assert stack.refusals_for(opened.handle.id) == []
        assert stack.models.paths == []

    async def assert_refused_before_start(self, harness: Any, state: EngineState) -> None:  # noqa: ANN401
        gateway = cast("EndpointConformanceGateway", self.make_gateway(()))
        handle, placeholders = await self._sandbox(gateway)
        recording = _RecordingSandbox(handle)
        try:
            launch = harness.adapter.render_launch(make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
            with pytest.raises(EngineStateIncompatible):
                await harness.adapter.open(recording, launch, state)
            assert recording.execs == []
        finally:
            await self._broker.release(handle)

    def assert_pin_unchanged(self, rig: Rig) -> None:
        bodies = [r.body for r in self._models.requests]
        assert bodies and all(body.get("model") == "engine-model" for body in bodies)

    async def test_rxacp1_the_agent_is_driven_over_acp_and_says_who_it_is(
        self, engine_harness: RealAcpOpencode, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        assert (await rig.session.health()).engine_version == PROFILE.agent_version
        assert rig.launch.argv[0] == PROFILE.binary and "acp" in rig.launch.argv
        config = json.loads(rig.launch.env["OPENCODE_CONFIG_CONTENT"])
        assert config["provider"]["gateway"]["options"]["baseURL"] == MODEL_URL
        assert "mcp" not in config, "the tool gateway reaches the agent through session/new only"

    async def test_rxacp2_plan_mode_shows_no_editing_or_shell_tool(
        self, engine_harness: RealAcpOpencode, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs, profile=make_profile(engine_harness, mode=EngineMode.PLAN))
        await self.collect(rig.session, _turn("say hi"))
        tools = set(rig.gateway.model_calls[0].tools)
        assert not tools & {"edit", "write", "bash", "task"}
        assert {"read", "glob", "grep"} <= tools

    async def test_rxacp3_the_model_gateways_budget_refusal_fails_the_turn(
        self, engine_harness: RealAcpOpencode, rigs: list[Rig]
    ) -> None:
        """The first call spends the run's one-token budget; E3 refuses with 402 and tags its message with its code
        (``[gateway:<code>]``), which opencode's ACP server passes on in its ``session/prompt`` error: the turn fails
        with the gateway's code, so the controller stops for the budget (E0 RX-6)."""
        gateway = self._gateway(DEFAULT_GRANT, budget=RunBudget(max_cost_usd=None, max_tokens=1))
        rig = await self._open(engine_harness, rigs, gateway=gateway)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        ended = events[-1]
        assert isinstance(ended, TurnCompleted)
        assert any(status == 402 for _, status, _ in self._models.refused)
        ((path, status, reason),) = self._models.refused
        assert (path, status) == ("/v1/chat/completions", 402)
        assert reason in {"budget_exhausted", "budget_insufficient"}
        assert ended.reason == "failed"
        assert [e.code for e in events if isinstance(e, EngineError)] == [reason]


def _turn(prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for("run-conformance", 0), prompt=prompt)
