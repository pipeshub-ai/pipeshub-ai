"""``BrokerCodingSandbox``: ``run_code`` through the sandbox broker instead of the Docker socket (P5.1).

Everything ``DockerCodingSandbox`` does on the host side stays as it is
(working directory, staged inputs, artifact detection, package policy); only
where a run happens changes. Each run or install acquires a fresh broker
sandbox, copies the same archives in, executes the same command with the
same environment, copies ``/output`` and ``/src`` back, and releases it, so
the tool behaves as before while this process holds no socket.

The broker's root filesystem is read-only, so ``HOME`` points at the
sandbox's ``/tmp`` (caches that tools write under ``~``, such as npm's and
matplotlib's, land there as they landed in the writable root before).
"""

from __future__ import annotations

import asyncio
import codecs
import logging
import os
import signal
import socket
import tempfile
import uuid
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.coding.base import (
    OUT_OF_MEMORY_EXIT,
    CodeOutOfMemory,
    CodingSandboxError,
    memory_limit_end,
)
from app.agent_loop_lib.sandbox.coding.credentials import proxied_registry
from app.agent_loop_lib.sandbox.coding.docker import (
    DockerCodingSandbox,
    _tar_directory,
    _tar_files,
    extract_archive_chunks,
)
from app.harness.sandbox.egress_credentials import (
    CredentialSealingUnavailable,
    new_placeholder,
    required_sealer,
)
from harness_core.activity import current_command_recorder
from harness_core.sandbox.errors import SandboxError, SandboxPathNotFound
from harness_core.sandbox.ports import memory_exhausted
from harness_core.sandbox.streams import collect, iter_bytes, spool
from harness_core.sandbox.types import (
    EgressDefault,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from app.agent_loop_lib.sandbox.coding.base import CodingLanguage, SandboxContext
    from app.agent_loop_lib.sandbox.coding.credentials import ProxiedRegistry
    from harness_core.activity import OutputStream
    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle

logger = logging.getLogger(__name__)

__all__ = ["BrokerCodingSandbox", "extract_dir", "release_quietly", "run_command"]

INSTALL_TIMEOUT_S = 300.0
_HOME = "/tmp"
_SCRATCH_MB = 1024
_HOLDER = f"{socket.gethostname()}-{os.getpid()}"
_SPOOL_BYTES = 16 * 1024 * 1024
_SPOOL_CHUNK = 64 * 1024

class BrokerCodingSandbox(DockerCodingSandbox):
    backend_name = "broker"

    def __init__(
        self,
        *,
        broker: Callable[[], Awaitable[SandboxBroker]],
        runtime_mode: RuntimeMode = RuntimeMode.DOCKER_HARDENED,
        **docker_options: object,
    ) -> None:
        super().__init__(**docker_options)  # type: ignore[arg-type]
        self._broker_source = broker
        self._runtime_mode = runtime_mode

    async def _broker(self) -> SandboxBroker:
        try:
            return await self._broker_source()
        except SandboxError as exc:
            raise CodingSandboxError(f"sandbox broker unavailable: {exc}") from exc

    async def _prepare_container(self, *, network: bool) -> str | None:
        # The broker owns images and networks; nothing to resolve on this side.
        return None

    def _spec(self, mounts: Sequence[str], *, network: bool, egress: EgressPolicy | None = None) -> SandboxSpec:
        return SandboxSpec(
            image_digest=self._image,
            mode=self._runtime_mode,
            resources=Resources(
                cpus=self._cpu_limit,
                memory_mb=self._memory_limit_mb,
                pids=self._pids_limit,
                scratch_mb=_SCRATCH_MB,
            ),
            egress=egress or EgressPolicy(default=EgressDefault.ALLOW if network else EgressDefault.DENY),
            mounts=tuple(MountSpec(target) for target in mounts),
            labels=self._labels_for_broker(),
        )

    def _labels_for_broker(self) -> dict[str, str]:
        ctx: SandboxContext | None = self._context
        ids = (
            (
                ("orgId", ctx.org_id),
                ("conversationId", ctx.conversation_id),
                ("runId", ctx.run_id),
                ("attemptId", ctx.attempt_id),
            )
            if ctx is not None
            else ()
        )
        return {"codingSandboxId": self.sandbox_id, **{key: value for key, value in ids if value}}

    async def _acquire(
        self, mounts: Sequence[str], *, network: bool, egress: EgressPolicy | None = None
    ) -> tuple[SandboxBroker, SandboxHandle]:
        broker = await self._broker()
        try:
            handle = await broker.acquire(
                self._spec(mounts, network=network, egress=egress),
                lease=LeaseRef(_HOLDER, 1),
                idempotency_key=f"run_code:{self.sandbox_id}:{uuid.uuid4().hex}",
            )
        except SandboxError as exc:
            raise CodingSandboxError(f"sandbox broker refused the run: {exc}") from exc
        return broker, handle

    async def _run_in_container(
        self,
        command: list[str],
        src_dir: str,
        timeout: float,
        network_enabled: bool,
        staged_inputs: dict[str, bytes],
        egress_network: str | None,
    ) -> tuple[int, str, str]:
        has_py_deps, has_node_deps = self._has_deps()
        mounts = ["/src", "/output"]
        if has_py_deps:
            mounts.append("/deps")
        if has_node_deps:
            mounts.append("/node_modules")
        broker, handle = await self._acquire(mounts, network=network_enabled)
        try:
            if staged_inputs:
                await handle.put_tar("/src", iter_bytes(_tar_files(staged_inputs)))
            await handle.put_tar("/src", iter_bytes(_tar_directory(src_dir)))
            if os.path.isdir(self._output_dir) and os.listdir(self._output_dir):
                await handle.put_tar("/output", iter_bytes(_tar_directory(self._output_dir)))
            if has_py_deps:
                await handle.put_tar("/deps", iter_bytes(_tar_directory(self._deps_python_dir)))
            if has_node_deps:
                await handle.put_tar("/node_modules", iter_bytes(_tar_directory(self._deps_node_dir)))
            env = {**self._run_env(has_py_deps=has_py_deps, has_node_deps=has_node_deps), "HOME": _HOME}
            try:
                exit_code, stdout, stderr = await run_command(handle, command, env, timeout)
            except SandboxError:
                # Under gVisor the memory limit ends the whole sandbox, which reads as the sandbox gone.
                if await memory_exhausted(handle):
                    raise CodeOutOfMemory(self._memory_limit_mb) from None
                raise
            # Under runc the kernel killed the code's process; the sandbox stands, so its files are kept below.
            out_of_memory = memory_limit_end(
                exit_code,
                oom_killed=exit_code == OUT_OF_MEMORY_EXIT and await memory_exhausted(handle),
                memory_mb=self._memory_limit_mb, stdout=stdout, stderr=stderr,
            )
            os.makedirs(self._output_dir, exist_ok=True)
            await extract_dir(handle, "/output", self._output_dir)
            await extract_dir(handle, "/src", src_dir)
            logger.info(
                "BrokerCodingSandbox.run: sandbox=%s broker_sandbox=%s exit_code=%d stdout_len=%d stderr_len=%d",
                self.sandbox_id, handle.id, exit_code, len(stdout), len(stderr),
            )
            if out_of_memory is not None:
                raise out_of_memory
            return exit_code, stdout, stderr
        finally:
            await release_quietly(broker, handle)

    async def _install_in_container(
        self, to_install: list[str], language: CodingLanguage, network_name: str | None,
    ) -> tuple[bool, str, str]:
        proxied = self._proxied(language)
        command, extract_path, host_target = self._install_command(to_install, language, proxied)
        mount = "/deps" if language == "python" else "/install"
        env = {**(proxied.env if proxied is not None else {}), "HOME": _HOME}
        broker, handle = await self._acquire(
            [mount], network=True, egress=proxied.egress if proxied is not None else None
        )
        try:
            exit_code, stdout, stderr = await run_command(handle, command, env, INSTALL_TIMEOUT_S)
            if exit_code != 0:
                logger.warning(
                    "BrokerCodingSandbox.install failed: sandbox=%s packages=%s exit_code=%d",
                    self.sandbox_id, to_install, exit_code,
                )
                return False, stdout, stderr
            os.makedirs(host_target, exist_ok=True)
            await extract_dir(handle, extract_path, host_target)
            return True, stdout, stderr
        finally:
            await release_quietly(broker, handle)


    def _proxied(self, language: CodingLanguage) -> ProxiedRegistry | None:
        """A credentialed registry, reached through the egress proxy that adds its credential (OWD-1)."""
        found = self._registry(language)
        if found.credential is None:
            return None
        try:
            return proxied_registry(found, required_sealer(), new_placeholder())
        except CredentialSealingUnavailable as exc:
            raise CodingSandboxError(f"package install refused: {exc}") from exc


async def _shown(stream: AsyncIterator[bytes], name: OutputStream) -> AsyncIterator[bytes]:
    """The stream, each chunk also handed to the durable run's command recorder (UX5) as it is read."""
    recorder = current_command_recorder()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    async for chunk in stream:
        if recorder is not None:
            recorder.write(name, decoder.decode(chunk))
        yield chunk
    if recorder is not None:
        recorder.write(name, decoder.decode(b"", final=True))


async def run_command(handle: SandboxHandle, command: list[str], env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    process = await handle.exec(command, env=env)
    output = asyncio.gather(collect(_shown(process.stdout, "stdout")), collect(_shown(process.stderr, "stderr")))
    timed_out = False
    try:
        exit_code = await process.wait(timeout_s=timeout)
    except TimeoutError:
        timed_out = True
        await process.signal(signal.SIGKILL)
        await process.wait(timeout_s=30)
        exit_code = -1
    out, err = await output
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    if timed_out:
        stderr = f"Execution timed out after {timeout}s\n{stderr}".strip()
    return exit_code, stdout, stderr


async def extract_dir(handle: SandboxHandle, path: str, local_dir: str) -> None:
    try:
        chunks = await handle.get_tar(path)
    except SandboxPathNotFound:
        logger.debug("No output artifacts to extract from sandbox path %s", path)
        return
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES) as spooled:
        _ = await spool(chunks, spooled, in_memory_bytes=_SPOOL_BYTES)
        await asyncio.to_thread(extract_archive_chunks, iter(lambda: spooled.read(_SPOOL_CHUNK), b""), path, local_dir)


async def release_quietly(broker: SandboxBroker, handle: SandboxHandle) -> None:
    try:
        await broker.release(handle)
    except SandboxError as exc:
        # The run's result stands; the broker's reaper removes what release could not.
        logger.warning("BrokerCodingSandbox: release failed for %s: %s", handle.id, type(exc).__name__)
