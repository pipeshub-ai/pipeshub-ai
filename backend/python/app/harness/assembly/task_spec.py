"""ADR-RUN-01 D5: a delegated task is a ``TaskSpec`` the harness composes, not a brief the model writes alone.

The engine child is given the person's request as they wrote it, the file it is to deliver, and the tool calls its
parent already made with their results (HANDOFF-CTX). The parent model adds only notes: what it put in code_task's
``goal`` and ``context``. A sentence of those notes that reads as a harness instruction (a hand-off, "call no other
tool") is dropped and logged: one model wrote PipesHub's steer into the context as "the only additional user
instruction is to delegate now via code_task once ... and call no other tools this turn", and the engine, which has
no code_task, stopped without starting. The person's request is never edited: it may itself name code_task.

The engine reads the rendered text; a person reads the parts (``JobTask``, BGUI-10). The job's Brief showed the whole
rendered prompt (the budget sentence to the model, the notes heading, the carried calls' raw JSON results), and text
cannot be split back reliably, so the parts travel beside it on the child's admission and are kept on its input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

from app.agents.agent_loop.connected_sources import ConnectedSources
from app.harness.assembly.handoff_instructions import ALL_TEXTS
from app.harness.contracts.generated.pipeshub.api.job_brief import JobTask
from harness_core.kernel.durable.harness_channel import HARNESS_FRAME
from harness_core.tools.call_tool import CALL_TOOL, unwrap_call

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.budget.run_budget import RunBudget
    from harness_core.kernel.durable.delegation import Gathered

DELIVERABLE_LINE: Final = "The file to deliver: {name}"
# R31HOT2: the skills the chat already chose for this task (preloaded at its start or loaded by name), as the
# deliverable line names the file: the engine has every skill mounted, and without this line no hint which one.
SKILLS_LINE: Final = "Skills the chat agent chose for this task (installed for you, read each one first): {names}"
# ADR-RUN-03 D1 (ANSWER-CONTRACT-SOURCES): where the check before the hand-off found the subject, when that was a
# connected source and not the person's PipesHub knowledge, so the job looks there.
FOUND_LINE: Final = "Where PipesHub found {subject}: {where}"
# The same fact on a connected source's own entry when the task lists its sources (D-DISC-2): told once, not twice.
FOUND_MARK: Final = "PipesHub found {subject} here"
# ADR-RUN-01 D4: the child's slice of its parent's budget, in plain terms (its own turn plan comes with its allowance).
BUDGET_LINE: Final = "This task's time and money: deliver your result by {by} (about {minutes} minutes from now)"
BUDGET_USD: Final = ", with at most USD {usd:.2f} of model use"
# A deadline further away than this does not bind a hand-off (the child's own working time does), and a clock time
# without its date would mislead: no line is given.
BUDGET_HORIZON_S: Final = 86_400
# ADR-OWD-07 D-DISC-2: the connected sources the task's tools come from, each with what it covers, as the deliverable
# line names the file: a fact about the task, never an instruction, so SR-17's frozen instructions stay as they are.
# A Codex job granted Rovo's 32 tools behind search_tools and never learnt the server was there.
SOURCES_HEADING: Final = "Connected sources this task can reach, besides company knowledge:"
NOTES_HEADING: Final = "Notes from the chat agent that handed this task to you:"
CONTEXT_HEADING: Final = "What earlier work in this conversation already produced:"
# HANDOFF-CTX: a loop made six Jira MCP calls, then handed off; the engine child started from the brief alone and
# made 13 calls, the same searches and issue reads again, its first refused for the cloudId the parent's first call
# had found. The child now starts from each call the run already made, its arguments and its result as the loop read
# it, sized like one large tool result.
GATHERED_HEADING: Final = (
    "Tool calls this chat already made before handing over, with their results. Use them: do not call a tool again "
    "with the same arguments; call one only for what they do not cover, or to read in full a result shortened here."
)
GATHERED_MAX_CHARS: Final = 24_000
GATHERED_RESULT_MAX_CHARS: Final = 6_000
GATHERED_ARGS_MAX_CHARS: Final = 600
# The most carried calls a job's brief names (the contract's bound); the engine is still given every one.
CARRIED_MAX: Final = 200
# A skill load names its skill by this argument; a name of another shape is not shown.
_SKILL_LOAD = "load_skill"
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SHORTENED = " … (shortened)"
_OVER_SIZE = "(result not repeated here: the hand-off's size was reached)"

# A note sentence that tells the engine to hand its work on or to stop using its tools: words for the chat model
# only, as the engine has no code_task. Run 8's leaked sentence names both.
_HAND_OFF = re.compile(
    r"\bcode_task\b|\bdelegat\w*|\bhand(?:ing)?\s+(?:it|this|the\s+\w+)\s+(?:off|over|on|to\s+the\s+(?:coding\s+)?engine)\b"
    r"|\bcall\s+no\s+other\s+tools?\b|\bdo\s+not\s+call\s+any\s+other\s+tools?\b|\[PipesHub\]",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# Seven words in a row from a harness instruction or the channel's frame: a copy, however it was cut.
_SHINGLE = 7


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[\w']+", text.lower())
    return {tuple(words[i : i + _SHINGLE]) for i in range(len(words) - _SHINGLE + 1)}


_HARNESS_SHINGLES: Final[frozenset[tuple[str, ...]]] = frozenset(
    shingle for text in (*ALL_TEXTS, HARNESS_FRAME) for shingle in _shingles(text)
)


def is_harness_text(sentence: str) -> bool:
    """Whether a sentence of the parent's notes is a harness instruction: a hand-off or tool rule, or a copy."""
    return bool(_HAND_OFF.search(sentence)) or not _shingles(sentence).isdisjoint(_HARNESS_SHINGLES)


def clean_notes(text: str) -> tuple[str, tuple[str, ...]]:
    """``text`` without its harness-instruction sentences, and the sentences dropped."""
    kept: list[str] = []
    dropped: list[str] = []
    for paragraph in text.split("\n"):
        sentences = [s for s in _SENTENCE.split(paragraph) if s.strip()]
        out = [s for s in sentences if not is_harness_text(s)]
        dropped.extend(s.strip() for s in sentences if is_harness_text(s))
        kept.append(" ".join(s.strip() for s in out))
    return "\n".join(kept).strip(), tuple(dropped)


@dataclass(frozen=True)
class TaskSpec:
    """What an engine child is given: the person's ``request`` as they wrote it, the file the run's pin names to deliver
    (ADR-RUN-01 D1), the parent's ``notes`` (harness text removed) and the calls it already made (``gathered``).
    ``dropped``: the note sentences removed, for the log."""

    request: str
    deliverable: str | None
    notes: str
    gathered: tuple[Gathered, ...]
    # What earlier work in the conversation produced (code_task's ``context``), harness text removed.
    earlier: str = ""
    dropped: tuple[str, ...] = ()
    # ADR-RUN-01 D4: the child's slice of its parent's budget (``RunBudget.child``) and when it was handed over
    # (epoch seconds); ``None``: the parent's budget names no deadline.
    budget: RunBudget | None = None
    handed_at_s: float = 0.0
    # FOUND_LINE as the hand-off writes it (``code_task.found_line``), when the subject was found in a connected
    # source. Empty: found nowhere but the knowledge, or not checked.
    found: str = ""
    # The same fact by place (``code_task.found_marks``): with every place among ``sources``, the list marks them and
    # FOUND_LINE is not repeated.
    found_at: Mapping[str, str] = field(default_factory=dict)
    sources: ConnectedSources = ConnectedSources()
    # R31HOT2: the skills the parent chose, by name, preloaded ones first, each once.
    skills: tuple[str, ...] = ()

    @classmethod
    def compose(
        cls,
        *,
        request: str,
        goal: object,
        context: object,
        gathered: Sequence[Gathered] = (),
        deliverable: str | None = None,
        budget: RunBudget | None = None,
        now_s: float = 0.0,
        found: str = "",
        found_at: Mapping[str, str] | None = None,
        sources: ConnectedSources | None = None,
        preloaded_skills: Sequence[str] = (),
    ) -> TaskSpec:
        """The spec of one hand-off: ``goal`` and ``context`` are the parent model's code_task arguments;
        ``deliverable``: the file the parent run's pin names (ADR-RUN-01 D1), never read from the words again;
        ``budget``: the parent run's, whose child slice the task is given (D4), at ``now_s``;
        ``preloaded_skills``: the skills the parent's start loaded in full, named with those it loaded by name."""
        person = request.strip()
        dropped: list[str] = []

        def cleaned(value: object) -> str:
            # A goal that repeats the request (the director's own hand-off) adds nothing.
            if not isinstance(value, str) or not value.strip() or value.strip() == person:
                return ""
            text, removed = clean_notes(value)
            dropped.extend(removed)
            return text

        notes = cleaned(goal)
        earlier = cleaned(context)
        return cls(
            request=person,
            deliverable=deliverable,
            notes=notes,
            earlier=earlier,
            gathered=tuple(gathered),
            dropped=tuple(dropped),
            budget=_slice(budget, now_s),
            handed_at_s=now_s,
            found=found,
            found_at=dict(found_at or {}),
            sources=sources or ConnectedSources(),
            skills=chosen_skills(preloaded_skills, gathered),
        )

    def _marked(self) -> list[str] | None:
        """The sources' lines with each place the subject was found in marked (``FOUND_MARK``); ``None`` when there is
        no such mark or a place is not among the listed sources (FOUND_LINE then says it on its own line)."""
        if not self.found_at or not self.sources:
            return None
        listed = self.sources.listed()
        if not set(self.found_at) <= {s.name for s in listed}:
            return None
        return [f"{s.line()} · {self.found_at[s.name]}" if s.name in self.found_at else s.line() for s in listed]

    def _notes(self) -> str:
        earlier = f"{CONTEXT_HEADING}\n{self.earlier}" if self.earlier else ""
        return "\n\n".join(part for part in (self.notes, earlier) if part)

    def render(self) -> str:
        """The child's task text: the request first, as the person's message to it. A host that names no request
        (a run with no input text) gives the notes as the task."""
        notes = self._notes()
        if self.request:
            sections = [self.request]
            if self.deliverable:
                sections.append(DELIVERABLE_LINE.format(name=self.deliverable))
            if self.skills:
                sections.append(SKILLS_LINE.format(names=", ".join(self.skills)))
            marked = self._marked()
            if self.found and marked is None:
                sections.append(self.found)
            if self.budget is not None:
                sections.append(self._budget_line(self.budget))
            if self.sources:
                sections.append("\n".join([SOURCES_HEADING, *(f"- {line}" for line in marked or self.sources.lines())]))
            if notes:
                sections.append(f"{NOTES_HEADING}\n{notes}")
        else:
            sections = [notes or "(no goal given)"]
        if self.gathered:
            sections.append(_gathered(self.gathered))
        return "\n\n".join(sections)

    def parts(self) -> JsonValue:
        """The task as a person reads it (``JobTask``): the request, the notes and the limits as facts, and the
        carried calls by tool alone, never their arguments or results."""
        task = JobTask.model_validate(
            {
                "request": self.request,
                "notes": self.notes,
                "earlier": self.earlier,
                "fileName": (self.deliverable or "")[:512] or None,
                "dueAt": _iso(self._deadline(self.budget)) if self.budget is not None else None,
                "maxCostUsd": self.budget.max_cost_usd if self.budget is not None else None,
                "carried": [_carried(item) for item in self.gathered[:CARRIED_MAX]],
                **({"sources": self.sources.parts()} if self.sources else {}),
            }
        )
        out = task.model_dump(mode="json", by_alias=True)
        if not self.sources:
            # Absent, never empty, for a task with none: a job admitted before D-DISC-2 reads the same.
            out.pop("sources", None)
        # The generated models read an optional field as `X | None = None`, so the dump writes null where the
        # contract allows only absent: ``skill`` for a call that loaded none, ``tools`` for a server known only from
        # its stored metadata (D-LAZY-7), whose null Node refused, failing every hand-off that named one.
        out["carried"] = [{k: v for k, v in c.items() if v is not None} for c in out["carried"]]
        if "sources" in out:
            out["sources"] = [{k: v for k, v in s.items() if v is not None} for s in out["sources"]]
        return cast("JsonValue", out)

    def _deadline(self, budget: RunBudget) -> float:
        return budget.deadline_at_s or self.handed_at_s

    def _budget_line(self, budget: RunBudget) -> str:
        deadline = self._deadline(budget)
        line = BUDGET_LINE.format(
            by=datetime.fromtimestamp(deadline, tz=UTC).strftime("%H:%M UTC"),
            minutes=max(1, round((deadline - self.handed_at_s) / 60)),
        )
        if budget.max_cost_usd is not None:
            line += BUDGET_USD.format(usd=budget.max_cost_usd)
        return line + "."


def request_of(task: str) -> str:
    """The person's request at the head of a task ``TaskSpec.render`` wrote: what the child's grounding is about
    (ADR-RUN-03 D2). A task with no request gives its notes, which name the subject as well as anything does."""
    cut = len(task)
    for heading in (
        DELIVERABLE_LINE.split("{", 1)[0], SKILLS_LINE.split("{", 1)[0], FOUND_LINE.split("{", 1)[0],
        BUDGET_LINE.split("{", 1)[0],
        SOURCES_HEADING, NOTES_HEADING, GATHERED_HEADING,
    ):
        at = task.find("\n\n" + heading)
        if at >= 0:
            cut = min(cut, at)
    return task[:cut].strip()


def _slice(budget: RunBudget | None, now_s: float) -> RunBudget | None:
    """The child's slice of ``budget`` (D4) when its deadline binds the hand-off; ``None`` otherwise."""
    if budget is None:
        return None
    child = budget.child()
    left = child.left_s(now_s)
    return child if left is not None and left <= BUDGET_HORIZON_S else None


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def chosen_skills(preloaded: Sequence[str], gathered: Sequence[Gathered]) -> tuple[str, ...]:
    """The skills a parent chose: those its start loaded in full, then those it loaded by name and got, each once."""
    loaded = [c.get("skill") for c in map(_carried, gathered) if not c["failed"]]
    names = [*preloaded, *(name for name in loaded if isinstance(name, str))]
    return tuple(dict.fromkeys(name for name in names if _SKILL_NAME.match(name)))


def _carried(item: Gathered) -> dict[str, JsonValue]:
    """One carried call by its tool: a lazily found tool by the name ``call_tool`` called it by, never ``call_tool``."""
    name, arguments = item.call.name, item.call.arguments
    if name == CALL_TOOL:
        inner = unwrap_call(arguments)
        if not isinstance(inner, str):
            name, arguments = inner
    out: dict[str, JsonValue] = {"tool": name[:512] or "tool", "failed": item.status not in (None, "ok")}
    skill = arguments.get("name") if name == _SKILL_LOAD else None
    if isinstance(skill, str) and _SKILL_NAME.match(skill):
        out["skill"] = skill
    return out


def _gathered(gathered: Sequence[Gathered]) -> str:
    """The calls the parent already made: every call named with its arguments, each result cut to
    ``GATHERED_RESULT_MAX_CHARS`` and all of them to ``GATHERED_MAX_CHARS``, oldest first."""
    lines: list[str] = []
    left = GATHERED_MAX_CHARS
    for item in gathered:
        arguments = _clip(json.dumps(item.call.arguments, ensure_ascii=False, sort_keys=True), GATHERED_ARGS_MAX_CHARS)
        failed = "" if item.status in (None, "ok") else " (failed)"
        lines.append(f"- {item.call.name}({arguments}){failed}:")
        if left <= 0:
            lines.append(f"  {_OVER_SIZE}")
            continue
        content = item.outcome if isinstance(item.outcome, str) else json.dumps(item.outcome, ensure_ascii=False)
        shown = _clip(content, min(GATHERED_RESULT_MAX_CHARS, left))
        left -= len(shown)
        lines.append(f"  {shown}")
    return f"{GATHERED_HEADING}\n" + "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - len(_SHORTENED), 0)] + _SHORTENED


__all__ = [
    "BUDGET_HORIZON_S",
    "BUDGET_LINE",
    "BUDGET_USD",
    "CARRIED_MAX",
    "CONTEXT_HEADING",
    "DELIVERABLE_LINE",
    "GATHERED_HEADING",
    "GATHERED_MAX_CHARS",
    "GATHERED_RESULT_MAX_CHARS",
    "NOTES_HEADING",
    "SOURCES_HEADING",
    "TaskSpec",
    "clean_notes",
    "is_harness_text",
    "request_of",
]
