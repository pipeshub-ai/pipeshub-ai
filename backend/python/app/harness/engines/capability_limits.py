"""What an engine job cannot do in its run, said plainly (ADR-RUN-03 D5, ADR-REPO-01).

A request to research two products by reading their docs and code repositories went to a Codex job whose run could
clone no repository. Nothing told the job, so it read web pages instead, and its report never said it
had not read either code base. The answer contract (say plainly what could not be found) holds for what could not be
*done* too, and it is built from what the run holds, never from what a model wrote:

* ``run_limits`` reads the run's own facts as it starts (web access, the git hosts it may clone from, the sources the
  person has not connected): the brief's "What this task can't do here" (``limits_instructions``).
* ``refused_clones`` reads the job's journal for the clones that did not happen (declined by the person, refused by a
  rule, a host not allowed): with the run's limits they travel in the job's hand-back (``LIMITS_KEY``).
* ``limits_note`` and ``limits_summary`` turn them into the parent's instruction and the job step's line, so the person
  is told even when a model leaves it out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from app.harness.repositories import FETCHED_EVENT, UNAVAILABLE_EVENT
from harness_core.provenance.stamp import PROVENANCE_EVENT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from app.harness.repositories import NamedRepository
    from harness_core.journal.types import JournalEvent

# Where a job's hand-back carries them, beside its grounding.
LIMITS_KEY: Final = "limits"
# What the parent's model reads beside the job's output when there are any.
LIMITS_NOTE_KEY: Final = "couldNotDo"
# The person declined the card (``harness_core.effects.types.APPROVAL_DENIAL``); any other rule is a policy's.
_DECLINED_RULE: Final = "interaction_denied"
_WEB_ENABLE: Final = "turn on web search for the chat, or give the agent web search"
_HOSTS_ENABLE: Final = "ask whoever runs PipesHub to allow a repository host"
_MAX_LIMITS: Final = 10
# What the run met while it worked, as the answer's typed view names it (``x.pipeshub.answer.finalized`` ``limits``).
DECLINED: Final = "repository.declined"
REFUSED: Final = "repository.refused"
NOT_ALLOWED: Final = "repository.not_allowed"
FAILED: Final = "repository.failed"
# R41-CLONE: a repository the task named that the run never cloned (``uncloned``).
NOT_CLONED: Final = "repository.not_cloned"
# The same fact for a Node that does not read the kind typed (no ``repository_not_cloned``): words only.
NOT_CLONED_WORDS: Final = "repositories.not_cloned"
MET_KINDS: Final = (DECLINED, REFUSED, NOT_ALLOWED, FAILED, NOT_CLONED)
# A file attached to the turn that never reached the model (R33JGAPS-FU-6): the reader may not open it, or its content
# could not be read (a file kind no reader takes, or its stored content is gone).
ATTACHMENT_NOT_ACCESSIBLE: Final = "attachment.not_accessible"
ATTACHMENT_UNREADABLE: Final = "attachment.unreadable"
# R46FIX-2: a file handed to a job over the per-input cap; for a Node without ``attachment_too_large``, unreadable.
ATTACHMENT_TOO_LARGE: Final = "attachment.too_large"
ATTACHMENT_KINDS: Final = (ATTACHMENT_NOT_ACCESSIBLE, ATTACHMENT_UNREADABLE, ATTACHMENT_TOO_LARGE)
ANSWER_KINDS: Final = (*MET_KINDS, *ATTACHMENT_KINDS)
_TARGET_MAX_CHARS: Final = 300
# The schema's bound on an item's ``count`` (could-not-do.schema.json).
_COUNT_MAX: Final = 100


@dataclass(frozen=True, slots=True)
class CapabilityLimit:
    """One thing the run cannot do: what (``cannot``), why, and what would enable it (``enable``, may be empty)."""

    kind: str
    cannot: str
    why: str
    enable: str = ""
    # What it could not read (a repository's host and path) and how many of them, for the answer's typed view.
    target: str = ""
    count: int = 1

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"kind": self.kind, "cannot": self.cannot, "why": self.why}
        if self.enable:
            out["enable"] = self.enable
        if self.target:
            out["target"] = self.target
        if self.count != 1:
            out["count"] = self.count
        return out

    @classmethod
    def from_json(cls, raw: object) -> CapabilityLimit | None:
        if not isinstance(raw, dict):
            return None
        data = cast("dict[str, Any]", raw)
        kind, cannot, why = data.get("kind"), data.get("cannot"), data.get("why")
        if not (isinstance(kind, str) and isinstance(cannot, str) and isinstance(why, str)):
            return None
        enable, target, count = data.get("enable"), data.get("target"), data.get("count")
        return cls(
            kind, cannot, why, enable if isinstance(enable, str) else "", target if isinstance(target, str) else "",
            count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1,
        )

    def sentence(self) -> str:
        return f"{self.cannot}: {self.why}" + (f" ({self.enable})" if self.enable else "")


def run_limits(
    *, web: bool, repository_hosts: Sequence[Mapping[str, JsonValue]], unavailable: Sequence[Any]
) -> tuple[CapabilityLimit, ...]:
    """The run's limits as it starts. ``unavailable``: the granted toolsets and MCP servers whose credential the
    person has not connected (``UnavailableItem``)."""
    limits: list[CapabilityLimit] = []
    if not web:
        limits.append(CapabilityLimit("web", "Search or read the web", "this task has no web access", _WEB_ENABLE))
    if not repository_hosts:
        limits.append(
            CapabilityLimit("repositories", "Clone or read code repositories", "this task has no web access", _WEB_ENABLE)
            if not web
            else CapabilityLimit(
                "repositories", "Clone or read code repositories",
                "cloning repositories is turned off for agents here", _HOSTS_ENABLE,
            )
        )
    for item in unavailable:
        name = str(getattr(item, "name", "") or "")
        if name:
            limits.append(CapabilityLimit(
                f"source:{getattr(item, 'kind', 'source')}", f"Use {name}", "the person has not connected it",
                f"connect {name} in PipesHub",
            ))
    return tuple(limits[:_MAX_LIMITS])


def limits_instructions(limits: Sequence[CapabilityLimit]) -> str:
    """The brief's section: one line per limit, then how to answer when the request needs one. "" when none."""
    if not limits:
        return ""
    lines = "\n".join(f"- {limit.sentence()}" for limit in limits)
    return (
        "\n\nWhat this task can't do here:\n" + lines + "\nWhen the request needs one of these, do what you can "
        "without it and say plainly in your answer what you could not do, why, and what would enable it. Never "
        "present your answer as covering something you could not read."
    )


def refused_clones(events: Sequence[JournalEvent], tool_id: str) -> tuple[CapabilityLimit, ...]:
    """The job's repository clones that did not happen, from its journal: declined by the person, refused by a rule
    or because the host is not allowed (``x.pipeshub.repository.unavailable`` names it), or failed.

    A clone the person was asked about is journaled through its card, not as a ``tool.intent``: the card's
    ``interaction.explained`` names the tool and the repository, its ``interaction.resolved`` the decision (the
    lane's declined clone carried no intent, so reading intents alone missed every decline)."""
    asked: set[str] = set()
    denied: dict[str, str] = {}
    failed: set[str] = set()
    not_allowed: list[str] = []
    cards: dict[str, str] = {}
    declined: list[str] = []
    for event in events:
        data = cast("Mapping[str, Any]", event.data)
        invocation = str(data.get("invocationId") or "")
        if event.type == "tool.intent" and data.get("toolId") == tool_id:
            asked.add(invocation)
        elif event.type == "tool.denied" and invocation in asked:
            denied[invocation] = str(data.get("rule") or "")
        elif event.type == "tool.outcome" and invocation in asked and data.get("status") != "ok":
            failed.add(invocation)
        elif event.type == UNAVAILABLE_EVENT:
            not_allowed.append(f"{data.get('host')}{data.get('path')}")
        elif event.type == "interaction.explained" and data.get("toolId") == tool_id:
            destination = data.get("destination")
            label = cast("dict[str, Any]", destination).get("label") if isinstance(destination, dict) else None
            cards[str(data.get("interactionId"))] = str(label).split(" (", 1)[0] if label else ""
        elif event.type == "interaction.resolved" and data.get("decision") == "denied":
            interaction = str(data.get("interactionId"))
            if interaction in cards:
                declined.append(cards[interaction])
    ruled = sum(1 for rule in denied.values() if rule != _DECLINED_RULE)
    unnamed = 0 if declined else sum(1 for rule in denied.values() if rule == _DECLINED_RULE)
    other = len(failed - denied.keys()) - len(not_allowed)
    limits = [
        CapabilityLimit(NOT_ALLOWED, f"Read {label}", "its host is not one this server may clone from",
                        _HOSTS_ENABLE, target=label)
        for label in dict.fromkeys(not_allowed)
    ]
    limits += [
        CapabilityLimit(DECLINED, f"Read {label}" if label else _clones(1), "the person declined the clone", target=label)
        for label in dict.fromkeys(declined)
    ]
    if unnamed:
        limits.append(CapabilityLimit(DECLINED, _clones(unnamed), "the person declined the clone", count=unnamed))
    if ruled:
        limits.append(CapabilityLimit(REFUSED, _clones(ruled), "a rule of your organization refused it", count=ruled))
    if other > 0:
        limits.append(CapabilityLimit(FAILED, _clones(other), "the clone failed", count=other))
    return tuple(limits[:_MAX_LIMITS])


def uncloned(
    events: Sequence[JournalEvent], named: Sequence[NamedRepository], met: Sequence[CapabilityLimit], *, typed: bool
) -> tuple[CapabilityLimit, ...]:
    """R41-CLONE: the repositories the task named that the run never read from a clone, from its journal: neither
    fetched (``x.pipeshub.repository.fetched``, or a repository source of a result's provenance) nor already one of
    ``met`` (declined, refused, failed). A host the run may not clone from says so; otherwise it was never cloned.
    ``typed``: the claim's Node reads ``repository.not_cloned``; else the fact travels as words only."""
    fetched: set[str] = set()
    for event in events:
        data = cast("Mapping[str, Any]", event.data)
        if event.type == FETCHED_EVENT:
            fetched.add(f"{data.get('host')}/{data.get('repository')}".lower())
        elif event.type == PROVENANCE_EVENT:
            provenance = data.get("provenance")
            for source in cast("Mapping[str, Any]", provenance).get("sources") or [] if isinstance(provenance, dict) else []:
                if isinstance(source, dict) and source.get("kind") == "repository":
                    url = str(cast("Mapping[str, Any]", source).get("url") or "")
                    fetched.add(url.removeprefix("https://").removesuffix(".git").lower())
    already = {limit.target.lower() for limit in met if limit.target}
    out: list[CapabilityLimit] = []
    for repository in named:
        key = repository.label.lower()
        if key in fetched or key in already:
            continue
        if not repository.allowed:
            out.append(CapabilityLimit(NOT_ALLOWED, f"Read {repository.label}", "its host is not one this server may clone from",
                                       _HOSTS_ENABLE, target=repository.label))
        else:
            out.append(CapabilityLimit(NOT_CLONED if typed else NOT_CLONED_WORDS, f"Read {repository.label}",
                                       "it was never cloned, so its code was not read", target=repository.label))
    return tuple(out[:_MAX_LIMITS])


@dataclass(frozen=True, slots=True)
class InputsAccepted:
    """What the claim's Node reads of the files handed to a job that it went on without: ``too_large``, the kind
    ``attachment.too_large`` (R46FIX-2), else such a file travels as unreadable; ``counted``, a ``count`` on an item
    standing for several files it may not name (R46FIX-FU-2), else the item carries none and reads as one file."""

    too_large: bool = True
    counted: bool = True

    @classmethod
    def of(cls, accepts: Callable[[str], bool]) -> InputsAccepted:
        from harness_core.journal.types import ATTACHMENT_COUNT
        from harness_core.journal.types import ATTACHMENT_TOO_LARGE as TOO_LARGE_FEATURE

        return cls(too_large=accepts(TOO_LARGE_FEATURE), counted=accepts(ATTACHMENT_COUNT))


def skipped_inputs(
    events: Sequence[JournalEvent], *, accepted: InputsAccepted = InputsAccepted()
) -> tuple[CapabilityLimit, ...]:
    """R36COVER-FU-3: the files handed to the job that it refused on its own and went on without (over the per-input
    cap, or gone since the hand-over), from its first ``turn.started`` (``skippedInputs``), as the claim's Node reads
    them (``accepted``)."""
    limits: list[CapabilityLimit] = []
    unnamed: dict[tuple[str, str], int] = {}
    for event in events:
        data = cast("Mapping[str, Any]", event.data)
        if event.type != "turn.started" or not isinstance(data.get("skippedInputs"), list):
            continue
        for item in cast("list[object]", data["skippedInputs"]):
            if not isinstance(item, dict):
                continue
            skipped = cast("Mapping[str, Any]", item)
            found = skipped.get("name")
            name = found if isinstance(found, str) else ""
            words = str(skipped.get("words") or "it could not be given to the task")
            too_large = skipped.get("reason") == "too_large" and accepted.too_large
            kind = ATTACHMENT_TOO_LARGE if too_large else ATTACHMENT_UNREADABLE
            if name:
                limits.append(CapabilityLimit(kind, f"Read {name}", words, target=name))
            else:
                unnamed[(kind, words)] = unnamed.get((kind, words), 0) + 1
        break
    # Files that may not be named are counted, so two of them read as two, never as one clause said twice; the step
    # says so for any Node, the typed item only for one that reads its count.
    for (kind, words), count in unnamed.items():
        cannot = "Read one of the files handed to it" if count == 1 else f"Read {count} of the files handed to it"
        limits.append(CapabilityLimit(kind, cannot, words, count=count if accepted.counted else 1))
    return tuple(limits[:_MAX_LIMITS])


def _clones(count: int) -> str:
    return "Read the code repository it asked to clone" if count == 1 else f"Read {count} code repositories it asked to clone"


def limits_of(document: Mapping[str, Any]) -> tuple[CapabilityLimit, ...]:
    raw = document.get(LIMITS_KEY)
    found = (CapabilityLimit.from_json(r) for r in raw) if isinstance(raw, list) else ()
    return tuple(limit for limit in found if limit is not None)


def limits_note(limits: Sequence[CapabilityLimit]) -> str:
    """The parent's instruction beside the job's output; "" when there are none. A limit the job never met (no web
    access on a task that needed none) is told too: the parent knows what was asked, and words it only if it mattered."""
    if not limits:
        return ""
    return (
        "The coding engine could not do all of this: " + "; ".join(limit.sentence() for limit in limits)
        + ". Where the person's request needed any of it, tell them plainly what could not be done, why, and what "
        "would enable it; never answer as if it had been done."
    )


def limits_summary(limits: Sequence[CapabilityLimit]) -> str:
    """What the job's step adds to its one-line result: only what the job met during its work (a refused clone, a
    file handed to it that it could not take)."""
    met = [limit for limit in limits if limit.kind in ANSWER_KINDS]
    if not met:
        return ""
    return " It could not " + _joined([f"{limit.cannot[0].lower()}{limit.cannot[1:]} ({limit.why})" for limit in met]) + "."


def _joined(clauses: Sequence[str]) -> str:
    """Clauses after "It could not", each saying its verb once (R46FIX-2: "read a (…); read b (…)" repeated it): a
    clause with the verb of the one before it drops the verb, and the last joins with "and"."""
    shown: list[str] = []
    for i, clause in enumerate(clauses):
        verb, _, rest = clause.partition(" ")
        shown.append(rest if i and rest and clauses[i - 1].partition(" ")[0] == verb else clause)
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


# The answer's own line (R32REPO-FU-1): one sentence per thing the run met and could not do, from the journal only.
_WHY: Final = {
    DECLINED: "the clone was declined",
    REFUSED: "a rule of your organization refused the clone",
    NOT_ALLOWED: "its host is not one this server may clone from",
    FAILED: "the clone failed",
    NOT_CLONED: "it was never cloned, so its code was not checked",
}


def met_items(limits: Sequence[CapabilityLimit]) -> list[dict[str, JsonValue]]:
    """The typed items of what the run met while it worked (a clone that did not happen), as the job's step frame and
    the answer carry them. A limit it started with and never met (no web access on a task that needed none) is the
    model's to word, never the answer's: the line says only what the journal shows was tried."""
    items: list[dict[str, JsonValue]] = []
    for limit in limits:
        if limit.kind in ATTACHMENT_KINDS:
            # A file handed to a job that it could not take (R36COVER-FU-3), named as the turn's own unread files are.
            items.append(attachment_item(limit.kind, limit.target or None, count=limit.count))
            continue
        if limit.kind not in MET_KINDS:
            continue
        item: dict[str, JsonValue] = {"kind": limit.kind}
        if limit.target:
            item["repository"] = limit.target[:_TARGET_MAX_CHARS]
        elif limit.count > 1:
            item["count"] = min(limit.count, _COUNT_MAX)
        items.append(item)
    return items[:_MAX_LIMITS]


_ATTACHMENT_WHY: Final = {
    ATTACHMENT_NOT_ACCESSIBLE: "access to it was refused",
    ATTACHMENT_UNREADABLE: "its content could not be read",
    ATTACHMENT_TOO_LARGE: "it is over the size limit for files given to a task",
}
_ATTACHMENTS_WHY: Final = {
    ATTACHMENT_NOT_ACCESSIBLE: "access to them was refused",
    ATTACHMENT_UNREADABLE: "their content could not be read",
    ATTACHMENT_TOO_LARGE: "they are over the size limit for files given to a task",
}


def attachment_item(kind: str, file: str | None, *, count: int = 1) -> dict[str, JsonValue]:
    """A file of the turn the model never read, as the answer's typed view names it; ``count``: how many files it may
    not name the item stands for (R46FIX-FU-2)."""
    item: dict[str, JsonValue] = {"kind": kind}
    if file:
        item["file"] = file[:_TARGET_MAX_CHARS]
    elif count > 1:
        item["count"] = min(count, _COUNT_MAX)
    return item


def _sentence(item: Mapping[str, Any]) -> str:
    kind = str(item["kind"])
    if kind in ATTACHMENT_KINDS:
        file, count = item.get("file"), item.get("count")
        if isinstance(file, str) and file:
            return f"I couldn't read the attached file {file}: {_ATTACHMENT_WHY[kind]}."
        if isinstance(count, int) and count > 1:
            return f"I couldn't read {count} of the attached files: {_ATTACHMENTS_WHY[kind]}."
        return f"I couldn't read one of the attached files: {_ATTACHMENT_WHY[kind]}."
    repository, count = item.get("repository"), item.get("count")
    if isinstance(repository, str) and repository:
        what = repository
    elif isinstance(count, int) and count > 1:
        what = f"{count} code repositories"
    else:
        what = "a code repository"
    hint = " Its host is not one this server clones from." if kind == NOT_ALLOWED else ""
    return f"I couldn't read {what}: {_WHY[kind]}.{hint}"


def could_not_do_view(items: Sequence[Mapping[str, Any]]) -> dict[str, JsonValue] | None:
    """The answer's typed "could not do" fact (``x.pipeshub.answer.finalized`` ``limits``, ADR-RUN-03 D5): the items
    and ``line``, the English the answer starts with for a surface with no UI; the web chat words the items in the
    reader's language and leaves the line out. Items of a kind this build does not know are dropped; equal items once.
    ``None``: nothing was met."""
    kept: list[dict[str, JsonValue]] = []
    for item in items:
        if item.get("kind") in ANSWER_KINDS and dict(item) not in kept:
            kept.append(cast("dict[str, JsonValue]", dict(item)))
    kept = kept[:_MAX_LIMITS]
    if not kept:
        return None
    return {"items": cast("JsonValue", kept), "line": " ".join(_sentence(item) for item in kept)}


__all__ = [
    "ANSWER_KINDS",
    "ATTACHMENT_KINDS",
    "ATTACHMENT_NOT_ACCESSIBLE",
    "ATTACHMENT_TOO_LARGE",
    "ATTACHMENT_UNREADABLE",
    "DECLINED",
    "FAILED",
    "LIMITS_KEY",
    "MET_KINDS",
    "NOT_ALLOWED",
    "NOT_CLONED",
    "NOT_CLONED_WORDS",
    "REFUSED",
    "LIMITS_NOTE_KEY",
    "CapabilityLimit",
    "InputsAccepted",
    "attachment_item",
    "could_not_do_view",
    "limits_instructions",
    "limits_note",
    "limits_of",
    "limits_summary",
    "met_items",
    "refused_clones",
    "skipped_inputs",
    "run_limits",
    "uncloned",
]
