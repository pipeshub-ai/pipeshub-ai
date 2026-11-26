"""The process's MCP runner: one ``SandboxMCPRunner`` over the broker, and the sweeper that idles runners out."""

from __future__ import annotations

import asyncio
import functools
import os
import socket
import sys
from typing import TYPE_CHECKING, Any

from app.core.org_context import spawn_detached
from app.harness.sandbox.client import process_broker_client
from app.harness.sandbox.egress_credentials import credential_sealer
from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import SandboxProviderUnavailable
from harness_core.sandbox.mcp_runner.errors import RunnerUnavailable
from harness_core.sandbox.mcp_runner.sandboxed import SandboxMCPRunner
from harness_core.sandbox.mcp_runner.types import RunnerConfig, RunnerSecrets

if TYPE_CHECKING:
    from app.harness.mcp_runner.settings import RunnerSettings
    from harness_core.sandbox.mcp_runner.ports import MCPRunner
    from harness_core.sandbox.mcp_runner.types import RunnerKey

# Every PipesHub runner sandbox carries it, so an install's sweep reaps only its own runners' orphans.
RUNNER_ROLE_LABELS = {"pipeshub.role": "mcp-runner"}
_MAX_SWEEP_INTERVAL_S = 60.0
_LOG = HarnessLog.named("app.harness.mcp_runner")


@functools.cache
def _config_service() -> Any:  # noqa: ANN401 - ConfigurationService
    """The process's configuration service, built once on the first sweep that asks (the runner has no DI
    container), as the broker client builds its own."""
    from app.harness.assembly.services import HarnessServices

    return HarnessServices().config_service()


async def sweep_forever(runner: MCPRunner, *, interval_s: float) -> None:
    """The runner sweeper: stop idle and over-age runners and reap orphans, until cancelled."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            _ = await runner.sweep()
        except Exception as exc:
            _LOG.error("mcp_runner.sweep_failed", exc=exc, errorType=type(exc).__name__)


def pool_name() -> str:
    """This process's pool by a name a restart keeps: its host and the service it runs (``app.query_main``,
    ``app.harness_main``). Each service runs one pool per host (container or pod), so the name is the process's."""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    service = getattr(spec, "name", None) or os.path.basename(sys.argv[0] or "python")
    return f"{socket.gethostname()}:{service}"[:63]


class _StrictReads:
    """The config service with every read raising on a store failure rather than answering its default: the
    sweep acts on absence, so an unreadable instance or sign-in must not read as a deleted one."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401 - ConfigurationService
        self._inner = inner

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **kwargs: Any) -> Any:  # noqa: ANN401, FBT001, FBT002
        kwargs.pop("raise_on_error", None)
        return await self._inner.get_config(key, default=default, use_cache=use_cache, raise_on_error=True, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


async def server_still_runs(key: RunnerKey) -> bool | RunnerSecrets:
    """Whether a runner's server still runs for its key: the instance is the org's (or its owner's own), still
    STDIO and not disabled on this deployment (R36COVER-FU-4), and its owner (or the shared admin credential) is
    still signed in (R43MCP-FU-2). If so, the secrets it would start with now, so a changed secret retires the
    runner at the sweep rather than at the next open. A failed read raises, and the sweep keeps the runner."""
    from app.agents.mcp.discovery import build_auth_env_and_headers
    from app.agents.mcp.models import MCPTransport
    from app.agents.mcp.service import (
        credentials_to_discovery_dict,
        instance_config_from_dict,
        is_effective_auth_authenticated,
        resolve_effective_user_auth,
    )
    from app.agents.mcp.stdio_policy import instance_disabled_reason
    from app.edition_config import get_mcp_instance_resolved

    # Duck-typed: the store reads take a ConfigurationService, and this is one whose reads raise.
    config: Any = _StrictReads(_config_service())
    instance = await get_mcp_instance_resolved(key.server_id, config, key.tenant, key.owner)
    if (
        instance is None
        or instance.get("transport") != MCPTransport.STDIO.value
        or instance_disabled_reason(instance) is not None
    ):
        return False
    auth = await resolve_effective_user_auth(instance, key.owner, config, adopt=False)
    if not is_effective_auth_authenticated(auth):
        return False
    credentials = credentials_to_discovery_dict(instance.get("authMode", ""), auth or {})
    env, _ = build_auth_env_and_headers(instance_config_from_dict(instance), credentials)
    return RunnerSecrets(env)


class ProcessMCPRunner:
    """Opened on first use (the broker client is the process's); the sweeper starts with it."""

    def __init__(self) -> None:
        self._runner: SandboxMCPRunner | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def get(self, settings: RunnerSettings) -> MCPRunner:
        async with self._lock:
            if self._runner is None:
                try:
                    broker = await process_broker_client.get()
                except SandboxProviderUnavailable as unavailable:
                    raise RunnerUnavailable(str(unavailable), cause=unavailable.reason_code) from unavailable
                except Exception as exc:
                    raise RunnerUnavailable(
                        f"the sandbox broker is unreachable: {type(exc).__name__}", cause="broker_unreachable"
                    ) from exc
                config = RunnerConfig(
                    idle_ttl_s=settings.idle_ttl_s, max_age_s=settings.max_age_s, labels=RUNNER_ROLE_LABELS,
                    max_per_tenant=settings.max_per_org,
                )
                self._runner = SandboxMCPRunner(
                    broker,
                    clock=SystemClock(),
                    ids=RandomIds(),
                    config=config,
                    instance=f"mcp-runner-{socket.gethostname()}-{os.getpid()}",
                    # A server's secrets reach the proxy sealed with the deployment's key, never the runner (OWD-1).
                    sealer=credential_sealer(),
                    retain=server_still_runs,
                    pool=pool_name(),
                )
                _ = await self._runner.reap_previous()
                interval = min(_MAX_SWEEP_INTERVAL_S, settings.idle_ttl_s / 4)
                self._sweeper = spawn_detached(sweep_forever(self._runner, interval_s=interval))
                _LOG.info("mcp_runner.pool_started", idleTtlS=settings.idle_ttl_s, maxAgeS=settings.max_age_s)
            return self._runner

    async def aclose(self) -> int:
        """Stop the sweeper and every runner (process shutdown); the number of runners stopped."""
        async with self._lock:
            if self._sweeper is not None:
                _ = self._sweeper.cancel()
                self._sweeper = None
            runner, self._runner = self._runner, None
        return await runner.stop_all() if runner is not None else 0


process_mcp_runner = ProcessMCPRunner()

__all__ = [
    "RUNNER_ROLE_LABELS",
    "ProcessMCPRunner",
    "pool_name",
    "process_mcp_runner",
    "server_still_runs",
    "sweep_forever",
]
