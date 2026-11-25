"""Sandbox start time on the real lane (P5.9a): acquire through the Docker broker to a ready engine.

One start = what ``EngineController`` does before a turn's first model call:
acquire (container, the sandbox's own /29 and the proxy's binding),
materialize a small workspace, seed a skill when asked (only runs that pin
skills do), open the engine (its server healthy, a session created). Each
phase is timed from the Docker calls the broker makes, so a cold and a warm
start can be compared phase by phase.
"""

from __future__ import annotations

import os
import secrets
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast

from harness_core.access.types import ModelApi
from harness_core.engines.tool_folder import granted_tools
from harness_core.engines.types import GatewayEndpoints
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
from harness_core.testing.engines.contract import SKILL, SKILLS_DIR, make_profile
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.pipeshub import pipeshub_tool_folder
from harness_engines.release import release_allowlist
from tests.unit.harness.engines.real_stack import OWNER, lane_mode
from tests.unit.harness.engines.test_real_conformance import RecordingEndpoint

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.ports import EngineAdapter
    from harness_core.engines.types import EngineVersion
    from harness_core.interactions.ports import RunBinding
    from tests.unit.harness.engines.real_stack import RealStack

RESOURCES = Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256)
GRANT = (GrantedTool("search", "Search the knowledge base."),)
LEASE = LeaseRef("worker-p59a", 1)
WORKSPACE = "/workspace"
WORKSPACE_FILES = {"README.md": b"# task\n", "src/main.py": b"print('hello')\n", "data/q3.csv": b"a,b\n1,2\n"}
# Docker calls of each phase, as the broker and its egress wiring make them.
CONTAINER_CALLS = frozenset({"create_container", "start", "create_volume"})
TOOL_FOLDER = pipeshub_tool_folder()
NETWORK_CALLS = frozenset({"create_network", "connect_container_to_network", "disconnect_container_from_network"})


def _release(kind: str) -> tuple[EngineVersion, str, float | None]:
    entry = next(r for r in release_allowlist() if r.version.kind == kind)
    return entry.version, entry.image, entry.cpus


@dataclass
class Engine:
    kind: str
    version: EngineVersion
    image: str
    adapter: EngineAdapter
    model_api: ModelApi
    # The release's own CPUs for its sandboxes (opencode: 2), as production's controller gives them.
    cpus: float | None = None

    @classmethod
    def of(cls, kind: str) -> Engine:
        version, image, cpus = _release(kind)
        adapters: dict[str, Callable[[], tuple[EngineAdapter, ModelApi]]] = {
            "codex": lambda: (CodexAdapter(), ModelApi.RESPONSES),
            "claude_code": lambda: (ClaudeCodeAdapter(), ModelApi.ANTHROPIC_MESSAGES),
            "opencode": lambda: (OpencodeAdapter(password=lambda: secrets.token_urlsafe(32)), ModelApi.CHAT_COMPLETIONS),
        }
        adapter, api = adapters[kind]()
        return cls(kind, version, image, adapter, api, cpus)


@dataclass
class StartSample:
    total_ms: float
    acquire_ms: float
    container_ms: float
    network_ms: float
    binding_ms: float
    files_ms: float
    engine_ms: float
    sandbox_id: str
    warm: bool = False
    load1: float = 0.0


@dataclass
class _Timer:
    by_call: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    binding_ms: float = 0.0

    def reset(self) -> None:
        self.by_call = defaultdict(float)
        self.binding_ms = 0.0

    def sum(self, names: frozenset[str]) -> float:
        return sum(ms for name, ms in self.by_call.items() if name in names)


def instrument(broker: DockerSandboxBroker) -> _Timer:
    """Time every Docker call the broker makes, and the proxy's binding, until the broker goes."""
    timer = _Timer()
    provider: Any = broker._provider  # pyright: ignore[reportPrivateUsage]
    run_blocking = provider.run_blocking

    async def timed(fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        started = time.perf_counter()
        try:
            return await run_blocking(fn, *args, **kwargs)
        finally:
            timer.by_call[getattr(fn, "__name__", "?")] += (time.perf_counter() - started) * 1000

    provider.run_blocking = timed
    egress = broker.egress
    if egress is not None:
        register = egress.bindings.register

        async def timed_register(binding: Any) -> None:  # noqa: ANN401
            started = time.perf_counter()
            try:
                await register(binding)
            finally:
                timer.binding_ms += (time.perf_counter() - started) * 1000

        egress.bindings.register = timed_register  # type: ignore[method-assign]
    return timer


def _endpoints(engine: Engine, placeholders: tuple[str, str]) -> GatewayEndpoints:
    return GatewayEndpoints(
        model_base_url="http://model-gateway:8097/v1",
        model_api=engine.model_api,
        tools_mcp_url="http://tool-gateway:8097/mcp",
        token_placeholder=placeholders[0],
        tools_token_placeholder=placeholders[1],
    )


def engine_spec(
    engine: Engine,
    *,
    model_upstream: str,
    tools_upstream: str,
    placeholders: tuple[str, str],
    labels: dict[str, str],
    folder: bool = False,
) -> SandboxSpec:
    """The spec ``EngineController._spec`` builds for a run (tar workspace strategy); ``folder``: with the
    ``/pipeshub`` tool folder production's controller mounts (R3)."""
    return SandboxSpec(
        image_digest=engine.image,
        mode=lane_mode(),
        resources=RESOURCES if engine.cpus is None else replace(RESOURCES, cpus=engine.cpus),
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
        mounts=(
            MountSpec("/workspace"),
            MountSpec("/state"),
            skills_mount(SKILLS_DIR),
            *((TOOL_FOLDER.mount(),) if folder else ()),
        ),
        labels={**OWNER, "pipeshub.agent": "P5.9a", **labels},
    )


async def one_start(
    stack: RealStack,
    broker: DockerSandboxBroker,
    timer: _Timer,
    engine: Engine,
    *,
    skills: bool = False,
    folder: bool = False,
) -> StartSample:
    """Acquire, materialize, seed (``skills``), write the tool folder as the controller does every turn
    (``folder``: its contract read, its files seeded), open; then close and release. Timed from acquire to the
    engine's session."""
    gateway = EndpointConformanceGateway(endpoints=_endpoints(engine, ("x" * 16, "y" * 16)), grant=GRANT)
    binding = gateway.claims.binding
    run_id = cast("RunBinding", binding).run_id
    attempt_id = getattr(binding, "attempt_id", "")
    stack.models.bind(run_id, attempt_id, binding)
    model_upstream = stack.model_upstream(gateway)
    tools_upstream = await stack.mount(RecordingEndpoint(gateway), binding)
    placeholders = (f"pxh-model-{uuid.uuid4().hex}", f"pxh-tools-{uuid.uuid4().hex}")
    spec = engine_spec(
        engine,
        model_upstream=model_upstream,
        tools_upstream=tools_upstream,
        placeholders=placeholders,
        labels={"orgId": binding.tenant, "sessionId": binding.scope, "runId": run_id, "attemptId": attempt_id},
        folder=folder,
    )
    warm_before = broker.warm_ids()
    load1 = os.getloadavg()[0]
    timer.reset()
    started = time.perf_counter()
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"p59a-{uuid.uuid4().hex}")
    acquired = time.perf_counter()
    container_ms = timer.sum(CONTAINER_CALLS)
    network_ms = timer.sum(NETWORK_CALLS)
    binding_ms = timer.binding_ms
    session = None
    try:
        if folder:
            _ = await TOOL_FOLDER.admit(handle)
        await handle.put_tar(WORKSPACE, tar_stream(WORKSPACE_FILES))
        if skills:
            await handle.seed_tar(SKILLS_DIR, tar_stream({"report/SKILL.md": SKILL}))
        endpoints = _endpoints(engine, placeholders)
        if folder:
            _ = await TOOL_FOLDER.write(
                handle, granted_tools(gateway.endpoint), endpoints, ("report",) if skills else ()
            )
        launch = engine.adapter.render_launch(make_profile(cast("Any", _Harness(engine))), endpoints, SKILLS_DIR)
        opened = time.perf_counter()
        files_ms = (opened - acquired) * 1000
        session = await engine.adapter.open(handle, launch, None, correlation={"runId": run_id})
        ready = time.perf_counter()
    finally:
        if session is not None:
            await session.close()
        await broker.release(handle)
    return StartSample(
        total_ms=(ready - started) * 1000,
        acquire_ms=(acquired - started) * 1000,
        container_ms=container_ms,
        network_ms=network_ms,
        binding_ms=binding_ms,
        files_ms=files_ms,
        engine_ms=(ready - opened) * 1000,
        sandbox_id=handle.id,
        warm=handle.id in warm_before,
        load1=load1,
    )


@dataclass
class _Harness:
    engine: Engine

    @property
    def version(self) -> EngineVersion:
        return self.engine.version


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    return statistics.quantiles(ordered, n=100, method="inclusive")[int(q) - 1]


def summary(samples: list[StartSample]) -> dict[str, tuple[float, float]]:
    """p50 and p95 of each phase, in ms."""
    phases = ("total_ms", "acquire_ms", "container_ms", "network_ms", "binding_ms", "files_ms", "engine_ms")
    return {p: (percentile([getattr(s, p) for s in samples], 50), percentile([getattr(s, p) for s in samples], 95)) for p in phases}


__all__ = ["Engine", "StartSample", "engine_spec", "instrument", "one_start", "percentile", "summary"]
