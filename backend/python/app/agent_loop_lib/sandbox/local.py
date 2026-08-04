from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

from app.agent_loop_lib.core.exceptions import AgentLoopError
from app.agent_loop_lib.sandbox.base import ExecResult, SandboxInfo, SandboxProvider


class LocalSandbox(SandboxProvider):
    """Subprocess-based sandbox for local development.

    FOR DEVELOPMENT ONLY — no isolation, no resource limits, no network sandboxing.
    Production: use E2B, Daytona, or Modal instead.
    """

    def __init__(self, working_dir: str | None = None) -> None:
        self._working_dir = working_dir or tempfile.gettempdir()
        self._sandbox_id = str(uuid.uuid4())

    async def provision(self) -> SandboxInfo:
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            status="ready",
            metadata={"working_dir": self._working_dir, "backend": "local"},
        )

    async def run(self, code: str, language: str = "python", timeout: float = 30.0) -> ExecResult:
        if language == "python":
            return await self._run_python(code, timeout)
        elif language == "bash":
            return await self._run_bash(code, timeout)
        else:
            raise AgentLoopError(f"Unsupported language: {language}")

    async def upload_file(self, path: str, content: bytes) -> None:
        full_path = os.path.join(self._working_dir, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)

    async def download_file(self, path: str) -> bytes:
        full_path = os.path.join(self._working_dir, path)
        with open(full_path, "rb") as f:
            return f.read()

    async def destroy(self) -> None:
        pass  # local sandbox has no remote resources to release

    async def _run_python(self, code: str, timeout: float) -> ExecResult:
        fd, tmp_path = tempfile.mkstemp(suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
            return await self._exec([sys.executable, tmp_path], timeout=timeout)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _run_bash(self, code: str, timeout: float) -> ExecResult:
        cmd = ["cmd", "/c", code] if sys.platform == "win32" else ["bash", "-c", code]
        return await self._exec(cmd, timeout=timeout)

    async def _exec(self, cmd: list[str], timeout: float) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.communicate()
            except Exception:
                pass
            return ExecResult(stdout="", stderr=f"Timed out after {timeout}s", exit_code=-1)

        return ExecResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    # Legacy alias — kept so old code that calls reset() still works
    async def reset(self) -> None:
        pass
