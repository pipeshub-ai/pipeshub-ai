"""``run_code`` on the durable path as a receipted effect (P5.11).

The legacy tool and hooks run unchanged (``SandboxSource``, ``build_hooks``);
this module adds what a durable run needs around them. Each call of the
run's own port goes through a route that records, under the call's
idempotency key, how far it got (``ExecutionReceipts``), and the tool's
effect becomes ``execute`` and idempotent: the kernel retries an intent
whose outcome it never journaled, and the retry answers from the receipt.

| Receipt found on a retry | Answer |
|---|---|
| none | the call runs (it never started) |
| ``started`` | ``run_code_interrupted``: the program may have run in a sandbox that died with its attempt; it is not run again |
| ``executed`` | the recorded output; each ``$OUTPUT_DIR`` file the dead attempt registered is delivered, the rest are reported |
| ``done`` | the recorded result, its artifacts delivered once |

A call outside the run's port (a composed child agent's, the legacy loop's)
has no tool context and runs as it always did.

A fresh call is also a command for the activity panel (UX5): what it prints reaches
the run's ``CommandActivity`` as the sandbox reads it, and its record is journaled
with the outcome when the claim's Node accepts it. A recorded answer is not run, so
it is not shown again.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from app.agent_loop_lib.tools.builtin.sandbox.input_staging import stage_workspace_files
from app.harness.activity import command_finished_event, finish_from_result
from harness_core.activity import CommandSpec, recording
from harness_core.clock import SystemClock
from harness_core.effects.failures import tool_error_code
from harness_core.effects.receipts import ExecutionReceipt
from harness_core.failpoints import afailpoint
from harness_core.kernel.core.types import ToolResult as CoreToolResult
from harness_core.security.token_patterns import redact_tokens, redact_tokens_in
from harness_core.tools.durable_port import RouteResult, current_tool_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import datetime

    from pydantic import JsonValue

    from app.agent_loop_lib.tools.base import ToolOutput
    from app.agents.agent_loop.context import AgentContext
    from app.harness.assembly.run_code_workspace import Checkout, RunCodeWorkspace
    from harness_core.activity import (
        CommandActivity,
        CommandRecorder,
        CommandStatus,
        OutputStream,
    )
    from harness_core.activity.types import CommandRecord
    from harness_core.clock import Clock
    from harness_core.contracts.generated.enums import ToolErrorCode
    from harness_core.effects.receipts import ExecutionReceipts
    from harness_core.effects.types import ToolEffect
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.hooks.registry import HookRegistry
    from harness_core.logs import HarnessLog
    from harness_core.tools.durable_port import Route
    from harness_core.tools.registry import ToolRegistry

RUN_CODE = "run_code"
# Their effect stays in the coding sandbox, which does not outlive the attempt.
RECEIPTED_TOOLS: Final = (RUN_CODE, "install_packages")
INTERRUPTED: Final = "run_code_interrupted"
INTERRUPTED_MESSAGE: Final = (
    f"{INTERRUPTED}: the worker running this call stopped before it finished, and the sandbox went with it. "
    "Nothing it produced was kept. Call the tool again if the result is still needed."
)
RECOVERED_NOTE: Final = (
    "The worker running this call stopped after the program finished; this is its recorded output. "
    "The sandbox is gone: do not pass its sandbox_id again."
)
NOT_KEPT_NOTE: Final = (
    "These files are outside $WORKSPACE_DIR and $OUTPUT_DIR, so they are gone once the sandbox goes. "
    "To keep a file for later calls and turns, write it under $WORKSPACE_DIR."
)
# How many such paths a result names: enough to recognise the mistake, never a listing of a whole tree.
_NOT_KEPT_SHOWN: Final = 20
UNSAVED_NOTE: Final = (
    "These files were produced but the worker stopped before they were saved, so they are NOT downloadable. "
    "Do not tell the user they are ready; re-run the code to produce them again."
)


def _name_unkept(data: object) -> None:
    """Names the files a call wrote outside the chat's workspace and ``$OUTPUT_DIR``: the model learns at the call
    that they will not be there in a later turn (QA-FP12 B1), not when it looks for them."""
    from app.agents.agent_loop.sandbox_bridge import (
        _partition_sandbox_outputs,  # pyright: ignore[reportPrivateUsage]
    )

    if not isinstance(data, dict):
        return
    paths = [p for p in cast("list[object]", data.get("artifacts") or []) if isinstance(p, str)]  # pyright: ignore[reportUnknownMemberType]
    _deliverables, scratch = _partition_sandbox_outputs(paths)
    if scratch:
        data["not_kept"] = sorted(scratch)[:_NOT_KEPT_SHOWN]
        data["not_kept_note"] = NOT_KEPT_NOTE


class _Tracked:
    """A recorder that remembers whether the backend streamed anything into it."""

    def __init__(self, inner: CommandRecorder) -> None:
        self.inner = inner
        self.streamed = False

    def write(self, stream: OutputStream, text: str) -> None:
        self.streamed = True
        self.inner.write(stream, text)

    async def finish(
        self, *, exit_code: int | None, status: CommandStatus, duration_ms: int | None = None
    ) -> CommandRecord:
        return await self.inner.finish(exit_code=exit_code, status=status, duration_ms=duration_ms)


def _command_of(call: ToolCall, typed_line: str | None = None) -> tuple[str, str | None]:
    arguments = call.arguments
    if call.name == RUN_CODE and typed_line is not None:
        return typed_line, "bash"
    if call.name == RUN_CODE:
        language = arguments.get("language")
        return str(arguments.get("code") or ""), language if isinstance(language, str) else None
    packages = arguments.get("packages")
    names = " ".join(str(p) for p in packages) if isinstance(packages, list) else str(packages or "")
    return f"install {names}".strip(), None


@dataclass(frozen=True)
class _Pending:
    started_at: datetime
    done: bool


def _json(data: object) -> JsonValue:
    """The tool's data as the journal and the receipt store hold it."""
    return cast("JsonValue", json.loads(json.dumps(data, default=str)))


class SessionWorkspaceCalls:
    """Each ``run_code`` call with the chat's workspace (QAFIX4 B1): its head staged under the sandbox's
    ``workspace/`` folder, and what the call left there saved as the next revision.

    The tool runs every call through this, so a delegate's call (``coding_agent``, whose loop reaches the tool
    without the durable route) keeps its files as the run's own call does (LONGTASK-B1)."""

    def __init__(self, workspace: RunCodeWorkspace, context: AgentContext, log: HarnessLog) -> None:
        self._workspace = workspace
        self._context = context
        self._log = log

    async def run(self, execute: Callable[[], Awaitable[ToolOutput]]) -> ToolOutput:
        checkout = await self._checkout()
        with stage_workspace_files(checkout.sandbox_files() if checkout is not None else None):
            output = await execute()
        if output.success and checkout is not None:
            await self._save(checkout, output.data)
            _name_unkept(output.data)
        return output

    async def _checkout(self) -> Checkout | None:
        """The session workspace's head for this call; a store that cannot be read costs the call its files only."""
        try:
            return await self._workspace.checkout()
        except Exception as exc:
            self._log.warning("run_code.workspace_unstaged", errorType=type(exc).__name__)
            return None

    async def _save(self, checkout: Checkout, data: object) -> None:
        """What the call left under ``workspace/`` becomes the session workspace's next revision."""
        from app.agent_loop_lib.sandbox.manager import SandboxType
        from app.harness.adapters.sandbox_workspace import SandboxWorkspaceExporter

        sandbox_id = data.get("sandbox_id") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType]
        manager = getattr(self._context, "sandbox_manager", None)
        if not isinstance(sandbox_id, str) or manager is None:
            return
        try:
            files = await SandboxWorkspaceExporter().snapshot(manager.get(SandboxType.CODING, sandbox_id))
            saved = await self._workspace.save(checkout, files)
        except Exception as exc:
            self._log.warning("run_code.workspace_unsaved", errorType=type(exc).__name__)
            return
        if saved is not None:
            self._log.info("run_code.workspace_saved", revision=saved.revision, files=len(saved.files))


class ReceiptedSandboxTools:
    """Routes, a POST hook and effects that make the sandbox tools receipted."""

    def __init__(
        self,
        registry: ToolRegistry,
        context: AgentContext,
        receipts: ExecutionReceipts,
        *,
        log: HarnessLog,
        clock: Clock | None = None,
        activity: CommandActivity | None = None,
        journal_commands: bool = False,
        typed_line: str | None = None,
    ) -> None:
        """``typed_line``: a person's ``/run`` line, the command its activity shows (SLASH3 W1)."""
        self._typed_line = typed_line
        self._activity = activity
        self._journal_commands = journal_commands
        self._registry = registry
        self._context = context
        self._receipts = receipts
        self._log = log
        self._clock = clock or SystemClock()
        self._pending: dict[str, _Pending] = {}
        self.names: tuple[str, ...] = tuple(n for n in RECEIPTED_TOOLS if registry.has(n))

    def effects(self, effects: Mapping[str, ToolEffect]) -> dict[str, ToolEffect]:
        return {
            name: dataclasses.replace(effect, idempotent=True) if name in self.names else effect
            for name, effect in effects.items()
        }

    def routes(self) -> dict[str, Route]:
        return dict.fromkeys(self.names, self._route)

    def register(self, hooks: HookRegistry) -> None:
        from app.agents.agent_loop.sandbox_bridge import CODING_SANDBOX_PATH_PATTERN
        from harness_core.kernel.hooks.events import HookEvent

        # Registered after the legacy bridge: it runs once the bridge has registered the outputs.
        hooks.on(HookEvent.POST_TOOL_USE).use(CODING_SANDBOX_PATH_PATTERN, self._settle)  # pyright: ignore[reportUnknownMemberType]

    async def _route(self, call: ToolCall) -> RouteResult:
        ctx = current_tool_context()
        if ctx is None:
            return RouteResult(await self._execute(call))
        key = ctx.idempotency_key
        found = await self._receipts.get(key)
        if found is not None:
            self._pending[key] = _Pending(found.started_at, done=found.state == "done")
            self._log.info("run_code.recorded", invocationId=ctx.invocation_id, tool=call.name, state=found.state)
            return RouteResult(await self._recorded(call, found))
        started = ExecutionReceipt("started", self._clock.now())
        try:
            await self._receipts.put(key, started)
        except Exception as exc:
            # Without a receipt a re-driven attempt could run the program twice.
            self._log.warning(
                "run_code.receipt_unavailable",
                reason="receipt_unavailable",
                invocationId=ctx.invocation_id,
                errorType=type(exc).__name__,
            )
            return RouteResult(_error(call, "run_code_unavailable: the call could not be recorded; try again"))
        await afailpoint("worker.run_code.after_started")
        self._pending[key] = _Pending(started.started_at, done=False)
        result, events = await self._shown(call, ctx.invocation_id)
        if not result.is_error:
            try:
                await self._receipts.put(
                    key, ExecutionReceipt("executed", started.started_at, _json(result.content))
                )
            except Exception as exc:
                self._log.warning(
                    "run_code.receipt_failed",
                    invocationId=ctx.invocation_id,
                    state="executed",
                    errorType=type(exc).__name__,
                )
            await afailpoint("worker.run_code.after_executed")
        return RouteResult(result, events=events)

    async def _shown(self, call: ToolCall, invocation_id: str) -> tuple[CoreToolResult, tuple[NewEvent, ...]]:
        """The call, with its output on the activity bus as it runs and its record for the journal."""
        if self._activity is None:
            return await self._execute(call), ()
        command, language = _command_of(call, self._typed_line)
        recorder = _Tracked(
            self._activity.begin(
                CommandSpec(invocation_id, "run_code", command, invocation_id=invocation_id, language=language)
            )
        )
        with recording(recorder):
            result = await self._execute(call)
        record = await finish_from_result(
            recorder.inner, result.content, is_error=result.is_error, streamed=recorder.streamed
        )
        if not self._journal_commands:
            return result, ()
        try:
            return result, (command_finished_event(record),)
        except ValueError as exc:  # a record the schema refuses costs the panel its row, never the call
            self._log.warning("run_code.command_unrecorded", invocationId=invocation_id, errorType=type(exc).__name__)
            return result, ()

    async def _execute(self, call: ToolCall) -> CoreToolResult:
        """The tool as the executor runs it: arguments validated, a raise is an error result."""
        from harness_core.tools.errors import ToolValidationError

        tool = self._registry.resolve_by_name(call.name)
        arguments = dict(call.arguments)
        try:
            tool.validate(arguments)
        except ToolValidationError as exc:
            return _error(call, str(exc))
        try:
            output = await tool.execute(**arguments)
        except Exception as exc:
            # The code by the cause chain: a sandbox broker that is down is ``unavailable``, not the call's fault.
            return _error(call, str(exc), tool_error_code(exc))
        if not output.success:
            return _error(call, output.error or "tool execution failed", output.error_code)
        # What the program printed reaches the model, the journal and the receipt as the activity panel shows it.
        content = redact_tokens_in(cast("JsonValue", output.data))
        return CoreToolResult(tool_call_id=call.id, name=call.name, content=content, sources=list(output.sources))

    async def _settle(self, ctx: Any, next_fn: Any) -> None:  # noqa: ANN401 - ToolResultContext, the next middleware
        tool_ctx = current_tool_context()
        pending = self._pending.pop(tool_ctx.idempotency_key, None) if tool_ctx is not None else None
        if tool_ctx is not None and pending is not None and not pending.done:
            await afailpoint("worker.run_code.before_done")
            response = ctx.tool_response
            receipt = ExecutionReceipt(
                "done",
                pending.started_at,
                _json(redact_tokens_in(cast("JsonValue", response.data)))
                if response.success
                else redact_tokens(response.error or "tool execution failed"),
                is_error=not response.success,
            )
            try:
                await self._receipts.put(tool_ctx.idempotency_key, receipt)
            except Exception as exc:
                self._log.warning(
                    "run_code.receipt_failed",
                    invocationId=tool_ctx.invocation_id,
                    state="done",
                    errorType=type(exc).__name__,
                )
            await afailpoint("worker.run_code.after_done")
        await next_fn()

    async def _recorded(self, call: ToolCall, receipt: ExecutionReceipt) -> CoreToolResult:
        if receipt.state == "started":
            return _error(call, INTERRUPTED_MESSAGE)
        if receipt.is_error:
            return _error(call, str(receipt.content))
        content = receipt.content
        if not isinstance(content, dict):
            return CoreToolResult(tool_call_id=call.id, name=call.name, content=content)
        data: dict[str, Any] = dict(content)
        # The sandbox died with its attempt; without its id the bridge does not try to read it again.
        data.pop("sandbox_id", None)
        if receipt.state == "executed":
            data["recovered_note"] = RECOVERED_NOTE
            await self._recover_outputs(data, receipt.started_at)
        else:
            await self._redeliver(data)
        return CoreToolResult(tool_call_id=call.id, name=call.name, content=data)

    async def _redeliver(self, data: dict[str, Any]) -> None:
        """A finished call's artifacts, delivered to this attempt's answer once each."""
        listed = cast("list[object]", data.get("artifacts") or [])
        blocks = [cast("dict[str, Any]", b) for b in listed if isinstance(b, dict)]
        blocks = [b for b in blocks if b.get("artifact_id")]
        if not blocks:
            return
        found = [await self._resolve(str(b["artifact_id"]), b.get("version")) for b in blocks]
        await self._deliver([m for m in found if m is not None])

    async def _recover_outputs(self, data: dict[str, Any], started_at: datetime) -> None:
        """An unfinished call's ``$OUTPUT_DIR`` files: those the dead attempt registered are delivered."""
        from app.agents.agent_loop.sandbox_bridge import (
            _artifact_names_for,  # pyright: ignore[reportPrivateUsage]
            _partition_sandbox_outputs,  # pyright: ignore[reportPrivateUsage]
        )

        paths = [p for p in cast("list[object]", data.get("artifacts") or []) if isinstance(p, str)]
        deliverables, _scratch = _partition_sandbox_outputs(paths)
        names = _artifact_names_for([(p, b"") for p in deliverables])
        registry = self._context.artifact_registry
        saved: list[Any] = []
        unsaved: list[dict[str, str]] = []
        since_ms = int(started_at.timestamp() * 1000)
        actor = self._context.artifact_actor
        for path in deliverables:
            name = names[path]
            metadata = None
            if registry is not None and self._context.conversation_id:
                metadata = await registry.resolve_by_logical_name(
                    actor, name, conversation_id=self._context.conversation_id
                )
            if (
                metadata is not None
                and metadata.source_tool == f"/toolsets/coding_sandbox/{RUN_CODE}"
                and (metadata.updated_at or 0) >= since_ms
            ):
                saved.append(metadata)
            else:
                unsaved.append({"file": name, "error": "not saved before the worker stopped"})
        data["artifacts"] = await self._deliver(saved)
        if unsaved:
            data["artifact_errors"] = unsaved
            data["artifact_errors_note"] = UNSAVED_NOTE

    async def _resolve(self, artifact_id: str, version: object) -> Any:  # noqa: ANN401 - ArtifactMetadata | None
        from app.services.artifact_registry.access import (
            AccessDeniedError,
            ArtifactNotFoundError,
        )

        registry = self._context.artifact_registry
        if registry is None:
            return None
        actor = self._context.artifact_actor
        try:
            metadata = await registry.resolve(
                actor=actor, ref=artifact_id, conversation_id=self._context.conversation_id
            )
        except (ArtifactNotFoundError, AccessDeniedError):
            self._log.warning("run_code.recorded_artifact_missing", reason="artifact_missing")
            return None
        if isinstance(version, int) and version != metadata.version:
            return metadata.model_copy(update={"version": version})
        return metadata

    async def _deliver(self, found: list[Any]) -> list[dict[str, Any]]:
        from app.agents.agent_loop.sandbox_bridge import (
            deliver_artifact,
            register_artifact_markers,
        )

        registry = self._context.artifact_registry
        if registry is None or not found:
            return []
        actor = self._context.artifact_actor
        blocks: list[dict[str, Any]] = []
        entries: list[dict[str, Any]] = []
        for metadata in found:
            block, entry = await deliver_artifact(self._context, registry, actor, metadata)
            blocks.append(block)
            if entry is not None:
                entries.append(entry)
        if blocks:
            self._context.artifacts_produced_this_run = True
        register_artifact_markers(self._context, entries)
        return blocks


def _error(call: ToolCall, message: str, code: ToolErrorCode | None = None) -> CoreToolResult:
    return CoreToolResult(
        tool_call_id=call.id, name=call.name, content=redact_tokens(message), is_error=True, error_code=code
    )


__all__ = [
    "INTERRUPTED",
    "INTERRUPTED_MESSAGE",
    "RECEIPTED_TOOLS",
    "RUN_CODE",
    "ReceiptedSandboxTools",
]
