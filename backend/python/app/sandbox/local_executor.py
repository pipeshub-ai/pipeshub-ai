"""LocalExecutor -- subprocess-based sandbox for developer mode.

Runs code on the host OS via ``asyncio.create_subprocess_exec``.
Each invocation gets its own temp directory for source files and artifacts.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import os
import shutil
import sys
import tempfile
import time
from uuid import uuid4

from app.sandbox.base_executor import BaseExecutor, build_sandbox_env
from app.sandbox.models import (
    DEFAULT_TIMEOUT_SECONDS,
    ExecutionResult,
    SandboxLanguage,
    validate_packages,
)
from app.sandbox.package_policy import canonicalize

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")

# Substrings that indicate ``pip install`` failed because the host can't reach
# PyPI (DNS / proxy / firewall issues). Matched against pip's stderr so we can
# surface an actionable error to the agent instead of ~5 lines of urllib3
# retry noise.
_PIP_OFFLINE_SIGNALS: tuple[str, ...] = (
    "getaddrinfo failed",
    "Failed to establish a new connection",
    "Temporary failure in name resolution",
    "Could not find a version that satisfies",
    "No matching distribution found",
    "ProxyError",
    "SSLError",
)


def _split_host_installed_python(packages: list[str]) -> tuple[list[str], list[str]]:
    """Split *packages* into ``(already_installed, needs_install)`` buckets.

    Uses :mod:`importlib.metadata` against the host interpreter's site-packages
    so we can skip ``pip install`` entirely when the requested distribution is
    already present (e.g. ``pandas``, ``numpy``, ``Pillow`` ship with pipeshub).
    This makes the sandbox usable on air-gapped hosts for the common case.
    """
    already: list[str] = []
    missing: list[str] = []
    for pkg in packages:
        name = canonicalize(pkg, SandboxLanguage.PYTHON)
        try:
            importlib.metadata.distribution(name)
            already.append(pkg)
        except importlib.metadata.PackageNotFoundError:
            missing.append(pkg)
    return already, missing


def _looks_offline(stderr: str) -> bool:
    """Return True if *stderr* from pip suggests the host can't reach PyPI."""
    return any(signal in stderr for signal in _PIP_OFFLINE_SIGNALS)


class LocalExecutor(BaseExecutor):
    """Execute code in a local subprocess (developer mode)."""

    def __init__(self) -> None:
        os.makedirs(_SANDBOX_ROOT, exist_ok=True)

    async def execute(
        self,
        code: str,
        language: str,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        packages: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        execution_id = str(uuid4())
        work_dir = os.path.join(_SANDBOX_ROOT, execution_id)
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        logger.info(
            "[LocalExecutor] execute START | id=%s language=%s timeout=%ds "
            "packages=%s code_length=%d work_dir=%s",
            execution_id, language, timeout_seconds,
            packages or [], len(code), work_dir,
        )
        start_ms = _now_ms()

        try:
            if language == SandboxLanguage.PYTHON:
                result = await self._run_python(code, work_dir, output_dir, timeout_seconds, packages, env)
            elif language == SandboxLanguage.TYPESCRIPT:
                result = await self._run_typescript(code, work_dir, output_dir, timeout_seconds, packages, env)
            elif language == SandboxLanguage.SQLITE:
                result = await self._run_sqlite(code, work_dir, output_dir, timeout_seconds, env)
            elif language == SandboxLanguage.POSTGRESQL:
                result = await self._run_postgresql(code, work_dir, output_dir, timeout_seconds, env)
            else:
                result = ExecutionResult(
                    success=False,
                    error=f"Unsupported language: {language}",
                    execution_time_ms=_now_ms() - start_ms,
                )
            logger.info(
                "[LocalExecutor] execute DONE | id=%s success=%s exit_code=%s "
                "time_ms=%s artifacts=%d",
                execution_id, result.success, result.exit_code,
                result.execution_time_ms, len(result.artifacts),
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("[LocalExecutor] execute TIMEOUT | id=%s after %ds", execution_id, timeout_seconds)
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout_seconds}s",
                exit_code=-1,
                execution_time_ms=_now_ms() - start_ms,
            )
        except Exception as exc:
            logger.exception("[LocalExecutor] execute FAILED | id=%s language=%s", execution_id, language)
            return ExecutionResult(
                success=False,
                error=str(exc),
                execution_time_ms=_now_ms() - start_ms,
            )

    # ------------------------------------------------------------------
    # Language-specific runners
    # ------------------------------------------------------------------

    async def _run_python(
        self,
        code: str,
        work_dir: str,
        output_dir: str,
        timeout: int,
        packages: list[str] | None,
        env: dict[str, str] | None,
    ) -> ExecutionResult:
        start_ms = _now_ms()
        safe_packages = validate_packages(packages, language=SandboxLanguage.PYTHON)

        deps_dir = os.path.join(work_dir, "deps")
        run_env: dict[str, str] = {**(env or {}), "OUTPUT_DIR": output_dir}

        if safe_packages:
            # Skip pip entirely for packages already present in the host
            # interpreter. pipeshub ships many of the allowlisted deps
            # (pandas, numpy, Pillow, openpyxl, ...) so this avoids a
            # guaranteed-to-fail network round trip on air-gapped hosts and
            # a pointless one on online hosts.
            host_satisfied, needs_install = _split_host_installed_python(safe_packages)
            if host_satisfied:
                logger.info(
                    "[LocalExecutor] skipping pip for host-installed packages: %s",
                    host_satisfied,
                )

            if needs_install:
                # Install into a per-execution directory with ``--target`` so
                # the host's global site-packages is never mutated. Invoke pip
                # via ``python -m pip`` to avoid requiring a ``pip`` shim on
                # PATH (notably absent for some Windows service accounts).
                os.makedirs(deps_dir, exist_ok=True)
                pip_result = await self._subprocess(
                    [
                        sys.executable, "-m", "pip", "install",
                        "--quiet", "--no-cache-dir",
                        "--disable-pip-version-check",
                        "--retries", "1", "--timeout", "10",
                        "--target", deps_dir,
                        *needs_install,
                    ],
                    work_dir,
                    timeout,
                    env,
                )
                if pip_result.exit_code != 0:
                    pip_result.execution_time_ms = _now_ms() - start_ms
                    if _looks_offline(pip_result.stderr):
                        pip_result.error = (
                            "Sandbox host cannot reach PyPI and the following "
                            f"packages are not pre-installed: {needs_install}. "
                            "Retry with a pre-installed alternative (pandas, "
                            "numpy, Pillow, openpyxl, python-docx, pdf2image, "
                            "reportlab, fpdf2, beautifulsoup4, Jinja2) or ask "
                            "the operator to pre-install them on the sandbox host."
                        )
                    else:
                        pip_result.error = f"pip install failed: {pip_result.stderr}"
                    return pip_result
                existing = run_env.get("PYTHONPATH", "")
                run_env["PYTHONPATH"] = (
                    f"{deps_dir}{os.pathsep}{existing}" if existing else deps_dir
                )

        # Always write the script as UTF-8 -- Python 3 parses source as UTF-8
        # by default, but on Windows ``open(..., "w")`` uses the locale codec
        # (typically cp1252), which turns smart quotes / em dashes into raw
        # bytes that blow up the parser (PEP-263 "Non-UTF-8 code" errors).
        script_path = os.path.join(work_dir, "main.py")
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)

        # Use ``sys.executable`` rather than the literal ``python3`` so the
        # sandbox runs under the same interpreter as the host service (and
        # works on Windows where ``python3`` is often absent or aliased to
        # the Store stub).
        result = await self._subprocess(
            [sys.executable, script_path],
            work_dir,
            timeout,
            run_env,
        )
        result.artifacts = self.collect_artifacts(output_dir)
        result.execution_time_ms = _now_ms() - start_ms
        return result

    async def _run_typescript(
        self,
        code: str,
        work_dir: str,
        output_dir: str,
        timeout: int,
        packages: list[str] | None,
        env: dict[str, str] | None,
    ) -> ExecutionResult:
        start_ms = _now_ms()
        safe_packages = validate_packages(packages, language=SandboxLanguage.TYPESCRIPT)

        run_env: dict[str, str] = {**(env or {}), "OUTPUT_DIR": output_dir}

        if safe_packages:
            # Install into the per-execution ``work_dir`` so that the host
            # project's node_modules / package.json is never touched.
            npm_result = await self._subprocess(
                [
                    "npm", "install", "--prefix", work_dir, "--no-save",
                    "--loglevel=error", *safe_packages,
                ],
                work_dir,
                timeout,
                env,
            )
            if npm_result.exit_code != 0:
                npm_result.execution_time_ms = _now_ms() - start_ms
                npm_result.error = f"npm install failed: {npm_result.stderr}"
                return npm_result
            node_modules_dir = os.path.join(work_dir, "node_modules")
            existing = run_env.get("NODE_PATH", "")
            run_env["NODE_PATH"] = (
                f"{node_modules_dir}{os.pathsep}{existing}" if existing else node_modules_dir
            )

        script_path = os.path.join(work_dir, "main.ts")
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)

        result = await self._subprocess(
            ["npx", "tsx", script_path],
            work_dir,
            timeout,
            run_env,
        )
        result.artifacts = self.collect_artifacts(output_dir)
        result.execution_time_ms = _now_ms() - start_ms
        return result

    async def _run_sqlite(
        self,
        sql: str,
        work_dir: str,
        output_dir: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> ExecutionResult:
        start_ms = _now_ms()
        db_path = os.path.join(work_dir, "sandbox.db")

        script_path = os.path.join(work_dir, "query.sql")
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(sql)

        run_env = {**(env or {}), "OUTPUT_DIR": output_dir}
        result = await self._subprocess(
            ["sqlite3", "-header", "-csv", db_path],
            work_dir,
            timeout,
            run_env,
            stdin_data=sql,
        )
        result.artifacts = self.collect_artifacts(output_dir)
        result.execution_time_ms = _now_ms() - start_ms
        return result

    async def _run_postgresql(
        self,
        sql: str,
        work_dir: str,
        output_dir: str,
        timeout: int,
        env: dict[str, str] | None,
    ) -> ExecutionResult:
        start_ms = _now_ms()
        pg_url = (env or {}).get("DATABASE_URL", "")
        if not pg_url:
            return ExecutionResult(
                success=False,
                error="DATABASE_URL not configured for PostgreSQL sandbox",
                execution_time_ms=_now_ms() - start_ms,
            )

        run_env = {**(env or {}), "OUTPUT_DIR": output_dir, "PGAPPNAME": "pipeshub_sandbox"}
        result = await self._subprocess(
            ["psql", pg_url, "-c", sql, "--csv"],
            work_dir,
            timeout,
            run_env,
        )
        result.artifacts = self.collect_artifacts(output_dir)
        result.execution_time_ms = _now_ms() - start_ms
        return result

    # ------------------------------------------------------------------
    # Subprocess helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _subprocess(
        cmd: list[str],
        cwd: str,
        timeout: int,
        env: dict[str, str] | None,
        *,
        stdin_data: str | None = None,
    ) -> ExecutionResult:
        logger.debug("[LocalExecutor._subprocess] cmd=%s cwd=%s timeout=%d", cmd, cwd, timeout)
        merged_env = build_sandbox_env(env)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            env=merged_env,
        )
        logger.debug("[LocalExecutor._subprocess] pid=%s started", proc.pid)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode() if stdin_data else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[LocalExecutor._subprocess] pid=%s killed after %ds timeout", proc.pid, timeout)
            proc.kill()
            await proc.wait()
            raise

        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

        logger.debug(
            "[LocalExecutor._subprocess] pid=%s exit_code=%s stdout_len=%d stderr_len=%d",
            proc.pid, proc.returncode, len(stdout), len(stderr),
        )
        if proc.returncode != 0 and stderr:
            logger.info("[LocalExecutor._subprocess] STDERR:\n%s", stderr[:2000])

        return ExecutionResult(
            success=proc.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode or 0,
        )

    # ------------------------------------------------------------------
    # Cleanup helper (used by artifact_cleanup)
    # ------------------------------------------------------------------

    @staticmethod
    def cleanup_execution(execution_id: str) -> None:
        """Remove the working directory for a given execution."""
        path = os.path.join(_SANDBOX_ROOT, execution_id)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def get_sandbox_root() -> str:
        return _SANDBOX_ROOT


def _now_ms() -> int:
    return int(time.time() * 1000)
