"""RX-C1..RX-C5 (E8): ``EngineAdapterContract`` on the real Codex app-server behind the real gateways.

``codex-app-server`` 0.160.0 runs in ``pxh-codex`` under the Docker broker (gVisor or runc, ``lane_mode``),
reaches the gateways only through the P5.4 proxy with its two placeholders, and
the proxy injects gateway tokens minted as Node mints them. The tool side is
E2's ``RunToolEndpoint`` served by ``ToolGatewayApp`` over MCP Streamable HTTP;
the model side is E3's ``ModelGateway`` on E3b's ``/v1/responses``, whose real
``OpenAITransport`` calls the conformance gateway's scripted model as the org's
provider. Approvals use the conformance gateway's fake ``InteractionBridge``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ModelApi, PinnedRoute, RunBudget, route_digest
from harness_core.engines.errors import (
    EngineStateIncompatible,
    EngineUnhealthy,
    GrantTooLarge,
)
from harness_core.engines.events import (
    EngineError,
    ToolCallSeen,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.engines.native_policy import POLICY_CHECK_TOOL, NativeCallPolicy
from harness_core.engines.policy import NativeTool, check_grant_size
from harness_core.engines.types import EngineTask, GatewayEndpoints
from harness_core.interactions.ports import RunBinding
from harness_core.sandbox.tar import put_files, tar_stream
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
from harness_core.testing.engines.gateway import GrantedTool, args_digest
from harness_core.testing.sandbox.contract import run
from harness_core.testing.sandbox.egress import FakeEgress
from harness_core.workspace.sync.archive import Owner
from harness_engines.codex.adapter import MAX_MCP_TOOLS, CodexAdapter
from harness_engines.codex.launch import (
    IMAGE_TOOL,
    NATIVE_TOOLS,
    PLAN_TOOL,
    exposed_tool_name,
)
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

IMAGE: str = lane_release("codex").image
_SKILLS = Path(__file__).resolve().parents[3] / "unit" / "agents" / "agent_loop" / "skills"
LAYOUT_FIXTURES = _SKILLS / "fixtures" / "layout"
CHECKER = Path(__file__).resolve().parents[4] / "app" / "agents" / "agent_loop" / "skills" / "builtin_packs" / "office-utils" / "scripts" / "check_layout.py"
MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
LEASE = LeaseRef("worker-e8", 1)
# Tool types E3b serves; anything else in a Codex request would be refused (E3b RW-4).
SERVED_TOOL_TYPES = frozenset({"function", "namespace"})


def _pinned() -> EngineVersion:
    return lane_release("codex").version


@dataclass
class RealCodex:
    """``EngineConformanceHarness`` for the real binary; sandboxes come from the broker."""

    adapter: CodexAdapter = field(default_factory=CodexAdapter)
    version: EngineVersion = field(default_factory=_pinned)
    issued: list[str] = field(default_factory=list[str])

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        # The contract's rigs start Codex without the managed policy hook, and only the hook confines its image viewer.
        return frozenset(NATIVE_TOOLS[tool]) - {IMAGE_TOOL}

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({PLAN_TOOL})

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
def codex_image() -> str:
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/codex/build.sh)")
    return IMAGE


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _free_port(stack.host_ip)).serving() as lane:
        yield lane


@pytest.fixture
async def codex_broker(stack: RealStack, codex_image: str) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.e8real.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e8r-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=codex_image)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def _endpoints(model_placeholder: str, tools_placeholder: str, *, policy_hook: str | None = None) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.RESPONSES,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
        policy_hook=policy_hook,
    )




class TestCodexOnTheRealBinary(EngineAdapterContract):
    # Codex 0.160.0 lists a repository's skills (scope repo) whatever the project's trust, and its config disables a
    # skill only by name or path, so they are lower-trust content (ADR-ENG-01 D1, R31ENG-FU-1 upstream); its project
    # docs are off (project_doc_max_bytes 0)
    repository_context_files = frozenset()
    repository_skills = frozenset({"skill:.agents", "skill:.codex"})
    turn_timeout_s = 120.0
    lists_skills = True
    runs_commands = True
    auto_compacts = True

    @pytest.fixture
    def engine_harness(self, stack: RealStack, codex_image: str) -> RealCodex:
        harness = RealCodex()
        harness.issued = stack.models.issued
        return harness

    @pytest.fixture
    async def rigs(self, codex_broker: DockerSandboxBroker) -> AsyncIterator[list[Rig]]:
        opened: list[Rig] = []
        self._opened: list[Opened] = []
        yield opened
        for rig in opened:
            await rig.session.close()
        for item in self._opened:
            await codex_broker.release(item.handle)

    @pytest.fixture(autouse=True)
    def _lane(self, stack: RealStack, codex_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane) -> None:
        self._stack = stack
        self._broker = codex_broker
        self._models = model_gateway

    def make_gateway(self, grant: Sequence[GrantedTool]) -> ConformanceGateway | None:
        return self._gateway(grant)

    def _gateway(
        self, grant: Sequence[GrantedTool], *, budget: RunBudget | None = None, native: NativeCallPolicy | None = None
    ) -> EndpointConformanceGateway:
        # One run, one route: the model gateway routes each run to its own scripted model.
        route = PinnedRoute("openai", f"scripted-{uuid.uuid4().hex[:8]}", config_model="gpt-4o")
        gateway = EndpointConformanceGateway(
            endpoints=_endpoints("x" * 16, "y" * 16),
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
                "pipeshub.agent": "E8",
                "orgId": binding.tenant,
                "sessionId": binding.scope,
                "runId": run_id,
                "attemptId": attempt_id,
            },
        )
        handle = await self._broker.acquire(spec, lease=LEASE, idempotency_key=f"e8-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        if folder:
            await pipeshub_tool_folder().write(handle, gateway.endpoint.granted_tools(), _endpoints(*placeholders))
        return handle, placeholders

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealCodex
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
        """RX-C2: what reached the gateways is the token Node issued for this sandbox's attempt, never a
        placeholder; the model gateway saw only Responses calls; the proxy refused nothing."""
        opened = next(o for o in self._opened if o.rig is rig)
        stack, models = self._stack, self._models
        issued = {f"Bearer {t}" for t in stack.models.issued}
        model = [r.authorization for r in models.requests]
        tools = stack.gateway.authorizations
        assert model and tools, "the engine reached neither gateway"
        assert set(model) <= issued
        assert set(tools) <= issued
        for placeholder in opened.placeholders:
            assert all(placeholder not in value for value in (*model, *tools))
        assert {(r.method, r.path) for r in models.requests} == {("POST", "/v1/responses")}
        assert models.refused == []
        assert stack.refusals_for(opened.handle.id) == []
        assert stack.models.paths == []
        # The provider heard only the model gateway, with the org's key.
        assert {a for calls in stack.models.authorizations.values() for a in calls} <= {f"Bearer {PROVIDER_KEY}"}

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

    async def test_rxc3_codex_sends_only_what_the_responses_gateway_serves(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs)
        await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        bodies = [r.body for r in self._models.requests]
        assert bodies
        for body in bodies:
            assert body.get("model") == "engine-model"
            assert body.get("store") is False
            assert "previous_response_id" not in body
            assert "text" not in body or cast("dict[str, Any]", body["text"]).get("format", {}).get("type", "text") == "text"
            types = {cast("dict[str, Any]", t).get("type") for t in cast("list[Any]", body.get("tools", []))}
            assert types <= SERVED_TOOL_TYPES, types
        assert self._models.refused == []

    async def test_rxc4_over_max_tools_codex_cannot_start_the_gateway_and_opens_no_thread(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        """Why the adapter refuses a grant over ``max_tools`` instead of passing it on: one tool more and Codex
        cannot start the gateway's MCP server. The gateway is required (E2E3-F6), so the thread never opens and
        the turn fails as an engine that never came up, with no model call made without the gateway's tools."""
        count = MAX_MCP_TOOLS + 1
        with pytest.raises(GrantTooLarge):
            check_grant_size(engine_harness.adapter.capabilities(), count)
        grant = tuple(GrantedTool(f"tool{i:04d}") for i in range(count))
        rig = await self._open(engine_harness, rigs, grant=grant)
        with pytest.raises(EngineUnhealthy):
            await self.collect(rig.session, _turn("say hi"))
        assert rig.gateway.model_calls == []

    async def test_rxc5_our_skills_are_listed_and_none_of_codexs(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        rig = await self._open(engine_harness, rigs)
        await self.collect(rig.session, _turn("say hi"))
        system = rig.gateway.model_calls[0].system
        assert "report" in system and "Use when asked for a report." in system
        for bundled in ("imagegen", "openai-docs", "skill-creator", "skill-installer"):
            assert bundled not in system

    async def test_rxc6_the_model_gateways_budget_refusal_ends_the_turn_as_a_budget_stop(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        """The first call spends the run's one-token budget; E3 refuses the next with 402 ``budget_exhausted``
        and no event (E0 RX-6): the controller stops on ``EngineError(budget_exhausted)``."""
        gateway = self._gateway(DEFAULT_GRANT, budget=RunBudget(max_cost_usd=None, max_tokens=1))
        rig = await self._open(engine_harness, rigs, gateway=gateway)
        events = await self.collect(rig.session, _turn('tool search {"q": 1}\nsay ok'))
        ended = events[-1]
        assert isinstance(ended, TurnCompleted) and ended.reason == "failed"
        # With streams not retried (STREAM_MAX_RETRIES) the refused call is sent once.
        ((path, status, reason),) = self._models.refused
        assert (path, status) == ("/v1/responses", 402)
        assert reason in {"budget_exhausted", "budget_insufficient"}
        # The gateway's own code, named in the refusal's message (DEEPROUTE2), not only the 402's stand-in.
        assert [e.code for e in events if isinstance(e, EngineError)] == [reason]

    async def test_rxc9_the_longest_tool_name_codex_admits_is_called_through_the_gateway(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        """FB-14: ``max_tool_name`` is exactly what E3b carries; one more character is refused at the grant (CA-3)."""
        name = "t" * engine_harness.adapter.capabilities().max_tool_name  # type: ignore[operator]
        rig = await self._open(engine_harness, rigs, grant=(GrantedTool(name),))
        events = await self.collect(rig.session, _turn(f'tool {name} {{"q": 1}}\nsay ok'))
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        assert sum(rig.gateway.executions.values()) == 1
        assert self._models.refused == []


    async def _hooked(
        self, engine_harness: RealCodex, rigs: list[Rig], native: NativeCallPolicy, run_id: str
    ) -> tuple[Rig, EndpointConformanceGateway]:
        """A rig whose Codex runs the managed policy hook from the turn's read-only session folder."""
        gateway = self._gateway(DEFAULT_GRANT, native=native)
        handle, placeholders = await self._sandbox(gateway, folder=True)
        launch = engine_harness.adapter.render_launch(
            make_profile(engine_harness), _endpoints(*placeholders, policy_hook=POLICY_HOOK_PATH), SKILLS_DIR
        )
        try:
            session = await engine_harness.adapter.open(handle, launch, None, correlation={"runId": run_id})
        except BaseException:
            await self._broker.release(handle)
            raise
        rig = Rig(sandbox=handle, gateway=gateway, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, gateway.binding.run_id, placeholders))
        rigs.append(rig)
        return rig, gateway

    async def test_ga14_c20_26_the_managed_hook_refuses_a_command_the_runs_policy_refuses(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        """GA-14 on the real binary: Codex runs the controller's trusted ``PreToolUse`` hook from its read-only
        config before its own shell call; the hook asks the Tool Gateway (``ph policy check`` from the read-only
        session folder, through the egress proxy) and the gateway refuses it. The run's policy here is narrower
        than the engine was started with (a live narrowing to plan): the command never runs, and the model is told
        why. The agent cannot edit the hook's config or program first: its shell is refused before it runs."""
        plan = NativeCallPolicy(frozenset({NativeTool.READ, NativeTool.SEARCH}))
        rig, gateway = await self._hooked(engine_harness, rigs, plan, "ga14")
        handle, launch = rig.sandbox, rig.launch
        events = await self.collect(
            rig.session, _turn("shell rm -f /state/codex/config.toml /pipeshub/session/ph.py; touch /workspace/ga14\nsay done")
        )
        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        checks = [c for c in gateway.tool_calls if c.name == POLICY_CHECK_TOOL]
        assert len(checks) == 1, gateway.tool_calls
        out, _, code = await run(await handle.exec(["sh", "-c", "test -e /workspace/ga14 || echo absent; sha256sum /state/codex/config.toml /pipeshub/session/ph.py"], env={}), timeout_s=30)
        assert code == 0 and out.decode().startswith("absent\n"), out
        assert launch.files["/state/codex/config.toml"] and "/pipeshub/session/ph.py" in out.decode()
        seen = "\n".join(m.content for call in gateway.model_calls for m in call.messages)
        assert "Command blocked by PreToolUse hook" in seen and "its mode does not permit Bash" in seen


    async def test_fixpdfskill_fu1_codex_looks_at_a_rendered_page_and_at_nothing_outside_the_workspace(
        self, engine_harness: RealCodex, rigs: list[Rig]
    ) -> None:
        """FIXPDFSKILL-FU-1 on the real binary: the document checker renders a PDF's pages into the workspace and Codex
        looks at one with ``view_image``; the image reaches the model through the Responses wire and the provider.
        ``view_image`` reads any path the process can (Codex runs under ``danger-full-access``), so the managed hook
        is what confines it: an image outside the workspace, a workspace symlink to it and ``/proc`` are refused by the
        run's policy before Codex reads them, and the model is told why."""
        build = NativeCallPolicy.for_profile(make_profile(engine_harness))
        rig, gateway = await self._hooked(engine_harness, rigs, build, "fu1")
        handle = rig.sandbox
        await put_files(
            handle,  # type: ignore[arg-type]
            {"/workspace/in/report.pdf": (LAYOUT_FIXTURES / "ipl_fixed.pdf").read_bytes(), "/workspace/in/check_layout.py": CHECKER.read_bytes()},
            owner=Owner(),
        )
        render = (
            "cd /workspace && python3 in/check_layout.py in/report.pdf && cp layout_check/report/report-page-2.png "
            "/tmp/outside.png && ln -s /tmp/outside.png escape.png && ls layout_check/report"
        )
        out, err, code = await run(await handle.exec(["sh", "-c", render], env={}), timeout_s=120)
        assert code == 0 and "report-page-2.png" in out.decode(), (out, err)

        events = await self.collect(
            rig.session,
            _turn(
                'tool view_image {"path": "layout_check/report/report-page-2.png"}\n'
                'tool view_image {"path": "/tmp/outside.png"}\n'
                'tool view_image {"path": "escape.png"}\n'
                'tool view_image {"path": "/proc/1/environ"}\n'
                "say looked"
            ),
        )

        assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"
        # The hook sent each path as it resolves in the sandbox: the symlink as its target.
        checks = [c.args_digest for c in gateway.tool_calls if c.name == POLICY_CHECK_TOOL]
        assert checks == [
            args_digest({"tool": "read", "name": IMAGE_TOOL, "paths": [path]})
            for path in ("/workspace/layout_check/report/report-page-2.png", "/tmp/outside.png", "/tmp/outside.png", "/proc/1/environ")
        ], gateway.tool_calls
        # Only the page was read: Codex reports an image it looked at as an ``imageView`` item, one per read.
        looked = [e for e in events if isinstance(e, ToolCallSeen) and e.tool == IMAGE_TOOL]
        assert [(e.status, e.arguments) for e in looked] == [
            (status, {"path": "/workspace/layout_check/report/report-page-2.png"}) for status in ("started", "completed")
        ]
        # Codex sent the page as the call's output, an ``input_image``, and the gateway served the request.
        outputs = [
            item
            for r in self._models.requests
            for item in (as_object(i) for i in as_list(r.body.get("input")))
            if item.get("type") == "function_call_output"
        ]
        images = {
            as_str(o.get("call_id"))
            for o in outputs
            if any(as_object(p).get("type") == "input_image" for p in as_list(o.get("output")))
        }
        assert len(images) == 1, outputs
        assert self._models.refused == []
        assert self._stack.models.images[self._stack.models.register(gateway)], "the provider never got the page"
        # The last call holds the whole turn: each refusal reached the model once, with the run's reason.
        refused = [m.content for m in gateway.model_calls[-1].messages if m.role == "tool" and "blocked by PreToolUse hook" in m.content]
        assert len(refused) == 3, refused
        for path, said in zip(("/tmp/outside.png", "/tmp/outside.png", "/proc/1/environ"), refused, strict=True):
            assert f"{path} is outside the workspace: view_image opens only files under /workspace." in said, said


    async def test_r10_cc1_h2_the_trusted_hook_hash_is_the_one_this_codex_lists(
        self, engine_harness: RealCodex, rigs: list[Rig], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Engine-candidate gate (CC1): ``TRUSTED_HOOKS`` must be the hash this Codex
        binary gives the managed hook (``hooks/list`` currentHash). A release that hashes hook definitions differently
        skips the hook without a word, and the adapter then refuses every run at its start; this lane runs on the
        candidate (``PXH_ENGINE_CANDIDATE``), so a Codex bump fails here, naming both hashes, before it is admitted."""
        from harness_core.engines.native_policy import policy_hook_command
        from harness_engines.codex.adapter import listed_policy_hook_hash
        from harness_engines.codex.launch import TRUSTED_HOOKS, WORKDIR

        pinned = TRUSTED_HOOKS[policy_hook_command(POLICY_HOOK_PATH)]
        version = engine_harness.version.version
        gateway = self._gateway(DEFAULT_GRANT, native=NativeCallPolicy(frozenset({NativeTool.READ})))
        handle, placeholders = await self._sandbox(gateway, folder=True)
        launch = engine_harness.adapter.render_launch(
            make_profile(engine_harness), _endpoints(*placeholders, policy_hook=POLICY_HOOK_PATH), SKILLS_DIR
        )
        try:
            session = await engine_harness.adapter.open(handle, launch, None, correlation={"runId": "r10-cc1-h2"})
        except EngineUnhealthy:
            await self._broker.release(handle)
            seen = [r.getMessage() for r in caplog.records if "engine.policy_hook.missing" in r.getMessage()]
            pytest.fail(
                f"Codex {version} does not trust the managed policy hook: TRUSTED_HOOKS in harness_engines/codex/"
                f"launch.py pins {pinned}, and the binary listed it as {seen or 'no hash'}. Pin the binary's hash "
                "(after reviewing the hook it renders) before this version is admitted.",
            )
        rig = Rig(sandbox=handle, gateway=gateway, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, gateway.binding.run_id, placeholders))
        rigs.append(rig)
        listed = await session._rpc.request("hooks/list", {"cwds": [WORKDIR]})  # pyright: ignore[reportPrivateUsage]
        assert listed_policy_hook_hash(listed) == pinned, (
            f"Codex {version} hashes the managed policy hook as {listed_policy_hook_hash(listed)}; "
            f"TRUSTED_HOOKS pins {pinned}"
        )


def _turn(prompt: str) -> EngineTask:
    return EngineTask(turn_id=turn_id_for("run-conformance", 0), prompt=prompt)
