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


def sanitized_subprocess_env(working_dir: str) -> dict[str, str]:
    """Allowlist-only environment for every subprocess this sandbox spawns
    (installs AND execution) — host secrets (API keys, credentials, cloud
    tokens) are never inherited. `HOME`/`TMPDIR` are pointed at the sandbox
    dir itself so tools that write config/cache there (npm, pip) stay
    within the confined write scope instead of touching the real home dir.
    """
    host_path = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")
    env = {
        "PATH": host_path,
        "HOME": working_dir,
        "TMPDIR": working_dir,
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
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
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            raise EnvironmentSetupError(
                f"failed to create python venv: {stderr_bytes.decode(errors='replace')}"
            )
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

    async def _run_confined(self, cmd: list[str], *, allow_network: bool) -> ExecResult:
        confined_cmd = confine_command(cmd, self._working_dir, allow_network=allow_network)
        proc = await asyncio.create_subprocess_exec(
            *confined_cmd,
            cwd=self._working_dir,
            env=sanitized_subprocess_env(self._working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        return ExecResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )
