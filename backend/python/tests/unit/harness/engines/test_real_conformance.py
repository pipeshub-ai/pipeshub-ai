"""RX-1, RX-2 (E0): ``EngineAdapterContract`` on the real opencode binary behind the real gateways.

opencode 1.18.34 runs in its release image (``pxh-opencode-tools``, E0b) under the Docker broker (gVisor or runc, ``lane_mode``), reaches
the gateways only through the P5.4 proxy with its two placeholders, and the proxy
injects gateway tokens minted as Node mints them. The tool side is E2's
``RunToolEndpoint`` served by ``ToolGatewayApp`` over MCP Streamable HTTP; the
model side is the conformance gateway's scripted model over OpenAI-compatible
HTTP. Approvals use the conformance gateway's fake ``InteractionBridge``.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.access.types import ModelApi
from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.types import GatewayEndpoints
from harness_core.sandbox.tar import tar_stream
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
from harness_core.testing.engines.contract import (
    SKILL,
    SKILLS_DIR,
    EngineAdapterContract,
    Rig,
    make_profile,
)
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import ToolCallRecord, args_digest
from harness_core.testing.sandbox.egress import FakeEgress
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.opencode.launch import NATIVE_TOOLS, exposed_tool_name
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.engines.real_stack import IMAGE, OWNER, lane_mode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from pydantic import JsonValue

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.engines.events import EngineState
    from harness_core.engines.policy import NativeTool
    from harness_core.engines.types import EngineProfile, EngineVersion
    from harness_core.gateway.ports import Progress
    from harness_core.gateway.types import GatewayClaims, McpResult, McpTool
    from harness_core.testing.engines.gateway import ConformanceGateway, GrantedTool
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

MODEL_URL = "http://model-gateway:8097/v1"
TOOLS_URL = "http://tool-gateway:8097/mcp"
LEASE = LeaseRef("worker-e0", 1)


def _pinned() -> EngineVersion:
    return lane_release("opencode").version


class RecordingEndpoint:
    """The conformance gateway's ``RunToolEndpoint`` as the real transport serves it, noting deferrals
    (what ``EndpointConformanceGateway._tools`` does on the fake wire)."""

    def __init__(self, gateway: EndpointConformanceGateway) -> None:
        self._gateway = gateway
        self._listed = 0
        self._connects = 0

    def listed(self) -> int:
        """The Tool Gateway's witness (``ToolListings``), as the controller's per-attempt endpoint counts it."""
        return self._listed

    def connected(self) -> int:
        return self._connects

    def note_connect(self) -> None:
        self._connects += 1

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        tools = await self._gateway.endpoint.list_tools(claims)
        self._listed += 1
        return tools

    async def call_tool(
        self, claims: GatewayClaims, name: str, arguments: Mapping[str, JsonValue], *, progress: Progress | None = None
    ) -> McpResult:
        gateway = self._gateway
        gateway.tool_calls.append(ToolCallRecord(name, args_digest(dict(arguments)), "verified"))
        result = await gateway.endpoint.call_tool(claims, name, arguments, progress=progress)
        if result.disposition == "deferred" and result.interaction_id is not None:
            gateway._deferred_calls[result.interaction_id] = (name, arguments)  # pyright: ignore[reportPrivateUsage]
            gateway.deferred.append(result.interaction_id)
        return result


@dataclass
class _RecordingSandbox:
    """A broker sandbox that records what it was asked to run (CF-10: nothing before a refusal)."""

    inner: DockerSandboxHandle
    execs: list[Sequence[str]] = field(default_factory=list[Any])

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def mode(self) -> RuntimeMode:
        return self.inner.mode

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return self.inner.verified_binaries

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> Any:  # noqa: ANN401
        self.execs.append(list(argv))
        return await self.inner.exec(argv, env=env, stdin=stdin)

    async def connect(self, port: int) -> Any:  # noqa: ANN401
        return await self.inner.connect(port)

    async def put_tar(self, dest: str, stream: Any) -> None:  # noqa: ANN401
        await self.inner.put_tar(dest, stream)

    async def seed_tar(self, dest: str, stream: Any) -> None:  # noqa: ANN401
        await self.inner.seed_tar(dest, stream)

    async def get_tar(self, src: str) -> Any:  # noqa: ANN401
        return await self.inner.get_tar(src)


@dataclass
class RealOpencode:
    """``EngineConformanceHarness`` for the real binary; sandboxes come from ``RealLane.sandbox``."""

    adapter: OpencodeAdapter = field(init=False)
    version: EngineVersion = field(default_factory=_pinned)
    passwords: list[str] = field(default_factory=list[str])
    issued: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self.adapter = OpencodeAdapter(password=self._password)

    def _password(self) -> str:
        value = secrets.token_urlsafe(32)
        self.passwords.append(value)
        return value

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset(NATIVE_TOOLS[tool])

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({"todowrite"})

    def exposed_name(self, granted: str) -> str:
        return exposed_tool_name(granted)

    async def new_sandbox(self, egress: FakeEgress) -> Any:  # noqa: ANN401
        raise NotImplementedError("the real lane opens sandboxes through the broker")

    def secrets(self) -> frozenset[str]:
        return frozenset([*self.passwords, *self.issued])


@dataclass
class Opened:
    rig: Rig
    handle: DockerSandboxHandle
    model_key: str
    placeholders: tuple[str, str]


class TestOpencodeOnTheRealBinary(EngineAdapterContract):
    turn_timeout_s = 90.0
    lists_skills = True
    runs_commands = True
    auto_compacts = True

    @pytest.fixture
    def engine_harness(self, stack: RealStack) -> RealOpencode:
        harness = RealOpencode()
        harness.issued = stack.models.issued
        return harness

    @pytest.fixture
    async def rigs(self, broker: DockerSandboxBroker) -> AsyncIterator[list[Rig]]:
        opened: list[Rig] = []
        self._opened: list[Opened] = []
        yield opened
        for rig in opened:
            await rig.session.close()
        for item in self._opened:
            await broker.release(item.handle)

    @pytest.fixture(autouse=True)
    def _lane(self, stack: RealStack, broker: DockerSandboxBroker) -> None:
        self._stack = stack
        self._broker = broker

    def make_gateway(self, grant: Sequence[GrantedTool]) -> ConformanceGateway | None:
        return EndpointConformanceGateway(endpoints=_endpoints("x" * 16, "y" * 16), grant=grant)

    async def _sandbox(self, gateway: EndpointConformanceGateway) -> tuple[DockerSandboxHandle, str, tuple[str, str]]:
        stack = self._stack
        binding = gateway.claims.binding
        run_id, attempt_id = binding.run_id, getattr(binding, "attempt_id", "")
        stack.models.bind(run_id, attempt_id, binding)
        model_upstream = stack.model_upstream(gateway)
        tools_upstream = await stack.mount(RecordingEndpoint(gateway), binding)
        placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
        spec = SandboxSpec(
            image_digest=IMAGE,
            mode=lane_mode(),
            resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
            egress=EgressPolicy(
                destinations=(
                    EgressDestination("model-gateway", (8097,), upstream=model_upstream),
                    EgressDestination("tool-gateway", (8097,), upstream=tools_upstream),
                ),
                token_routes=(
                    TokenRoute(placeholders[0], "model-gateway", "gateway:model"),
                    TokenRoute(placeholders[1], "tool-gateway", "gateway:tools"),
                ),
            ),
            mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount(SKILLS_DIR)),
            labels={**OWNER, "orgId": binding.tenant, "sessionId": binding.scope, "runId": run_id, "attemptId": attempt_id},
        )
        handle = await self._broker.acquire(spec, lease=LEASE, idempotency_key=f"e0-{uuid.uuid4().hex}")
        await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        await self.seed_workspace(handle)
        return handle, model_upstream.rsplit("/", 1)[-1], placeholders

    async def open_rig(
        self,
        harness: Any,  # noqa: ANN401 - RealOpencode
        *,
        profile: EngineProfile | None,
        grant: Sequence[GrantedTool],
        gateway: ConformanceGateway | None,
        restore: EngineState | None,
    ) -> Rig:
        gw = cast("EndpointConformanceGateway", gateway or self.make_gateway(grant))
        handle, model_key, placeholders = await self._sandbox(gw)
        launch = self.watched(
            harness.adapter.render_launch(profile or make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
        )
        try:
            session = await harness.adapter.open(handle, launch, restore, correlation={"runId": "conformance"})
        except BaseException:
            await self._broker.release(handle)
            raise
        rig = Rig(sandbox=handle, gateway=gw, egress=FakeEgress(), launch=launch, session=session)  # type: ignore[arg-type]
        self._opened.append(Opened(rig, handle, model_key, placeholders))
        return rig

    def phone_home_attempts(self, rig: Rig) -> list[str]:
        """CF-17: what the proxy refused from this sandbox for a destination other than the gateways."""
        opened = next(o for o in self._opened if o.rig is rig)
        return self._stack.outside_attempts(opened.handle.id)

    def assert_token_via_proxy_only(self, rig: Rig, harness: Any) -> None:  # noqa: ANN401
        """RX-2: what reached the gateways is the token Node issued for this sandbox's attempt,
        never a placeholder; the proxy refused nothing from this sandbox."""
        opened = next(o for o in self._opened if o.rig is rig)
        stack = self._stack
        model = stack.models.authorizations[opened.model_key]
        tools = stack.gateway.authorizations
        issued = {f"Bearer {t}" for t in stack.models.issued}
        assert model and tools, "the engine reached neither gateway"
        assert set(model) <= issued
        assert set(tools) <= issued
        for placeholder in opened.placeholders:
            assert all(placeholder not in value for value in (*model, *tools))
        assert stack.refusals_for(opened.handle.id) == []
        assert stack.models.paths == []

    async def assert_refused_before_start(self, harness: Any, state: EngineState) -> None:  # noqa: ANN401
        gateway = cast("EndpointConformanceGateway", self.make_gateway(()))
        handle, _, placeholders = await self._sandbox(gateway)
        recording = _RecordingSandbox(handle)
        try:
            launch = harness.adapter.render_launch(make_profile(harness), _endpoints(*placeholders), SKILLS_DIR)
            with pytest.raises(EngineStateIncompatible):
                await harness.adapter.open(recording, launch, state)
            assert recording.execs == []
        finally:
            await self._broker.release(handle)


def _endpoints(model_placeholder: str, tools_placeholder: str) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url=MODEL_URL,
        model_api=ModelApi.CHAT_COMPLETIONS,
        tools_mcp_url=TOOLS_URL,
        token_placeholder=model_placeholder,
        tools_token_placeholder=tools_placeholder,
    )


