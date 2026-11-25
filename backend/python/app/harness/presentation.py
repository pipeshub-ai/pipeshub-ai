"""How a durable PipesHub run is shown (REPORT 8): the legacy presentation, journaled.

The durable kernel journals facts; this ``RunPresenter`` journals how the
legacy stream showed them, as non-critical ``x.pipeshub.*`` events next to
the facts, so Node's projector renders the same frames and ``parts[]``
without any PipesHub logic of its own:

* model responses: the assistant text (so Node never reads the worker's
  content store) and, per tool call, the arguments the way ``TOOL_CALL_ARGS`` carried them, the
  bounded ``parts[]`` form, the tool's summary and display name;
* tool results: legacy's bounded preview (``tool_loop._result_preview``), status and summary, the
  ``ask_user_question`` card, and the artifacts the tool delivered (read from
  the marker tasks and live frames the existing tools produce);
* the answer: the legacy ``AnswerFinalizer`` over the run's text parts
  (citation normalization, confidence, model-authored marker stripping, the
  backend-authored artifact markers, the fallback and error shapes).

Artifacts are rebuilt at the end from the journal, not from process memory, so
a run resumed on another worker still delivers every file (REPORT 5.1).
Everything here is best effort: an event that fails its schema is dropped and
logged, never committed (a rejected commit would fail the run).
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from app.core.org_context import spawn_in_request
from app.harness import step_display
from app.harness.assembly.code_task import CODE_TASK, RESULT_PRESENTED
from app.harness.assembly.plan import PLAN_TOOL
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import TELEMETRY_EVENT
from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.grounding import GROUNDING_CARD_KEY
from app.harness.labels import label_wire, own_work
from app.harness.memories import RECALLED_EVENT, recalled_data
from harness_core.contracts import validate_contract
from harness_core.journal.types import NewEvent
from harness_core.kernel.agent.tool_loop import (
    RESULT_PREVIEW_CHARS as TOOL_LOOP_PREVIEW_CHARS,
)
from harness_core.kernel.agent.tool_loop import (
    _result_preview,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.logs import HarnessLog
from harness_core.tools.call_tool import unwrapped

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import BaseModel, JsonValue

    from app.harness.citations import CitationJournal
    from app.harness.grounding import Grounding
    from app.harness.labels import TurnLabel
    from harness_core.engines.outcome.loop import LoopSettled
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import AssistantMessage, ToolCall
    from harness_core.kernel.core.types import ToolResult
    from harness_core.kernel.durable.ports import RunEnding
    from harness_core.provenance.types import Integrity
    from harness_core.tools.registry import ToolRegistry

# `tool_loop.execute_tool_call` bounds what the stream shows of a result.
RESULT_PREVIEW_CHARS = TOOL_LOOP_PREVIEW_CHARS
# Internal tools the legacy stream never shows (`tool_loop._NO_EMIT_TOOLS`), and the plan tool: the chat shows its
# list as the plan checklist (`task.*`), never as a step named after the tool (R49-PLAN).
HIDDEN_TOOLS = frozenset({"retrieve_artifact_content", PLAN_TOOL})
# The meta tool whose unavailable hits the stream shows (`SearchToolsTool.handle`).
SEARCH_TOOLS = "search_tools"
# The call id of a question the pre-run asked (no tool call made it).
PRE_RUN_QUESTION = "pre-run"
QUESTION_ASKED = "x.pipeshub.question.asked"
# What legacy's ask_user_question outcome carries as `needs_input` (`_default_terminal_outcome`).
_WAITING_FOR_ANSWERS = "Waiting for user answers"
_MODEL_NAME = re.compile(r"[^A-Za-z0-9_-]")
# The CUSTOM frames `includeRetrievalContext` turns on (protocol/retrieval_context.py, protocol/run_usage.py).
TELEMETRY_FRAMES = frozenset({"retrieval_context", "run_usage"})


def _streamed_answer(message: AssistantMessage) -> str:
    """The answer a tool call delivered as its argument (``final_answer``),
    which legacy streams into the turn's text message."""
    from harness_core import bindings

    resolve = bindings.current().streamed_answer_argument
    if resolve is None:
        return ""
    parts: list[str] = []
    for call in message.tool_calls or []:
        argument = resolve(call.name)
        value = call.arguments.get(argument) if argument else None
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)


def _streams_answer(name: str) -> bool:
    from harness_core import bindings

    resolve = bindings.current().streamed_answer_argument
    return resolve is not None and bool(resolve(name))


class _CapturedFrames:
    """The ``EventSink`` legacy tools write live frames to (artifact cards)."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> None:
        self.frames.append(event)

    async def flush(self) -> None:
        return None

    def take(self) -> list[dict[str, Any]]:
        taken, self.frames = self.frames, []
        return taken


class _DiscardSink:
    async def write(self, event: dict[str, Any]) -> None:
        return None

    async def flush(self) -> None:
        return None


def _live_artifacts(frames: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Artifact payloads from captured ``STATE_DELTA add /artifacts/-`` frames."""
    found: dict[str, dict[str, Any]] = {}
    for frame in frames:
        # `protocol.agui.frame()` shape: {"event": ..., "data": {..., "delta": [...]}}.
        body = frame.get("data") if isinstance(frame.get("data"), dict) else frame
        for op in body.get("delta") or []:
            if op.get("op") == "add" and op.get("path") == "/artifacts/-":
                value = op.get("value") or {}
                if value.get("artifactId"):
                    found[str(value["artifactId"])] = value
    return found


class PipesHubPresenter:
    def __init__(
        self,
        context: Any,  # noqa: ANN401 - the legacy AgentContext the tools run in
        registry: ToolRegistry,
        *,
        summarizer: Any = None,  # noqa: ANN401 - a ToolSummarizer
        log: HarnessLog | None = None,
        citations: CitationJournal | None = None,
        report_confidence: bool = True,
        max_turns: int | None = None,
        model: str | None = None,
    ) -> None:
        """``report_confidence``: the run's own confidence goes on the answer, as legacy's agent route
        reports it; its ``/chat/stream`` does not (``run_chat_stream``), so a chat run passes False."""
        from app.agents.agent_loop.tool_summarizer import PipesHubToolSummarizer

        self._report_confidence = report_confidence
        self._outcome: Callable[[], list[NewEvent]] | None = None
        self._settle: Callable[[RunEnding], LoopSettled | None] | None = None
        # What ``run_usage`` names (opt-in telemetry): the run's turn cap and model, as legacy's agent had them.
        self._max_turns = max_turns
        self._model = model
        self._context = context
        self._registry = registry
        self._summarizer = summarizer or PipesHubToolSummarizer()
        self._log = log or HarnessLog.named("app.harness.presentation")
        self._sink = _CapturedFrames()
        self._citations = citations
        self._other_tasks: list[dict[str, Any]] = []
        from app.agents.agent_loop.protocol.result_views import ResultViewBudget

        # Legacy's per-reply budget of result views (`result_views.py`), here per attempt.
        self._views = ResultViewBudget()
        # ADR-RUN-03 D1: the grounding question the goal step kept for a hand-off the model makes itself (its text and
        # typed card), read from the pre-run slots: its ``code_task`` is refused and the director asks it instead.
        self._pending_card: tuple[str, dict[str, Any]] | None = None
        # `has_ui_client` stays as the tool environment set it: the run shows a card by ending on needs_input, and
        # main's ask-in-text recovery (#4570) re-prompts only a run that can. The eager card frames the question
        # hook writes here are not journaled (`_question` journals the card).
        context.event_sink = self._sink
        context.tool_state["event_sink"] = self._sink
        context.tool_state["sse_protocol"] = "agui"

    def use_outcome(
        self,
        outcome: Callable[[], list[NewEvent]],
        *,
        settle: Callable[[RunEnding], LoopSettled | None] | None = None,
    ) -> None:
        """The run's outcome facts not journaled yet (ADR-RUN-01 D1: what it gathered, by the session's provenance
        ledger), journaled with its answer; ``settle``: for a journal whose chat words the outcome (``outcome_words``),
        a cut-short answer's settled outcome (WP-100), whose prose is then the answer saved."""
        self._outcome = outcome
        self._settle = settle

    # ---- events ----

    def _event(self, event_type: str, data: dict[str, Any]) -> NewEvent | None:
        model = cast("type[BaseModel]", EVENT_MODELS[event_type])
        data_model = cast("type[BaseModel]", model.model_fields["data"].annotation)
        clean = json.loads(json.dumps(data, default=str))
        try:
            _ = validate_contract(data_model, clean)
        except ValidationError as exc:
            self._log.warning(
                "presentation.dropped",
                eventType=event_type,
                reason="schema",
                errors=len(exc.errors()),
            )
            return None
        return NewEvent(event_type, clean, critical=False)

    def _tool(self, name: str) -> Any:  # noqa: ANN401
        try:
            return self._registry.resolve_by_name(name)
        except Exception:
            return None

    # ---- RunPresenter ----

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        """The goal the stream's state snapshots name (legacy ``goal_description``)."""
        from app.harness.assembly.code_task import NOTICE
        from app.harness.assembly.loop import GOAL_SLOT
        from app.harness.assembly.tool_environment import UNAVAILABLE_STEP
        from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX

        events: list[NewEvent | None] = []
        goal = slots.get(GOAL_SLOT)
        self._pending_card = _pending_card(goal)
        # A request the pre-run asks back never starts the agent: legacy shows no goal.
        if (
            isinstance(goal, dict)
            and isinstance(goal.get("description"), str)
            and not goal.get("clarifyingQuestions")
        ):
            events.append(
                self._event("x.pipeshub.goal.presented", {"description": goal["description"]})
            )
        events.append(self._citation_sources(None))
        # What governed memory gave the run (P7.3): the timeline shows it, the own-work label joins it.
        recalled = recalled_data(slots)
        if recalled is not None:
            events.append(self._event(RECALLED_EVENT, recalled))
        unavailable = slots.get(PRE_RUN_SLOT_PREFIX + UNAVAILABLE_STEP)
        for entry in unavailable if isinstance(unavailable, list) else []:
            if isinstance(entry, dict):
                notice = {"code": "credential_missing", "item": str(entry.get("item"))[:200], "kind": entry.get("kind")}
                events.append(self._event(NOTICE, notice))
        events.extend(self._stage("telemetry", self._telemetry))
        return [e for e in events if e is not None]

    def citation_sources(self) -> NewEvent | None:
        """What the citation state gained since the last event (e.g. a steering
        message's attachments), journaled with the commit that gained it."""
        return self._citation_sources(None)

    def citations_for(self, call_id: str) -> NewEvent | None:
        """What one call added to the citation state, for a caller that journals it with
        the call's outcome itself (the tool gateway); ``None`` once already taken."""
        return self._citation_sources(call_id)

    def _citation_sources(self, call_id: str | None) -> NewEvent | None:
        if self._citations is None:
            return None
        delta = self._citations.delta(call_id)
        return self._event(CITATIONS_EVENT, dict(delta)) if delta is not None else None

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        from app.agents.agent_loop.protocol.transcript_collector import (
            saved_args,
            serialize_tool_args,
        )

        streamed = _streamed_answer(message)
        presented: dict[str, Any] = {"text": message.text + streamed}
        if streamed:
            presented["answerFrom"] = len(message.text)
        reasoning = _reasoning_of(message)
        if reasoning:
            presented["reasoning"] = reasoning
        if message.truncated:
            presented["truncated"] = True
        shown = self._event("x.pipeshub.message.presented", presented)
        events: list[NewEvent] = [shown] if shown is not None else []
        for made in message.tool_calls or []:
            # The chat names the tool a call_tool calls, as the kernel runs it (R4).
            call = unwrapped(made)
            args = dict(call.arguments)
            args_json = json.dumps(args, default=str)
            bounded = serialize_tool_args(args)
            data: dict[str, Any] = {
                "callId": call.id,
                "toolName": _MODEL_NAME.sub("_", call.name)[:64] or "invalid",
                "argsJson": args_json,
            }
            if bounded != args_json:
                data["args"] = bounded
            saved, _, _ = saved_args(args, self._refs())
            if saved != bounded:
                data["savedArgs"] = saved
            if call.name in HIDDEN_TOOLS or (call.name == CODE_TASK and self._pending_card is not None):
                # FU-2: a hand-off refused while the person is asked first is part of asking, never a failed step.
                data["hidden"] = True
            if _streams_answer(call.name):
                data["streamsAnswer"] = True
            tool = self._tool(call.name)
            summary = self._args_summary(tool, call.name, args)
            if summary:
                data["argsSummary"] = summary
            display = getattr(tool, "display_name", None) if tool is not None else None
            if isinstance(display, str) and display:
                data["displayName"] = display
            step = step_display.step_of(call.name, args)
            if step is not None:
                data["step"] = step.wire()
            event = self._event("x.pipeshub.tool.call_presented", data)
            if event is not None:
                events.append(event)
        return events

    async def result(self, call: ToolCall, result: ToolResult) -> tuple[NewEvent, ...]:
        """For ``RegistryToolPort``: journaled with the call's outcome. Each stage is built on its own, so one
        that cannot be built never drops the artifacts and citation sources the others carry."""
        # Taken before the artifact stage drains the sink; journaled last, after the outcome's presentation.
        telemetry = self._stage("telemetry", self._telemetry)
        events: list[NewEvent | None] = []
        events.extend(self._stage("result", lambda: [self._result_event(call, result)]))
        events.extend(self._stage("question", lambda: self._question(call, result)))
        events.extend(self._stage("unavailable", lambda: [self._unavailable(call, result)]))
        try:
            events.extend(await self._artifacts(call))
        except Exception as exc:
            self._stage_failed("artifacts", exc)
        events.extend(self._stage("citations", lambda: [self._citation_sources(call.id)]))
        events.extend(telemetry)
        return tuple(e for e in events if e is not None)

    def _telemetry(self) -> list[NewEvent | None]:
        """The opt-in measurement frames (``includeRetrievalContext``) legacy code wrote to the sink, journaled
        for Node to show as the same CUSTOM frames (SYNC2-F1). None unless the run opted in."""
        frames = self._sink.frames
        bodies = [f.get("data") if isinstance(f.get("data"), dict) else f for f in frames]
        taken = [b for b in bodies if b.get("name") in TELEMETRY_FRAMES]
        if not taken:
            return []
        self._sink.frames = [f for f, b in zip(frames, bodies, strict=True) if b.get("name") not in TELEMETRY_FRAMES]
        return [self._event(TELEMETRY_EVENT, {"name": b["name"], "value": b.get("value")}) for b in taken]

    def _run_usage(self, ending: RunEnding) -> NewEvent | None:
        """Legacy's ``run_usage`` frame: the main loop's calls from the journal (every attempt's), the
        auxiliary calls (tools, critics, sub-agents) from this attempt's ledger."""
        from app.agents.agent_loop.protocol.run_usage import (
            RunUsagePayload,
            UsageLedger,
        )
        from harness_core.kernel.core.responses import TokenUsage
        from harness_core.kernel.core.usage_source import MAIN_SOURCE
        from harness_core.kernel.fold import UsageRecord, fold_run

        context = self._context
        if getattr(context, "include_retrieval_context", False) is not True:
            return None
        responses = fold_run(ending.events, run_id=context.run_id).responses
        main = [(MAIN_SOURCE, TokenUsage(**(r.usage or UsageRecord()).model_dump())) for r in responses]
        auxiliary = [(s, u) for s, u in context.usage_ledger.entries if s != MAIN_SOURCE]
        payload = RunUsagePayload.from_ledger(UsageLedger(entries=[*main, *auxiliary]), model=self._model)
        # Legacy's `AgentResult.turns`: every turn but a final text-only answer.
        payload.turns = len(responses) - (1 if responses and not responses[-1].tool_calls else 0)
        payload.maxTurns = self._max_turns
        payload.completionGateNudges = context.completion_gate_nudges
        payload.agentError = ending.error_type if ending.outcome == "error" else None
        return self._event(TELEMETRY_EVENT, {"name": "run_usage", "value": payload.to_wire_dict()})

    def _stage(self, stage: str, build: Callable[[], list[NewEvent | None]]) -> list[NewEvent | None]:
        try:
            return build()
        except Exception as exc:
            self._stage_failed(stage, exc)
            return []

    def _settled(self, ending: RunEnding) -> LoopSettled | None:
        if self._settle is None:
            return None
        try:
            return self._settle(ending)
        except Exception as exc:
            # The answer keeps its own words then, as an older journal's does.
            self._stage_failed("outcome", exc)
            return None

    def _stage_failed(self, stage: str, exc: Exception) -> None:
        self._log.warning("presentation.failed", stage=stage, errorType=type(exc).__name__)

    def _result_event(self, call: ToolCall, result: ToolResult) -> NewEvent | None:
        from app.agents.agent_loop.protocol.transcript_collector import saved_text

        # A failure is shown by its code, which Node reads off the outcome: neither the detail nor a summary made
        # from it reaches the person's stream (ERR-UX); the owner reads the detail in the activity panel.
        content = "" if result.is_error else _result_preview(result.content)
        data: dict[str, Any] = {
            "callId": call.id,
            "status": "failed" if result.is_error else "completed",
            "content": content,
        }
        refs = self._refs()
        saved, _, _ = saved_text(content, refs, RESULT_PREVIEW_CHARS)
        if saved != content:
            data["savedContent"] = saved
        summary = None if result.is_error else self._result_summary(
            self._tool(call.name), call.name, dict(call.arguments), result
        )
        if summary:
            data["resultSummary"] = summary
            saved, _, _ = saved_text(summary, refs)
            if saved != summary:
                data["savedSummary"] = saved
        meta = getattr(result, "artifact_meta", None)
        if meta is not None and getattr(meta, "artifact_id", None):
            data["artifactId"] = str(meta.artifact_id)
        if not result.is_error and (view := self._views.admit(self._result_view(call, result))):
            data["resultView"] = view
        # Named by the tool it ran, as its call was presented; a failure keeps what it asked, never its detail.
        ran = unwrapped(call)
        step = step_display.step_of(ran.name, dict(ran.arguments), result)
        if step is not None:
            data["step"] = step.wire()
        return self._event("x.pipeshub.tool.result_presented", data)

    def _refs(self) -> Mapping[str, str] | None:
        """This request's citation refs, which a saved part keeps as the URLs they stand for (legacy's collector):
        the live frames show the tool's own words, the saved part what a later request can still resolve."""
        from app.agents.agent_loop.protocol.transcript_collector import request_refs

        return request_refs(self._context.tool_state)

    def _result_view(self, call: ToolCall, result: ToolResult) -> Any:  # noqa: ANN401
        return step_display.result_view(self._tool(call.name), dict(call.arguments), result)

    def _question(self, call: ToolCall, result: ToolResult) -> list[NewEvent | None]:
        from app.agents.agent_loop.hooks.ask_user_question import (
            _ASK_USER_QUESTION_TOOL_NAMES,
        )

        if call.name not in _ASK_USER_QUESTION_TOOL_NAMES:
            return []
        payload: Any = result.content
        if isinstance(payload, str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                payload = json.loads(payload)
        if not isinstance(payload, dict | str):
            payload = str(payload)
        status = "error" if result.is_error else "success"
        if (
            isinstance(payload, dict)
            and self._pending_card is not None
            and _first_question(cast("dict[str, Any]", payload)) == self._pending_card[0]
        ):
            # The director's grounding question (ADR-RUN-03 D1): its typed fields, which the web chat words (FU-2).
            payload = {**payload, GROUNDING_CARD_KEY: self._pending_card[1]}
        return [self._event(QUESTION_ASKED, question_asked(call.id, status, payload))]

    def _unavailable(self, call: ToolCall, result: ToolResult) -> NewEvent | None:
        """What ``SearchToolsTool.handle`` emits as ``TOOL_UNAVAILABLE``: the
        hits that exist but this agent cannot call, one frame each."""
        content = result.content
        if call.name != SEARCH_TOOLS or result.is_error or not isinstance(content, dict):
            return None
        hits = [
            {
                "tool": str(hit.get("name") or ""),
                "toolset": str(hit.get("toolset") or ""),
                "reason": str(hit.get("reason") or ""),
                "message": str(hit.get("description") or ""),
            }
            for hit in content.get("unavailable") or []
            if isinstance(hit, dict)
        ]
        if not hits:
            return None
        return self._event("x.pipeshub.tool.unavailable", {"callId": call.id, "hits": hits})

    async def _artifacts(self, call: ToolCall) -> list[NewEvent | None]:
        from app.utils.conversation_tasks import await_and_collect_results

        live = _live_artifacts(self._sink.take())
        conversation_id = self._context.conversation_id
        results = (
            await await_and_collect_results(conversation_id) if conversation_id else []
        )
        events: list[NewEvent | None] = []
        for task in results:
            if task.get("type") != "artifacts":
                self._other_tasks.append(task)
                continue
            for art in task.get("artifacts") or []:
                record_id = str(art.get("recordId") or "")
                shown = live.pop(record_id, None) or {}
                events.append(self._artifact_event(call, art, shown))
        events.extend(self._artifact_event(call, {}, shown) for shown in live.values())
        return events

    def _artifact_event(
        self, call: ToolCall, marker: dict[str, Any], shown: dict[str, Any]
    ) -> NewEvent | None:
        artifact_id = marker.get("recordId") or shown.get("artifactId")
        data: dict[str, Any] = {
            "callId": call.id,
            "artifactId": artifact_id,
            "fileName": marker.get("fileName") or shown.get("fileName"),
            "mimeType": marker.get("mimeType")
            or shown.get("mimeType")
            or "application/octet-stream",
            "sizeBytes": int(marker.get("sizeBytes") or shown.get("sizeBytes") or 0),
            "artifactType": marker.get("artifactType")
            or shown.get("artifactType")
            or "",
            "isTemporary": bool(shown.get("isTemporary", False)),
            "visibility": shown.get("visibility") or "VISIBLE",
        }
        optional = {
            "recordId": marker.get("recordId") or shown.get("recordId"),
            "documentId": marker.get("documentId"),
            "version": marker.get("version") or shown.get("version"),
            "derivedFromCodeArtifactId": shown.get("derivedFromCodeArtifactId"),
        }
        data.update({key: value for key, value in optional.items() if value})
        if not data["artifactType"]:
            data["artifactType"] = "DOCUMENT"
        if shown:
            data["card"] = True
        return self._event("x.pipeshub.artifact.presented", data)

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        # Before the answer, as legacy streams `run_usage` before its finalizer.
        usage = [e for e in self._stage("telemetry", lambda: [self._run_usage(ending)]) if e is not None]
        outcome = self._stage("outcome", lambda: list(self._outcome())) if self._outcome is not None else []
        settled = self._settled(ending)
        answer = await self._answer(ending, prose=settled.framed.prose if settled is not None else None)
        return [
            *usage, *answer, *(e for e in outcome if e is not None), *([settled.event] if settled is not None else [])
        ]

    async def _answer(self, ending: RunEnding, *, prose: str | None = None) -> list[NewEvent]:
        """``prose``: the answer's own words when its settled outcome is journaled beside it (WP-100); the chat words
        the rest, so the presenter's English the answer held is not saved with it."""
        from app.agents.agent_loop.confidence import normalize as normalize_confidence
        from app.agents.agent_loop.hooks.citations import CitationCollector
        from app.agents.agent_loop.mcp_sign_in import sign_in_part
        from app.agents.agent_loop.protocol.transcript_collector import (
            TranscriptCollector,
        )
        from app.agents.agent_loop.respond import AnswerFinalizer
        from app.utils.conversation_tasks import pop_tasks, register_task

        context = self._context
        collector = TranscriptCollector()
        run_id = context.run_id
        for text in _joined_texts(ending):
            part: dict[str, Any] = {"type": "text", "content": text, "runId": run_id}
            if text:
                part["content"] = collector._prenormalize_source_citations(text)
            collector.parts.append(part)  # type: ignore[arg-type]
        context.transcript_collector = collector

        conversation_id = context.conversation_id
        if conversation_id:
            for task in pop_tasks(conversation_id):
                _ = task.cancel()
            delivered = self._delivered(ending)
            for result in [*self._other_tasks, *delivered]:
                register_task(conversation_id, _resolved(result))

        final_text = (
            ending.answer
            if ending.answer is not None
            else (ending.texts[-1] if ending.texts else "")
        )
        label, integrity = own_work(ending.events, run_id)
        clarifying = _clarification(ending)
        if clarifying is not None:
            intent, questions, card = clarifying
            return await self._clarify(intent, questions, label=label, integrity=integrity, card=card)
        if ending.outcome == "error":
            if ending.error_type != MAX_TURNS_EXCEEDED:
                # A model call failed: legacy had opened its (empty) text part,
                # which the error answer then replaces.
                collector.parts.append({"type": "text", "content": "", "runId": run_id})  # type: ignore[arg-type]
            completion = _error_answer(collector, ending.error_type)
        else:
            stopped = ending.outcome == "stopped"
            if stopped and ending.answer is not None:
                # Stopped mid-call: legacy writes the streamed text into the message that
                # was streaming, so the turns the user saw complete keep theirs (UX3).
                collector.parts.append({"type": "text", "content": "", "runId": run_id})  # type: ignore[arg-type]
            finalizer = AnswerFinalizer(context, CitationCollector(context))
            completion = await finalizer.run(
                agent_success=True,
                agent_error=None,
                event_sink=_DiscardSink(),
                agent_output=final_text if ending.outcome == "answered" else "",
                streamed_answer=(ending.answer or "") if stopped else "",
                agent_cancelled=stopped,
                reasoning_turns=_reasoning_turns(ending),
                agent_confidence=normalize_confidence(ending.confidence) if self._report_confidence else None,
                # main #3592: the agent route (the one that reports confidence) answers a pending
                # question with an empty "waiting_input" reply, not the empty-answer fallback.
                agent_needs_input=(
                    _WAITING_FOR_ANSWERS
                    if self._report_confidence and ending.outcome == "needs_input"
                    else None
                ),
            )
        data: dict[str, Any] = {
            "outcome": ending.outcome,
            "answer": str(completion.get("answer") or ""),
            "partTexts": [
                str(p.get("content") or "")
                for p in completion.get("parts") or []
                if p.get("type") == "text"
            ],
            "citations": list(completion.get("citations") or []),
            "confidence": completion.get("confidence"),
        }
        for key in ("answerMatchType", "errorCode"):
            if completion.get(key):
                data[key] = str(completion[key])
        if prose is not None and prose != data["answer"]:
            said = data["answer"]
            data["answer"] = prose
            data["partTexts"] = [prose if text == said else text for text in data["partTexts"]]
        if ending.outcome == "answered":
            # The not-found line leads, then what could not be done: both from the journal, whoever wrote the rest.
            with_limits(data, run_could_not_do(ending.events), unread_attachments_of(context, ending.events))
            _grounded(data, run_grounding(ending.events))
        if ending.truncated_by is not None and ending.outcome == "answered":
            # The chat shows the answer with a notice naming the limit that cut the work short (BUDGETEND).
            data["truncatedBy"] = ending.truncated_by
            if ending.budget_scope is not None:
                # Which shared budget ran out, so an extension's stop line can name it (WP-51).
                data["budgetScope"] = ending.budget_scope
        if isinstance(completion.get("reasoning"), list):
            data["reasoning"] = completion["reasoning"]
        # Main's sign-in card, on every answer `AnswerFinalizer._attach_parts` shapes, the error one too.
        sign_in = sign_in_part(context) if getattr(context, "mcp_sign_in_needed", None) else None
        if sign_in is not None:
            data["mcpSignIn"] = sign_in["servers"]
        data["label"] = label_wire(label, integrity=integrity)
        event = self._event("x.pipeshub.answer.finalized", data)
        self._log.info(
            "presentation.finalized",
            outcome=ending.outcome,
            truncatedBy=ending.truncated_by,
            textParts=len(data["partTexts"]),
            citations=len(data["citations"]),
            artifacts=sum(
                len(t.get("artifacts") or []) for t in self._delivered(ending)
            ),
            labelComplete=label.complete,
            labelSources=len(label.sources),
        )
        return [event] if event is not None else []

    async def _clarify(
        self,
        user_intent: str,
        questions: list[Any],
        *,
        label: TurnLabel,
        integrity: Integrity,
        card: dict[str, Any] | None = None,
    ) -> list[NewEvent]:
        """``emit_pre_run_clarification``'s card and answer, journaled. ``card``: a grounding question's typed fields
        (ADR-RUN-03 D1), which the web chat words in the reader's language (FU-2)."""
        from app.agents.actions.internal_tools.intrim_tools import (
            AskUserQuestionItemInput,
            InternalTools,
        )

        items = [AskUserQuestionItemInput.model_validate(q) for q in questions]
        tool_data = json.loads(
            await InternalTools().ask_user_question(user_intent=user_intent, questions=items)
        )
        if card is not None and isinstance(tool_data, dict):
            tool_data[GROUNDING_CARD_KEY] = card
        answer = items[0].question if items else user_intent
        events = [
            self._event(QUESTION_ASKED, question_asked(PRE_RUN_QUESTION, "success", tool_data)),
            self._event(
                "x.pipeshub.answer.finalized",
                {
                    "outcome": "needs_input",
                    "answer": answer,
                    "partTexts": [],
                    "citations": [],
                    # A question is not an answer (ANSWER-CONTRACT-FU-5): the classic clarification's none, as there.
                    "confidence": None,
                    "answerMatchType": "Clarification Needed",
                    "label": label_wire(label, integrity=integrity),
                },
            ),
        ]
        self._log.info("presentation.finalized", outcome="needs_input", reason="pre_run", questions=len(items))
        return [e for e in events if e is not None]

    @staticmethod
    def _delivered(ending: RunEnding) -> list[dict[str, Any]]:
        """Marker entries for every artifact the run presented, from the journal.

        An engine artifact is presented again when its draft becomes final or
        is discarded (``lifecycle``, P3.5): one marker per (artifact, version),
        none for a discarded version."""
        presented = [
            cast("dict[str, JsonValue]", event.data)
            for event in ending.events
            if event.type == "x.pipeshub.artifact.presented"
        ]
        discarded = {
            (data.get("artifactId"), data.get("version"))
            for data in presented
            if data.get("lifecycle") == "discarded"
        }
        seen: set[tuple[JsonValue, JsonValue]] = set()
        entries: list[dict[str, Any]] = []
        for data in presented:
            key = (data.get("artifactId"), data.get("version"))
            if data.get("lifecycle") is not None and (key in discarded or key in seen):
                continue
            seen.add(key)
            entries.append(
                {
                    "documentId": data.get("documentId") or "",
                    "fileName": data.get("fileName"),
                    "mimeType": data.get("mimeType"),
                    "sizeBytes": data.get("sizeBytes"),
                    "recordId": data.get("recordId") or "",
                    "artifactType": data.get("artifactType"),
                    "version": data.get("version") or "",
                }
            )
        return [{"type": "artifacts", "artifacts": entries}] if entries else []

    # ---- summaries (tool_loop's precedence: the tool's own, then the registry's): one place, ``step_display`` ----

    def _args_summary(
        self,
        tool: Any,  # noqa: ANN401
        name: str,
        args: dict[str, Any],
    ) -> str | None:
        return step_display.args_summary(tool, self._summarizer, name, args, self._log)

    def _result_summary(
        self,
        tool: Any,  # noqa: ANN401
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> str | None:
        return step_display.result_summary(tool, self._summarizer, name, args, result, self._log)


def _joined_texts(ending: RunEnding) -> list[str]:
    """One text per transcript part, as legacy's collector keeps them: a
    text-only reply that continues one cut off at the output-token limit joins
    the cut-off part (``joins_truncated``) instead of starting its own."""
    turns: list[tuple[bool, bool]] = []  # (truncated, calls a tool), one per response
    calls = 0
    for event in ending.events:
        data = cast("dict[str, Any]", event.data)
        if event.type == "model.response":
            calls = len(data.get("toolCalls") or [])
        elif event.type == "x.pipeshub.message.presented":
            turns.append((bool(data.get("truncated")), calls > 0))
    if len(turns) != len(ending.texts):
        return list(ending.texts)
    joined: list[str] = []
    continues = False
    for text, (truncated, tools) in zip(ending.texts, turns, strict=True):
        if continues and not tools and joined:
            joined[-1] += text
        else:
            joined.append(text)
        continues = truncated and not tools
    return joined


def question_asked(call_id: str, status: str, tool_data: Any) -> dict[str, Any]:  # noqa: ANN401 - the card's payload
    """A question card as every durable writer journals it (the loop's call, its pre-run, an engine's ``ask_user``):
    with the time it expires, as main's legacy card carries it (``parked_turn.with_expiry``), so either path's card
    turns read-only at the same moment and the person replies in the chat."""
    from app.agents.agent_loop.parked_turn import with_expiry

    return {"callId": call_id, "status": status, "toolData": with_expiry(tool_data) if status == "success" else tool_data}


def run_grounding(events: Sequence[JournalEvent]) -> Grounding | None:
    """ADR-RUN-03: the run's grounding, from its journaled slots: the check before it delegated (the goal slot's) and
    its job's typed summary (the hand-off's), combined. ``None``: neither was made."""
    from app.harness.assembly.loop import GOAL_SLOT
    from app.harness.grounding import (
        GROUNDING_KEY,
        JOB_GROUNDING_SLOT,
        Grounding,
        combined,
    )

    before: Grounding | None = None
    job: Grounding | None = None
    for event in events:
        if event.type != "loop.state_saved":
            continue
        slots = cast("dict[str, Any]", event.data).get("slots") or {}
        goal = slots.get(GOAL_SLOT)
        if isinstance(goal, dict) and GROUNDING_KEY in goal:
            before = Grounding.from_json(goal[GROUNDING_KEY])
        if JOB_GROUNDING_SLOT in slots:
            job = Grounding.from_json(slots[JOB_GROUNDING_SLOT]) or job
    return combined(before, job)


def lead_with(data: dict[str, Any], line: str) -> None:
    """The answer, and the text part that holds it, starting with ``line`` (a typed fact's English, for a surface
    with no UI; the web chat words the fact itself and leaves the line out)."""
    said = str(data.get("answer") or "")
    data["answer"] = f"{line}\n\n{said}" if said.strip() else line
    texts = list(data.get("partTexts") or [])
    at = next((i for i in range(len(texts) - 1, -1, -1) if texts[i] == said), None)
    if at is not None:
        texts[at] = data["answer"]
    else:
        texts.append(data["answer"])
    data["partTexts"] = texts


def run_could_not_do(events: Sequence[JournalEvent]) -> list[dict[str, Any]]:
    """ADR-RUN-03 D5 (R32REPO-FU-1): what the run's jobs met and could not do, from their steps' typed frames
    (``code_task``'s ``x.pipeshub.tool.result_presented`` ``limits``), in the order they ran."""
    items: list[dict[str, Any]] = []
    for event in events:
        found = cast("dict[str, Any]", event.data).get("limits") if event.type == RESULT_PRESENTED else None
        if isinstance(found, list):
            items.extend(cast("dict[str, Any]", i) for i in cast("list[object]", found) if isinstance(i, dict))
    return items


def unread_attachments_of(
    context: object, events: Sequence[JournalEvent] = ()
) -> tuple[Sequence[Mapping[str, Any]], bool]:
    """The run's files the model was never given (R33JGAPS-FU-6, R34FIX-FU-1): the turn's and each steered message's,
    and whether the claim's Node reads them typed."""
    from app.harness.assembly.attachment_limits import unread_of

    return unread_of(getattr(context, "tool_state", None), events)


def with_limits(
    data: dict[str, Any],
    items: Sequence[Mapping[str, Any]],
    unread: tuple[Sequence[Mapping[str, Any]], bool] = ((), True),
) -> None:
    """The answer led by the plain "could not do" line, with its typed view (``limits``), when anything was met.
    ``unread``: the turn's files the model never read, and whether they travel typed; a Node that cannot read them
    typed gets their line only, after the typed one, so the chat that words the typed items still shows it."""
    from app.harness.engines.capability_limits import could_not_do_view

    files, typed = unread
    untyped = None if typed else could_not_do_view(files)
    if untyped is not None:
        lead_with(data, str(untyped["line"]))
    view = could_not_do_view([*items, *files] if typed else items)
    if view is not None:
        data["limits"] = view
        lead_with(data, str(view["line"]))


def _grounded(data: dict[str, Any], grounding: Grounding | None) -> None:
    """The answer as the contract has it (ADR-RUN-03 D2, D3): led by the plain not-found line when the subject was not
    found, with the confidence its grounding allows (a model's own rating never above it)."""
    from app.harness.grounding import capped, confidence_for, not_found_view

    if grounding is None:
        return
    view = not_found_view(grounding)
    if view is not None:
        data["notFound"] = view
        lead_with(data, str(view["line"]))
    level = confidence_for(grounding, cited=len(data.get("citations") or []))
    data["confidence"] = capped(data.get("confidence"), level) if level is not None else data.get("confidence")


def _clarification(ending: RunEnding) -> tuple[str, list[Any], dict[str, Any] | None] | None:
    """The pre-run's questions, when the run asked them instead of running, and a grounding question's typed card."""
    if ending.outcome != "needs_input" or ending.texts:
        return None
    from app.harness.assembly.loop import CLARIFYING_INTENT_KEY, GOAL_SLOT
    from app.harness.grounding import GROUNDING_KEY, Grounding, grounding_card

    for event in ending.events:
        if event.type != "loop.state_saved":
            continue
        goal = cast("dict[str, Any]", event.data).get("slots", {}).get(GOAL_SLOT)
        if isinstance(goal, dict) and goal.get("clarifyingQuestions"):
            intent = goal.get(CLARIFYING_INTENT_KEY)
            grounding = Grounding.from_json(goal.get(GROUNDING_KEY)) if intent else None
            card: dict[str, Any] | None = (
                dict(grounding_card(grounding)) if grounding is not None and grounding.not_found else None
            )
            return str(intent or goal.get("description") or ""), list(goal["clarifyingQuestions"]), card
    return None


def _pending_card(goal: object) -> tuple[str, dict[str, Any]] | None:
    """The grounding question the goal step kept for a ``code_task`` the model calls itself (ADR-RUN-03 D1): its
    question's text and typed card; ``None`` when there is none."""
    from app.harness.assembly.code_task import PENDING_QUESTION_KEY
    from app.harness.grounding import GROUNDING_KEY, Grounding, grounding_card

    if not isinstance(goal, dict):
        return None
    slot = cast("dict[str, Any]", goal)
    pending = slot.get(PENDING_QUESTION_KEY)
    question = cast("dict[str, Any]", pending).get("question") if isinstance(pending, dict) else None
    text = cast("dict[str, Any]", question).get("question") if isinstance(question, dict) else None
    grounding = Grounding.from_json(slot.get(GROUNDING_KEY))
    if not isinstance(text, str) or grounding is None or not grounding.not_found:
        return None
    return text, dict(grounding_card(grounding))


def _first_question(payload: Mapping[str, Any]) -> str | None:
    questions = payload.get("questions")
    first: object = cast("list[object]", questions)[0] if isinstance(questions, list) and questions else None
    text = cast("dict[str, Any]", first).get("question") if isinstance(first, dict) else None
    return text if isinstance(text, str) else None


def _reasoning_of(message: AssistantMessage) -> str:
    from harness_core.kernel.core.messages import ThinkingPart

    content = message.content
    if isinstance(content, str):
        return ""
    return "".join(part.thinking for part in content if isinstance(part, ThinkingPart))


def _reasoning_turns(ending: RunEnding) -> list[dict[str, Any]]:
    """What legacy's answer streamer collects: one entry per turn that
    reasoned, keyed by the 0-based turn index, from the journaled responses."""
    turns: list[dict[str, Any]] = []
    turn = 0
    for event in ending.events:
        if event.type == "turn.started":
            turn = int(cast("dict[str, Any]", event.data).get("turn") or turn)
        elif event.type == "x.pipeshub.message.presented":
            reasoning = cast("dict[str, Any]", event.data).get("reasoning")
            if reasoning:
                turns.append({"turnIndex": turn - 1, "content": reasoning})
    return turns


# The kernel's own error for a run past its turn budget: no model call failed.
MAX_TURNS_EXCEEDED = "max_turns_exceeded"


def _error_answer(collector: Any, error_type: str | None) -> dict[str, Any]:  # noqa: ANN401
    """`AnswerFinalizer._emit_error_response` for a failure the model port
    already classified (its provider message never reaches the journal)."""
    from app.agents.agent_loop.error_classification import (
        _USER_MESSAGES,
        model_call_failure_message,
    )

    code = error_type if error_type in _USER_MESSAGES else "unknown"
    # Only the model port journals `unknown`: a kernel ending of its own (max_turns_exceeded) keeps the generic text.
    message = model_call_failure_message(code) if error_type == code else _USER_MESSAGES[code]
    collector.replace_final_text(message)
    return {
        "answer": message,
        "citations": [],
        "confidence": "Low",
        "answerMatchType": "Error",
        "errorCode": code,
        "parts": collector.parts,
    }


def _resolved(result: dict[str, Any]) -> asyncio.Task[dict[str, Any]]:
    async def done() -> dict[str, Any]:
        return result

    return spawn_in_request(done())


__all__ = ["HIDDEN_TOOLS", "QUESTION_ASKED", "RESULT_PREVIEW_CHARS", "PipesHubPresenter", "question_asked"]
