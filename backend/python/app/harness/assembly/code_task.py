"""``code_task`` (CTL-6): a coder agent's loop hands a task to an engine child.

A coder agent's turn runs on the loop. When the turn needs code run or files made, or is work bigger than a chat
reply (DEEPROUTE: research, planning, many tool calls; ``deep_route``), the loop calls ``code_task``,
which spawns one engine child on a fork of the session workspace under the run's pinned engine authority
(``x.pipeshub.codeTask``, narrowed by Node). The child's answer and the files it published come back as the call's
outcome: the files become this run's own artifact cards, so the loop's answer names and links them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.harness.artifacts.events import PRESENTED
from app.harness.assembly.task_spec import (
    GATHERED_HEADING,
    GATHERED_MAX_CHARS,
    GATHERED_RESULT_MAX_CHARS,
    TaskSpec,
)
from app.harness.citations import CHILD_SOURCES_KEY
from app.harness.engines.capability_limits import (
    LIMITS_KEY,
    LIMITS_NOTE_KEY,
    limits_note,
    limits_of,
    limits_summary,
    met_items,
)
from app.harness.grounding import GROUNDING_KEY, Grounding, result_note
from app.utils.answer_links import link_workspace_files
from harness_core.budget.run_budget import scoped_now_s
from harness_core.clock import SystemClock
from harness_core.journal.children import (
    ChildExecutorRequest,
    ChildGrantRequest,
    ChildRefusal,
    ChildRefused,
)
from harness_core.journal.types import NewEvent
from harness_core.kernel.agent.tool_loop import (
    _result_preview,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.durable.delegation import (
    ChildRequest,
    Decision,
    DelegatedOutcome,
    Refuse,
    delegate_effect,
    goal_tool,
    is_success,
)
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.tools.base import ToolValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from app.agents.agent_loop.connected_sources import ConnectedSources
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.deep import DeepCitations
    from harness_core.budget.run_budget import RunBudget
    from harness_core.clock import Clock
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.delegation import (
        Delegated,
        DelegationTools,
        DelegationView,
    )
    from harness_core.logs import HarnessLog
    from harness_core.memories.tool import RememberTool
    from harness_core.workspace.forks import ChildMergeReport

CODE_TASK = "code_task"
# What the engine child's result document lists of the files it published (``app.harness.engines.child_result``).
FILES_KEY = "files"
PROMPT_SECTION = "code_task"
CODE_TASK_DESCRIPTION = (
    "Hand a task to a coding engine that works in this conversation's workspace with this conversation's tools "
    "(web search and fetch, company knowledge, connected apps) and a sandbox to run code. Use it for work bigger "
    "than a chat reply: research or analysis across many sources or pages, planning, a long report, anything that "
    "needs many tool calls or more room than one reply; and for running code or creating, reading or editing files, "
    "documents, spreadsheets or charts. Give one complete goal with the facts, links and constraints the work needs, "
    "and put what earlier work in this conversation already produced in context. It runs once per message: a second "
    "call in the same message is refused, so hand over the whole task in one call. Answer simple questions and short "
    "lookups directly instead."
)
# SPENDCONT: a model wrote a 2.9k-character ``context`` (the tickets an earlier attempt had found) that the schema
# did not have, and ``decide`` dropped it: the engine child started from the goal alone and searched again.
CONTEXT_FIELD = "context"
CONTEXT_FIELD_HELP = (
    "What the engine needs from earlier in this conversation: results an earlier task returned, items already done, "
    "files already made, and what is still missing. Empty when there is none."
)
# ADR-RUN-01 D5: the engine is given the person's request as they wrote it (``TaskSpec``); the goal is the parent's
# notes on it.
GOAL_FIELD_HELP = (
    "What the engine must do for the person's request, with every name, link, figure and constraint from this "
    "conversation it needs. PipesHub passes the person's request itself to the engine as they wrote it."
)
# DEEPROUTE: before it, the guidance kept research in the loop ("only … code to run or a file"): a PR triage made
# 25 web calls in the chat and ended on its budget with no answer.
CODE_TASK_GUIDANCE = (
    "Answer simple questions, short lookups and single actions directly with your tools. Use code_task when the work "
    "needs research or analysis across many sources or pages, planning, a long write-up, many tool calls, or code run "
    "or files made; call it early, before you spend many steps yourself. When it returns, tell the user what it found "
    "and made, keeping its citations and links, and name the files it made; do not redo its work."
)
# BGUI-11: a parent, told to give the engine's answer "in full", repeated its process talk ("the PDF's layout check
# passed (`layout: clean`), and I visually inspected the rendered pages") under a report that already said it. The
# result itself says whose words they are and what the person needs from them, beside the words.
PRESENT_KEY = "present"
PRESENT_NOTE = (
    "This output is the coding engine's own report to you. Tell the person the outcome: what it found, with its "
    "citations and links, and the files it made, by name. Leave out how it worked (its checks, tools, scripts, "
    "renders and steps) unless the person asked; keep any limit or problem that changes what they can rely on."
)
# DEEPROUTE2: a routed turn ends with the engine's answer, so a fact the person asked to have remembered was kept only
# when the model also called remember beside code_task: in the live A/B it did so in half the hand-offs, and the
# engine, told of it in the brief, mostly wrote it into a file. The hand-off call itself carries it: the model is
# already writing every requirement of the request into code_task's arguments.
REMEMBER_FIELD = "remember"
REMEMBER_FIELD_HELP = (
    "Only when the person asked you to remember something for later conversations: each fact or preference to keep, "
    "one per item, in your own words. They are kept for the person when the task is handed over; do not also call "
    "remember."
)
# SPENDCONT: a parent spent its wrap-up on this refusal after a cut-short child, then said "not ready" and dropped
# the child's partial result; the refusal now says what to answer with.
ONE_ENGINE_PER_MESSAGE = (
    "A coding task already ran for this message; only one runs per message. Answer now with its result in full, as it "
    "returned it (if it stopped early, say it is partial and what is missing); the person can send 'continue' to "
    "carry on in their next message."
)


# Deep mode's spawn_agent role that runs on the coding engine (CM-02, ``harness_agent_runner``).
CODER_ROLE = "coder"
SPAWN_AGENT = "spawn_agent"


def is_engine_hand_off(call: ToolCall) -> bool:
    """A call that hands work to the run's coding engine: ``code_task``, or a deep spawn of the coder role."""
    return call.name == CODE_TASK or (call.name == SPAWN_AGENT and call.arguments.get("role") == CODER_ROLE)


class CodeTaskDelegation:
    """The ``code_task`` delegating tool: one engine child per turn, its files presented as this run's cards and its
    citation sources adopted into this run's, its answer's refs renumbered to this run's (ENGCITE2)."""

    def __init__(
        self,
        remember: RememberTool | None = None,
        *,
        citations: DeepCitations | None = None,
        request: str = "",
        deliverable: str | None = None,
        budget: RunBudget | None = None,
        clock: Clock | None = None,
        log: HarnessLog | None = None,
        sources: ConnectedSources | None = None,
        preloaded_skills: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        """``request``: the person's message this run answers, given to the engine as they wrote it (D5);
        ``deliverable``: the file the run's pin names (D1); ``budget``: the run's, whose slice the engine is told (D4);
        ``sources``: the run's connected sources, which the task names (ADR-OWD-07 D-DISC-2); ``preloaded_skills``:
        the skills the run's start loaded in full, which the task names with those it loaded by name (R31HOT2)."""
        self._schema = code_task_schema(remember)
        self._remember = remember
        self._citations = citations
        self._request = request
        self._deliverable = deliverable
        self._budget = budget
        self._clock = clock or SystemClock()
        self._log = log
        self._sources = sources
        self._preloaded_skills = preloaded_skills
        self._grants = ChildGrantRequest(mounts=("/workspace",))

    def delegates(self, call: ToolCall) -> bool:
        return call.name == CODE_TASK

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        before = [*view.earlier, *view.batch[: _index(view.batch, call.invocation_id)]]
        if any(is_engine_hand_off(d.call) and d.state != "refused" for d in before):
            return Refuse(ONE_ENGINE_PER_MESSAGE)
        if pending_question(view.slots) is not None:
            # ADR-RUN-03 D1: the subject was not found and the person has not been asked; the director asks first.
            return Refuse(NOT_FOUND_REFUSAL)
        arguments = call.call.arguments
        task = arguments.get("task_id")
        spec = TaskSpec.compose(
            request=self._request, goal=arguments.get("goal"), context=arguments.get(CONTEXT_FIELD),
            gathered=view.gathered, deliverable=self._deliverable, budget=self._budget,
            now_s=scoped_now_s(lambda: self._clock.now().timestamp()),
            found=found_line(view.slots),
            found_at=found_marks(view.slots),
            sources=self._sources,
            preloaded_skills=self._preloaded_skills() if self._preloaded_skills is not None else (),
        )
        if spec.dropped and self._log is not None:
            # Lengths only: the sentences are the model's words about the person's request.
            self._log.warning(
                "code_task.harness_text_dropped", invocationId=call.invocation_id, sentences=len(spec.dropped),
                chars=sum(len(s) for s in spec.dropped),
            )
        return ChildRequest(
            invocation_id=call.invocation_id,
            goal=spec.render(),
            task=spec.parts(),
            grants=self._grants,
            task_id=task if isinstance(task, str) and task else None,
            executor=ChildExecutorRequest(),
        )

    async def outcome(
        self,
        call: ToolCall,
        document: JsonValue,
        *,
        workspace: ChildMergeReport | None = None,
    ) -> DelegatedOutcome:
        body = dict(cast("dict[str, Any]", document)) if isinstance(document, dict) else {}
        sources = body.pop(CHILD_SOURCES_KEY, None)
        adopted: tuple[NewEvent, ...] = ()
        if self._citations is not None and isinstance(sources, dict):
            body["output"], adopted = self._citations.adopt(cast("dict[str, Any]", sources), body.get("output"), call.id)
        files = [
            f
            for f in body.get(FILES_KEY) or []
            if isinstance(f, dict) and f.get("artifactId")
        ]
        shown: dict[str, Any] = {
            key: value for key, value in body.items() if key != FILES_KEY
        }
        if isinstance(shown.get("output"), str):
            # The engine's kernel links its text before it hands it back (MR-18); a child result journaled by an older
            # worker still holds its sandbox paths, which name nothing the user can open: the loop reads file names.
            shown["output"] = link_workspace_files(shown["output"], {})
        if files:
            # The model names the files by name; the card under the answer links each one.
            shown[FILES_KEY] = [str(f.get("fileName")) for f in files]
        finished = is_success(document) and not ({"stopped_early", "needs_input"} & shown.keys())
        # A partial or a blocked result keeps its own instruction (SPENDCONT): it is given in full, labelled.
        if finished and isinstance(shown.get("output"), str) and shown["output"].strip():
            shown[PRESENT_KEY] = PRESENT_NOTE
        # The model reads the limits once, as the note's words.
        _ = shown.pop(LIMITS_KEY, None)
        note = limits_note(limits_of(body))
        if note:
            shown[LIMITS_NOTE_KEY] = note
        if workspace is not None:
            shown["workspace"] = workspace.as_json()
        remembered = await self._remembered(call)
        if remembered is not None:
            shown[REMEMBER_FIELD] = remembered
        # The result's own frame first: Node reads the presentation that follows the outcome (WP-56).
        frame = _result_frame(call, body, shown.get("output"), files=len(files), workspace=workspace)
        events = (frame, *adopted, *(_presented(call, f) for f in files))
        return DelegatedOutcome(
            cast("JsonValue", shown), error=not is_success(document), events=events
        )

    async def _remembered(self, call: ToolCall) -> JsonValue | None:
        """What the hand-off asked to keep, proposed into the run's memory as ``remember`` proposes it (Node settles
        it with the turn). Proposals are idempotent per content, so a re-joined call proposes nothing twice."""
        items = call.arguments.get(REMEMBER_FIELD)
        if self._remember is None or not isinstance(items, list) or not items:
            return None
        arguments: dict[str, Any] = {"memories": items}
        try:
            self._remember.validate(arguments)
        except ToolValidationError as exc:
            return {"error": str(exc)}
        out = await self._remember.execute(**arguments)
        return cast("JsonValue", out.data) if out.success else {"error": out.error}

    def refused(self, call: ToolCall, refusal: ChildRefused) -> tuple[NewEvent, ...]:
        """A hand-off refused for a reason the person can act on: the CTL-5b notice the chat words, once."""
        notice = _notice_for(refusal)
        return (NewEvent(NOTICE, notice, critical=False),) if notice is not None else ()

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [self._schema]


# The goal step's slot (``loop.GOAL_SLOT``) and the question it keeps for a hand-off the model makes itself.
_GOAL_SLOT = PRE_RUN_SLOT_PREFIX + "goal"
PENDING_QUESTION_KEY = "groundingQuestion"
NOT_FOUND_REFUSAL = (
    "Not handed over: what the person named is not in their knowledge, and PipesHub is asking them which they mean "
    "before any task starts."
)


def _found(slots: Mapping[str, JsonValue]) -> Grounding | None:
    goal = slots.get(_GOAL_SLOT)
    grounding = Grounding.from_json(goal.get(GROUNDING_KEY)) if isinstance(goal, dict) else None
    if grounding is None or grounding.status != "found" or not grounding.found_in or not grounding.subject:
        return None
    return grounding


def _with_tool(text: str, tool: str) -> str:
    return f"{text} (its `{tool}` tool)" if tool else text


def found_line(slots: Mapping[str, JsonValue]) -> str:
    """Where the check before the hand-off found the subject in a connected source (``FOUND_LINE``), for the job;
    empty when it was found only in the person's knowledge, or nowhere."""
    from app.harness.assembly.task_spec import FOUND_LINE

    grounding = _found(slots)
    if grounding is None:
        return ""
    where = "; ".join(_with_tool(f.place, f.tool) for f in grounding.found_in)
    return FOUND_LINE.format(subject=f"“{grounding.subject}”", where=where)


def found_marks(slots: Mapping[str, JsonValue]) -> dict[str, str]:
    """The same fact by place (``FOUND_MARK``), for a task that lists its connected sources: each place's entry is
    marked where the subject was found, so the job is told once (ADR-OWD-07 D-DISC-2 with ADR-RUN-03 D1)."""
    from app.harness.assembly.task_spec import FOUND_MARK

    grounding = _found(slots)
    if grounding is None:
        return {}
    return {f.place: _with_tool(FOUND_MARK.format(subject=f"“{grounding.subject}”"), f.tool) for f in grounding.found_in}


def pending_question(slots: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """The grounding question the goal step kept for a hand-off the model makes itself: its intent and question."""
    goal = slots.get(_GOAL_SLOT)
    pending = goal.get(PENDING_QUESTION_KEY) if isinstance(goal, dict) else None
    return pending if isinstance(pending, dict) else None


def _index(batch: Sequence[Delegated], invocation_id: str) -> int:
    for at, item in enumerate(batch):
        if item.invocation_id == invocation_id:
            return at
    return len(batch)


# WP-56: what the chat shows of a hand-off's result, in place of Node's fallback (the result document as raw JSON,
# ids included). Plain words; the answer itself is the next message's text, so the frame carries its preview.
RESULT_PRESENTED = "x.pipeshub.tool.result_presented"
_STOPPED_EARLY = {
    "budget": "The coding engine stopped early at its spending limit; this is what it had done.",
    "turns": "The coding engine stopped early at its step limit; this is what it had done.",
    "time": "The coding engine stopped early when its working time ran out; this is what it had done.",
    "stall": "The coding engine stopped early because it was not making progress; this is what it had done.",
    "provider": (
        "The coding engine stopped early because the AI model kept refusing requests for several minutes (it was busy "
        "or getting too many requests); this is what it had done."
    ),
    "engine_error": "The coding engine stopped early because it ran into an error; this is what it had done.",
    "secret_blocked": (
        "The coding engine stopped early because PipesHub blocked a request that looked like it contained a secret; "
        "this is what it had done."
    ),
}


def _result_frame(
    call: ToolCall, body: dict[str, Any], output: object, *, files: int, workspace: ChildMergeReport | None
) -> NewEvent:
    if not is_success(body):
        summary = _failed_summary(body)
        if workspace is not None and workspace.unfinished is not None:
            summary += f" Its saved work is in the folder “{workspace.unfinished}”."
        data: dict[str, Any] = {"callId": call.id, "status": "failed", "content": "", "resultSummary": summary}
        return NewEvent(RESULT_PRESENTED, data, critical=False)
    truncated = body.get("truncatedBy")
    if body.get("needs_input"):
        summary = "The coding engine stopped to ask you something."
    elif isinstance(truncated, str):
        summary = _STOPPED_EARLY.get(truncated, _STOPPED_EARLY["budget"])
    elif files:
        summary = f"The coding engine finished the task and saved {files} file{'s' if files != 1 else ''}."
    else:
        summary = "The coding engine finished the task."
    # ADR-RUN-03 D2: the job's step says what its searches did not find, from the typed summary, not its words.
    limits = limits_of(body)
    summary += result_note(Grounding.from_json(body.get(GROUNDING_KEY))) + limits_summary(limits)
    data = {
        "callId": call.id,
        "status": "completed",
        "content": _result_preview(output) if isinstance(output, str) else "",
        "resultSummary": summary,
    }
    # D5 (R32REPO-FU-1): what the job met and could not do, typed, so the parent's answer leads with it.
    met = met_items(limits)
    if met:
        data["limits"] = met
    return NewEvent(RESULT_PRESENTED, data, critical=False)


def _failed_summary(body: Mapping[str, Any]) -> str:
    """A failed job's step line: an out-of-memory end names its limit (R32SECCOMP-FU-4), as the job card does."""
    limit = body.get("memoryLimitMb")
    if body.get("error") == "out_of_memory" and isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        from harness_core.sandbox.errors import memory_limit_text

        return f"The coding engine ran out of memory (limit {memory_limit_text(limit)}) and was stopped."
    return "The coding engine stopped with an error before it finished."


def _presented(call: ToolCall, file: dict[str, Any]) -> NewEvent:
    """One of the child's published files as this run's card (the presenter's ``artifact.presented`` shape)."""
    data: dict[str, Any] = {
        "callId": call.id,
        "artifactId": str(file["artifactId"]),
        "fileName": str(file.get("fileName") or ""),
        "mimeType": str(file.get("mimeType") or "application/octet-stream"),
        "sizeBytes": int(file.get("sizeBytes") or 0),
        "artifactType": str(file.get("artifactType") or "DOCUMENT"),
        "isTemporary": False,
        "recordId": str(file["artifactId"]),
        "visibility": "VISIBLE",
        "card": True,
        "lifecycle": "final",
    }
    if file.get("version"):
        data["version"] = file["version"]
    if file.get("documentId"):
        data["documentId"] = str(file["documentId"])
    return NewEvent(PRESENTED, data, critical=False)


# Node's refusal codes for a child's model access or engine (its catalog groups), as the notice the chat words.
NOTICE = "x.pipeshub.run.notice"
_ENGINE_NOTICES = {
    "ACCOUNT_MISSING": "account_missing",
    "ENGINE_PROFILE_WIDENING": "engine_profile",
    "ENGINE_PROFILE_INVALID": "engine_profile",
    "ENGINE_UNAVAILABLE": "engine_unavailable",
    "ENGINE_SANDBOX_MISSING": "engine_unavailable",
}


# WP-111: the engine was turned off after the turn was admitted; worded as G-48's "ran without code tools", with why.
_ENGINE_OFF = {"code": "ran_without_code_tools", "item": "engine_off"}


def _notice_for(refusal: ChildRefused) -> dict[str, str] | None:
    if refusal.reason is not ChildRefusal.ACCESS_REFUSED or refusal.code is None:
        return None
    if refusal.code == "ENGINE_OFF":
        return dict(_ENGINE_OFF)
    return {"code": _ENGINE_NOTICES.get(refusal.code, "model_access")}


class CombinedDelegation:
    """Deep mode's ``spawn_agent`` and ``code_task`` side by side: each call goes to the one that delegates it."""

    def __init__(self, *parts: DelegationTools) -> None:
        self._parts = parts

    def _for(self, call: ToolCall) -> DelegationTools:
        return next(p for p in self._parts if p.delegates(call))

    def delegates(self, call: ToolCall) -> bool:
        return any(p.delegates(call) for p in self._parts)

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        return self._for(call.call).decide(call, view)

    async def outcome(
        self,
        call: ToolCall,
        document: JsonValue,
        *,
        workspace: ChildMergeReport | None = None,
    ) -> DelegatedOutcome:
        return await self._for(call).outcome(call, document, workspace=workspace)

    def refused(self, call: ToolCall, refusal: ChildRefused) -> tuple[NewEvent, ...]:
        noting = getattr(self._for(call), "refused", None) if self.delegates(call) else None
        return tuple(noting(call, refusal)) if callable(noting) else ()

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [schema for part in self._parts for schema in part.schemas()]


def offers_code_task(run: RunContext) -> bool:
    """Whether the run's loop may hand off: its spec pins an engine authority and it is not itself a sub-agent."""
    return run.code_task and run.sub_agent is None


def code_task_schema(remember: RememberTool | None) -> ToolSchema:
    """``code_task``'s arguments: the goal, task id and context, and, for a run that may keep memories,
    ``remember``, whose items are ``remember``'s own."""
    schema = goal_tool(CODE_TASK, CODE_TASK_DESCRIPTION)
    properties: dict[str, Any] = {
        **cast("dict[str, Any]", schema.input_schema["properties"]),
        "goal": {"type": "string", "description": GOAL_FIELD_HELP},
        CONTEXT_FIELD: {"type": "string", "description": CONTEXT_FIELD_HELP},
    }
    if remember is not None:
        memories = cast("dict[str, Any]", remember.to_schema().input_schema["properties"])["memories"]
        properties[REMEMBER_FIELD] = {**memories, "description": REMEMBER_FIELD_HELP}
    return ToolSchema(
        name=schema.name,
        description=schema.description,
        input_schema={**schema.input_schema, "properties": properties},
    )


def with_code_task(
    delegation: DelegationTools | None,
    *,
    offered: bool,
    remember: RememberTool | None = None,
    citations: DeepCitations | None = None,
    request: str = "",
    deliverable: str | None = None,
    budget: RunBudget | None = None,
    log: HarnessLog | None = None,
    sources: ConnectedSources | None = None,
    preloaded_skills: Callable[[], Sequence[str]] | None = None,
) -> DelegationTools | None:
    """The run's delegation with ``code_task`` beside whatever it already delegates, when the run may hand off;
    ``remember``: the run's memory tool, when its pin grants a write scope; ``citations``: where the child's
    sources are adopted."""
    from app.harness.assembly.deep import DeepDelegation
    from app.harness.assembly.harness_agent_runner import HarnessAgentRunner

    if not offered:
        return delegation
    code_task = CodeTaskDelegation(
        remember, citations=citations, request=request, deliverable=deliverable, budget=budget, log=log,
        sources=sources, preloaded_skills=preloaded_skills,
    )
    if isinstance(delegation, DeepDelegation):
        # Deep mode's coder spawns run on the engine too (CM-02).
        delegation = HarnessAgentRunner(delegation, code_task)
    return (
        code_task if delegation is None else CombinedDelegation(delegation, code_task)
    )


__all__ = [
    "CODER_ROLE",
    "CODE_TASK",
    "CODE_TASK_DESCRIPTION",
    "CODE_TASK_GUIDANCE",
    "CONTEXT_FIELD",
    "FILES_KEY",
    "GATHERED_HEADING",
    "GATHERED_MAX_CHARS",
    "GATHERED_RESULT_MAX_CHARS",
    "NOT_FOUND_REFUSAL",
    "ONE_ENGINE_PER_MESSAGE",
    "PENDING_QUESTION_KEY",
    "PRESENT_KEY",
    "PRESENT_NOTE",
    "PROMPT_SECTION",
    "REMEMBER_FIELD",
    "CodeTaskDelegation",
    "CombinedDelegation",
    "code_task_schema",
    "found_line",
    "found_marks",
    "is_engine_hand_off",
    "offers_code_task",
    "pending_question",
    "with_code_task",
]
