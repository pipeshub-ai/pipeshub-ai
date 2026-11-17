from __future__ import annotations

import asyncio
import os
import sys

from app.agent_loop_lib.sandbox.base import ExecResult
from app.agent_loop_lib.sandbox.coding.base import (
    CodingLanguage,
    EnvironmentSetupError,
    InstallResult,
)
from app.agent_loop_lib.sandbox.coding.output_limits import read_stdio_capped
from app.agent_loop_lib.sandbox.coding.validation import (
    canonical_package_key,
    matches_package_set,
    package_name,
    validate_package_spec,
)
from app.agent_loop_lib.sandbox.confinement import confine_command

"""`EnvironmentManager`: owns npm/venv setup and package installation for a
single coding sandbox's working directory.

Key safety decisions (see the design plan's "Safety Practices Summary"):
    - TypeScript support (`tsx`, `typescript`) is installed ONCE during
      `ensure_typescript_runtime()`, never lazily via `npx` at execution
      time — execution runs with network denied (see `CodeExecutor`), so
      `npx` would try to download and fail.
    - `npm install --ignore-scripts` — no postinstall script execution,
      the canonical npm supply-chain attack vector.
    - `pip install --no-cache-dir` — no cross-sandbox cache poisoning.
    - Installs run under the same kernel confinement as execution (file
      writes scoped to the sandbox dir); only the network policy differs
      (installs may allow network, execution never does by default).
    - Package specs are validated (see `sandbox/coding/validation.py`)
      against a strict allow-charset regex before ever reaching a
      subprocess argv — rejects shell-metacharacter smuggling,
      `git+`/`file:`/URL specs, and leading `-flag` injection.
"""

__all__ = ["EnvironmentManager"]

# The same limit Docker installs get (`docker.py` `_INSTALL_TIMEOUT_S`): a
# slow mirror or a build that hangs must not hold a sandbox slot forever.
_INSTALL_TIMEOUT_S = 300
_INSTALL_TIMEOUT_NOTE = "[sandbox] Package installation timed out after {seconds}s.\n"


def sanitized_subprocess_env(working_dir: str) -> dict[str, str]:
    """Allowlist-only environment for every subprocess this sandbox spawns
    (installs AND execution) — host secrets (API keys, credentials, cloud
    tokens) are never inherited. `HOME`/`TMPDIR` are pointed at the sandbox
    dir itself so tools that write config/cache there (npm, pip) stay
    within the confined write scope instead of touching the real home dir.

    `OUTPUT_DIR` gives the local backend the same explicit deliverable
    directory the Docker backend already exports (`docker.py`'s
    `env = {"OUTPUT_DIR": "/output"}`) — parity that lets callers treat
    "write it to $OUTPUT_DIR" as one contract that holds on either backend.
    """
    host_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")
    env = {
        "PATH": host_path,
        "HOME": working_dir,
        "TMPDIR": working_dir,
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "OUTPUT_DIR": os.path.join(working_dir, "output"),
    }
    if sys.platform == "win32":
        # Node/npm on Windows resolve several things (APPDATA, SystemRoot,
        # ComSpec) through the environment; without these npm can fail
        # outright rather than just losing niceties.
        for key in ("SystemRoot", "ComSpec", "APPDATA", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
        env["TEMP"] = working_dir
        env["TMP"] = working_dir
    return env


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole process group (not just `proc` itself) so a
    fork-bombing or backgrounding script can't outlive the timeout.

    Every caller starts *proc* with ``start_new_session=True``, so its group
    id is its pid. Killing that group directly also works once *proc* itself
    has exited and been reaped while a child it started still holds the
    output pipes; ``getpgid`` would fail for the reaped pid and skip the
    kill."""
    try:
        if sys.platform != "win32":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass


async def _communicate_or_kill(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
    """`proc.communicate()`, killing the process group if the caller is
    cancelled. Without it a stopped pip or npm install keeps running after
    the call that started it has given its slot back."""
    try:
        return await proc.communicate()
    except asyncio.CancelledError:
        await asyncio.shield(_kill_process_tree(proc))
        raise


class EnvironmentManager:
    """Manages the Node and Python environments living inside one sandbox
    working directory. A sandbox can host both lazily — `ensure_typescript_runtime()`/
    `ensure_python_venv()` only do work the first time they're called."""

    def __init__(
        self,
        working_dir: str,
        *,
        allow_network_on_install: bool = True,
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
    ) -> None:
        self._working_dir = working_dir
        self._allow_network_on_install = allow_network_on_install
        self._allowlist = set(package_allowlist) if package_allowlist else None
        self._denylist = set(package_denylist or [])
        self._installed: dict[str, set[str]] = {"typescript": set(), "python": set()}
        self._node_initialized = False
        self._venv_initialized = False

    @property
    def installed_packages(self) -> dict[str, set[str]]:
        return {lang: set(names) for lang, names in self._installed.items()}

    @property
    def python_venv_path(self) -> str:
        return os.path.join(self._working_dir, ".venv")

    @property
    def python_bin(self) -> str:
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        exe = "python.exe" if sys.platform == "win32" else "python"
        return os.path.join(self.python_venv_path, bin_dir, exe)

    @property
    def pip_bin(self) -> str:
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        exe = "pip.exe" if sys.platform == "win32" else "pip"
        return os.path.join(self.python_venv_path, bin_dir, exe)

    @property
    def node_bin_dir(self) -> str:
        return os.path.join(self._working_dir, "node_modules", ".bin")

    @property
    def tsx_binary(self) -> str:
        return os.path.join(self.node_bin_dir, "tsx.cmd" if sys.platform == "win32" else "tsx")

    @property
    def tsc_binary(self) -> str:
        return os.path.join(self.node_bin_dir, "tsc.cmd" if sys.platform == "win32" else "tsc")

    async def ensure_typescript_runtime(self) -> None:
        """Idempotently `npm init -y` then install `tsx` + `typescript` as
        the runtime this sandbox will execute TypeScript with."""
        if self._node_initialized:
            return
        os.makedirs(self._working_dir, exist_ok=True)
        init = await self._run_confined(["npm", "init", "-y"], allow_network=False)
        if init.exit_code != 0:
            raise EnvironmentSetupError(f"npm init failed: {init.stderr}")
        result = await self.install_packages(["tsx", "typescript"], "typescript", _is_runtime_setup=True)
        if not result.success:
            raise EnvironmentSetupError(
                f"failed to install TypeScript runtime (tsx/typescript): {result.stderr}"
            )
        self._node_initialized = True

    async def ensure_python_venv(self) -> None:
        """Idempotently create the sandbox's isolated virtualenv."""
        if self._venv_initialized:
            return
        os.makedirs(self._working_dir, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", self.python_venv_path,
            cwd=self._working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=sys.platform != "win32",
        )
        stdout_bytes, stderr_bytes = await _communicate_or_kill(proc)
        if proc.returncode != 0:
            # Debian's venv explains a missing ensurepip (python3-venv) on stdout, leaving stderr empty
            detail = (stderr_bytes + stdout_bytes).decode(errors="replace").strip()
            raise EnvironmentSetupError(f"failed to create python venv: {detail}")
        self._venv_initialized = True

    async def install_packages(
        self,
        packages: list[str],
        language: CodingLanguage,
        *,
        _is_runtime_setup: bool = False,
    ) -> InstallResult:
        """Ensure `packages` are installed. Invalid/denylisted specs and
        non-zero installer exit codes are reported as
        `InstallResult(success=False, ...)` (soft-error, not raised) so the
        agent sees why an install failed and can adjust — only foundational
        setup failures (npm init, venv creation) raise `EnvironmentSetupError`.
        """
        if not packages:
            return InstallResult(success=True, installed=[])

        to_install: list[str] = []
        for spec in packages:
            name = package_name(spec, language)
            if not validate_package_spec(spec, language):
                return InstallResult(success=False, stderr=f"invalid or unsafe package spec: {spec!r}")
            if not _is_runtime_setup:
                if self._denylist and matches_package_set(name, self._denylist, language):
                    return InstallResult(success=False, stderr=f"package {name!r} is denylisted")
                if self._allowlist is not None and not matches_package_set(name, self._allowlist, language):
                    return InstallResult(success=False, stderr=f"package {name!r} is not in the configured allowlist")
            if canonical_package_key(name, language) not in self._installed[language]:
                to_install.append(spec)

        if not to_install:
            return InstallResult(success=True, installed=[])

        if language == "typescript":
            if not _is_runtime_setup:
                await self.ensure_typescript_runtime()
            cmd = ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", *to_install]
        else:
            await self.ensure_python_venv()
            cmd = [self.pip_bin, "install", "--no-cache-dir", *to_install]

        result = await self._run_confined(cmd, allow_network=self._allow_network_on_install)
        success = result.exit_code == 0
        if success:
            for spec in to_install:
                self._installed[language].add(canonical_package_key(package_name(spec, language), language))
        return InstallResult(
            success=success,
            installed=[package_name(s, language) for s in to_install] if success else [],
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def _run_confined(
        self, cmd: list[str], *, allow_network: bool, timeout: float | None = None,
    ) -> ExecResult:
        """Run an install step, killing its process group after `timeout`
        seconds (`_INSTALL_TIMEOUT_S` by default). Only the head and tail of
        its output are kept."""
        timeout = _INSTALL_TIMEOUT_S if timeout is None else timeout
        confined_cmd = confine_command(cmd, self._working_dir, allow_network=allow_network)
        proc = await asyncio.create_subprocess_exec(
            *confined_cmd,
            cwd=self._working_dir,
            env=sanitized_subprocess_env(self._working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=sys.platform != "win32",
        )
        try:
            stdout, stderr = await asyncio.wait_for(read_stdio_capped(proc), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill_process_tree(proc)
            return ExecResult(
                stdout="", stderr=_INSTALL_TIMEOUT_NOTE.format(seconds=f"{timeout:g}"), exit_code=-1,
            )
        except asyncio.CancelledError:
            await asyncio.shield(_kill_process_tree(proc))
            raise
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )
