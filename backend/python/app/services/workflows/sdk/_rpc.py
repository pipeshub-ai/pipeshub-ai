"""Sandbox-side RPC client for workflow code running inside a subprocess.

Two roles:

1. **Module** — exposes `_RpcJournal` and `_RpcBroker` for constructing a
   fully-capable `Ctx` inside the sandbox. Import and use from the harness.

2. **Entry point** (`python -m app.services.workflows.sdk._rpc <src_path> <run_id> <principal_json>`) —
   acts as the subprocess harness:
     a. Reads workflow source from `src_path`.
     b. Creates `_RpcJournal` and `_RpcBroker` over stdin/stdout.
     c. Instantiates `Ctx` with those adapters.
     d. Finds and calls the `@workflow`-decorated entry point.
     e. Writes a terminal `{"type": "done", ...}` message and exits.

Protocol (JSON Lines over stdin/stdout — no extra file descriptors needed):

  subprocess → host:
    {"type": "broker.call", "id": N, "capability": <str>, "target": <str>,
     "arguments": {...}, "run_id": <str>, "step_key": <str>}

    {"type": "journal.lookup", "id": N, "run_id": <str>, "step_key": <str>}

    {"type": "journal.append", "id": N, "run_id": <str>,
     "entry": <JournalEntry dict>}

    {"type": "done", "status": "succeeded|awaiting_input|failed",
     "output": <json|null>, "error": <str|null>,
     "suspension_kind": <str|null>, "event_type": <str|null>,
     "step_key": <str|null>, "label": <str|null>, "payload": <json|null>}

  host → subprocess:
    {"ok": true, "value": <json>}
    {"ok": false, "error": <str>}

The subprocess's own print()/sys.stderr output must be redirected away from
the real stdout/stdin pipes before the protocol starts — see the __main__
block at the bottom which redirects both before loading user code.

Mirrors app/agent_loop_lib/sandbox/rpc.py's ToolBridge protocol but adds
journal and full capability set.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.workflows.interface.broker import BrokerCall, BrokerResult, RunPrincipal
    from app.services.workflows.domain.models import JournalEntry

PROTOCOL_VERSION = "1.1"

# Must match `runtime/sandbox.py`; duplicated rather than imported so the
# harness stays importable without pulling in host-side runtime modules.
TRIGGER_PAYLOAD_FILENAME = "trigger_payload.json"

# ---------------------------------------------------------------------------
# Subprocess-side adapters (IExecutionJournal + IPlatformBroker over pipes)
# ---------------------------------------------------------------------------

class _RpcChannel:
    """Low-level JSON-lines pipe channel (subprocess side).

    All message I/O goes through the REAL stdout/stdin, not the user-code
    redirected buffers.  Callers must redirect sys.stdout before constructing
    user code if they want to capture print() output.
    """

    def __init__(self) -> None:
        # Capture the REAL stdout/stdin before they are replaced.
        self._out = sys.__stdout__
        self._in = sys.__stdin__
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def call_sync(self, msg: dict) -> Any:
        """Send a request, block for the response, return value or raise."""
        call_id = self._next_id()
        msg["id"] = call_id
        self._out.write(json.dumps(msg) + "\n")
        self._out.flush()
        while True:
            line = self._in.readline()
            if not line:
                raise RuntimeError("RPC channel closed by host")
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error") or "RPC call failed")
            return resp.get("value")

    def send_done(self, msg: dict) -> None:
        """Write the terminal done message."""
        msg["type"] = "done"
        self._out.write(json.dumps(msg) + "\n")
        self._out.flush()


class _RpcJournal:
    """IExecutionJournal backed by RPC calls to the host process."""

    def __init__(self, channel: _RpcChannel, run_id: str) -> None:
        self._ch = channel
        self._run_id = run_id

    async def append(self, entry: "JournalEntry") -> None:
        import json as _json
        try:
            entry_dict = json.loads(entry.model_dump_json())
        except Exception:
            entry_dict = {
                "run_id": entry.run_id,
                "seq": entry.seq,
                "step_key": entry.step_key,
                "entry_kind": entry.entry_kind,
                "idempotency_key": entry.idempotency_key,
                "outcome": entry.outcome if isinstance(entry.outcome, str) else entry.outcome.value,
            }
        self._ch.call_sync({
            "type": "journal.append",
            "run_id": self._run_id,
            "entry": entry_dict,
        })

    async def lookup(self, run_id: str, step_key: str):
        raw = self._ch.call_sync({
            "type": "journal.lookup",
            "run_id": run_id,
            "step_key": step_key,
        })
        if raw is None:
            return None
        from app.services.workflows.domain.models import (
            ErrorRecord, JournalEntry, ResultRef, StepOutcome,
        )
        result_ref = None
        if raw.get("result_ref"):
            result_ref = ResultRef(**raw["result_ref"])
        error = None
        if raw.get("error"):
            error = ErrorRecord(**raw["error"])
        return JournalEntry(
            run_id=raw["run_id"],
            seq=raw.get("seq", 0),
            step_key=raw["step_key"],
            entry_kind=raw["entry_kind"],
            idempotency_key=raw.get("idempotency_key", raw["step_key"]),
            outcome=StepOutcome(raw["outcome"]),
            result_ref=result_ref,
            error=error,
            attempt=raw.get("attempt", 1),
        )

    async def load(self, run_id: str):
        return []  # subprocess only needs lookup, not full load

    async def compact(self, run_id: str, upto_seq: int) -> None:
        pass  # host-side only

    async def touch(self, run_id: str) -> str | None:
        return None  # host-side only: retention is managed after the run returns


class _RpcBroker:
    """IPlatformBroker backed by RPC calls to the host process."""

    def __init__(self, channel: _RpcChannel) -> None:
        self._ch = channel

    def register(self, handler: Any) -> None:
        pass  # no-op in subprocess; handlers live on the host

    async def dispatch(self, call: "BrokerCall", principal: "RunPrincipal"):
        from app.services.workflows.interface.broker import BrokerResult
        try:
            value = self._ch.call_sync({
                "type": "broker.call",
                "capability": call.capability if isinstance(call.capability, str) else call.capability.value,
                "target": call.target,
                "arguments": call.arguments,
                "run_id": call.run_id,
                "step_key": call.step_key,
            })
            return BrokerResult(success=True, data=value)
        except RuntimeError as exc:
            return BrokerResult(success=False, error=str(exc))


# ---------------------------------------------------------------------------
# Harness entry point
# ---------------------------------------------------------------------------

def _load_trigger_payload(src_path: str) -> dict:
    """Read the payload the provisioner staged next to the source. Absent for
    schedule-driven runs, and for the in-process test path."""
    import os

    path = os.path.join(os.path.dirname(src_path), TRIGGER_PAYLOAD_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _run_harness(
    src_path: str,
    run_id: str,
    principal_json: str,
    *,
    is_dry_run: bool = False,
    in_replay: bool = False,
) -> None:
    """Execute the workflow source and write the terminal done message."""
    import io
    import traceback

    channel = _RpcChannel()

    # Redirect user print() away from the real stdout pipes BEFORE exec.
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    sys.stdout = stdout_buf
    sys.stderr = stderr_buf

    status = "failed"
    output = None
    error_msg = None
    suspension_kind = None
    event_type = None
    suspension_step_key = None
    suspension_label = None
    suspension_payload = None

    try:
        with open(src_path, encoding="utf-8") as f:
            source = f.read()

        from app.services.workflows.interface.broker import RunPrincipal
        principal_data = json.loads(principal_json)
        principal = RunPrincipal.model_validate(principal_data)

        journal = _RpcJournal(channel, run_id)
        broker = _RpcBroker(channel)

        from app.services.workflows.sdk.context import (
            Ctx,
            _ApprovalSuspension,
            _WaitForEventSuspension,
        )
        ctx = Ctx(
            run_id=run_id,
            journal=journal,
            broker=broker,
            principal=principal,
            is_dry_run=is_dry_run,
            in_replay=in_replay,
        )
        trigger_payload = _load_trigger_payload(src_path)

        # Find and call the @workflow-decorated entry point.
        # Restricted builtins, not the real ones: this process has the whole
        # application on PYTHONPATH and can reach Arango/Redis/Qdrant/etcd on
        # localhost, so `import os` here is cross-tenant data access. The
        # verifier rejects the same code at commit, but a version row written
        # by any other path reaches this exec unverified.
        from app.services.workflows.security.sandbox_policy import build_safe_builtins

        ns: dict[str, Any] = {
            "__builtins__": build_safe_builtins(),
            "__name__": "__main__",
        }
        compiled = compile(source, src_path, "exec")
        exec(compiled, ns)  # noqa: S102

        entry = None
        for obj in ns.values():
            if callable(obj) and hasattr(obj, "__workflow_meta__"):
                entry = obj
                break

        if entry is None:
            raise ValueError("No @workflow-decorated function found in source")

        import asyncio
        import inspect
        sig = inspect.signature(entry)
        params = list(sig.parameters.keys())
        if len(params) >= 2:
            result = asyncio.run(entry(ctx, trigger_payload))
        else:
            result = asyncio.run(entry(ctx))

        try:
            json.dumps(result)
            output = result
        except (TypeError, ValueError):
            output = repr(result)
        status = "succeeded"

    except _WaitForEventSuspension as susp:
        status = "awaiting_input"
        suspension_kind = "wait_for_event"
        event_type = susp.event_type
        suspension_step_key = susp.step_key

    except _ApprovalSuspension as susp:
        status = "awaiting_input"
        suspension_kind = "approval"
        suspension_step_key = susp.step_key
        suspension_label = susp.label
        try:
            json.dumps(susp.payload)
            suspension_payload = susp.payload
        except (TypeError, ValueError):
            suspension_payload = repr(susp.payload)

    except Exception:
        error_msg = traceback.format_exc()

    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    channel.send_done({
        "status": status,
        "output": output,
        "error": error_msg,
        "suspension_kind": suspension_kind,
        "event_type": event_type,
        "step_key": suspension_step_key,
        "label": suspension_label,
        "payload": suspension_payload,
    })


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(
            json.dumps({"type": "done", "status": "failed",
                        "error": "Usage: _rpc.py <src_path> <run_id> <principal_json> [--dry-run]"}),
            file=sys.__stdout__,
        )
        sys.exit(1)
    _flags = sys.argv[4:]
    _run_harness(
        sys.argv[1], sys.argv[2], sys.argv[3],
        is_dry_run="--dry-run" in _flags,
        in_replay="--replay" in _flags,
    )
