"""LocalExecutor -- subprocess-based sandbox for developer mode.

Runs code on the host OS via ``asyncio.create_subprocess_exec``.
Each invocation gets its own temp directory for source files and artifacts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
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

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox")


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
            # Install into a per-execution directory with ``--target`` so that
            # the host's global site-packages is never mutated.
            os.makedirs(deps_dir, exist_ok=True)
            pip_result = await self._subprocess(
                [
                    "pip", "install", "--quiet", "--no-cache-dir",
                    "--target", deps_dir,
                    *safe_packages,
                ],
                work_dir,
                timeout,
                env,
            )
            if pip_result.exit_code != 0:
                pip_result.execution_time_ms = _now_ms() - start_ms
                pip_result.error = f"pip install failed: {pip_result.stderr}"
                return pip_result
            existing = run_env.get("PYTHONPATH", "")
            run_env["PYTHONPATH"] = (
                f"{deps_dir}{os.pathsep}{existing}" if existing else deps_dir
            )

        script_path = os.path.join(work_dir, "main.py")
        with open(script_path, "w") as f:
            f.write(code)

        result = await self._subprocess(
            ["python3", script_path],
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
        with open(script_path, "w") as f:
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
        with open(script_path, "w") as f:
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
