"""``MCPRunner`` over a ``SandboxBroker``: each runner is one sandbox running the bridge.

The broker gives isolation (the strongest mode at or above the spec's floor),
egress (P5.4's proxy, listed hosts only) and the ``connect`` relay; nothing here
reaches a container runtime. The server never holds its secrets (OWD-1, WP-06):
its environment has a placeholder for each, and the sandbox's egress routes carry
the secret sealed, for the proxy to put back where the placeholder appears on a
request to a listed host (inside TLS the proxy ends; the bridge first trusts the
proxy's certificate). No secret is in the spec's labels, the idempotency key, a
process's environment or a log line.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections import deque
from importlib import resources
from typing import TYPE_CHECKING, override

from harness_core.engines.controller import engine_mode
from harness_core.sandbox.egress.sealed import new_placeholder
from harness_core.sandbox.egress.trust import trust_env
from harness_core.sandbox.egress.types import EGRESS_PROXY_HOST, EGRESS_PROXY_PORT
from harness_core.sandbox.errors import (
    IsolationUnavailable,
    SandboxError,
    SandboxRefusal,
)
from harness_core.sandbox.mcp_runner.errors import (
    MCPRunnerRefused,
    RunnerIsolationUnavailable,
    RunnerStartFailed,
    RunnerUnavailable,
)
from harness_core.sandbox.mcp_runner.policy import runner_egress_policy, secret_routes
from harness_core.sandbox.mcp_runner.table import RunnerTable
from harness_core.sandbox.mcp_runner.types import (
    BRIDGE_DIR,
    BRIDGE_PATH,
    BRIDGE_PORT,
    BRIDGE_READY,
    POOL_LABEL,
    RUNNER_LABEL,
)
from harness_core.sandbox.tar import tar_stream
from harness_core.sandbox.types import (
    LIFETIME_LABEL,
    LeaseRef,
    MountSpec,
    SandboxSpec,
    TokenRoute,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.egress.sealed import CredentialSealer
    from harness_core.sandbox.mcp_runner.table import Retain
    from harness_core.sandbox.mcp_runner.types import (
        MCPRunnerSpec,
        RunnerConfig,
        RunnerRequest,
    )
    from harness_core.sandbox.ports import (
        ByteStream,
        ProcessHandle,
        SandboxBroker,
        SandboxHandle,
    )
    from harness_core.sandbox.types import EgressPolicy, RuntimeMode

_STDERR_TAIL_BYTES = 4096


def bridge_source() -> bytes:
    return resources.files("harness_core.sandbox.mcp_runner").joinpath("bridge_program.py").read_bytes()


def runner_base_env(spec: MCPRunnerSpec) -> dict[str, str]:
    """What every runner process sees besides its secrets: a writable home, and the proxy when it may go out."""
    env = {"HOME": "/tmp", "PYTHONUNBUFFERED": "1"}
    if spec.egress:
        proxy = f"http://{EGRESS_PROXY_HOST}:{EGRESS_PROXY_PORT}"
        # HTTPS goes through the proxy as CONNECT; it is refused unless the proxy offers tunnels (P5.4b).
        env |= {
            "HTTP_PROXY": proxy,
            "http_proxy": proxy,
            "HTTPS_PROXY": proxy,
            "https_proxy": proxy,
            "NODE_USE_ENV_PROXY": "1",
        }
    return env


def runner_sandbox_spec(
    spec: MCPRunnerSpec,
    request: RunnerRequest,
    *,
    modes: frozenset[RuntimeMode],
    runner_id: str,
    labels: Mapping[str, str],
    egress: EgressPolicy | None = None,
) -> SandboxSpec:
    """The sandbox one runner runs in; it carries the server's names and ids, and its secrets only sealed in
    ``egress`` (unset: the listed hosts with no route)."""
    mode = engine_mode(modes, spec.isolation_floor)
    if mode is None:
        raise RunnerIsolationUnavailable(f"no runtime at or above {spec.isolation_floor.value}")
    return SandboxSpec(
        image_digest=spec.image,
        mode=mode,
        resources=spec.resources,
        egress=egress if egress is not None else runner_egress_policy(spec),
        mounts=(MountSpec(BRIDGE_DIR, read_only=True),),
        labels={
            **request.labels,
            **labels,
            RUNNER_LABEL: "1",
            "orgId": request.key.tenant,
            "mcpServer": request.key.server_id,
            "mcpRunnerKey": request.key.digest(),
            "mcpRunnerId": runner_id,
        },
    )


class _SandboxRunner:
    def __init__(self, broker: SandboxBroker, handle: SandboxHandle, process: ProcessHandle) -> None:
        self._broker = broker
        self._handle = handle
        self._process = process
        self._ready = asyncio.get_running_loop().create_future()
        self._stderr: deque[bytes] = deque()
        self._stderr_size = 0
        self._stdout_task = asyncio.ensure_future(self._drain_stdout())
        self._stderr_task = asyncio.ensure_future(self._drain_stderr())

    @property
    def sandbox_id(self) -> str | None:
        return self._handle.id

    async def _drain_stdout(self) -> None:
        seen = b""
        async for chunk in self._process.stdout:
            if not self._ready.done():
                seen += chunk
                if BRIDGE_READY in seen:
                    self._ready.set_result(None)
        if not self._ready.done():
            self._ready.set_exception(RunnerStartFailed("the bridge exited before it listened"))

    async def _drain_stderr(self) -> None:
        async for chunk in self._process.stderr:
            self._stderr.append(chunk)
            self._stderr_size += len(chunk)
            while self._stderr_size > _STDERR_TAIL_BYTES and len(self._stderr) > 1:
                self._stderr_size -= len(self._stderr.popleft())

    def stderr_tail(self, secrets: Mapping[str, str]) -> str:
        text = b"".join(self._stderr).decode("utf-8", "replace")[-_STDERR_TAIL_BYTES:]
        for value in secrets.values():
            if value:
                text = text.replace(value, "***")
        return text

    async def ready(self, timeout_s: float) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), timeout_s)
        except TimeoutError as exc:
            raise RunnerStartFailed(f"the bridge did not listen within {timeout_s:g} s") from exc

    def alive(self) -> bool:
        return not self._stdout_task.done()

    async def connect(self) -> ByteStream:
        return await self._handle.connect(BRIDGE_PORT)

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self._process.signal(signal.SIGKILL)
        for task in (self._stdout_task, self._stderr_task):
            _ = task.cancel()
        if not self._ready.done():
            self._ready.cancel()
        await self._broker.release(self._handle)


class SandboxMCPRunner(RunnerTable):
    """``MCPRunner`` whose runners are broker sandboxes; ``instance`` names this pool's lease holder."""

    def __init__(
        self,
        broker: SandboxBroker,
        *,
        clock: Clock,
        ids: IdSource,
        config: RunnerConfig | None = None,
        instance: str = "mcp-runner",
        fingerprint_key: bytes | None = None,
        sealer: CredentialSealer | None = None,
        placeholder: Callable[[], str] = new_placeholder,
        retain: Retain | None = None,
        pool: str | None = None,
    ) -> None:
        """``sealer``: seals a server's secrets for the egress proxy; without it a server with a secret is refused
        (``runner_unavailable``, cause ``credential_sealing_unavailable``) rather than handed the secret.

        ``pool``: a name this process keeps across restarts (its host and service), put on each runner sandbox so
        the next process of the same name stops the runners a restart left behind (``reap_previous``)."""
        super().__init__(clock=clock, ids=ids, config=config, fingerprint_key=fingerprint_key, retain=retain)
        self._broker = broker
        self._instance = instance
        self._pool = pool
        self._sealer = sealer
        self._placeholder = placeholder

    @override
    async def _start(self, runner_id: str, request: RunnerRequest) -> _SandboxRunner:
        lifetime = str(int(self.config.max_age_s + self.config.reap_grace_s))
        env, routes = self._stand_ins(request)
        spec = runner_sandbox_spec(
            request.spec,
            request,
            modes=self._broker.modes(),
            runner_id=runner_id,
            labels={**self.config.labels, LIFETIME_LABEL: lifetime, **self._pool_labels()},
            egress=runner_egress_policy(request.spec, routes),
        )
        try:
            handle = await self._broker.acquire(
                spec, lease=LeaseRef(self._instance, 1), idempotency_key=f"mcp-runner:{runner_id}"
            )
        except IsolationUnavailable as exc:
            raise RunnerIsolationUnavailable(str(exc)) from exc
        except SandboxRefusal as exc:
            raise RunnerUnavailable(f"the broker refused the runner: {exc.reason}", cause=exc.reason) from exc
        except (SandboxError, OSError) as exc:
            raise RunnerUnavailable(f"the broker failed: {type(exc).__name__}", cause=type(exc).__name__) from exc
        return await self._boot(handle, request, {**env, **(trust_env() if routes else {})})

    def _stand_ins(self, request: RunnerRequest) -> tuple[dict[str, str], tuple[TokenRoute, ...]]:
        secrets = request.secrets.values()
        if not any(secrets.values()):
            return dict(secrets), ()
        if self._sealer is None:
            raise RunnerUnavailable(
                "the server's secret cannot be handed to the egress proxy here (no deployment key)",
                cause="credential_sealing_unavailable",
            )
        return secret_routes(request.spec, secrets, self._sealer, self._placeholder)

    async def _boot(self, handle: SandboxHandle, request: RunnerRequest, env: Mapping[str, str]) -> _SandboxRunner:
        secrets = request.secrets.values()
        runner: _SandboxRunner | None = None
        try:
            await handle.seed_tar(BRIDGE_DIR, tar_stream({"bridge.py": bridge_source()}))
            process = await handle.exec(
                ["python3", BRIDGE_PATH, str(BRIDGE_PORT), "--", request.spec.command, *request.spec.args],
                env={**runner_base_env(request.spec), **env},
            )
            runner = _SandboxRunner(self._broker, handle, process)
            await runner.ready(self.config.ready_timeout_s)
        except BaseException as exc:
            tail = runner.stderr_tail(secrets) if runner is not None else ""
            if runner is not None:
                await runner.stop()
            else:
                with contextlib.suppress(Exception):
                    await self._broker.release(handle)
            if isinstance(exc, MCPRunnerRefused):
                raise RunnerStartFailed(f"{exc}{f' | {tail}' if tail else ''}") from exc
            if isinstance(exc, Exception):
                raise RunnerStartFailed(f"runner did not start: {type(exc).__name__}") from exc
            raise
        return runner

    def _pool_labels(self) -> dict[str, str]:
        return {POOL_LABEL: self._pool} if self._pool else {}

    async def reap_previous(self) -> int:
        """Stop the runner sandboxes an earlier process of this pool left (R36COVER-FU-4: a restart orphaned them
        until their lifetime, 7 h, holding the broker's ceiling). Call before this pool starts any runner."""
        if not self._pool:
            return 0
        try:
            reaped = await self._broker.reap(labels={**self.config.labels, RUNNER_LABEL: "1", POOL_LABEL: self._pool},
                                             older_than_s=0)
        except (SandboxError, OSError) as exc:
            self._log.warning("mcp_runner.reap_failed", errorType=type(exc).__name__)
            return 0
        if reaped:
            self._log.info("mcp_runner.reaped_previous", count=reaped, pool=self._pool)
        return reaped

    @override
    async def sweep(self) -> int:
        stopped = await super().sweep()
        try:
            orphans = await self._broker.reap(
                labels={**self.config.labels, RUNNER_LABEL: "1"},
                older_than_s=int(self.config.max_age_s + self.config.reap_grace_s),
            )
        except (SandboxError, OSError) as exc:
            self._log.warning("mcp_runner.reap_failed", errorType=type(exc).__name__)
            return stopped
        if orphans:
            self._log.info("mcp_runner.reaped", count=orphans)
        return stopped + orphans


__all__ = ["SandboxMCPRunner", "bridge_source", "runner_base_env", "runner_sandbox_spec"]
