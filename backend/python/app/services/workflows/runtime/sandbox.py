"""WorkflowToolBridge and SubprocessSandboxProvisioner.

WorkflowToolBridge extends ToolBridge (agent_loop_lib/sandbox/rpc.py) from
a single `tool(name, **kwargs)` verb to the full capability set:
  - broker.call  → routes to PlatformBroker for TOOL/AGENT_RUN/KNOWLEDGE_SEARCH/etc.
  - journal.lookup / journal.append → routes to IExecutionJournal
  - done           → terminal message, returns the run result

SubprocessSandboxProvisioner implements ISandboxSessionProvisioner:
  1. Creates a temp dir with the workflow source + sdk/_rpc.py as entry point.
  2. Launches a Python subprocess with stdin/stdout pipes via ToolBridge machinery.
  3. Applies OS-level confinement (setrlimit) where available.
  4. Tears down the temp dir on teardown().

Security note: the child gets an allowlisted environment (no provider API
keys, secrets, or datastore DSNs) and POSIX rlimits for CPU/memory/fds, and
every credentialed call goes through the RPC channel where the broker
enforces RunGrant.  It does NOT get filesystem or network isolation — the
child runs as the service user and can reach the network.  Treat this
provisioner as suitable for trusted-tenant deployments only; a container or
E2B backend implementing the same ISandboxSessionProvisioner interface is
required for untrusted multi-tenant code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.workflows.interface.broker import BrokerCall, IPlatformBroker, RunPrincipal
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.interface.provisioner import SessionSpec, SandboxSession

__all__ = ["WorkflowToolBridge", "SubprocessSandboxProvisioner", "TRIGGER_PAYLOAD_FILENAME"]

logger = logging.getLogger(__name__)

# Staged into the sandbox root by the provisioner, read by the harness.
TRIGGER_PAYLOAD_FILENAME = "trigger_payload.json"

# Max wall-clock seconds for one workflow run.
_DEFAULT_TIMEOUT_S = 300.0

# Max broker calls (guards runaway loops in generated code).
_DEFAULT_MAX_CALLS = 200

# Tail of child stderr retained for failure reporting.
_STDERR_KEEP_LINES = 200

# Per-run OS limits applied to the child (see `_apply_os_limits`).
_CPU_SECONDS = 300
_MAX_OPEN_FILES = 256
_MAX_ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# WorkflowToolBridge
# ---------------------------------------------------------------------------

class WorkflowToolBridge:
    """Host-side subprocess bridge for the full workflow capability set.

    Extends the simple ToolBridge tool() verb to also handle:
      - journal.lookup / journal.append
      - broker.call (routes to PlatformBroker for every Capability)
      - done (terminal run result)

    Spawns a Python subprocess (the sdk/_rpc.py harness) and serves
    incoming JSON-Lines messages from its stdout.
    """

    def __init__(
        self,
        *,
        broker: "IPlatformBroker",
        principal: "RunPrincipal",
        journal: "IExecutionJournal",
        working_dir: str,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_calls: int = _DEFAULT_MAX_CALLS,
        is_dry_run: bool = False,
        in_replay: bool = False,
    ) -> None:
        self._broker = broker
        self._principal = principal
        self._journal = journal
        self._working_dir = working_dir
        self._timeout_s = timeout_s
        self._max_calls = max_calls
        self._is_dry_run = is_dry_run
        self._in_replay = in_replay

    async def run(self, src_path: str) -> dict[str, Any]:
        """Execute the workflow source and return a result dict.

        Returns {"status": "succeeded", "output": ...},
                {"status": "awaiting_input", "suspension_kind": ..., ...}, or
                {"status": "failed", "error": ...}.
        """
        principal_json = self._principal.model_dump_json()
        cmd = [
            sys.executable, "-m", "app.services.workflows.sdk._rpc",
            src_path, self._principal.run_id, principal_json,
        ]
        if self._is_dry_run:
            cmd.append("--dry-run")
        if self._in_replay:
            cmd.append("--replay")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
            env=self._make_env(),
            preexec_fn=_apply_os_limits if os.name == "posix" else None,
        )
        logger.info(
            "WorkflowToolBridge: launched subprocess pid=%s run_id=%s",
            proc.pid, self._principal.run_id,
        )
        # Drain stderr continuously: a child that writes more than the pipe
        # buffer would otherwise block forever waiting for us to read it.
        stderr_chunks: list[str] = []
        drain_task = asyncio.create_task(self._drain_stderr(proc, stderr_chunks))
        try:
            return await self._serve(proc, stderr_chunks)
        finally:
            drain_task.cancel()
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process, sink: list[str]) -> None:
        """Continuously read stderr, keeping only the last _STDERR_KEEP_LINES."""
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                sink.append(line.decode(errors="replace"))
                if len(sink) > _STDERR_KEEP_LINES:
                    del sink[: len(sink) - _STDERR_KEEP_LINES]
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("WorkflowToolBridge: stderr drain ended early")

    async def _serve(
        self, proc: asyncio.subprocess.Process, stderr_chunks: list[str],
    ) -> dict[str, Any]:
        """Read messages from subprocess stdout, dispatch them, write responses."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout_s
        calls_made = 0

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "WorkflowToolBridge: run_id=%s timed out after %.1fs",
                    self._principal.run_id, self._timeout_s,
                )
                return {"status": "failed", "error": f"Workflow timed out after {self._timeout_s:.0f}s"}

            try:
                line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=min(remaining, 30.0))
            except asyncio.TimeoutError:
                continue
            if not line_bytes:
                # Subprocess exited without sending "done".
                stderr = "".join(stderr_chunks)
                return {
                    "status": "failed",
                    "error": f"Subprocess exited unexpectedly: {stderr[-500:] or '(no stderr)'}",
                }
            try:
                msg = json.loads(line_bytes)
            except json.JSONDecodeError:
                logger.debug("WorkflowToolBridge: non-JSON line from subprocess: %r", line_bytes[:120])
                continue

            msg_type = msg.get("type")

            if msg_type == "done":
                return {
                    "status": msg.get("status", "failed"),
                    "output": msg.get("output"),
                    "error": msg.get("error"),
                    "suspension_kind": msg.get("suspension_kind"),
                    "event_type": msg.get("event_type"),
                    "step_key": msg.get("step_key"),
                    "label": msg.get("label"),
                    "payload": msg.get("payload"),
                }

            elif msg_type == "broker.call":
                calls_made += 1
                response = await self._handle_broker_call(msg, calls_made)
                await self._write_response(proc, response)

            elif msg_type == "journal.lookup":
                response = await self._handle_journal_lookup(msg)
                await self._write_response(proc, response)

            elif msg_type == "journal.append":
                response = await self._handle_journal_append(msg)
                await self._write_response(proc, response)

            else:
                logger.debug("WorkflowToolBridge: unknown message type %r; ignoring", msg_type)

    async def _handle_broker_call(self, msg: dict, calls_made: int) -> dict:
        if calls_made > self._max_calls:
            return {"ok": False, "error": f"Exceeded max_calls={self._max_calls}"}
        from app.services.workflows.interface.broker import BrokerCall, Capability
        try:
            cap_str = msg.get("capability", "tool")
            try:
                capability = Capability(cap_str)
            except ValueError:
                return {"ok": False, "error": f"Unknown capability: {cap_str!r}"}
            call = BrokerCall(
                capability=capability,
                target=msg.get("target", ""),
                arguments=msg.get("arguments") or {},
                run_id=self._principal.run_id,
                step_key=msg.get("step_key", ""),
            )
            result = await self._broker.dispatch(call, self._principal)
            if result.success:
                try:
                    json.dumps(result.data)
                    value = result.data
                except (TypeError, ValueError):
                    value = repr(result.data)
                return {"ok": True, "value": value}
            return {"ok": False, "error": result.error or "Broker call failed"}
        except Exception as exc:
            logger.exception("WorkflowToolBridge: broker.call raised")
            return {"ok": False, "error": str(exc)}

    async def _handle_journal_lookup(self, msg: dict) -> dict:
        try:
            # run_id comes from the host-side principal, never the child: a
            # compromised workflow must not read another run's journal.
            entry = await self._journal.lookup(self._principal.run_id, msg.get("step_key", ""))
            if entry is None:
                return {"ok": True, "value": None}
            return {"ok": True, "value": json.loads(entry.model_dump_json())}
        except Exception as exc:
            logger.exception("WorkflowToolBridge: journal.lookup raised")
            return {"ok": False, "error": str(exc)}

    async def _handle_journal_append(self, msg: dict) -> dict:
        try:
            from app.services.workflows.domain.models import (
                ErrorRecord, JournalEntry, ResultRef, StepOutcome,
            )
            raw = msg.get("entry", {})
            result_ref = None
            if raw.get("result_ref"):
                result_ref = ResultRef(**raw["result_ref"])
            error = None
            if raw.get("error"):
                error = ErrorRecord(**raw["error"])
            entry = JournalEntry(
                run_id=self._principal.run_id,  # never trust the child's run_id
                seq=raw.get("seq", 0),
                step_key=raw["step_key"],
                entry_kind=raw["entry_kind"],
                idempotency_key=raw.get("idempotency_key", raw["step_key"]),
                outcome=StepOutcome(raw["outcome"]),
                result_ref=result_ref,
                error=error,
                attempt=raw.get("attempt", 1),
            )
            await self._journal.append(entry)
            return {"ok": True, "value": None}
        except Exception as exc:
            logger.exception("WorkflowToolBridge: journal.append raised")
            return {"ok": False, "error": str(exc)}

    @staticmethod
    async def _write_response(proc: asyncio.subprocess.Process, response: dict) -> None:
        try:
            proc.stdin.write((json.dumps(response) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            logger.debug("WorkflowToolBridge: could not write response (subprocess gone)")

    @staticmethod
    def _make_env() -> dict[str, str]:
        """Allowlist-only env for the subprocess.

        Anything not named here is dropped, so provider API keys, JWT and
        encryption secrets, and datastore DSNs never reach workflow code even
        when new credential env vars are introduced later.  Credentialed work
        happens host-side behind the broker.
        """
        allowed = {
            "PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
            "TMPDIR", "TMP", "TEMP", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "PYTHONUNBUFFERED", "VIRTUAL_ENV",
            "USER", "LOGNAME", "SYSTEMROOT",
        }
        env = {k: v for k, v in os.environ.items() if k in allowed}

        # The subprocess runs with cwd=<temp sandbox dir> and invokes
        # `python -m app.services.workflows.sdk._rpc`. The `app` package
        # lives under the application root (parent of this file's package
        # tree). Ensure it's always on PYTHONPATH so the module resolves
        # regardless of whether the host env sets PYTHONPATH explicitly.
        app_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
        )
        existing = env.get("PYTHONPATH", "")
        if app_root not in existing.split(os.pathsep):
            env["PYTHONPATH"] = f"{app_root}{os.pathsep}{existing}" if existing else app_root

        return env


# ---------------------------------------------------------------------------
# SubprocessSandboxProvisioner
# ---------------------------------------------------------------------------

class SubprocessSandboxProvisioner:
    """ISandboxSessionProvisioner: stages + runs workflow code in a subprocess.

    Phase 1 implementation: subprocess in a temp dir with rlimit-based resource
    limits.  Docker/E2B back-ends implement the same interface.
    """

    def __init__(self, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    async def provision(self, spec: "SessionSpec") -> "SandboxSession":
        from app.services.workflows.interface.provisioner import SandboxSession
        sandbox_root = tempfile.mkdtemp(prefix=f"wf_{spec.run_id[:8]}_")
        src_path = os.path.join(sandbox_root, "workflow.py")
        with open(src_path, "wb") as f:
            f.write(spec.source_bundle)
        # Staged as a file rather than an argv entry: trigger bodies are
        # arbitrarily large and argv is world-readable via /proc.
        with open(os.path.join(sandbox_root, TRIGGER_PAYLOAD_FILENAME), "w", encoding="utf-8") as f:
            json.dump(spec.trigger_payload, f)
        session_id = str(uuid.uuid4())
        logger.info(
            "SubprocessSandboxProvisioner: provisioned session=%s run_id=%s dir=%s",
            session_id, spec.run_id, sandbox_root,
        )
        return SandboxSession(session_id=session_id, sandbox_root=sandbox_root)

    async def stage(self, session: "SandboxSession", items: list) -> None:
        """Write extra files into the sandbox root."""
        for item in items:
            dest = os.path.join(session.sandbox_root, item.path)
            os.makedirs(os.path.dirname(dest) or session.sandbox_root, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(item.content)

    async def teardown(self, session: "SandboxSession") -> None:
        try:
            shutil.rmtree(session.sandbox_root, ignore_errors=True)
            logger.debug("SubprocessSandboxProvisioner: torn down sandbox %s", session.session_id)
        except Exception:
            logger.warning("SubprocessSandboxProvisioner: teardown failed for %s", session.session_id)


# ---------------------------------------------------------------------------
# OS-level confinement (best-effort, non-fatal on failure)
# ---------------------------------------------------------------------------

def _apply_os_limits() -> None:
    """`preexec_fn` for the workflow subprocess: caps CPU, address space, and
    file descriptors so runaway generated code cannot exhaust the host.

    Runs post-fork, pre-exec in the child. An exception here kills the child
    before it starts, so every limit is applied independently and best-effort
    (Windows and some container runtimes reject individual rlimits, and Docker
    may already impose stricter ones). Do not log here -- logging post-fork can
    deadlock on a lock held by another thread at fork time.
    """
    try:
        import resource
    except ImportError:
        return

    limits = [
        ("RLIMIT_CPU", _CPU_SECONDS),
        ("RLIMIT_NOFILE", _MAX_OPEN_FILES),
        ("RLIMIT_AS", _MAX_ADDRESS_SPACE_BYTES),
        # No core dumps: generated code may hold tenant data in memory.
        ("RLIMIT_CORE", 0),
    ]
    for name, value in limits:
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except (OSError, ValueError):
            continue


def build_workflow_tool_bridge(
    *,
    broker: "IPlatformBroker",
    principal: "RunPrincipal",
    journal: "IExecutionJournal",
    working_dir: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> WorkflowToolBridge:
    """Convenience factory used by CodeWorkflowRunner."""
    return WorkflowToolBridge(
        broker=broker,
        principal=principal,
        journal=journal,
        working_dir=working_dir,
        timeout_s=timeout_s,
    )
