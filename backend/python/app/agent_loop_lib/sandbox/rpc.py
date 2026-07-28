from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

"""RPC tool bridge for `execute_code` (Phase 3) — Hermes' "programmatic tool
calling" pattern: the model writes Python that calls OTHER tools via
`tool(name, **kwargs)` from inside a single sandboxed script, collapsing an
N-step tool-call pipeline into one turn instead of N.

Protocol (JSON Lines over the subprocess's stdout/stdin — no extra file
descriptors needed, so this works identically on every platform):

    child -> host:  {"type": "call", "id": <int>, "tool": <str>, "args": {...}}
    host  -> child: {"ok": true, "value": <json>} | {"ok": false, "error": <str>}
    child -> host:  {"type": "done", "stdout": <str>, "stderr": <str>,
                      "result": <json|null>, "error": <traceback str|null>}

The child's own `print()`/`sys.stderr` output never touches the real
stdout/stderr pipes — the harness redirects `sys.stdout`/`sys.stderr` to
in-memory buffers for the DURATION of the user's code and reserves the real
pipes exclusively for this protocol, then reports the captured buffers back
in the final "done" message.

FOR DEVELOPMENT ONLY, same caveat as `sandbox/local.py`: this is a bare
subprocess with no OS-level isolation or resource limits — hardening
(Docker/E2B/Modal backends, kernel-level confinement) is explicit follow-up
work (see Phase 3 sandbox taxonomy).
"""

ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[Any]]

_HARNESS = r'''
import sys, json, io, traceback, itertools

_real_stdout = sys.stdout
_real_stderr = sys.stderr


def _send(obj):
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


_rpc_ids = itertools.count(1)


def tool(name, **kwargs):
    call_id = next(_rpc_ids)
    _send({"type": "call", "id": call_id, "tool": name, "args": kwargs})
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("RPC channel closed by host")
    resp = json.loads(line)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "tool call failed")
    return resp.get("value")


_stdout_buf = io.StringIO()
_stderr_buf = io.StringIO()
sys.stdout = _stdout_buf
sys.stderr = _stderr_buf

_result = None
_error = None
try:
    with open(sys.argv[1], "r", encoding="utf-8") as _f:
        _source = _f.read()
    _globals = {"tool": tool, "__name__": "__main__"}
    exec(compile(_source, "<execute_code>", "exec"), _globals)
    _result = _globals.get("result")
except Exception:
    _error = traceback.format_exc()
finally:
    sys.stdout = _real_stdout
    sys.stderr = _real_stderr

try:
    json.dumps(_result)
except TypeError:
    _result = repr(_result)

_send({
    "type": "done",
    "stdout": _stdout_buf.getvalue(),
    "stderr": _stderr_buf.getvalue(),
    "result": _result,
    "error": _error,
})
'''


class CodeExecResult(BaseModel):
    stdout: str
    stderr: str
    result: Any = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


class ToolBridge:
    """Runs one `execute_code` invocation: writes the user's code plus the
    RPC harness to temp files, spawns a subprocess, and services `tool()`
    calls it makes by awaiting `dispatch(name, args)` until the subprocess
    reports "done" (or crashes, or times out).
    """

    def __init__(
        self,
        dispatch: ToolDispatch,
        working_dir: str | None = None,
        max_tool_calls: int = 50,
    ) -> None:
        self._dispatch = dispatch
        self._working_dir = working_dir or tempfile.gettempdir()
        self._max_tool_calls = max_tool_calls

    async def run(self, code: str, timeout: float = 30.0) -> CodeExecResult:
        harness_fd, harness_path = tempfile.mkstemp(suffix="_harness.py")
        code_fd, code_path = tempfile.mkstemp(suffix="_usercode.py")
        try:
            with os.fdopen(harness_fd, "w", encoding="utf-8") as f:
                f.write(_HARNESS)
            with os.fdopen(code_fd, "w", encoding="utf-8") as f:
                f.write(code)
            return await self._run(harness_path, code_path, timeout)
        finally:
            for p in (harness_path, code_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def _run(self, harness_path: str, code_path: str, timeout: float) -> CodeExecResult:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, harness_path, code_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        final: dict | None = None
        calls_made = 0

        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # stray non-protocol output on the shared pipe — ignore

                if msg.get("type") == "call":
                    calls_made += 1
                    if calls_made > self._max_tool_calls:
                        response = {"ok": False, "error": f"execute_code exceeded max_tool_calls={self._max_tool_calls}"}
                    else:
                        try:
                            value = await self._dispatch(msg.get("tool", ""), msg.get("args") or {})
                            response = {"ok": True, "value": value}
                        except Exception as e:
                            response = {"ok": False, "error": str(e)}
                    proc.stdin.write((json.dumps(response) + "\n").encode())
                    await proc.stdin.drain()
                elif msg.get("type") == "done":
                    final = msg
                    break
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CodeExecResult(stdout="", stderr="", error=f"Timed out after {timeout}s")

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        if final is None:
            stderr_bytes = await proc.stderr.read()
            stderr = stderr_bytes.decode(errors="replace")
            return CodeExecResult(
                stdout="", stderr=stderr,
                error=stderr or "Sandbox exited without producing a result",
            )

        return CodeExecResult(
            stdout=final.get("stdout", ""),
            stderr=final.get("stderr", ""),
            result=final.get("result"),
            error=final.get("error"),
        )
