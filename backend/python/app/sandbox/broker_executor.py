"""BrokerExecutor -- the legacy executor stack (``coding_sandbox``, ``database_sandbox``) on the sandbox broker.

``SANDBOX_MODE=broker`` takes the Docker socket away from this process, so
``DockerExecutor`` cannot run; without this executor both toolsets were
skipped. The run is ``DockerExecutor``'s: the same command
(``write_run_command``), the same offline run with dependencies installed
first in a separate sandbox with open egress (which the broker firewalls
against private, link-local/metadata and bridge addresses, as main's
docker backend does), the same environment allowlist, artifacts copied back
from ``/output``. Only where the containers come from changes.

Credentials never enter these sandboxes (OWD-1): an install from a registry
configured with a password, and a PostgreSQL run, get an egress allowlist
instead, and the egress proxy adds the password on the way out
(``coding.credentials``). That is also what lets a PostgreSQL run reach its
database at all: the run is otherwise offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import time
import uuid
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.coding.broker import (
    extract_dir,
    release_quietly,
    run_command,
)
from app.agent_loop_lib.sandbox.coding.credentials import (
    database,
    proxied_database,
    proxied_registry,
)
from app.agent_loop_lib.sandbox.coding.docker import _tar_directory
from app.harness.sandbox.egress_credentials import new_placeholder, required_sealer
from app.sandbox.base_executor import BaseExecutor, build_sandbox_env
from app.sandbox.docker_executor import (
    install_command,
    registry_for,
    write_run_command,
)
from app.sandbox.models import (
    DEFAULT_CPU_LIMIT,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIMEOUT_SECONDS,
    SANDBOX_IMAGE,
    ExecutionResult,
    SandboxLanguage,
    validate_packages,
)
from app.sandbox.roots import BROKER, ensure_private_dir, sandbox_root
from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.streams import iter_bytes
from harness_core.sandbox.types import (
    LIFETIME_LABEL,
    EgressDefault,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle

logger = logging.getLogger(__name__)

__all__ = ["BrokerExecutor"]

INSTALL_TIMEOUT_S = 300.0
# Past its command's timeout, for copying files in and out: then the broker's lifetime sweep removes a sandbox whose
# query service died before releasing it.
LIFETIME_GRACE_S = 10 * 60
_PIDS = 128
_SCRATCH_MB = 1024
_HOME = "/tmp"
_HOLDER = f"{socket.gethostname()}-{os.getpid()}"


class BrokerExecutor(BaseExecutor):
    """Execute code in broker sandboxes: one per run, one more per dependency install."""

    def __init__(
        self,
        *,
        broker: Callable[[], Awaitable[SandboxBroker]],
        image: str = SANDBOX_IMAGE,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        cpu_limit: float = DEFAULT_CPU_LIMIT,
        runtime_mode: RuntimeMode = RuntimeMode.DOCKER_HARDENED,
        work_root: str | None = None,
    ) -> None:
        self._broker_source = broker
        self._image = image
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit = cpu_limit
        self._runtime_mode = runtime_mode
        self._root = work_root

    @property
    def work_root(self) -> str:
        """The folder runs are copied in and out of: the one named at construction, else the running user's own
        (R13-ROOT), read at each use as the artifact reader reads it."""
        return self._root if self._root is not None else sandbox_root(BROKER)

    async def execute(
        self,
        code: str,
        language: str,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        packages: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        # The work folder's I/O runs in threads: a dependency tree is large enough to stall the event loop.
        root = await asyncio.to_thread(ensure_private_dir, self.work_root)
        work_dir = os.path.join(root, uuid.uuid4().hex)
        src_dir, output_dir, deps_dir = (os.path.join(work_dir, d) for d in ("src", "output", "deps"))
        await asyncio.to_thread(_make_dirs, src_dir, output_dir)
        start_ms = _now_ms()
        try:
            lang = SandboxLanguage(language)
            safe_packages = validate_packages(packages, language=lang)
            deps_target: str | None = None
            if safe_packages:
                deps_target = await self._install(safe_packages, lang, deps_dir)
            egress: EgressPolicy | None = None
            if lang == SandboxLanguage.POSTGRESQL and (env or {}).get("DATABASE_URL"):
                # The database is reached through the egress proxy, which signs in; psql never holds the password.
                proxied = proxied_database(database(env["DATABASE_URL"]), required_sealer(), new_placeholder())
                env, egress = {**env, "DATABASE_URL": proxied.url}, proxied.egress
            command = await asyncio.to_thread(write_run_command, code, lang, src_dir)
            run_env: dict[str, str] = {**(env or {}), "OUTPUT_DIR": "/output"}
            if deps_target == "/deps":
                run_env["PYTHONPATH"] = "/deps"
            elif deps_target == "/node_modules":
                run_env["NODE_PATH"] = "/node_modules"
            # The allowlist reads this process's environment; HOME must be the sandbox's writable /tmp.
            container_env = {**build_sandbox_env(run_env), "HOME": _HOME}
            mounts = ["/src", "/output", *([deps_target] if deps_target else [])]
            broker, handle = await self._acquire(
                mounts, network=False, purpose="run", timeout_s=float(timeout_seconds), egress=egress
            )
            try:
                await handle.put_tar("/src", iter_bytes(await asyncio.to_thread(_tar_directory, src_dir)))
                if deps_target:
                    await handle.put_tar(deps_target, iter_bytes(await asyncio.to_thread(_tar_directory, deps_dir)))
                exit_code, stdout, stderr = await run_command(handle, command, container_env, float(timeout_seconds))
                await extract_dir(handle, "/output", output_dir)
            finally:
                await release_quietly(broker, handle)
            if exit_code == -1 and stderr.startswith(f"Execution timed out after {float(timeout_seconds)}s"):
                return ExecutionResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=-1,
                    error=f"Execution timed out after {timeout_seconds}s",
                    execution_time_ms=_now_ms() - start_ms,
                )
            return ExecutionResult(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                artifacts=await asyncio.to_thread(self.collect_artifacts, output_dir),
                execution_time_ms=_now_ms() - start_ms,
            )
        except Exception as exc:
            logger.warning("BrokerExecutor.execute failed for %s: %s", language, type(exc).__name__)
            return ExecutionResult(success=False, error=str(exc), execution_time_ms=_now_ms() - start_ms)
        finally:
            await asyncio.to_thread(_remove_dirs, os.path.join(work_dir, "src"), deps_dir)

    async def _install(self, packages: list[str], language: SandboxLanguage, deps_dir: str) -> str:
        found = registry_for(language)
        # A registry's password is added by the egress proxy on the way out; the install never holds it (OWD-1).
        proxied = (
            proxied_registry(found, required_sealer(), new_placeholder())
            if found is not None and found.credential is not None
            else None
        )
        command, extract_path, mount_point = install_command(packages, language, proxied)
        mount = "/deps" if language == SandboxLanguage.PYTHON else "/install"
        broker, handle = await self._acquire(
            [mount],
            network=True,
            purpose="install",
            timeout_s=INSTALL_TIMEOUT_S,
            egress=proxied.egress if proxied is not None else None,
        )
        env = {**(proxied.env if proxied is not None else {}), "HOME": _HOME}
        try:
            exit_code, stdout, stderr = await run_command(handle, command, env, INSTALL_TIMEOUT_S)
            if exit_code != 0:
                raise RuntimeError(
                    f"Package install failed (exit {exit_code}). stderr: {stderr[:500]} stdout: {stdout[:500]}"
                )
            await asyncio.to_thread(_make_dirs, deps_dir)
            await extract_dir(handle, extract_path, deps_dir)
        finally:
            await release_quietly(broker, handle)
        return mount_point

    async def _acquire(
        self,
        mounts: Sequence[str],
        *,
        network: bool,
        purpose: str,
        timeout_s: float,
        egress: EgressPolicy | None = None,
    ) -> tuple[SandboxBroker, SandboxHandle]:
        try:
            broker = await self._broker_source()
        except SandboxError as exc:
            raise RuntimeError(f"sandbox broker unavailable: {exc}") from exc
        spec = SandboxSpec(
            image_digest=self._image,
            mode=self._runtime_mode,
            resources=Resources(cpus=self._cpu_limit, memory_mb=self._memory_limit_mb, pids=_PIDS, scratch_mb=_SCRATCH_MB),
            egress=egress or EgressPolicy(default=EgressDefault.ALLOW if network else EgressDefault.DENY),
            mounts=tuple(MountSpec(target) for target in mounts),
            labels={
                "legacyExecutor": purpose,
                "legacyExecutorHolder": _HOLDER,
                LIFETIME_LABEL: str(int(timeout_s) + LIFETIME_GRACE_S),
            },
        )
        try:
            handle = await broker.acquire(
                spec, lease=LeaseRef(_HOLDER, 1), idempotency_key=f"legacy-executor:{uuid.uuid4().hex}"
            )
        except SandboxError as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            raise RuntimeError(f"sandbox broker refused the {purpose} ({reason}): {exc}") from exc
        return broker, handle


def _make_dirs(*paths: str) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)


def _remove_dirs(*paths: str) -> None:
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def _now_ms() -> int:
    return int(time.time() * 1000)
