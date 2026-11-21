"""RX-GEM (WP-13; WS-05, WS-06, GA-23): ``EngineAdapterContract`` on Gemini CLI's ACP mode behind the real gateways.

The ACP driver runs the shipped ``acp-gemini`` profile (``harness_engines/acp_agents.json``): Gemini CLI 0.63.0's
own ACP mode (``gemini --acp``) in its image, under the Docker broker (gVisor or runc, ``lane_mode``), reaching the gateways only through the
P5.4 proxy with its two placeholders. The model side is E3's real ``ModelGateway`` on its Gemini wire
(``/v1beta/models/engine-model:streamGenerateContent``), whose real ``OpenAITransport`` calls the conformance
gateway's scripted model as the org's provider (no live model); the tool side is E2's ``RunToolEndpoint`` over MCP
Streamable HTTP, named only in the rendered ``settings.json`` ``mcpServers`` (``session/new`` and ``session/load``
send none, WP-59: an ACP entry would replace it without the run's tool timeout). Skipped
without Docker and the image (``deployment/sandbox-images/acp-gemini/build.sh``).

The profile is read unsigned here: the lane tests the profile and the driver; the signature is the release's.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import (
    ENGINE_MODEL_ID,
    ModelApi,
    PinnedRoute,
    RunBudget,
    route_digest,
)
from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.events import (
    EngineError,
    ToolCallSeen,
    TurnCompleted,
    turn_id_for,
)
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
LEASE = LeaseRef("worker-acp-gemini", 1)
STREAM_PATH = f"/v1beta/models/{ENGINE_MODEL_ID}:streamGenerateContent"
# What Gemini CLI 0.63.0 bundles and the profile switches off (``skills.disabled``).
BUNDLED_SKILLS = ("skill-creator", "antigravity-support")


# The shipped profile on its kind's lane release: a candidate under the CC1 gate when one is named.
PROFILE = lane_acp_profile("gemini")
IMAGE = PROFILE.image


@dataclass
class RealAcpGemini:
    """``EngineConformanceHarness`` for Gemini CLI's ACP mode; sandboxes come from the broker."""

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
def gemini_image() -> str:
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/acp-gemini/build.sh)")
    return IMAGE


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _free_port(stack.host_ip)).serving() as lane:
        yield lane


@pytest.fixture
async def gemini_broker(stack: RealStack, gemini_image: str) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.gemreal.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-gemr-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=gemini_image)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def _endpoints(model_placeholder: str, tools_placeholder: str) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.GEMINI_GENERATE_CONTENT,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
    )




class TestAcpGeminiOnTheRealBinary(EngineAdapterContract):
    # Gemini CLI 0.63.0, in the trusted workspace it needs to start MCP servers, reads the workspace's AGENTS.md and
    # GEMINI.md (D2) and lists its skills: its settings disable a skill only by name (skills.disabled), so they are
    # lower-trust content (ADR-ENG-01 D1, R31ENG-FU-1 upstream)
    repository_context_files = frozenset({"AGENTS.md", "GEMINI.md"})
    repository_skills = frozenset({"skill:.agents"})
    turn_timeout_s = 180.0
    lists_skills = True
    runs_commands = True

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "upstream, Gemini CLI 0.63.0: its ACP tool_call_update for a command carries only the display text (the "
            "output) and status completed, no rawOutput and no exit code; the code is only in the model's own copy of "
            "the result (Exit Code: 3), so a failed command reads completed (R32ENGFU-FU-2)"
        ),
    )
    async def test_cf8_a_failed_command_reads_failed_with_its_exit_code(
        self, engine_harness: Any, rigs: Any  # noqa: ANN401
    ) -> None:
        await super().test_cf8_a_failed_command_reads_failed_with_its_exit_code(engine_harness, rigs)

    @pytest.fixture
    def engine_harness(self, stack: RealStack, gemini_image: str) -> RealAcpGemini:
        harness = RealAcpGemini()
        harness.issued = stack.models.issued
        return harness

    @pytest.fixture
    async def rigs(self, gemini_broker: DockerSandboxBroker) -> AsyncIterator[list[Rig]]:
        opened: list[Rig] = []
        self._opened: list[Opened] = []
        yield opened
        for rig in opened:
            await rig.session.close()
        for item in self._opened:
            await gemini_broker.release(item.handle)

    @pytest.fixture(autouse=True)
    def _lane(self, stack: RealStack, gemini_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane) -> None:
        self._stack = stack
        self._broker = gemini_broker
        self._models = model_gateway

    def make_gateway(self, grant: Sequence[GrantedTool]) -> ConformanceGateway | None:
        return self._gateway(grant)

    def _gateway(self, grant: Sequence[GrantedTool], *, budget: RunBudget | None = None) -> EndpointConformanceGateway:
        route = PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")
        gateway = EndpointConformanceGateway(
            endpoints=_endpoints("x" * 16, "y" * 16),
            grant=grant,
            # The engine's capabilities, as the run's endpoint is built with them (``controller.capabilities_for``).
            capabilities=acp_adapter(PROFILE).capabilities(),
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
            resources=Resources(cpus=1.0, memory_mb=1536, pids=256, scratch_mb=256),
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
            mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount(SKILLS_DIR), MountSpec(SEALED_DIR, read_only=True)),
            labels={
                **OWNER,
                "pipeshub.agent": "ACP",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": attempt_id,
            },
        )
        handle = await self._broker.acquire(spec, lease=LEASE, idempotency_key=f"gem-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        return handle, placeholders

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealAcpGemini
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
        model gateway saw only the Gemini wire; the proxy refused nothing (no Google host was tried)."""
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
        assert {(r.method, r.path) for r in models.requests} == {("POST", STREAM_PATH)}
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
        # Gemini names the model in the path, never in the body.
        paths = [r.path for r in self._models.requests]
        assert paths and all(p == STREAM_PATH for p in paths)

    async def test_rxgem1_ws05_the_agent_is_driven_over_acp_with_its_settings_json(
        self, engine_harness: RealAcpGemini, rigs: list[Rig]
    ) -> None:
        """WS-05: the rendered ``settings.json`` names the tool gateway in ``mcpServers``; Gemini CLI connects to it
        (the gateway's tools are in its very first model call) and calls a granted tool through it."""
        rig = await self._open(engine_harness, rigs)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        assert (await rig.session.health()).engine_version == PROFILE.agent_version
        assert rig.launch.argv == (PROFILE.binary, "--acp")
        assert json.loads(rig.launch.files["/state/acp/gemini/.gemini/settings.json"]) == {}
        # WP-59, CF-02: the gateway and the run's settings are in the sealed system settings, above a repository's.
        system = json.loads(rig.launch.files[f"{SEALED_DIR}/gemini/settings.json"])
        assert system["mcpServers"]["tools"]["httpUrl"] == TOOLS_URL
        assert system["context"]["fileName"] == ["AGENTS.md"]
        first = set(rig.gateway.model_calls[0].tools)
        assert {PROFILE.exposed_tool_name(t.name) for t in DEFAULT_GRANT} <= first
        assert [c.name for c in rig.gateway.tool_calls] == ["search"]
        assert [e.tool for e in events if isinstance(e, ToolCallSeen) and e.status == "completed"] == ["search"]

    async def test_rxgem2_ws06_our_skill_is_listed_and_no_bundled_one(
        self, engine_harness: RealAcpGemini, rigs: list[Rig]
    ) -> None:
        """WS-06: Gemini CLI's own skill discovery (``~/.gemini/skills``, the root-owned link to the run's skills)
        lists the run's skill; its bundled skills are switched off."""
        rig = await self._open(engine_harness, rigs)
        await self.collect(rig.session, _turn("say hi"))
        call = rig.gateway.model_calls[0]
        seen = call.system + "\n".join(m.content for m in call.messages)
        assert "Use when asked for a report." in seen
        for bundled in BUNDLED_SKILLS:
            assert bundled not in seen

    async def test_rxgem3_plan_mode_shows_no_editing_or_shell_tool(
        self, engine_harness: RealAcpGemini, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs, profile=make_profile(engine_harness, mode=EngineMode.PLAN))
        await self.collect(rig.session, _turn("say hi"))
        tools = set(rig.gateway.model_calls[0].tools)
        assert not tools & {"replace", "write_file", "run_shell_command", "web_fetch", "google_web_search"}
        assert {"read_file", "glob"} <= tools

    async def test_rxgem4_the_model_gateways_budget_refusal_fails_the_turn(
        self, engine_harness: RealAcpGemini, rigs: list[Rig]
    ) -> None:
        """The first call spends the run's one-token budget; E3 refuses the next with 402 on the Gemini wire, which
        Gemini CLI does not retry, and the gateway's code reaches the turn's error."""
        gateway = self._gateway(DEFAULT_GRANT, budget=RunBudget(max_cost_usd=None, max_tokens=1))
        rig = await self._open(engine_harness, rigs, gateway=gateway)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        ended = events[-1]
        assert isinstance(ended, TurnCompleted) and ended.reason == "failed"
        ((path, status, reason),) = self._models.refused
        assert (path, status) == (STREAM_PATH, 402)
        assert reason in {"budget_exhausted", "budget_insufficient"}
        assert [e.code for e in events if isinstance(e, EngineError)] == [reason]


def _turn(prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for("run-conformance", 0), prompt=prompt)
