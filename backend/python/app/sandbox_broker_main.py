"""Sandbox broker process role (P5.1): the only PipesHub process that holds the Docker socket.

Run with ``python -m app.sandbox_broker_main``. It serves the broker HTTP API
(``harness_core.sandbox.remote``, ``/v1``) on ``SANDBOX_BROKER_PORT``; every
route but ``/healthz`` requires the worker credential Node provisions. App
containers reach sandboxes only through this API, so they need no socket.

On SIGTERM it stops serving and leaves sandboxes running: a restarted broker
adopts them by key, and the RunSweeper's ``reap`` removes the ones whose runs
are gone. Its own sweep removes sandboxes past their ``LIFETIME_LABEL`` (MCP
runners), whoever held them. A sandbox it removes on its own that names a
``FAREWELL_LABEL`` (a kept sign-in) gets that engine's logout first (GAPS-2). At start it removes what its own
install left that can never run again (``DockerSandboxBroker.reconcile``, CLEANUP-G6).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from aiohttp import web

import app.utils.runtime_threads  # noqa: F401 - must precede all ML library imports
from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    open_egress_pool,
    parse_cidrs,
)
from app.core.org_context import spawn_detached
from app.edition import check_runtime_settings
from app.harness.credential_roles import listener_auth
from app.harness.placement import deployment_region
from app.harness.sandbox import docker_broker, docker_policy
from app.harness.sandbox.docker_broker import (
    VOLUME_PREFIX,
    DockerSandboxBroker,
    WriteBound,
)
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.egress_config import broker_egress_wiring
from app.harness.sandbox.engine_images import EngineImageAvailability
from app.harness.sandbox.images import mcp_runner_image, sandbox_image
from app.harness.supply_chain.images import image_trust_from_env
from app.sandbox.docker_proxy import ENV_BROKER_CALLER_TOKEN
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.sandbox_broker_metrics import BackendBrokerMetrics
from harness_core.clock import SystemClock
from harness_core.engines.controller import (
    EngineControllerConfig,
    engine_mode,
    engine_warm_shapes,
)
from harness_core.http_limits import ReadLimits, start_guarded_site
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_core.repos.fetch import hand_off_mount, references_mount
from harness_core.sandbox.errors import SandboxProviderUnavailable
from harness_core.sandbox.pool import WarmPoolConfig
from harness_core.sandbox.provider import (
    PROVIDER_ENV,
    SandboxProviderFactory,
    SandboxProviderKind,
    provider_kind,
)
from harness_core.sandbox.remote.server import create_broker_app
from harness_engines.claude_code.signin import FAREWELL as CLAUDE_CODE_FAREWELL
from harness_engines.pipeshub import pipeshub_tool_folder
from harness_engines.release import (
    release_allowlist,
    release_binary_pins,
    release_image_pins,
    release_image_resources,
    release_images,
    run_image,
    uninstalled_engines,
    untrusted_engines,
)

# Stop at load on a setting this edition does not run.
check_runtime_settings()

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.types import RuntimeMode

SANDBOX_BROKER_PORT = 8095
WARM_SWEEP_INTERVAL_S = 30.0
DOCKER_WORKERS = 32
# Persistent sandbox directories live under subpaths of this volume; Docker creates it on first use. It holds each
# session's workspace and each org's read-only caches (C20.20: ``<install>/<org>/caches/<name>-<digest>``).
DATA_VOLUME = docker_policy.DATA_VOLUME
_ENV_DATA_VOLUME = "SANDBOX_DATA_VOLUME"
# The same variables and defaults the coding sandbox reads (app/agent_loop_lib/sandbox/coding/settings.py).
_ENV_EGRESS_NETWORK = "SANDBOX_EGRESS_NETWORK"
_ENV_EGRESS_ALLOW_CIDRS = "SANDBOX_EGRESS_ALLOW_CIDRS"
# Which install's folders on the data volume are this broker's: one daemon can serve several installs.
_ENV_INSTALL_ID = "SANDBOX_INSTALL_ID"
_ENV_COMPOSE_PROJECT = "COMPOSE_PROJECT_NAME"
# The operator's ceiling on warm engine sandboxes (P5.9a); 0 or unset is off.
_ENV_WARM_POOL_MAX = "SANDBOX_WARM_POOL_MAX"
# How much a session's persistent workspace may hold on the host's disk (SEC-ISO-06); twice the sync's archive limit.
_ENV_WORKSPACE_QUOTA_MB = "SANDBOX_WORKSPACE_QUOTA_MB"
DEFAULT_WORKSPACE_QUOTA_MB = 4096
# The install's ceiling on live sandboxes (C2.19): the legacy governor's variable and default, now counted across
# every broker process of the namespace instead of per process.
_ENV_MAX_TOTAL = "SANDBOX_MAX_TOTAL"
DEFAULT_MAX_SANDBOXES = 50
# Runtimes the daemon has that the broker must not offer (a runsc after a bad upgrade, a runc test lane on a gVisor
# host): the release floor still refuses engines without gVisor unless the dev override is on.
_ENV_WITHHOLD_RUNTIMES = "SANDBOX_WITHHOLD_RUNTIMES"
WITHHOLDABLE_RUNTIMES = frozenset({"runsc"})
_DEFAULT_EGRESS_NETWORK = docker_policy.DEFAULT_EGRESS_NETWORK
# sysexits EX_CONFIG: the deployment's configuration names something this process cannot run.
EX_CONFIG = 78

_LOG = HarnessLog.named("app.sandbox_broker")


def allowed_images() -> frozenset[str]:
    """The legacy sandbox image, the MCP runner image (P5.10) and every pinned engine image (P5.1-f); anything
    else is refused."""
    return frozenset({sandbox_image(), mcp_runner_image()}) | release_images()


def engine_images() -> dict[str, tuple[str, ...]]:
    """Each pinned engine's run image -> the engines (``kind@version``) it runs, whose availability health reports
    (H1); an ACP agent can share a native engine's image."""
    images: dict[str, tuple[str, ...]] = {}
    for release in release_allowlist():
        image = run_image(release.image)
        images[image] = (*images.get(image, ()), release.version.ref)
    return images


class InstallIdUnset(ValueError):
    reason_code = "install_id_unset"


def install_id() -> str:
    """``SANDBOX_INSTALL_ID``, else the Compose project. Neither set refuses to start: a shared fallback would let two
    installs on one daemon share their folders, and each one's start-up reconcile remove the other's sandboxes.
    Compose and Helm always set it; a manual run sets it (``default`` included) on purpose."""
    configured = (os.getenv(_ENV_INSTALL_ID) or "").strip() or (os.getenv(_ENV_COMPOSE_PROJECT) or "").strip()
    if not configured:
        raise InstallIdUnset(f"set {_ENV_INSTALL_ID} to a name for this install (one per Docker daemon)")
    return configured


class InvalidWarmPoolMax(ValueError):
    reason_code = "invalid_warm_pool_max"


def warm_pool_max() -> int:
    """``SANDBOX_WARM_POOL_MAX``: unset or blank is 0 (off); anything but a non-negative integer refuses to start."""
    raw = (os.getenv(_ENV_WARM_POOL_MAX) or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        raise InvalidWarmPoolMax(f"{_ENV_WARM_POOL_MAX}={raw!r} is not a non-negative integer")
    return value


class InvalidWorkspaceQuota(ValueError):
    reason_code = "invalid_workspace_quota"


def workspace_quota_mb() -> int:
    """``SANDBOX_WORKSPACE_QUOTA_MB``: unset or blank is the default; anything but a positive integer refuses to start
    (no value turns the quota off: an unbounded workspace can fill the host's disk)."""
    raw = (os.getenv(_ENV_WORKSPACE_QUOTA_MB) or "").strip()
    if not raw:
        return DEFAULT_WORKSPACE_QUOTA_MB
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise InvalidWorkspaceQuota(f"{_ENV_WORKSPACE_QUOTA_MB}={raw!r} is not a positive integer")
    return value


_ENV_DATA_DEVICE = "SANDBOX_DATA_DEVICE"
_ENV_WRITE_MBPS = "SANDBOX_WRITE_MBPS"
DEFAULT_WRITE_MBPS = 32


class InvalidWriteBound(ValueError):
    reason_code = "invalid_write_bound"


def write_bound() -> WriteBound | None:
    """SEC-A-03: ``SANDBOX_DATA_DEVICE`` (the whole disk under the daemon's volumes, found by the installer) and
    ``SANDBOX_WRITE_MBPS`` (default 32): the kernel's cap on each sandbox's writes. No device: no cap, logged at
    start; a value that cannot be read refuses to start."""
    device = (os.getenv(_ENV_DATA_DEVICE) or "").strip()
    if not device:
        return None
    raw = (os.getenv(_ENV_WRITE_MBPS) or "").strip()
    try:
        return WriteBound(device, int(raw) if raw else DEFAULT_WRITE_MBPS)
    except ValueError:
        raise InvalidWriteBound(f"{_ENV_DATA_DEVICE}={device!r} {_ENV_WRITE_MBPS}={raw!r} is not a write bound") from None


class InvalidMaxSandboxes(ValueError):
    reason_code = "invalid_max_sandboxes"


def max_sandboxes() -> int | None:
    """``SANDBOX_MAX_TOTAL``: unset or blank is the default; zero or less is no ceiling, as the legacy governor reads
    it; anything but an integer refuses to start."""
    raw = (os.getenv(_ENV_MAX_TOTAL) or "").strip()
    if not raw:
        return DEFAULT_MAX_SANDBOXES
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidMaxSandboxes(f"{_ENV_MAX_TOTAL}={raw!r} is not an integer") from exc
    return value if value > 0 else None


class InvalidWithheldRuntimes(ValueError):
    reason_code = "invalid_withheld_runtimes"


def withheld_runtimes() -> frozenset[str]:
    """``SANDBOX_WITHHOLD_RUNTIMES``: comma-separated; a name the broker could not offer anyway refuses to start, so a
    typo never leaves gVisor offered while the operator believes it withheld."""
    names = frozenset(name.strip() for name in (os.getenv(_ENV_WITHHOLD_RUNTIMES) or "").split(",") if name.strip())
    unknown = sorted(names - WITHHOLDABLE_RUNTIMES)
    if unknown:
        raise InvalidWithheldRuntimes(
            f"{_ENV_WITHHOLD_RUNTIMES} names {unknown}; only {sorted(WITHHOLDABLE_RUNTIMES)} can be withheld"
        )
    return names


def warm_images() -> frozenset[str]:
    """The pinned engine images some engine runnable here starts from: an image only uninstalled ACP engines use
    never serves a run, so warming it would hold memory for nothing (R23FU-FU-1)."""
    uninstalled = frozenset(uninstalled_engines())
    return frozenset(image for image, refs in engine_images().items() if any(ref not in uninstalled for ref in refs))


def warm_pool_config(modes: frozenset[RuntimeMode]) -> WarmPoolConfig | None:
    """Warm sandboxes of every image an installed engine runs on, in the mode engine turns get on this host, shaped
    as the controller's default configuration builds them; other shapes of those images are learned from acquires."""
    ceiling = warm_pool_max()
    mode = engine_mode(modes)
    if ceiling == 0 or mode is None:
        return None
    images = warm_images()
    from app.harness.repositories import fetch_config_from_env

    return WarmPoolConfig(
        ceiling=ceiling,
        shapes=engine_warm_shapes(
            EngineControllerConfig(
                images={},
                tool_folder=pipeshub_tool_folder(),
                image_resources=release_image_resources(),
                # The worker's repository folder (WP-63), so a warm sandbox has the shape its turns ask for.
                repos_mount=hand_off_mount(fetch_config_from_env()),
                references_mount=references_mount(fetch_config_from_env()),
            ),
            sorted(images),
            mode,
        ),
        learn_images=images,
    )


@dataclass(frozen=True)
class BrokerService:
    broker: DockerSandboxBroker
    app: web.Application


@dataclass(frozen=True)
class BrokerBackendContext:
    """What this process's provider builders need."""

    config_service: ConfigurationService
    provider: DockerClientProvider
    clock: Clock
    ids: IdSource


async def docker_backend(context: BrokerBackendContext) -> DockerSandboxBroker:
    # A bad SANDBOX_OPEN_EGRESS_SUBNETS stops the broker here, not at its first open-egress sandbox.
    open_egress_pool()
    provider, clock = context.provider, context.clock
    withheld = withheld_runtimes()
    pool = warm_pool_config((await docker_broker.probe(provider, withheld=withheld)).modes())
    # ADR-SEC-01: engine images on a release signature (once OB-1 turns it on), and what the edition's check adds.
    trust = image_trust_from_env(release_images(), release_images() | {sandbox_image()})
    return await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=context.ids,
        warm_pool=pool,
        allowed_images=allowed_images() | (trust.added_images if trust is not None else frozenset()),
        image_trust=trust,
        egress_network=os.getenv(_ENV_EGRESS_NETWORK) or _DEFAULT_EGRESS_NETWORK,
        egress_allow_cidrs=parse_cidrs(os.getenv(_ENV_EGRESS_ALLOW_CIDRS)),
        data_volume=os.getenv(_ENV_DATA_VOLUME) or DATA_VOLUME,
        install_id=install_id(),
        helper_image=sandbox_image(),
        # An ACP engine without an installed profile is never runnable here, whatever its image (ACP-driver).
        engine_images=EngineImageAvailability(
            provider,
            engine_images(),
            clock,
            uninstalled=uninstalled_engines(),
            untrusted=untrusted_engines(),
            pins=release_image_pins(),
        ),
        binary_pins=release_binary_pins(),
        # A kept sign-in the broker ends itself is signed out at the vendor first (GAPS-2), keyed by engine kind.
        farewells={"claude_code": CLAUDE_CODE_FAREWELL},
        workspace_quota_mb=workspace_quota_mb(),
        write_bound=write_bound(),
        max_sandboxes=max_sandboxes(),
        metrics=BackendBrokerMetrics(),
        withheld_runtimes=withheld,
        egress=await broker_egress_wiring(
            context.config_service, provider=provider, clock=clock, network_prefix=f"{VOLUME_PREFIX}-net-"
        ),
    )


# C2.14: the providers this process can serve. It holds the Docker socket, so Docker is the one; any other kind the
# deployment names is refused at start with a plain message.
BROKER_PROVIDERS: SandboxProviderFactory[BrokerBackendContext, DockerSandboxBroker] = SandboxProviderFactory(
    {SandboxProviderKind.DOCKER: docker_backend}, where="this sandbox broker"
)


def configured_kind() -> SandboxProviderKind:
    return provider_kind(os.getenv(PROVIDER_ENV))


async def create_service(
    config_service: ConfigurationService,
    *,
    provider: DockerClientProvider,
    clock: Clock,
    ids: IdSource,
    kind: SandboxProviderKind | None = None,
) -> BrokerService:
    opened = await BROKER_PROVIDERS.open(
        configured_kind() if kind is None else kind, BrokerBackendContext(config_service, provider, clock, ids)
    )
    broker = opened.broker
    if broker.capabilities.reachable:
        try:
            _ = await broker.reconcile()
        except Exception as exc:
            # Clean-up only: a broker that cannot reconcile still serves; the reaps and sweeps go on.
            _LOG.warning("broker.reconcile_failed", errorType=type(exc).__name__)
    # SEC-C-10: the worker's credential on the broker's routes, the scraper's on /metrics, nothing else.
    auth = listener_auth(config_service, "sandbox-broker")
    app = create_broker_app(broker, auth, region=deployment_region(), metrics=METRICS_BACKEND.serialize)
    return BrokerService(broker, app)


async def _sweep_warm(broker: DockerSandboxBroker, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), WARM_SWEEP_INTERVAL_S)
        try:
            # Its own backstop: a crashed worker's runners are reaped even if no worker opens a runner again.
            _ = await broker.reap_expired()
        except Exception as exc:
            _LOG.warning("broker.expired_reap_failed", errorType=type(exc).__name__)
        try:
            swept = await broker.sweep_warm()
        except Exception as exc:
            _LOG.error("broker.warm_sweep_failed", exc=exc, errorType=type(exc).__name__)
            continue
        if swept:
            _LOG.info("broker.warm_swept", count=swept)
        try:
            _ = await broker.trim_warm()
            _ = await broker.fill_warm()
        except Exception as exc:
            # Acquires go cold until the next round.
            _LOG.warning("broker.warm_pool_failed", errorType=type(exc).__name__)
        try:
            _ = await broker.resync_egress()
        except Exception as exc:
            # The proxy refuses unknown sandboxes until the next attempt succeeds (fail closed).
            _LOG.warning("broker.egress_resync_failed", errorType=type(exc).__name__)


async def start_broker_listener(
    app: web.Application, host: str, port: int, *, read_limits: ReadLimits | None = None
) -> web.AppRunner:
    """The broker's API on ``host:port``, its requests' reads bounded (SEC-DOS-04); ``runner.addresses`` has the port."""
    runner = web.AppRunner(app)
    await runner.setup()
    _ = await start_guarded_site(
        runner, host, port, limits=read_limits or ReadLimits.from_env(), listener="sandbox_broker"
    )
    return runner


async def run_broker(port: int = SANDBOX_BROKER_PORT) -> None:
    from app.harness.assembly.services import HarnessServices

    try:
        kind = configured_kind()
        BROKER_PROVIDERS.require(kind)
    except SandboxProviderUnavailable as unavailable:
        _LOG.error("broker.provider_unavailable", reasonCode=unavailable.reason_code, message=str(unavailable))
        raise SystemExit(EX_CONFIG) from None
    services = HarnessServices()
    # R41SUPPLY-FU-6: a release key revoked in the deployment's stored list vouches for no engine image here; the
    # broker only reads that copy (the worker fetches it), and keeps the list in force when the store is down.
    from app.harness.supply_chain.revocations import RevocationRefresher

    revocations = RevocationRefresher(services.config_service(), url=None)
    await revocations.start()
    # R38IDENT-FU-1: the broker holds no Docker socket; DOCKER_HOST is the policy proxy, which admits this credential.
    provider = DockerClientProvider(max_workers=DOCKER_WORKERS, caller_token_env=ENV_BROKER_CALLER_TOKEN)
    service = await create_service(
        services.config_service(), provider=provider, clock=SystemClock(), ids=RandomIds(), kind=kind
    )
    runner = await start_broker_listener(service.app, "0.0.0.0", port)

    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopped.set)
    service.broker.pool.kick()
    sweeper = spawn_detached(_sweep_warm(service.broker, stopped))
    # C5.01: the engine images come down now, in the background, so no session waits for a pull.
    prepull = spawn_detached(service.broker.prepull_engine_images())
    # engineImages: what the human-stack smoke compares with the allowlist of the tree it runs (HTIMG).
    images = sorted(release_images())
    # enginesUntrusted: the ACP engines whose profile no trusted key signed (R32-TRUST), what the smoke check reads.
    _LOG.info(
        "broker.started",
        port=port,
        modes=sorted(service.broker.modes()),
        engineImages=images,
        enginesUntrusted=list(untrusted_engines()),
        # SEC-A-03: whether the kernel caps each sandbox's disk writes (MiB/s); 0 when no data device is set.
        writeMbps=service.broker.write_mbps,
    )
    try:
        await stopped.wait()
    finally:
        await runner.cleanup()
        await sweeper
        if not prepull.done():
            _ = prepull.cancel()
        await service.broker.aclose()
        await revocations.stop()
        provider.close()
        _LOG.info("broker.stopped")
        shutdown = cast("object", services.shutdown_resources())  # pyright: ignore[reportUnknownMemberType]
        if asyncio.iscoroutine(shutdown):
            await shutdown


if __name__ == "__main__":
    asyncio.run(run_broker())
