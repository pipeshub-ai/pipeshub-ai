"""The run outcome ledger (ADR-RUN-01 D1): the only record of what a run produced.

The kernel records typed facts as it learns them and journals each turn's new facts as one
``x.pipeshub.outcome.recorded``; a run's ledger is the fold of those events. A run journaled before the ledger
existed replays through ``ledger_from_events``, which derives the same facts from its tool outcomes, turns,
completion, children and workspace commits. Which file is the deliverable, and whether it is still owed, is read
from these facts and the pinned ``Deliverable``: nothing parses the request again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from harness_core.journal.types import OUTCOME_LEDGER, NewEvent

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.engines.outcome.deliverable import Deliverable
    from harness_core.journal.types import JournalEvent
    from harness_core.workspace.types import WorkspaceFile

OUTCOME_RECORDED = "x.pipeshub.outcome.recorded"
OUTCOME_SETTLED = "x.pipeshub.outcome.settled"
OUTPUTS_DIR = "out"
# WP-70: below this a file is a placeholder unless its text reads as a short deliverable at a normal turn end
# (a 178-byte "Research in progress" report.md was finalized).
PLACEHOLDER_MAX_BYTES = 1536
# The most facts one recorded event carries; a research child can make about 150 fetches in a turn.
MAX_FACTS_PER_EVENT = 500
MAX_REF_CHARS = 2048
ArtifactState = Literal["placeholder", "draft", "final"]
Writer = Literal["engine", "wrap_up"]
LimitKind = Literal["budget", "turns", "time", "stall", "provider", "engine_error", "secret_blocked"]
_LIMITS: tuple[str, ...] = ("budget", "turns", "time", "stall", "provider", "engine_error", "secret_blocked")


@dataclass(frozen=True, slots=True)
class ArtifactWritten:
    """A file the run wrote for the person (under out/, or named as the deliverable), at a workspace revision.
    ``bytes`` is ``None`` when replayed from a journal that did not record it; ``placeholder``: its text reads as one."""

    kind: ClassVar[str] = "artifact_written"
    path: str
    revision: int
    bytes: int | None
    state: ArtifactState = "draft"
    by: Writer = "engine"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def in_outputs(self) -> bool:
        return self.path.startswith(f"{OUTPUTS_DIR}/")

    @property
    def large(self) -> bool:
        return self.bytes is not None and self.bytes >= PLACEHOLDER_MAX_BYTES


@dataclass(frozen=True, slots=True)
class SourceGathered:
    """A tool result the run gathered: ``ref`` is the page's URL when it has one, else ``tool:argsDigest``."""

    kind: ClassVar[str] = "source_gathered"
    ref: str
    tool: str
    url: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class NoteSaved:
    """A note kept for the person or the run that continues: the handoff, the partial answer, the engine's notes."""

    kind: ClassVar[str] = "note_saved"
    path: str


@dataclass(frozen=True, slots=True)
class StepCompleted:
    kind: ClassVar[str] = "step_completed"
    turn: int


@dataclass(frozen=True, slots=True)
class LimitReached:
    kind: ClassVar[str] = "limit_reached"
    limit: LimitKind


@dataclass(frozen=True, slots=True)
class ChildEnded:
    kind: ClassVar[str] = "child_ended"
    run_id: str
    status: str


@dataclass(frozen=True, slots=True)
class SaveRefused:
    """The workspace store refused a save; the last saved revision stands (ADR-RUN-01 D6)."""

    kind: ClassVar[str] = "save_refused"
    reason: str


Fact = ArtifactWritten | SourceGathered | NoteSaved | StepCompleted | LimitReached | ChildEnded | SaveRefused


@dataclass
class OutcomeLedger:
    """The facts of one run, in the order they were recorded; a fact recorded twice is kept once."""

    facts: list[Fact] = field(default_factory=list[Fact])

    def record(self, *facts: Fact) -> list[Fact]:
        """Add ``facts``; returns the ones that were new."""
        seen = set(self.facts)
        added = [f for f in dict.fromkeys(facts) if f not in seen]
        self.facts.extend(added)
        return added

    @property
    def sources(self) -> tuple[SourceGathered, ...]:
        by_ref: dict[str, SourceGathered] = {}
        for fact in self.facts:
            if isinstance(fact, SourceGathered) and fact.ref not in by_ref:
                by_ref[fact.ref] = fact
        return tuple(by_ref.values())

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f.path for f in self.facts if isinstance(f, NoteSaved)))

    @property
    def limit(self) -> LimitKind | None:
        found: list[LimitKind] = [f.limit for f in self.facts if isinstance(f, LimitReached)]
        return found[-1] if found else None

    @property
    def save_refused(self) -> SaveRefused | None:
        found = [f for f in self.facts if isinstance(f, SaveRefused)]
        return found[-1] if found else None

    def artifacts(self) -> dict[str, ArtifactWritten]:
        """Each file's latest record, by path."""
        latest: dict[str, ArtifactWritten] = {}
        for fact in self.facts:
            if isinstance(fact, ArtifactWritten):
                latest[fact.path] = fact
        return latest

    def written(self, deliverable: Deliverable) -> ArtifactWritten | None:
        """WP-81: the file the engine wrote for a request that asked for one, larger than a placeholder: the file the
        request names (under out/ first, else anywhere), else the largest text file under out/."""
        if not deliverable.is_file:
            return None
        real = [a for a in self.artifacts().values() if a.large and a.by == "engine"]
        if deliverable.name is not None:
            named = sorted((not a.in_outputs, a.path) for a in real if a.name == deliverable.name)
            if named:
                return next(a for a in real if a.path == named[0][1])
        text = [a for a in real if a.in_outputs and a.name.lower().endswith((".md", ".markdown", ".txt"))]
        return max(text, key=lambda a: (a.bytes or 0, a.path)) if text else None

    def owed_after_cut(self, deliverable: Deliverable) -> str | None:
        """WP-70: the file a cut-short run still owes, as its partial is saved: the requested one while no file under
        out/ is larger than a placeholder; ``None`` when it asked for none or one was written."""
        if not deliverable.is_file or any(a.in_outputs and a.large for a in self.artifacts().values()):
            return None
        return deliverable.partial_name()

    def owed_at_end(self, deliverable: Deliverable) -> str | None:
        """WP-70: the file a turn that ended normally still owes: only one the request names (an engine that decided
        it was done may have answered a request for "a file" in the chat), while neither out/ nor a file of that name
        holds more than a placeholder, and no file of that name reads as a short deliverable."""
        name = deliverable.name if deliverable.is_file else None
        if name is None:
            return None
        for artifact in self.artifacts().values():
            if artifact.large and (artifact.in_outputs or artifact.name == name):
                return None
            if artifact.name == name and artifact.state != "placeholder" and artifact.bytes is not None:
                return None
        return name


# What a file an engine writes before it has written anything says ("Research in progress. Final report
# will ..."): at a normal turn end a small file is the engine's answer unless it reads like this.
_PLACEHOLDER_TEXT = re.compile(
    r"\b(?:in progress|placeholder|to be (?:written|completed|added|filled)|will (?:follow|be (?:written|added))"
    r"|coming soon|not (?:yet )?written|todo|tbd|wip|lorem ipsum)\b",
    re.IGNORECASE,
)


def is_placeholder_text(text: str) -> bool:
    """Whether a small file's text is a placeholder rather than a short deliverable (WP-70)."""
    return not text.strip() or _PLACEHOLDER_TEXT.search(text) is not None


def workspace_artifacts(
    files: Mapping[str, WorkspaceFile],
    revision: int,
    deliverable: Deliverable,
    *,
    texts: Mapping[str, str] | None = None,
) -> list[ArtifactWritten]:
    """The files of a workspace revision the ledger records: those under out/ and those named as the deliverable
    (a symbolic link is no file the run wrote). A small file is a placeholder unless ``texts`` holds its text and it
    reads as more than one."""
    found: list[ArtifactWritten] = []
    for path, entry in files.items():
        if entry.is_symlink or not (path.startswith(f"{OUTPUTS_DIR}/") or path.rsplit("/", 1)[-1] == deliverable.name):
            continue
        size = entry.content.size_bytes
        text = (texts or {}).get(path)
        placeholder = size < PLACEHOLDER_MAX_BYTES and (text is None or is_placeholder_text(text))
        found.append(ArtifactWritten(path, revision, size, "placeholder" if placeholder else "draft"))
    return found


def fact_wire(fact: Fact) -> dict[str, JsonValue]:
    """One fact as ``x.pipeshub.outcome.recorded`` carries it."""
    match fact:
        case ArtifactWritten():
            return {
                "kind": fact.kind, "path": fact.path, "revision": fact.revision,
                **({"bytes": fact.bytes} if fact.bytes is not None else {}), "state": fact.state, "by": fact.by,
            }
        case SourceGathered():
            return {
                "kind": fact.kind, "ref": fact.ref[:MAX_REF_CHARS], "tool": fact.tool,
                **({"url": fact.url[:MAX_REF_CHARS]} if fact.url is not None else {}),
                **({"title": fact.title[:300]} if fact.title is not None else {}),
            }
        case NoteSaved():
            return {"kind": fact.kind, "path": fact.path}
        case StepCompleted():
            return {"kind": fact.kind, "turn": fact.turn}
        case LimitReached():
            return {"kind": fact.kind, "limit": fact.limit}
        case ChildEnded():
            return {"kind": fact.kind, "runId": fact.run_id, "status": fact.status}
        case SaveRefused():
            return {"kind": fact.kind, "reason": fact.reason[:200]}


def fact_of(wire: Mapping[str, object]) -> Fact | None:
    """The fact ``wire`` holds; ``None`` for one this worker does not know (a newer writer's)."""
    kind = wire.get("kind")

    def text(key: str) -> str | None:
        value = wire.get(key)
        return value if isinstance(value, str) else None

    def number(key: str) -> int | None:
        value = wire.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    if kind == "artifact_written" and (path := text("path")) and (revision := number("revision")) is not None:
        state = wire.get("state") if wire.get("state") in ("placeholder", "draft", "final") else "draft"
        by = "wrap_up" if wire.get("by") == "wrap_up" else "engine"
        return ArtifactWritten(path, revision, number("bytes"), cast("ArtifactState", state), by)
    if kind == "source_gathered" and (ref := text("ref")) and (tool := text("tool")):
        return SourceGathered(ref, tool, text("url"), text("title"))
    if kind == "note_saved" and (path := text("path")):
        return NoteSaved(path)
    if kind == "step_completed" and (turn := number("turn")) is not None:
        return StepCompleted(turn)
    if kind == "limit_reached" and wire.get("limit") in _LIMITS:
        return LimitReached(cast("LimitKind", wire["limit"]))
    if kind == "child_ended" and (run_id := text("runId")) and (status := text("status")):
        return ChildEnded(run_id, status)
    if kind == "save_refused" and (reason := text("reason")) is not None:
        return SaveRefused(reason)
    return None


def recorded_events(facts: Iterable[Fact]) -> list[NewEvent]:
    """The ``x.pipeshub.outcome.recorded`` events of ``facts``, at most ``MAX_FACTS_PER_EVENT`` each."""
    wires = [fact_wire(f) for f in facts]
    return [
        NewEvent(OUTCOME_RECORDED, {"facts": cast("JsonValue", wires[i : i + MAX_FACTS_PER_EVENT])}, critical=False)
        for i in range(0, len(wires), MAX_FACTS_PER_EVENT)
    ]


def ledger_from_events(events: Iterable[JournalEvent], *, run_id: str) -> OutcomeLedger:
    """The run's ledger from its journal: its recorded facts, and for what it did before it recorded any (a run
    journaled before the ledger), the facts its other events imply. Tool outcomes count as gathered by invocation."""
    ledger = OutcomeLedger()
    implied: list[Fact] = []
    tools: dict[str, str] = {}
    for event in events:
        if event.run_id != run_id:
            continue
        data = cast("dict[str, object]", event.data)
        if event.type == OUTCOME_RECORDED:
            facts = data.get("facts")
            for wire in cast("list[object]", facts) if isinstance(facts, list) else []:
                fact = fact_of(cast("dict[str, object]", wire)) if isinstance(wire, dict) else None
                if fact is not None:
                    _ = ledger.record(fact)
            continue
        implied.extend(_implied(event.type, data, tools))
    if not ledger.facts:
        _ = ledger.record(*implied)
    return ledger


def _implied(kind: str, data: Mapping[str, object], tools: dict[str, str]) -> list[Fact]:
    """What one event of a run journaled before the ledger says about its outcome."""
    if kind == "tool.intent" and isinstance(data.get("invocationId"), str):
        tools[str(data["invocationId"])] = str(data.get("toolId") or data.get("name") or "tool").rsplit("/", 1)[-1]
    elif kind == "tool.outcome" and data.get("status") == "ok" and isinstance(data.get("invocationId"), str):
        invocation = str(data["invocationId"])
        return [SourceGathered(f"invocation:{invocation}", tools.get(invocation, "tool"))]
    elif kind == "turn.completed" and isinstance(turn := data.get("turn"), int):
        return [StepCompleted(turn)]
    elif kind == "run.completed" and data.get("truncatedBy") in _LIMITS:
        return [LimitReached(cast("LimitKind", data["truncatedBy"]))]
    elif kind == "child.completed" and isinstance(data.get("childRunId"), str):
        return [ChildEnded(str(data["childRunId"]), str(data.get("outcome") or "completed"))]
    elif kind == "workspace.committed" and isinstance(revision := data.get("revision"), int):
        changed = data.get("changed")
        listed = cast("list[object]", changed) if isinstance(changed, list) else []
        paths = [p.removeprefix("/") for p in listed if isinstance(p, str)]
        return [ArtifactWritten(p, revision, None) for p in paths if p.startswith(f"{OUTPUTS_DIR}/")]
    return []


__all__ = [
    "MAX_FACTS_PER_EVENT",
    "OUTCOME_LEDGER",
    "OUTCOME_RECORDED",
    "OUTCOME_SETTLED",
    "PLACEHOLDER_MAX_BYTES",
    "ArtifactState",
    "ArtifactWritten",
    "ChildEnded",
    "Fact",
    "LimitKind",
    "LimitReached",
    "NoteSaved",
    "OutcomeLedger",
    "SaveRefused",
    "SourceGathered",
    "StepCompleted",
    "is_placeholder_text",
    "workspace_artifacts",
    "fact_of",
    "fact_wire",
    "ledger_from_events",
    "recorded_events",
]
