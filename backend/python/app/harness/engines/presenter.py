"""``PipesHubEngineRunPresenter``: a durable engine run's progress as the chat's task card shows it (E1, U1).

Each frame (``progress.py``) is journaled as ``x.pipeshub.engine.progress``
(non-critical, the frame plus the turn and the sequence it was journaled
under), and Node's projector turns it into the ``CUSTOM engine_progress``
frame the card reads. Engine approvals get an ``approval`` line
naming the interaction, the tool and its arguments, which is what the approval
card needs (P2.6c FB-7).
"""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from app.harness.activity import command_finished_event
from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.engines.capability_limits import InputsAccepted
from app.harness.engines.progress import (
    SHOWN_OUTPUT_CHARS,
    EngineProgress,
    artifact_progress,
    bounded,
    finished_progress,
    frame,
    progress_for,
    revision_progress,
    started_progress,
    tool_result_progress,
)
from app.harness.step_display import (
    StepFacts,
    call_facts,
    result_facts,
    shown_arguments,
)
from app.utils.answer_links import link_workspace_files
from harness_core.activity import (
    ActivityLimits,
    CommandSpec,
    StreamRedactor,
    shared_redactor,
)
from harness_core.contracts import validate_contract
from harness_core.effects.preview import preview_effect
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    PlanUpdated,
    ToolCallSeen,
    ToolCallUpdated,
)
from harness_core.gateway.asking import ASK_USER_NAMES
from harness_core.gateway.disclosure import shown_arguments as asked_arguments
from harness_core.gateway.disclosure import shown_tool
from harness_core.journal.types import NewEvent
from harness_core.kernel.task_ledger import TaskLedger
from harness_core.logs import HarnessLog
from harness_core.workspace.forks import session_scope
from harness_core.workspace.types import ObjectScope, WorkspaceError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import BaseModel, JsonValue

    from app.harness.repositories import NamedRepository
    from app.harness.step_display import GatewaySteps
    from harness_core.activity import CommandActivity, CommandRecorder
    from harness_core.artifacts.types import ArtifactCard
    from harness_core.budget.run_budget import PhasePlan
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.engines.durable.ports import ProgressStamp
    from harness_core.engines.events import EngineEvent, NativeUnknown
    from harness_core.engines.native import TranscriptPart
    from harness_core.gateway.asking import AskedQuestion
    from harness_core.gateway.types import McpResult
    from harness_core.journal.types import JournalEvent
    from harness_core.tools.base import Tag
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectRef, WorkspaceFile, WorkspaceManifest

PROGRESS = "x.pipeshub.engine.progress"
# What an engine sent natively (C2.39, C2.40): kept for audit and re-parsing, never a line of the card or the activity.
NATIVE_UNKNOWN = "x.pipeshub.native.unknown"
NATIVE_TRANSCRIPT = "x.pipeshub.native.transcript"
ANSWER = "x.pipeshub.answer.finalized"
QUESTION = "x.pipeshub.question.asked"
# What the loop's own question ending says it is (``presentation._clarify``).
CLARIFICATION = "Clarification Needed"
LOGGER = "app.harness.engines.presenter"
# The event's own bound on a line's text (its schema).
MAX_LINE_CHARS = 20_000
# A step's arguments behind "Details", bounded as the loop bounds a call's.
SHOWN_ARGS_CHARS = 2_000

# ADR-RUN-01 D4: the notice a turn's time plan is shown with (``x.pipeshub.run.notice`` ``time_plan``).
RUN_NOTICE = "x.pipeshub.run.notice"
TIME_PLAN = "time_plan"


def _timestamp(epoch_s: float) -> str:
    """The contract's timestamp (RFC 3339, UTC, milliseconds, a literal Z)."""
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True)
class SessionRevisions:
    """A durable run's revisions: parents from its workspace store, contents from the session's sealed objects
    (every fork of a session shares its object scope)."""

    workspaces: WorkspaceStore
    objects: BinaryObjectStore
    tenant: str
    session_id: str

    async def parent(self, manifest: WorkspaceManifest) -> Mapping[str, WorkspaceFile]:
        if manifest.parent_revision is None:
            return {}
        previous = await self.workspaces.revision(ObjectScope(self.tenant), manifest.workspace_id, manifest.parent_revision)
        return previous.files

    async def read(self, ref: ObjectRef) -> bytes:
        return await self.objects.get(session_scope(self.tenant, self.session_id), ref)


def _native_event(event_type: str, data: dict[str, JsonValue]) -> NewEvent:
    model = EVENT_MODELS[event_type]
    _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), data)
    return NewEvent(event_type, data, critical=False)


class PipesHubEngineRunPresenter:
    def __init__(
        self,
        revisions: SessionRevisions | None = None,
        *,
        log: HarnessLog | None = None,
        label: Callable[[], dict[str, JsonValue]] | None = None,
        recalled: NewEvent | None = None,
        mounted: NewEvent | None = None,
        answer: Callable[[str], tuple[str, list[dict[str, Any]]]] | None = None,
        commands: CommandActivity | None = None,
        journal_commands: bool = False,
        display_name: Callable[[str], str | None] | None = None,
        tags_of: Callable[[str], Iterable[Tag]] | None = None,
        steps: GatewaySteps | None = None,
        tool_named: Callable[[str], str | None] | None = None,
    ) -> None:
        self._revisions = revisions
        # R41-CLONE: the repositories the run's task names, each one it never cloned led with in its answer.
        self._named: tuple[NamedRepository, ...] = ()
        self._typed_uncloned = True
        # R46FIX-2, R46FIX-FU-2: how the claim's Node reads the files the job went on without.
        self._accepted_inputs = InputsAccepted()
        # The engine's shell commands live on the activity bus and as records (UX5).
        self._commands = commands
        self._journal_commands = journal_commands
        self._recorders: dict[str, CommandRecorder] = {}
        # Progress lines carry command text and output too: redacted with the same hold-back, per command and stream.
        self._redact = shared_redactor()
        self._shown_output: dict[tuple[str, str], StreamRedactor] = {}
        self._plan = TaskLedger()
        # What governed memory gave the run (P7.3), journaled with its first turn.
        self._recalled = recalled
        # The labels of the agent-written skills it mounts (FOLLOWUPS1-1), journaled before the engine can read them.
        self._mounted = mounted
        self._log = log or HarnessLog.named(LOGGER)
        # The run's turn label (P2.5a), asked of its session ledger when the run finishes.
        self._label = label
        # The engine's text with its citations resolved against the run's citation state (E2E3-F3).
        self._answer = answer
        # A tool step is named as the loop names it (CTL-4): the tool's display name, when the run's registry has one.
        self._display_name = display_name
        # A tool's declared per-call cost rides its approval card (C3.71).
        self._tags_of = tags_of
        # The files this run published, by name, so its answer links them instead of their sandbox path.
        self._files: dict[str, str] = {}
        # The gateway names no engine call id: a result goes to the step of its tool, still waiting for one, that asked
        # with the same arguments (else the oldest), or waits for that step when the engine reports it after the call
        # returned.
        self._awaiting: dict[str, deque[_Waiting]] = {}
        self._early: dict[str, deque[_Early]] = {}
        # R30ENG-FU-1: the tool a name the engine called stands for, as the gateway resolves it (a tool's own name at its
        # server, ``create_ticket``, runs as ``mcp_e2esaas_create_ticket``): an ask names the resolved tool, the engine's
        # step the name it called. ``None``: the name is no known tool's.
        self._tool_named = tool_named
        # Asks journaled before the engine's line for the call they gate (its start can reach the worker after the
        # gateway held the call): the line, when it comes, names the ask, so the ask is that step, never a second one.
        self._asked_early: dict[str, deque[_EarlyAsk]] = {}
        # What each gateway call's ToolResult said (WP-112), by invocation; and each step's facts as last shown.
        self._steps = steps
        self._facts: dict[str, StepFacts] = {}
        self._served: set[str] = set()

    def _line(self, stamp: ProgressStamp, shown: EngineProgress) -> NewEvent:
        value = frame(stamp.task_id, shown)
        text = str(value["text"])[:MAX_LINE_CHARS]
        data = cast("dict[str, JsonValue]", json.loads(json.dumps(value["data"], default=str)))
        payload: dict[str, Any] = {
            **value,
            "text": text,
            "data": data,
            "turnId": stamp.turn_id,
            "seq": stamp.allocate(),
        }
        model = EVENT_MODELS[PROGRESS]
        _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), payload)
        return NewEvent(PROGRESS, cast("dict[str, JsonValue]", payload), critical=False)

    def started(self, stamp: ProgressStamp, goal: str) -> list[NewEvent]:
        line = self._line(stamp, started_progress(goal))
        return [e for e in (line, self._recalled, self._mounted) if e is not None]

    def planned(self, stamp: ProgressStamp, plan: PhasePlan) -> list[NewEvent]:
        """ADR-RUN-01 D4: the turn's time plan as the chat's notice (``time_plan``): when it starts writing at the
        latest and when its work must be finished."""
        data: dict[str, JsonValue] = {
            "code": TIME_PLAN,
            "writeBy": _timestamp(plan.write_by_s),
            "finishBy": _timestamp(plan.stop_by_s),
        }
        return [NewEvent(RUN_NOTICE, data, critical=False)]

    def event(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        if isinstance(event, PlanUpdated):
            return self._tasks(event)
        lines: list[NewEvent] = []
        match event:
            case CommandStarted():
                event = dataclasses.replace(event, command=self._redact(event.command))
            case CommandOutput():
                text = self._output_redactor(event.call_id, event.stream).feed(event.text)
                if not text:
                    return []
                event = dataclasses.replace(event, text=text)
            case CommandFinished():
                # The held-back tail is already redacted: fed again it would be held again and lost (a last line with
                # no newline, R31JOURNEY-FU-1).
                for stream in ("stdout", "stderr"):
                    rest = self._shown_output.pop((event.call_id, stream), None)
                    tail = rest.flush() if rest is not None else ""
                    shown_tail = progress_for(CommandOutput(event.meta, event.call_id, tail, stream)) if tail else None
                    if shown_tail is not None:
                        lines.append(self._line(stamp, shown_tail))
            case _:
                pass
        if isinstance(event, (ToolCallSeen, ToolCallUpdated)):
            if _is_ask_user(event.tool):
                # The question card is the step: a tool line for it would show the meta tool (activity clarity).
                return lines
            return lines + self._tool_step(stamp, event)
        shown = progress_for(event)
        if shown is None:
            return lines
        lines.append(self._line(stamp, shown))
        return lines

    def _tool_step(self, stamp: ProgressStamp, event: ToolCallSeen | ToolCallUpdated) -> list[NewEvent]:
        """A tool step's lines with its facts (WP-112): what it asked and, for the engine's own tools, what came back.
        Facts sent after the start (``ToolCallUpdated``) are a ``tool_update`` line of the same step, never a second
        ``tool`` line."""
        shown = progress_for(event)
        assert shown is not None
        data: dict[str, Any] = dict(shown.data)
        event = dataclasses.replace(event, arguments=asked_arguments(event.tool, event.arguments))
        known = event.call_id in self._facts or any(w.call_id == event.call_id for w in self._awaiting.get(event.tool, ()))
        if isinstance(event, ToolCallUpdated) or (event.status == "started" and known):
            return [self._line(stamp, EngineProgress("tool_update", shown.text, self._asked(event, data)))]
        if event.status == "started":
            asked = self._asked(event, data)
            early_ask = self._early_ask(event.tool, event.arguments)
            if early_ask is not None:
                asked["interactionId"] = early_ask.interaction_id
            lines = [self._line(stamp, EngineProgress("tool", shown.text, asked))]
            early = self._early.get(event.tool)
            found = _matching(early, event.arguments) if early else None
            if early and found is not None:
                early.remove(found)
                lines.append(self._result_line(stamp, event.tool, event.call_id, found.result))
            else:
                self._awaiting.setdefault(event.tool, deque()).append(_Waiting(event.call_id, event.arguments))
            return lines
        waiting = self._awaiting.get(event.tool)
        if waiting:
            for entry in [w for w in waiting if w.call_id == event.call_id]:
                waiting.remove(entry)
        facts = self._facts.pop(event.call_id, None)
        served = event.call_id in self._served
        self._served.discard(event.call_id)
        if facts is None and event.arguments is not None:
            facts = call_facts(event.tool, event.arguments, title=event.title, paths=event.paths)
        if not served and event.result is not None:
            # One of the engine's own tools (a gateway tool's result came from the gateway, on its own line).
            text, cut = bounded(self._redact(event.result), SHOWN_OUTPUT_CHARS)
            data |= {"result": text, "truncated": cut}
            if facts is not None:
                facts = result_facts(facts, text=event.result, failed=event.status == "failed")
        if facts is not None and not served:
            data["step"] = self._redacted_step(facts)
        return [self._line(stamp, EngineProgress("tool_result", shown.text, data))]

    def _asked(self, event: ToolCallSeen | ToolCallUpdated, data: dict[str, Any]) -> dict[str, Any]:
        """What the step asked, on its ``tool`` or ``tool_update`` line: its name, its arguments and its facts. An
        update also makes the step still waiting for its gateway result ask with its new arguments."""
        display = self._display_name(self._resolved(event.tool)) if self._display_name is not None else None
        if display:
            data["displayName"] = display
        args = shown_arguments(event.arguments, SHOWN_ARGS_CHARS)
        if args is not None:
            data["args"] = self._redact(args)
        facts = call_facts(event.tool, event.arguments, title=event.title, paths=event.paths)
        if facts is not None:
            known = self._facts.get(event.call_id)
            # A result the gateway already joined keeps what it said of the call (a title, a count).
            facts = _joined(facts, known) if known is not None else facts
            self._facts[event.call_id] = facts
            data["step"] = self._redacted_step(facts)
        self._refresh_waiting(event)
        return data

    def _resolved(self, called: str) -> str:
        """The tool the engine's name ``called`` runs as at the gateway; the name itself when none is known."""
        return (self._tool_named(called) if self._tool_named is not None else None) or called

    def _waiting_for(self, tool: str) -> deque[_Waiting] | None:
        """The steps still waiting on the gateway for ``tool``, under the name the engine called it by."""
        exact = self._awaiting.get(tool)
        if exact:
            return exact
        return next((w for name, w in self._awaiting.items() if w and self._resolved(name) == tool), None)

    def _early_ask(self, called: str, arguments: Mapping[str, JsonValue] | None) -> _EarlyAsk | None:
        """The ask already journaled for the call the engine reports now, taken: same tool, same arguments, else the
        oldest of that tool."""
        tool = self._resolved(called)
        asks = self._asked_early.get(tool) or self._asked_early.get(called)
        found = _matching(asks, arguments) if asks else None
        if asks and found is not None:
            asks.remove(found)
        return found

    def _refresh_waiting(self, event: ToolCallSeen | ToolCallUpdated) -> None:
        """The step waiting for its gateway result asks with the arguments the engine last sent."""
        waiting = self._awaiting.get(event.tool)
        if not waiting:
            return
        for index, entry in enumerate(waiting):
            if entry.call_id == event.call_id:
                waiting[index] = _Waiting(event.call_id, event.arguments)
                return

    def _redacted_step(self, facts: StepFacts) -> dict[str, Any]:
        wire = facts.wire()
        for key in ("target", "title"):
            if isinstance(wire.get(key), str):
                wire[key] = self._redact(wire[key])
        return wire

    def native_unknown(self, stamp: ProgressStamp, event: NativeUnknown, content: ContentRef) -> list[NewEvent]:
        return [
            _native_event(
                NATIVE_UNKNOWN,
                {
                    "turnId": stamp.turn_id,
                    "kind": event.kind,
                    "sizeBytes": event.size_bytes,
                    "truncated": event.truncated,
                    "content": content.model_dump(mode="json"),
                },
            )
        ]

    def native_transcript(self, stamp: ProgressStamp, part: TranscriptPart, content: ContentRef) -> list[NewEvent]:
        return [
            _native_event(
                NATIVE_TRANSCRIPT,
                {
                    "turnId": part.turn_id,
                    "part": part.index,
                    "lines": part.lines,
                    "sizeBytes": len(part.data),
                    "final": part.final,
                    "truncated": part.truncated,
                    "content": content.model_dump(mode="json"),
                },
            )
        ]

    def tool_result(
        self, stamp: ProgressStamp, tool: str, arguments: Mapping[str, JsonValue], result: McpResult
    ) -> list[NewEvent]:
        name = shown_tool(tool, arguments)
        arguments = asked_arguments(name, arguments) or {}
        waiting = self._awaiting.get(name)
        found = _matching(waiting, arguments) if waiting else None
        if not waiting or found is None:
            self._early.setdefault(name, deque()).append(_Early(arguments, result))
            return []
        waiting.remove(found)
        return [self._result_line(stamp, name, found.call_id, result)]

    def _result_line(self, stamp: ProgressStamp, tool: str, call_id: str, result: McpResult) -> NewEvent:
        """The gateway's answer on the engine's step, as the loop's step shows a result: its summary, the tool's view
        of it and the step's facts, named by the call's invocation (its ``tool.intent`` and its citations)."""
        failed = result.is_error or result.disposition not in ("executed", "recorded")
        self._served.add(call_id)
        line = tool_result_progress(tool, call_id, self._redact(result.text), failed=failed)
        data: dict[str, Any] = dict(line.data)
        if result.invocation_id is not None:
            data["invocationId"] = result.invocation_id
        kept = self._steps.take(result.invocation_id) if self._steps is not None else None
        facts = self._facts.get(call_id) or (kept.facts if kept is not None else None) or call_facts(tool, None)
        if kept is not None and kept.facts is not None and facts is not None:
            facts = _joined(facts, kept.facts)
        elif facts is not None:
            facts = result_facts(
                facts, text=result.text, sources=((c.url, None) for c in result.citations), failed=failed
            )
        if facts is not None:
            self._facts[call_id] = facts
            data["step"] = self._redacted_step(facts)
        if kept is not None:
            if kept.args_summary:
                data["argsSummary"] = self._redact(kept.args_summary)
            if kept.result_summary and not failed:
                data["resultSummary"] = self._redact(kept.result_summary)
            if kept.result_view is not None and not failed:
                data["resultView"] = kept.result_view
        return self._line(stamp, EngineProgress("tool_result", line.text, data))

    def _output_redactor(self, call_id: str, stream: str) -> StreamRedactor:
        found = self._shown_output.get((call_id, stream))
        if found is None:
            limits = ActivityLimits()
            found = StreamRedactor(self._redact, hold_chars=limits.hold_chars, key_hold_chars=limits.key_hold_chars)
            self._shown_output[(call_id, stream)] = found
        return found

    def _tasks(self, plan: PlanUpdated) -> list[NewEvent]:
        """The engine's task list as core ``task.*`` events (``task-<n>``): what changed, a dropped item cancelled."""
        return self._plan.replace(plan.items)

    async def command(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        commands = self._commands
        if commands is None:
            return []
        match event:
            case CommandStarted():
                spec = CommandSpec(
                    event.call_id, "engine", event.command, task_id=stamp.task_id,
                    output_may_be_lost=event.output_may_be_lost,
                )
                self._recorders[event.call_id] = commands.begin(spec)
                return []
            case CommandOutput():
                recorder = self._recorders.get(event.call_id)
                if recorder is not None:
                    recorder.write(event.stream, event.text)
                return []
            case CommandFinished():
                recorder = self._recorders.pop(event.call_id, None)
                if recorder is None:
                    return []
                record = await recorder.finish(exit_code=event.exit_code, status=event.status)
                if not self._journal_commands:
                    return []
                try:
                    return [command_finished_event(record)]
                except ValueError as exc:  # the panel loses a row, never the run its progress
                    self._log.warning("engine.command_unrecorded", errorType=type(exc).__name__)
                    return []
            case _:
                return []

    async def saved(self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason) -> list[NewEvent]:
        revisions = self._revisions
        if revisions is None:
            return []
        try:
            frames = await revision_progress(await revisions.parent(manifest), manifest, reason, revisions.read)
        except (WorkspaceError, FileNotFoundError, LookupError) as exc:  # a view of the save, never a reason to stop
            self._log.info("engine.revision_unshown", errorType=type(exc).__name__)
            return []
        return [self._line(stamp, shown) for shown in frames]

    def artifact(self, stamp: ProgressStamp, card: ArtifactCard) -> list[NewEvent]:
        if card.lifecycle == "discarded":
            self._files.pop(card.name, None)
        else:
            self._files[card.name] = card.artifact_id
        return [self._line(stamp, artifact_progress(card))]

    def approval(
        self, stamp: ProgressStamp, interaction_id: str, tool: str, arguments: Mapping[str, JsonValue]
    ) -> list[NewEvent]:
        args = json.dumps(dict(arguments), sort_keys=True, default=str)
        data: dict[str, JsonValue] = {"interactionId": interaction_id, "toolName": tool, "argsJson": args}
        # The step the engine opened for this call: the ask is that step, never a second one beside it.
        asked = asked_arguments(tool, arguments)
        waiting = self._waiting_for(tool)
        gated = _matching(waiting, asked) if waiting else None
        if gated is not None:
            data["callId"] = gated.call_id
        else:
            self._asked_early.setdefault(tool, deque()).append(_EarlyAsk(interaction_id, asked))
        # The card names the server as the loop's card does ("E2Esaas · create_ticket"), not by its gateway id.
        display = self._display_name(tool) if self._display_name is not None else None
        if display:
            data["displayName"] = display
        # What the call would change and cost (C3.71); the card's UI for it waits on the owner's mock review.
        preview = preview_effect(arguments, self._tags_of(tool) if self._tags_of is not None else ())
        if preview is not None:
            data["effect"] = preview.wire()
        return [self._line(stamp, EngineProgress("approval", tool, data))]

    def question(self, stamp: ProgressStamp, asked: AskedQuestion) -> list[NewEvent]:
        """The loop's question card (``ask_user_question``'s payload), so the chat renders and answers it the same
        way: the answer is the person's next message, which starts the session's next run."""
        from app.agents.actions.internal_tools.intrim_tools import question_payload
        from app.harness.presentation import question_asked

        payload = question_payload(
            "", [{"question": asked.question, "options": [{"label": o} for o in asked.options], "multiSelect": False}]
        )
        # Its card id is the question's own, so a re-driven turn's card replaces the first rather than adding one.
        payload["questions"][0]["uuid"] = asked.call_id
        data = cast("dict[str, JsonValue]", question_asked(asked.call_id, "success", payload))
        model = EVENT_MODELS[QUESTION]
        _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), data)
        self._log.info("engine.question_asked", callId=asked.call_id, options=len(asked.options))
        return [NewEvent(QUESTION, data, critical=False)]

    def expect_repositories(self, named: tuple[NamedRepository, ...], *, typed: bool) -> None:
        """The repositories the run's task names (R41-CLONE); ``typed``: the claim's Node reads ``repository.not_cloned``."""
        self._named = named
        self._typed_uncloned = typed

    def expect_inputs(self, accepted: InputsAccepted) -> None:
        """How the claim's Node reads the files the job went on without (R46FIX-2, R46FIX-FU-2)."""
        self._accepted_inputs = accepted

    def awaiting_answer(self, stamp: ProgressStamp, output: str) -> list[NewEvent]:
        """The run's ending while the person's answer is awaited: the loop's ``needs_input`` answer."""
        events = self.finished(stamp, success=True, reason=None, output=output)
        return [
            NewEvent(ANSWER, {**e.data, "outcome": "needs_input", "answerMatchType": CLARIFICATION}, critical=False)
            if e.type == ANSWER
            else e
            for e in events
        ]

    def linked(self, text: str) -> str:
        """``LinksFiles``: the engine's text with each sandbox file it names linked to the artifact this run
        published under that name, or named in plain words when it published none."""
        return link_workspace_files(text, self._files)

    def finished(
        self,
        stamp: ProgressStamp,
        *,
        success: bool,
        reason: str | None,
        output: str,
        truncated_by: str | None = None,
        budget_scope: str | None = None,
        journal: Sequence[JournalEvent] = (),
    ) -> list[NewEvent]:
        line = self._line(stamp, finished_progress(success=success, reason=reason, output=output))
        if not success:
            return [line]
        # The answer the conversation keeps and the stream's final frame read (as the loop's finalizer writes it).
        # ``output`` is the kernel's final text, already ``linked`` (MR-18).
        text, citations = self._answer(output) if self._answer is not None else (output, [])
        answer: dict[str, JsonValue] = {
            "outcome": "answered",
            "answer": text,
            "partTexts": [text],
            "citations": cast("list[JsonValue]", citations),
        }
        if self._label is not None:
            answer["label"] = self._label()
        # ADR-RUN-03 D5 (R32REPO-FU-1): the answer leads with the clones its journal shows did not happen.
        from app.harness.engines.capability_limits import (
            met_items,
            refused_clones,
            skipped_inputs,
            uncloned,
        )
        from app.harness.presentation import with_limits
        from app.harness.repositories import TOOL_ID as REPOSITORY_TOOL_ID

        refused = refused_clones(journal, REPOSITORY_TOOL_ID)
        met = (*skipped_inputs(journal, accepted=self._accepted_inputs), *refused, *uncloned(journal, self._named, refused, typed=self._typed_uncloned))
        with_limits(answer, met_items(met))
        if truncated_by is not None:
            # A limit ended the engine's turn: the chat shows the notice with the partial result (BUDGETEND).
            answer["truncatedBy"] = truncated_by
            if truncated_by == "budget" and budget_scope is not None:
                # Which shared budget ran out, so an extension's stop line can name it, as the loop's does (WP56-STOP-1).
                answer["budgetScope"] = budget_scope
        model = EVENT_MODELS[ANSWER]
        _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), answer)
        return [line, NewEvent(ANSWER, answer, critical=False)]


@dataclass(frozen=True, slots=True)
class _Waiting:
    call_id: str
    arguments: Mapping[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class _Early:
    arguments: Mapping[str, JsonValue] | None
    result: McpResult


@dataclass(frozen=True, slots=True)
class _EarlyAsk:
    interaction_id: str
    arguments: Mapping[str, JsonValue] | None


def _canonical(arguments: Mapping[str, JsonValue] | None) -> str | None:
    return json.dumps(dict(arguments), sort_keys=True, default=str) if arguments is not None else None


def _matching(entries: deque[Any], arguments: Mapping[str, JsonValue] | None) -> Any:  # noqa: ANN401
    """The entry asked with these arguments, else the oldest one (an engine that reports none, or another spelling):
    two reads of one tool in flight at once are told apart by what each asked, not by which returned first."""
    wanted = _canonical(arguments)
    if wanted is not None:
        same = next((e for e in entries if _canonical(e.arguments) == wanted), None)
        if same is not None:
            return same
    return entries[0] if entries else None


def _joined(asked: StepFacts, answered: StepFacts) -> StepFacts:
    """The engine's facts of what it asked with the gateway's of what came back."""
    return dataclasses.replace(
        asked,
        target=asked.target or answered.target,
        href=asked.href or answered.href,
        title=answered.title or asked.title,
        count=answered.count if answered.count is not None else asked.count,
        sources=answered.sources or asked.sources,
    )


def _is_ask_user(tool: str) -> bool:
    """The gateway's ``ask_user`` as an engine names it (bare or by its alias, or behind its MCP server's prefix)."""
    return tool in ASK_USER_NAMES or tool.endswith(tuple(f"{sep}{name}" for name in ASK_USER_NAMES for sep in ("__", ".", "/")))


__all__ = ["ANSWER", "MAX_LINE_CHARS", "PROGRESS", "QUESTION", "PipesHubEngineRunPresenter", "SessionRevisions"]
