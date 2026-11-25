"""The worker's side of durable engine runs (E1): the engine kernel, its tool gateway and what they share.

A worker runs engine runs only when ``HARNESS_ENGINE_RUNS`` is on and the
sandbox broker answers at start; otherwise it advertises no ``executor_engine``
and Node never hands it one (CC-5). The worker serves its runs' tool endpoints
(``ToolGatewayApp``) on ``HARNESS_TOOL_GATEWAY_PORT``; the egress proxy reaches
them at ``HARNESS_TOOL_GATEWAY_URL``, else at this worker's own address on its
network (a sandbox's upstream names the worker that holds its run).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.org_context import spawn_detached
from app.harness.engines.durable import DurableEngineServices, PipesHubEngineAssembler
from app.harness.engines.org_references import BackgroundJob, org_references
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.gateway.host import HttpToolGatewayHost
from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from app.harness.metrics_exposition import worker_metrics_exposition
from app.harness.repositories import fetch_config_from_env
from app.harness.sandbox.client import open_sandbox_provider
from app.harness.sandbox.egress_config import connect_tunnels_enabled
from app.harness.skills.bundles import GraphProviderSkillSource, session_skill_bundles
from app.harness.telemetry import harness_tracer
from app.services.artifact_registry.limits import input_max_bytes
from app.telemetry.modules.harness_gateway_metrics import BackendGatewayMetrics
from app.utils.env_flags import HARNESS_ENGINE_RUNS, HARNESS_ENGINE_SNAPSHOTS, env_flag
from harness_core.engines.controller import EngineControllerConfig, KeptSandboxWatches
from harness_core.engines.durable.kernel import EngineKernel
from harness_core.engines.ports import EngineSuccessors
from harness_core.engines.signin import KEEP_IDLE_S, KEEP_MAX_S, SignInHosts, SignInKeep
from harness_core.http_limits import ReadLimits, guarded_uvicorn_protocol
from harness_core.logs import HarnessLog
from harness_core.repos.fetch import hand_off_mount, references_mount
from harness_core.sandbox.errors import SandboxProviderUnavailable
from harness_core.sandbox.ports import close_broker
from harness_core.telemetry.exposition import METRICS_PATH, PROMETHEUS_TEXT
from harness_core.telemetry.metrics import NOOP_GATEWAY_METRICS
from harness_engines.pipeshub import pipeshub_tool_folder
from harness_engines.release import (
    ByEngineRef,
    release_allowlist,
    release_image_resources,
    release_registry,
    release_snapshot_policy,
    release_successors,
    run_image,
)
from harness_engines.vendor_hosts import release_sign_in_hosts

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

    from fastapi import FastAPI

    from app.config.configuration_service import ConfigurationService
    from app.harness.assembly.services import RunServices
    from app.harness.journal_node import AttemptRoute, NodeJournal
    from app.harness.run_policy.select import RunPolicies
    from harness_core.clock import Clock
    from harness_core.engines.ports import ToolGatewayHost
    from harness_core.engines.signin import SignInRelay
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.interactions.ports import InteractionWakeup
    from harness_core.journal.ports import LiveSinkPort
    from harness_core.journal.types import AttemptToken
    from harness_core.priority import PreemptRequests
    from harness_core.sandbox.ports import SandboxBroker
    from harness_core.telemetry.exposition import MetricsExposition
    from harness_core.telemetry.metrics import GatewayMetrics
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_engines.release import EngineRelease

ENGINE_RUNS_ENV = "HARNESS_ENGINE_RUNS"
GATEWAY_PORT_ENV = "HARNESS_TOOL_GATEWAY_PORT"
SIGNIN_IDLE_ENV = "HARNESS_SIGNIN_IDLE_MINUTES"
SIGNIN_MAX_ENV = "HARNESS_SIGNIN_MAX_HOURS"
GATEWAY_URL_ENV = "HARNESS_TOOL_GATEWAY_URL"
DEFAULT_GATEWAY_PORT = 8098
GATEWAY_MOUNT = "/harness/tool-gateway"
BROKER_WAIT_S = 300.0
BROKER_RETRY_MAX_S = 10.0
_LOG = HarnessLog.named("app.harness.engines.worker")


def engine_runs_enabled() -> bool:
    return env_flag(HARNESS_ENGINE_RUNS)


def engine_snapshots_enabled() -> bool:
    return env_flag(HARNESS_ENGINE_SNAPSHOTS)


def gateway_base_url(port: int) -> str:
    """Where the egress proxy reaches this worker's tool gateway."""
    configured = (os.getenv(GATEWAY_URL_ENV) or "").strip()
    if configured:
        return configured.rstrip("/")
    return f"http://{socket.gethostbyname(socket.gethostname())}:{port}{GATEWAY_MOUNT}"


def tool_gateway_api(gateway: ToolGatewayApp, *, exposition: MetricsExposition | None = None) -> FastAPI:
    """The worker's Tool Gateway listener: the gateway under ``GATEWAY_MOUNT`` and a liveness route at ``/healthz``
    that answers without a token and says nothing but that it is up, as the Model Gateway's does. The port is never
    published, and a sandbox reaches only paths under a run's mount (the egress proxy appends to ``{base}/{key}``).
    ``exposition``: this process's metrics at ``/metrics``, for the worker credential only (C9.04). FastAPI's own
    docs and schema routes are off: nothing else is served here."""
    from fastapi import FastAPI, Request, Response

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    api.add_api_route("/healthz", healthz, methods=["GET"])
    if exposition is not None:
        served = exposition

        async def metrics(request: Request) -> Response:
            text = await served.answer(dict(request.headers))
            if text is None:
                return Response(
                    content='{"error":{"code":"credential_not_accepted"}}', status_code=401, media_type="application/json"
                )
            return Response(content=text, headers={"Content-Type": PROMETHEUS_TEXT})

        # A plain route: the handler takes the request itself, nothing FastAPI would read as a parameter.
        api.add_route(METRICS_PATH, metrics, methods=["GET"])
    api.mount(GATEWAY_MOUNT, gateway)
    return api


@dataclass
class EngineWorker:
    kernel: EngineKernel
    gateway: ToolGatewayApp
    port: int
    # SIGNIN-5: the signed-in sandboxes this worker keeps between turns; their watches stop when it does.
    parked: KeptSandboxWatches = field(default_factory=KeptSandboxWatches)
    read_limits: ReadLimits | None = None
    host: str = "0.0.0.0"
    # C9.04: this process's metrics at ``/metrics`` for Node's control-plane metrics route (worker credential).
    exposition: MetricsExposition | None = None
    # The clients its runs share (the sandbox broker's, the egress proxy's), in the order they were opened.
    clients: tuple[Callable[[], Awaitable[None]], ...] = ()

    async def aclose(self) -> None:
        """Close the clients its runs shared, the last opened first: once no run uses them (R46LANE-FU-3)."""
        async with contextlib.AsyncExitStack() as stack:
            for close in self.clients:
                _ = stack.push_async_callback(close)

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncIterator[None]:
        """The tool gateway on its port for as long as the worker runs."""
        import uvicorn

        api = tool_gateway_api(self.gateway, exposition=self.exposition)
        http = guarded_uvicorn_protocol(self.read_limits or ReadLimits.from_env(), listener="tool_gateway")
        server = uvicorn.Server(
            uvicorn.Config(api, host=self.host, port=self.port, log_level="warning", lifespan="off", http=http)
        )
        # Detached: each request binds the org its token names (OrgBoundGatewayTokenVerifier), never the starter's.
        task = spawn_detached(server.serve())
        try:
            yield
        finally:
            server.should_exit = True
            with contextlib.suppress(Exception):
                await task
            await self.parked.aclose()


@dataclass(frozen=True)
class EnginePlan:
    """What a worker found at start for engine runs, before its journal client exists (which advertises it)."""

    broker: SandboxBroker
    releases: tuple[EngineRelease, ...]
    port: int


async def plan_engines(
    services: RunServices,
    *,
    open_broker: Callable[[ConfigurationService], Awaitable[SandboxBroker]] = open_sandbox_provider,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> EnginePlan | None:
    """``None``: this worker runs no engine runs (switched off, nothing released, or no broker).

    The advertisement is decided once, here, so a broker that is still starting is waited for (E2E2 FB-1): in
    Compose the worker starts with the app container and the broker answers only once Node wrote its credential.
    """
    if not engine_runs_enabled():
        return None
    releases = tuple(release_allowlist())
    if not releases:
        _LOG.warning("engine.worker.unavailable", reasonCode="engine_not_released")
        return None
    deadline = clock() + BROKER_WAIT_S
    delay = 1.0
    while True:
        try:
            broker = await open_broker(services.config_service())
            break
        except SandboxProviderUnavailable as unavailable:
            # The deployment names a provider this worker cannot reach: waiting would not change that.
            _LOG.warning(
                "engine.worker.unavailable", reasonCode=unavailable.reason_code, message=str(unavailable)
            )
            return None
        except Exception as exc:
            if clock() + delay > deadline:
                _LOG.warning("engine.worker.unavailable", reasonCode="broker_unavailable", errorType=type(exc).__name__)
                return None
            if delay == 1.0:
                _LOG.info("engine.worker.waiting_for_broker", errorType=type(exc).__name__, waitS=BROKER_WAIT_S)
            await sleep(delay)
            delay = min(delay * 2, BROKER_RETRY_MAX_S)
    return EnginePlan(broker, releases, int(os.getenv(GATEWAY_PORT_ENV) or DEFAULT_GATEWAY_PORT))


def references_job(plan: EnginePlan, journal: NodeJournal, worker_id: str, clock: Clock) -> BackgroundJob | None:
    """The org references' background work a worker that runs engines carries (an extension point); ``None`` here."""
    provider = org_references()
    return None if provider is None else provider.background(plan, journal, worker_id, clock)


def controller_config(releases: Iterable[EngineRelease]) -> EngineControllerConfig:
    """The release images, the release snapshot policy when ``HARNESS_ENGINE_SNAPSHOTS`` is on (P5.9b), and the
    ``/pipeshub`` session folder ``ph`` reads the run's tools from (R3). A version a release declares it resumes
    (ADR-OPS-01 D6) resolves to its successor's image and pin; the CPUs follow the image."""
    chosen = tuple(releases)
    successors = {old: release.version for old, release in release_successors(chosen).items()}
    return EngineControllerConfig(
        images=ByEngineRef({r.version: run_image(r.image) for r in chosen}, successors),
        image_pins=ByEngineRef({r.version: r.image_digest for r in chosen if r.image_digest is not None}, successors),
        image_resources=release_image_resources(chosen),
        snapshot=release_snapshot_policy() if engine_snapshots_enabled() else None,
        signin_keep=signin_keep(),
        tool_folder=pipeshub_tool_folder(),
        # WP-63 part A: the folder repository_fetch hands repositories over in, sized to HARNESS_REPO_MAX_MB.
        repos_mount=hand_off_mount(fetch_config_from_env()),
        # WP98-FU-1: where ph git clone checks repositories out, outside the saved workspace.
        references_mount=references_mount(fetch_config_from_env()),
        # J-26: HARNESS_INPUT_MAX_MB; a value that is not a positive whole number refuses to start.
        input_max_bytes=input_max_bytes(),
    )


def signin_keep() -> SignInKeep:
    """SIGNIN-5: ``HARNESS_SIGNIN_IDLE_MINUTES`` (5..120, default 30) and ``HARNESS_SIGNIN_MAX_HOURS`` (1..24,
    default 12); anything else refuses to start rather than keep a sign-in longer or shorter than meant."""

    def number(name: str, raw: str | None, default: int) -> int:
        raw = (raw or "").strip()
        if not raw:
            return default
        if not raw.isdigit():
            raise ValueError(f"{name}={raw!r} is not a whole number")
        return int(raw)

    return SignInKeep(
        idle_s=number(SIGNIN_IDLE_ENV, os.getenv(SIGNIN_IDLE_ENV), KEEP_IDLE_S // 60) * 60,
        max_s=number(SIGNIN_MAX_ENV, os.getenv(SIGNIN_MAX_ENV), KEEP_MAX_S // 3600) * 3600,
    )


def sign_in_hosts(*, relay: bool) -> SignInHosts | None:
    """The signed hosts this worker's sandboxes may tunnel to for a person's own sign-in (SIGNIN): only with the
    relay a code reaches it through, the proxy's tunnels (``EGRESS_CONNECT_TUNNELS``) and the release's signed list."""
    return release_sign_in_hosts() if relay and connect_tunnels_enabled() else None


def carries_sign_ins(*, relay: bool) -> bool:
    """Whether this worker's engine controller admits a run pinned on its actor's own plan: what it advertises as
    ``vendor_signin`` (SIGNGAP), decided by the same hosts ``engine_services`` hands the controller."""
    return sign_in_hosts(relay=relay) is not None


def engine_services(
    plan: EnginePlan,
    *,
    services: RunServices,
    tool_host: ToolGatewayHost,
    route_for: Callable[[AttemptToken], AttemptRoute],
    clock: Clock,
    objects: BinaryObjectStore,
    tracer: HarnessTracer,
    activity: LiveSinkPort | None = None,
    signin_relay: SignInRelay | None = None,
    metrics: GatewayMetrics = NOOP_GATEWAY_METRICS,
) -> DurableEngineServices:
    """What every engine attempt of this worker shares; a run's pinned skills come from the org's graph store.

    A person's own sign-in (SIGNIN) needs the relay, the proxy's tunnels (``EGRESS_CONNECT_TUNNELS``) and the signed
    host list; short of any, no hosts are given and such a run is refused (and the worker does not advertise
    ``vendor_signin``, so Node pins none: ``carries_sign_ins``)."""
    hosts = sign_in_hosts(relay=signin_relay is not None)
    return DurableEngineServices(
        broker=plan.broker,
        tool_host=tool_host,
        engines=release_registry(releases=plan.releases),
        config=controller_config(plan.releases),
        objects=objects,
        route_for=route_for,
        clock=clock,
        placeholder_key=secrets.token_bytes(32),
        skill_bundles_for=session_skill_bundles(GraphProviderSkillSource(services.graph_provider)),
        tracer=tracer,
        activity=activity,
        signin_relay=signin_relay,
        signin_hosts=hosts,
        gateway_metrics=metrics,
    )


def engine_worker(
    plan: EnginePlan,
    *,
    services: RunServices,
    journal: NodeJournal,
    clock: Clock,
    objects: BinaryObjectStore,
    interactions: InteractionWakeup | None,
    renew_interval_s: float,
    activity: LiveSinkPort | None = None,
    policies: RunPolicies | None = None,
    signin_relay: SignInRelay | None = None,
    preempt: PreemptRequests | None = None,
    admission: ProviderAdmission | None = None,
) -> EngineWorker:
    """The engine kernel this worker registers with its ``RunDriver`` and the gateway it serves."""
    metrics = BackendGatewayMetrics()
    gateway = ToolGatewayApp(
        OrgBoundGatewayTokenVerifier(ConfiguredGatewayKeys(services.config_service()), clock), metrics=metrics
    )
    tracer = harness_tracer("worker")
    engines = engine_services(
        plan,
        services=services,
        tool_host=HttpToolGatewayHost(gateway, base_url=gateway_base_url(plan.port)),
        route_for=journal.attempt_route,
        clock=clock,
        objects=objects,
        tracer=tracer,
        activity=activity,
        signin_relay=signin_relay,
        metrics=metrics,
    )
    assembler = PipesHubEngineAssembler(services, engines, policies=policies, admission=admission)
    kernel = EngineKernel(
        journal=journal,
        assembler=assembler,
        clock=clock,
        broker=plan.broker,
        presenter=PipesHubEngineRunPresenter(),
        interactions=interactions,
        renew_interval_s=renew_interval_s,
        tracer=tracer,
        preempt=preempt,
        # ADR-OPS-01 D6: a run of a retired version resumes on its declared successor.
        successors=engines.engines if isinstance(engines.engines, EngineSuccessors) else None,
    )
    _LOG.info("engine.worker.ready", port=plan.port, engines=sorted(r.version.kind for r in plan.releases))
    return EngineWorker(
        kernel,
        gateway,
        plan.port,
        engines.parked,
        exposition=worker_metrics_exposition(services.config_service()),
        clients=(functools.partial(close_broker, plan.broker), assembler.aclose),
    )


__all__ = [
    "BROKER_WAIT_S",
    "DEFAULT_GATEWAY_PORT",
    "ENGINE_RUNS_ENV",
    "GATEWAY_MOUNT",
    "GATEWAY_PORT_ENV",
    "GATEWAY_URL_ENV",
    "SIGNIN_IDLE_ENV",
    "SIGNIN_MAX_ENV",
    "EnginePlan",
    "EngineWorker",
    "carries_sign_ins",
    "controller_config",
    "signin_keep",
    "engine_runs_enabled",
    "engine_snapshots_enabled",
    "engine_services",
    "engine_worker",
    "gateway_base_url",
    "plan_engines",
    "sign_in_hosts",
    "tool_gateway_api",
]
