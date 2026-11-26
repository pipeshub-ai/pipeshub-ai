"""DEEPROUTE: work bigger than a chat reply goes to the coding engine.

Two decisions, one hand-off:

* **Up front.** The goal step classifies the request (``classify``) from deterministic signals over its text, the
  model's own goal analysis where the run already has it (``auto``'s tier verdict, the intent rewrite's
  requirements) and the person's Deep research switch. No model call of its own. The decision is journaled in the
  goal slot as ``route``.
* **Mid run.** At each boundary of a run that was not handed off, ``escalation`` measures the journal against
  ``Thresholds`` (tool calls, model turns, the last request's share of the window, spend).

Either way ``DeepRouteDirector`` hands off at a turn boundary: it journals ``x.pipeshub.run.handoff`` (the reason,
shown on the engine's group in the timeline) and gives that turn one instruction, in the harness's system channel
(ADR-RUN-01 D5, wording in ``handoff_instructions``), asking the model to call ``code_task``. The kernel's tool list is fixed for the session (R4) and a named
``tool_choice`` is not portable, so the turn is not forced: when the model calls other tools instead, the director
takes the next turn itself with a synthetic ``code_task`` whose goal it builds from the journal. A run that already
has a ``code_task`` call, or was handed off once, is never handed off again.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from app.harness.assembly import handoff_instructions as instructions
from app.harness.assembly.code_task import (
    CODE_TASK,
    NOT_FOUND_REFUSAL,
    offers_code_task,
    pending_question,
)
from app.harness.assembly.handoff_instructions import (
    ENGINE_FAILED_INSTRUCTION,
    ENGINE_FAILED_WITH_WORK_INSTRUCTION,
    ESCALATION_INSTRUCTION,
    REMEMBER_IN_HANDOFF,
    UPFRONT_INSTRUCTION,
)
from app.harness.engines.child_result import QUESTION_KEY
from app.harness.grounding import GROUNDING_KEY, JOB_ASK_INTENT, JOB_GROUNDING_SLOT
from app.utils.env_flags import HARNESS_DEEP_ROUTING, env_flag
from harness_core.contracts.generated.enums import CUT_SHORT_REASONS
from harness_core.engines.outcome.briefing import BRIEFING, ChildReport
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.durable.director import is_synthetic
from harness_core.kernel.durable.ports import TurnPlan
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from app.harness.assembly.context import RunContext
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.kernel.durable.harness_channel import HarnessInstruction
    from harness_core.kernel.durable.ports import TurnResult
    from harness_core.kernel.fold import RunState
    from harness_core.logs import Field, HarnessLog

GOAL_SLOT = PRE_RUN_SLOT_PREFIX + "goal"
# A route the goal step set because the person answered a question asked for a hand-off (ADR-RUN-03 D1, D2): the
# grounding question before it, or a job's own. The director hands off at once.
GROUNDED_SIGNAL = "card_answer"
ROUTE_KEY = "route"
HANDOFF_SLOT = "deepRoute.handoff"
HANDOFF_EVENT = "x.pipeshub.run.handoff"
ENGINE: Literal["engine"] = "engine"
LOOP: Literal["loop"] = "loop"

Trigger = Literal["upfront", "escalation"]
# Why a run was handed off; the chat words each one (``agentActivity.handoff.reason.*``).
Reason = Literal["research", "analysis", "planning", "long_report", "many_steps", "context", "budget"]

# ---- up-front routing ----

_RESEARCH = re.compile(
    r"\b(research|investigate|investigation|deep[- ]dive|look into|dig into|find out (?:everything|all|why)"
    r"|survey|literature|across the web|state of the art|go through)\b",
    re.IGNORECASE,
)
# Objects a quantifier makes many of; singular too, as "every"/"each" take it ("list every failing test").
_PLURAL_OBJECTS = (
    r"(?:pull requests?|prs?|issues?|tickets?|bugs?|incidents?|repos?|repositor(?:y|ies)|pages?|sites?|websites?"
    r"|sources?|articles?|papers?|documents?|docs?|files?|records?|compan(?:y|ies)|vendors?|competitors?|products?"
    r"|tools?|librar(?:y|ies)|frameworks?|databases?|candidates?|customers?|accounts?|deals?|tests?|failures?"
    r"|errors?|options?|alternatives?|services?|apis?|providers?|models?|reports?|releases?|commits?|branch(?:es)?"
    r"|threads?|emails?|messages?|meetings?|projects?|tasks?)"
)
_MANY_ITEMS = re.compile(
    r"\b(?:all|every|each|many|multiple|several|dozens of|hundreds of|numerous|various|top \d+|\d{2,})\s+"
    r"(?:of\s+(?:the|our|their|its)\s+)?(?:\w+[- ]?){0,3}?" + _PLURAL_OBJECTS + r"\b",
    re.IGNORECASE,
)
_ANALYSIS = re.compile(
    r"\b(analy[sz]e|analysis|audit|triage|prioriti[sz]e|rank|assess|evaluate|compare|comparison|benchmark"
    r"|root cause|immediate value|most (?:valuable|important|impactful|urgent)|quick wins|highest impact"
    r"|which (?:ones? )?(?:need|needs|should|deserve|give|have)|pros and cons|trade-?offs)\b",
    re.IGNORECASE,
)
_PLANNING = re.compile(
    r"\b(plan|roadmap|strategy|migration plan|step[- ]by[- ]step|break (?:it|this|that) down|project plan"
    r"|rollout|milestones)\b",
    re.IGNORECASE,
)
# An imperative to plan ("plan the migration…", "draft a roadmap…") is the request itself, not a word in it.
_PLANNING_IMPERATIVE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:plan|draft (?:a|the) (?:plan|roadmap|strategy)"
    r"|create (?:a|the) (?:project )?(?:plan|roadmap|strategy)|make (?:a|the) plan|build (?:a|the) roadmap"
    r"|design (?:a|the) (?:migration|rollout))\b",
    re.IGNORECASE,
)
_LONG_OUTPUT = re.compile(
    r"\b(report|write-?up|white ?paper|comprehensive|in[- ]depth|thorough|exhaustive"
    r"|detailed (?:analysis|document|guide|overview|comparison|plan)|full (?:list|analysis|report|breakdown))\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+")
_WORD = re.compile(r"\w+")
LONG_REQUEST_WORDS = 80
SHORT_REQUEST_WORDS = 6
MANY_REQUIREMENTS = 5
ENGINE_WEIGHT = 2
# The family a hand-off names, most specific first.
_REASON_ORDER: tuple[tuple[str, Reason], ...] = (
    ("research", "research"),
    ("deep_research", "research"),
    ("analysis", "analysis"),
    ("planning", "planning"),
    ("long_output", "long_report"),
)


@dataclass(frozen=True)
class RouteDecision:
    """Where a request runs and why; journaled in the goal slot as ``route``."""

    to: Literal["engine", "loop"]
    reason: str
    signals: tuple[str, ...] = ()

    def slot(self) -> dict[str, JsonValue]:
        return {"to": self.to, "reason": self.reason, "signals": list(self.signals)}

    @classmethod
    def from_slot(cls, value: object) -> RouteDecision | None:
        if not isinstance(value, dict):
            return None
        data = cast("dict[str, object]", value)
        to = data.get("to")
        if to not in (ENGINE, LOOP):
            return None
        signals = data.get("signals")
        return cls(
            to=to,
            reason=str(data.get("reason") or ""),
            signals=tuple(str(s) for s in cast("list[object]", signals)) if isinstance(signals, list) else (),
        )


def classify(
    query: str,
    *,
    tier: str | None = None,
    requirements: int = 0,
    deep_research: bool = False,
    answering_approval: bool = False,
    asked_question: bool = False,
) -> RouteDecision:
    """The up-front route for one request. ``tier``: ``auto``'s verdict, when the intent call made one;
    ``requirements``: how many the intent rewrite listed (0 when it did not run)."""
    if answering_approval:
        return RouteDecision(LOOP, "approval_answer")
    if asked_question:
        return RouteDecision(LOOP, "clarifying_question")
    words = len(_WORD.findall(query))
    weights: dict[str, int] = {}
    if deep_research:
        weights["deep_research"] = ENGINE_WEIGHT
    if tier == "deep":
        weights["tier_deep"] = ENGINE_WEIGHT
    if words < SHORT_REQUEST_WORDS and not weights:
        return RouteDecision(LOOP, "short_request")
    if _RESEARCH.search(query):
        weights["research"] = 1
    if _MANY_ITEMS.search(query):
        weights["many_items"] = 1
    if _ANALYSIS.search(query):
        weights["analysis"] = 1
    if _PLANNING_IMPERATIVE.search(query):
        weights["planning"] = ENGINE_WEIGHT
    elif _PLANNING.search(query):
        weights["planning"] = 1
    if _LONG_OUTPUT.search(query):
        weights["long_output"] = 1
    if len(_URL.findall(query)) >= 2:
        weights["multi_url"] = 1
    if words >= LONG_REQUEST_WORDS:
        weights["long_request"] = 1
    if requirements >= MANY_REQUIREMENTS:
        weights["many_requirements"] = 1
    signals = tuple(sorted(weights))
    if sum(weights.values()) < ENGINE_WEIGHT:
        return RouteDecision(LOOP, "chat_sized", signals)
    reason = next((r for name, r in _REASON_ORDER if name in weights), "many_steps")
    return RouteDecision(ENGINE, reason, signals)


# ---- mid-run escalation ----


@dataclass(frozen=True)
class Thresholds:
    """When a loop run has outgrown a chat reply (``HARNESS_DEEP_ROUTING_THRESHOLDS``)."""

    tool_calls: int = 12
    turns: int = 6
    context_share: float = 0.5
    spend_share: float = 0.3

    _KEYS = {
        "toolCalls": "tool_calls",
        "turns": "turns",
        "contextShare": "context_share",
        "spendShare": "spend_share",
    }

    @classmethod
    def parse(cls, raw: str | None) -> Thresholds:
        """``toolCalls=12,turns=6,contextShare=0.5,spendShare=0.3``; a key left out keeps its default,
        ``0`` turns that threshold off. Raises ``ValueError`` naming what it cannot read."""
        values: dict[str, float | int] = {}
        for part in (raw or "").split(","):
            if not part.strip():
                continue
            key, sep, value = part.partition("=")
            name = cls._KEYS.get(key.strip())
            if not sep or name is None:
                raise ValueError(f"HARNESS_DEEP_ROUTING_THRESHOLDS: unknown setting {part.strip()!r}")
            number = float(value)
            if number < 0:
                raise ValueError(f"HARNESS_DEEP_ROUTING_THRESHOLDS: {key.strip()} is negative")
            values[name] = number if name.endswith("share") else int(number)
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Measured:
    """What the journal says about a run at a boundary."""

    tool_calls: int
    turns: int
    context_share: float
    spend_share: float

    def fields(self) -> dict[str, Field]:
        return {
            "toolCalls": self.tool_calls,
            "turns": self.turns,
            "contextShare": round(self.context_share, 3),
            "spendShare": round(self.spend_share, 3),
        }


def measure(state: RunState, *, window_tokens: int, max_tokens: int) -> Measured:
    """From the run's journal alone (a director is a pure function of it): the ledger's view is per process, and
    tool calls that spend on a model of their own are not in ``consumed``; the call and context thresholds catch
    those. The last request's share of the window stands for compaction too: the auto-compact rung fires at 80%,
    past the default 50%."""
    calls = sum(1 for inv in state.invocations.values() if inv.state == "completed" and inv.name != CODE_TASK)
    model = [r for r in state.responses if not is_synthetic(r)]
    last = next((r for r in reversed(model) if r.usage is not None), None)
    context = (last.usage.input_tokens / window_tokens) if last is not None and last.usage and window_tokens else 0.0
    spent = state.consumed.input_tokens + state.consumed.output_tokens
    return Measured(
        tool_calls=calls,
        turns=len(model),
        context_share=context,
        spend_share=(spent / max_tokens) if max_tokens else 0.0,
    )


def escalation(measured: Measured, thresholds: Thresholds) -> Reason | None:
    """The first threshold the run crossed, as the reason the chat words; ``None``: none."""
    if thresholds.spend_share and measured.spend_share >= thresholds.spend_share:
        return "budget"
    if thresholds.context_share and measured.context_share >= thresholds.context_share:
        return "context"
    if (thresholds.tool_calls and measured.tool_calls >= thresholds.tool_calls) or (
        thresholds.turns and measured.turns >= thresholds.turns
    ):
        return "many_steps"
    return None


# ---- the hand-off ----

@dataclass(frozen=True)
class Handoff:
    """A hand-off as its slot keeps it: the boundary it was asked at, why, the turn the director took itself, and
    the turn it answered with the engine's answer."""

    turn: int
    trigger: Trigger
    reason: str
    taken: int | None = None
    answered: int | None = None

    def slot(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"turn": self.turn, "trigger": self.trigger, "reason": self.reason}
        if self.taken is not None:
            out["taken"] = self.taken
        if self.answered is not None:
            out["answered"] = self.answered
        return out

    @classmethod
    def from_slot(cls, value: object) -> Handoff | None:
        if not isinstance(value, dict):
            return None
        data = cast("dict[str, object]", value)
        turn = data.get("turn")
        trigger = data.get("trigger")
        if not isinstance(turn, int) or trigger not in ("upfront", "escalation"):
            return None
        taken = data.get("taken")
        answered = data.get("answered")
        return cls(
            turn=turn,
            trigger=trigger,
            reason=str(data.get("reason") or ""),
            taken=taken if isinstance(taken, int) else None,
            answered=answered if isinstance(answered, int) else None,
        )


def _cut(report: ChildReport) -> CutShort | None:
    """The limit that cut the child short, when it names one this build knows."""
    stopped = report.truncated_by
    return cast("CutShort", stopped) if stopped in CUT_SHORT_REASONS else None


def _has_code_task(state: RunState) -> bool:
    return any(inv.name == CODE_TASK for inv in state.invocations.values())


def _settled(state: RunState) -> bool:
    """Nothing is waiting for a person or still running."""
    return all(inv.state == "completed" for inv in state.invocations.values()) and state.suspension is None


def _goal_text(slots: Mapping[str, JsonValue], query: str) -> str:
    goal = slots.get(GOAL_SLOT)
    description = goal.get("description") if isinstance(goal, dict) else None
    return str(description) if isinstance(description, str) and description.strip() else query


class DeepRouteDirector:
    """The kernel's ``LoopDirector`` for a loop run that may hand off (CTL-6 ``code_task`` offered, routing on).

    After a hand-off it routed, the engine's answer is the run's answer: the director ends the run with it, so the
    loop does not re-read its whole history to repeat it (in one run its last requests were 77k-121k tokens, 19-24 s each). The
    engine answered with its citations and named its files, which come back as this run's cards. An engine that
    failed or answered nothing leaves the turn to the model, which says so."""

    def __init__(
        self,
        *,
        query: str,
        thresholds: Thresholds,
        window_tokens: int,
        max_tokens: int,
        max_turns: int,
        log: HarnessLog,
        routing: bool = True,
        worded: bool = False,
        ask_tool: str | None = None,
    ) -> None:
        """``routing``: hand off on its own (DEEPROUTE). Off, it only presents a cut-short ``code_task`` the model
        called itself, which every run that may hand off needs (SPENDCONT). ``worded``: the run's chat words a
        settled outcome itself (WP-89, ``outcome_words``), so an engine's answer is presented as its prose with its
        outcome relayed. ``ask_tool``: the run's question tool, when this turn may still put a card to the person:
        a job's question is then asked with it (ADR-RUN-03 D2)."""
        self._ask_tool = ask_tool
        self._routing = routing
        self._worded = worded
        self._query = query
        self._thresholds = thresholds
        self._window = window_tokens
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._log = log

    @property
    def routing(self) -> bool:
        return self._routing

    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        job = next((r for r in previous if r.name == CODE_TASK), None)
        if job is not None:
            question = self._job_question(turn, state, job)
            if question is not None:
                return question
        return _with_job_grounding(self._before(turn, state, previous), job)

    def _job_question(self, turn: int, state: RunState, job: TurnResult) -> TurnPlan | None:
        """ADR-RUN-03: the question a hand-off waits on, asked with the run's own card (the call runs like the
        model's, and the run ends waiting; the answer starts the next run, which hands it over with the request).
        D1: the model handed off itself and ``code_task`` refused, the subject not found: the goal step's question.
        D2: the job stopped to ask the person something. ``None``: nothing to ask, or this turn may not ask."""
        if self._ask_tool is None:
            return None
        pending = pending_question(state.slots)
        if job.content == NOT_FOUND_REFUSAL and pending is not None and isinstance(pending.get("question"), dict):
            return self._asking(turn, str(pending.get("intent") or ""), pending["question"], reason="grounding")
        content = job.content if isinstance(job.content, dict) else {}
        asked = content.get(QUESTION_KEY)
        if not content.get("needs_input") or not isinstance(asked, dict):
            return None
        question = asked.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        options = [o for o in cast("list[object]", asked.get("options") or []) if isinstance(o, str) and o.strip()]
        return _with_job_grounding(self._asking(turn, JOB_ASK_INTENT, job_question(question, options), reason="job"), job)

    def _asking(self, turn: int, intent: str, question: JsonValue, *, reason: str) -> TurnPlan:
        """The turn taken with the run's question card: the call runs like the model's and ends the run waiting."""
        assert self._ask_tool is not None
        call = ToolCall(
            id=f"deeproute-ask-{turn}", name=self._ask_tool, arguments={"user_intent": intent, "questions": [question]}
        )
        self._log.info("deeproute.question", turn=turn, reason=reason)
        return TurnPlan(synthetic=AssistantMessage(content=[], tool_calls=[call]))

    def _before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        asked = Handoff.from_slot(state.slots.get(HANDOFF_SLOT))
        if _has_code_task(state):
            if asked is not None and asked.answered is None:
                return self._present(turn, previous, asked)
            return self._present_cut_short(turn, previous) if asked is None else TurnPlan()
        if not self._routing:
            return TurnPlan()
        if asked is not None:
            return self._follow_up(turn, state, previous, asked)
        if not _settled(state):
            return TurnPlan()
        model_turns = sum(1 for r in state.responses if not is_synthetic(r))
        if model_turns == 0:
            route = RouteDecision.from_slot(_route_of(state.slots))
            if route is not None and route.to == ENGINE and GROUNDED_SIGNAL in route.signals:
                return self._take_now(turn, state, route)
            if route is not None and route.to == ENGINE:
                return self._hand_off(
                    turn, "upfront", route.reason, instructions.upfront(route.reason), {"signals": route.signals}
                )
            return TurnPlan()
        last = state.response_for(turn - 1)
        if last is None or not last.tool_calls:
            # The model answered (or a review asked it to go on): it is not gathering any more.
            return TurnPlan()
        if self._max_turns - model_turns < 2:
            return TurnPlan()
        measured = measure(state, window_tokens=self._window, max_tokens=self._max_tokens)
        reason = escalation(measured, self._thresholds)
        if reason is None:
            return TurnPlan()
        return self._hand_off(
            turn,
            "escalation",
            reason,
            instructions.escalation(reason),
            measured.fields(),
        )

    def _hand_off(
        self, turn: int, trigger: Trigger, reason: str, instruction: HarnessInstruction, detail: dict[str, Field]
    ) -> TurnPlan:
        handoff = Handoff(turn=turn, trigger=trigger, reason=reason)
        self._log.info("deeproute.handoff", turn=turn, trigger=trigger, reason=reason, **detail)
        return TurnPlan(
            harness=(instruction,),
            slots={HANDOFF_SLOT: handoff.slot()},
            events=(NewEvent(HANDOFF_EVENT, {"trigger": trigger, "reason": reason}, critical=False),),
        )

    def _take_now(self, turn: int, state: RunState, route: RouteDecision) -> TurnPlan:
        """ADR-RUN-03 D1: the person answered the question asked before a hand-off; the director makes it, with the
        request and their answer (``hand_off_request``), rather than asking a model to."""
        # The goal is the request itself: the goal step's goal for a card reply is the reply's words alone.
        call = ToolCall(id=f"deeproute-{turn}", name=CODE_TASK, arguments={"goal": self._query})
        self._log.info("deeproute.handoff", turn=turn, trigger="upfront", reason=route.reason, grounded=True)
        return TurnPlan(
            synthetic=AssistantMessage(content=[], tool_calls=[call]),
            slots={HANDOFF_SLOT: Handoff(turn=turn, trigger="upfront", reason=route.reason, taken=turn).slot()},
            events=(NewEvent(HANDOFF_EVENT, {"trigger": "upfront", "reason": route.reason}, critical=False),),
        )

    def _present(self, turn: int, previous: Sequence[TurnResult], asked: Handoff) -> TurnPlan:
        result = next((r for r in previous if r.name == CODE_TASK), None)
        if result is None:
            return TurnPlan()
        content = result.content
        report = ChildReport.read(content, call_ok=result.status == "ok")
        if report.failed:
            self._log.warning("deeproute.engine_failed", turn=turn, trigger=asked.trigger)
            # WP-65: handed off up front, the run has nothing of its own to answer from; the work its engine kept is
            # what the answer points to, written without a model (ADR-RUN-01 D1, the briefing's).
            briefing = BRIEFING.brief(report, upfront=asked.trigger == "upfront")
            if briefing.answer is not None:
                answered = Handoff(asked.turn, asked.trigger, asked.reason, asked.taken, answered=turn)
                return TurnPlan(
                    synthetic=AssistantMessage(content=[TextPart(text=briefing.answer)]),
                    slots={HANDOFF_SLOT: answered.slot()},
                )
            # Not marked answered: a re-driven turn is asked the same, and the turn after it has no code_task result.
            return TurnPlan(harness=(instructions.engine_failed_briefed(report),))
        if not isinstance(content, dict) or content.get("success") is not True or not report.output.strip():
            return TurnPlan()
        answered = Handoff(asked.turn, asked.trigger, asked.reason, asked.taken, answered=turn)
        cut = _cut(report)
        if BRIEFING.brief(report).situation == "unanswered":
            # ENGBUDGET: the engine stopped before it wrote an answer, and the model takes the turn back and answers
            # from what this run gathered, flagged, on the run's wrap-up reserve; WP-70: it ended its turn without
            # the file and the run could not write it, and the model answers from the sources the result lists.
            self._log.warning("deeproute.taken_back", turn=turn, trigger=asked.trigger, reason=cut or "unfinished")
            return TurnPlan(slots={HANDOFF_SLOT: answered.slot()}, cut_short=cut)
        self._log.info("deeproute.answered", turn=turn, trigger=asked.trigger, chars=len(report.output), cutShort=cut)
        return self._presented(report, cut, slots={HANDOFF_SLOT: answered.slot()})

    def _presented(self, report: ChildReport, cut: CutShort | None, *, slots: Mapping[str, JsonValue]) -> TurnPlan:
        """The engine's answer as the run's: its prose, with its settled outcome relayed for a chat that words it
        (WP-89); its whole text otherwise."""
        relayed = report.settled_event() if self._worded else None
        text = report.prose if relayed is not None else report.output
        return TurnPlan(
            synthetic=AssistantMessage(content=[TextPart(text=text)]),
            slots=slots,
            events=(relayed,) if relayed is not None else (),
            cut_short=cut,
        )

    def _present_cut_short(self, turn: int, previous: Sequence[TurnResult]) -> TurnPlan:
        """SPENDCONT: a ``code_task`` the model called itself (no hand-off asked for it) whose engine stopped early
        with an answer: that partial answer is the turn's answer, flagged. One parent instead spent its wrap-up on
        a second ``code_task``, which was refused, then said the file was "not ready yet": the child's 8.3 KB
        partial inventory never reached the person. A complete answer stays the model's to present."""
        result = next((r for r in previous if r.name == CODE_TASK), None)
        if result is None or result.status != "ok" or not isinstance(result.content, dict):
            return TurnPlan()
        report = ChildReport.read(result.content)
        cut = _cut(report)
        if (
            cut is None
            or result.content.get("success") is not True
            or not report.output.strip()
            or BRIEFING.brief(report).situation == "unanswered"
        ):
            return TurnPlan()
        self._log.info("deeproute.partial_presented", turn=turn, chars=len(report.output), cutShort=cut)
        return self._presented(report, cut, slots={})

    def _follow_up(self, turn: int, state: RunState, previous: Sequence[TurnResult], asked: Handoff) -> TurnPlan:
        """The turn after the instruction: when the model called other tools instead, the director takes this turn
        with the hand-off itself, once. A model that answered ended the run with its answer."""
        if asked.taken is not None or turn != asked.turn + 1 or not _settled(state):
            return TurnPlan()
        last = state.response_for(asked.turn)
        if last is None or not last.tool_calls:
            return TurnPlan()
        # The results this run gathered reach the engine with every code_task (HANDOFF-CTX), so the goal is the goal.
        call = ToolCall(id=f"deeproute-{turn}", name=CODE_TASK, arguments={"goal": _goal_text(state.slots, self._query)})
        self._log.info("deeproute.handoff_taken", turn=turn, trigger=asked.trigger, reason=asked.reason)
        return TurnPlan(
            synthetic=AssistantMessage(content=[], tool_calls=[call]),
            slots={HANDOFF_SLOT: Handoff(asked.turn, asked.trigger, asked.reason, taken=turn).slot()},
        )


# The card's own choices when the job offered fewer than two: the person types the answer, or lets the job decide.
TYPE_ANSWER = {"label": "Type your answer", "isUserInput": True}
LET_IT_DECIDE = {"label": "Go on with your best judgement"}
CARD_MAX_OPTIONS = 4


def job_question(question: str, options: Sequence[str]) -> dict[str, JsonValue]:
    """A job's question as one card question: its options (at most four; the app adds 'Something else' for a typed
    answer), or, with fewer than two, going on without an answer as the other choice."""
    labels: list[dict[str, JsonValue]] = [
        {"label": o.strip()[:120]} for o in list(dict.fromkeys(options))[:CARD_MAX_OPTIONS]
    ]
    if not labels:
        labels.append(dict(TYPE_ANSWER))
    if len(labels) < 2:
        labels.append(dict(LET_IT_DECIDE))
    return {"question": question.strip()[:300], "options": cast("JsonValue", labels), "multiSelect": False}


def _with_job_grounding(plan: TurnPlan, job: TurnResult | None) -> TurnPlan:
    """The plan, recording the job's typed grounding summary for the presenter (ADR-RUN-03 D2, D3)."""
    content = job.content if job is not None and isinstance(job.content, dict) else {}
    grounding = content.get(GROUNDING_KEY)
    if not isinstance(grounding, dict):
        return plan
    return dataclasses.replace(plan, slots={**plan.slots, JOB_GROUNDING_SLOT: grounding})


def routes(run: RunContext) -> bool:
    """Whether this run's loop hands deep work to the engine on its own: it may hand off (an engine authority is
    pinned and it is not a child), the org left the switch on, the operator's flag is on, and it is neither deep
    mode (whose orchestrator already plans and delegates) nor a person's ``/run``."""
    return (
        offers_code_task(run)
        and run.code_task_auto_route
        and not run.deep
        and run.sandbox_command_timeout_s is None
        and deep_routing_enabled()
    )


def deep_routing_enabled() -> bool:
    """The operator's switch (``HARNESS_DEEP_ROUTING``, on)."""
    return env_flag(HARNESS_DEEP_ROUTING)


def thresholds_from_env(log: HarnessLog) -> Thresholds:
    """The operator's thresholds; one it cannot read is logged and the defaults apply."""
    import os

    raw = os.getenv("HARNESS_DEEP_ROUTING_THRESHOLDS")
    try:
        return Thresholds.parse(raw)
    except ValueError as exc:
        log.warning("deeproute.thresholds_invalid", error=str(exc), choice="defaults")
        return Thresholds()


def _route_of(slots: Mapping[str, JsonValue]) -> JsonValue:
    goal = slots.get(GOAL_SLOT)
    return goal.get(ROUTE_KEY) if isinstance(goal, dict) else None


__all__ = [
    "ENGINE_FAILED_INSTRUCTION",
    "ENGINE_FAILED_WITH_WORK_INSTRUCTION",
    "ESCALATION_INSTRUCTION",
    "GROUNDED_SIGNAL",
    "HANDOFF_EVENT",
    "HANDOFF_SLOT",
    "JOB_ASK_INTENT",
    "REMEMBER_IN_HANDOFF",
    "ROUTE_KEY",
    "UPFRONT_INSTRUCTION",
    "DeepRouteDirector",
    "Handoff",
    "Measured",
    "RouteDecision",
    "Thresholds",
    "classify",
    "deep_routing_enabled",
    "escalation",
    "job_question",
    "measure",
    "routes",
    "thresholds_from_env",
]
