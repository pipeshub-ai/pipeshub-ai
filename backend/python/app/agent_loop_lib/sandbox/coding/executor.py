from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass

from app.agent_loop_lib.sandbox.coding.base import CodeRequest, CodeResult
from app.agent_loop_lib.sandbox.coding.environment import (
    EnvironmentManager,
    sanitized_subprocess_env,
)
from app.agent_loop_lib.sandbox.confinement import confine_command

"""`CodeExecutor`: runs one `CodeRequest` as a confined subprocess.

Layered safety (see the design plan's "Safety Practices Summary" — this is
layers 1 and 2 of it):
    1. Kernel confinement (`confine_command`) — file writes scoped to the
       sandbox dir, network denied unless `request.allow_network`.
    2. Process resource limits via `setrlimit` — confinement does NOT cap
       CPU/memory/process count, so a `while(true){}` or fork bomb would
       otherwise pass every other guard except the wall-clock timeout.

`resource`/`setrlimit`/process-group signaling are POSIX-only; on Windows
this degrades to timeout-based killing without rlimits (documented
limitation, matching this repo's existing degrade-gracefully convention
for `sandbox/confinement.py`).
"""

__all__ = ["ExecutionLimits", "CodeExecutor"]

# `.npm` (HOME points into the sandbox dir, so npm's cache/config lands
# here), `node-compile-cache` (Node's V8 compile cache, rewritten on nearly
# every `tsx`/`node` invocation with different code), and `tsx-<uid>`
# (tsx's transient IPC socket directory, TMPDIR-scoped — see
# `sanitized_subprocess_env`) are bootstrap/runtime noise, not
# agent-authored artifacts.
_ARTIFACT_IGNORED_DIRS = {"node_modules", ".venv", "__pycache__", ".git", ".npm", "node-compile-cache"}
_ARTIFACT_IGNORED_DIR_PREFIXES = ("tsx-",)

# Modern, non-deprecated compiler flags for the `tsc --noEmit` type-check
# pre-pass — tsx itself only strips types (no checking), so without this
# pass no TYPE-category error could ever be produced. `bundler` resolution
# (not `node`/`node10`, deprecated as of TS 5.x and a hard error on newer
# compilers) mirrors what tsx itself documents as the recommended tsconfig.
_TSC_CHECK_FLAGS = [
    "--noEmit",
    "--target", "ES2022",
    "--module", "ESNext",
    "--moduleResolution", "Bundler",
    "--esModuleInterop",
    "--skipLibCheck",
    "--allowJs",
]


@dataclass(frozen=True)
class ExecutionLimits:
    """Config-driven `setrlimit` values applied to every executed process
    (and its children, via inheritance across exec).

    `max_processes` (RLIMIT_NPROC) caveat: on macOS/BSD this limits the
    total number of processes owned by the REAL UID **system-wide**, not
    just this subprocess tree — it is not a precise per-sandbox budget.
    Sizing it too close to what a normal desktop session already uses
    (easily several hundred processes) makes ordinary tool invocations
    fail with a confusing `EAGAIN` on `spawn()`/`fork()` that has nothing
    to do with the sandboxed code itself. The default here is deliberately
    generous — high enough to never collide with normal usage, still low
    enough to bound a genuine fork bomb well below the OS default (macOS
    ships `ulimit -u` around 4000).
    """

    max_memory_bytes: int = 1536 * 1024 * 1024
    max_cpu_seconds: int = 30
    max_file_size_bytes: int = 50 * 1024 * 1024
    max_processes: int = 2048


def _snapshot_mtimes(root: str) -> dict[str, float]:
    """Cheap before/after diff basis for artifact detection — skips
    dependency directories so a fresh `npm install`/venv creation doesn't
    get misreported as thousands of "artifacts"."""
    snapshot: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _ARTIFACT_IGNORED_DIRS and not d.startswith(_ARTIFACT_IGNORED_DIR_PREFIXES)
        ]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                snapshot[os.path.relpath(full, root)] = os.path.getmtime(full)
            except OSError:
                continue
    return snapshot


def _rlimit_preexec_fn(limits: ExecutionLimits):
    """Built (and only ever called) on POSIX — imports `resource` lazily so
    this module still imports cleanly on Windows."""
    import resource

    def _preexec() -> None:
        for res, value in (
            (resource.RLIMIT_AS, limits.max_memory_bytes),
            (resource.RLIMIT_CPU, limits.max_cpu_seconds),
            (resource.RLIMIT_FSIZE, limits.max_file_size_bytes),
            (resource.RLIMIT_NPROC, limits.max_processes),
        ):
            try:
                resource.setrlimit(res, (value, value))
            except (ValueError, OSError):
                continue  # best-effort — some rlimits are unavailable in containers/CI

    return _preexec


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole process group (not just `proc` itself) so a
    fork-bombing or backgrounding script can't outlive the timeout."""
    try:
        if sys.platform != "win32":
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass


class CodeExecutor:
    """Writes `request.code` to the sandbox dir and runs it with the
    language-appropriate confined runtime. Returns a `CodeResult` with
    `error_analysis` left `None` — populating it is `ReflectionEngine`'s
    job, composed in by `LocalCodingSandbox`."""

    def __init__(
        self,
        working_dir: str,
        environment: EnvironmentManager,
        *,
        typecheck_typescript: bool = True,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._working_dir = working_dir
        self._env = environment
        self._typecheck_typescript = typecheck_typescript
        self._limits = limits or ExecutionLimits()

    async def execute(self, request: CodeRequest) -> CodeResult:
        start = time.monotonic()

        # Bootstrap (npm init/install of tsx+typescript, or venv creation)
        # happens BEFORE the artifact snapshot — it's one-time environment
        # setup, not something the agent's code produced this run.
        if request.language == "typescript":
            await self._env.ensure_typescript_runtime()
        else:
            await self._env.ensure_python_venv()

        entry_name = request.entry_file or ("main.ts" if request.language == "typescript" else "main.py")

        before = _snapshot_mtimes(self._working_dir)
        if request.language == "typescript":
            stdout, stderr, exit_code = await self._run_typescript(request)
        else:
            stdout, stderr, exit_code = await self._run_python(request)

        duration_ms = (time.monotonic() - start) * 1000
        after = _snapshot_mtimes(self._working_dir)
        # The entry file is (re)written to disk between the two snapshots on
        # every run (see _run_python/_run_typescript below), so it always
        # looks "new" by mtime — exclude it or every single code execution
        # would misreport its own script as a downloadable artifact.
        artifacts = sorted(
            p for p, mtime in after.items() if before.get(p) != mtime and p != entry_name
        )

        return CodeResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            language=request.language,
            duration_ms=duration_ms,
            artifacts=artifacts,
        )

    async def _run_typescript(self, request: CodeRequest) -> tuple[str, str, int]:
        entry = request.entry_file or "main.ts"
        entry_path = os.path.join(self._working_dir, entry)
        with open(entry_path, "w", encoding="utf-8") as f:
            f.write(request.code)

        if self._typecheck_typescript:
            tsc_stdout, tsc_stderr, tsc_exit = await self._run(
                [self._env.tsc_binary, *_TSC_CHECK_FLAGS, entry],
                request.timeout,
                allow_network=False,
            )
            if tsc_exit != 0:
                return tsc_stdout, tsc_stderr, tsc_exit

        return await self._run([self._env.tsx_binary, entry], request.timeout, allow_network=request.allow_network)

    async def _run_python(self, request: CodeRequest) -> tuple[str, str, int]:
        entry = request.entry_file or "main.py"
        entry_path = os.path.join(self._working_dir, entry)
        with open(entry_path, "w", encoding="utf-8") as f:
            f.write(request.code)
        return await self._run([self._env.python_bin, entry], request.timeout, allow_network=request.allow_network)

    async def _run(self, cmd: list[str], timeout: float, *, allow_network: bool) -> tuple[str, str, int]:
        confined_cmd = confine_command(cmd, self._working_dir, allow_network=allow_network)
        is_posix = sys.platform != "win32"
        proc = await asyncio.create_subprocess_exec(
            *confined_cmd,
            cwd=self._working_dir,
            env=sanitized_subprocess_env(self._working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_rlimit_preexec_fn(self._limits) if is_posix else None,
            start_new_session=is_posix,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill_process_tree(proc)
            return "", f"Timed out after {timeout}s", -1
        return (
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )
