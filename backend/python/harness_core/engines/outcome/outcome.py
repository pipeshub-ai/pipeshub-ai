"""One typed outcome at a run's end (ADR-RUN-01 D1): ``Completed``, ``Partial`` or ``Failed``.

``delivered`` lists what exists, from the ledger: the deliverable's file, the sources, the notes, and whether the
work was kept for a "continue". The presenter words it and the briefing tells a parent about it; neither looks at
anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.engines.outcome.ledger import OUTCOME_SETTLED, ArtifactWritten
from harness_core.journal.types import NewEvent
from harness_core.workspace.sync.storage_classes import TOOL_RESULTS_DIR

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.outcome.deliverable import Deliverable
    from harness_core.engines.outcome.ledger import (
        LimitKind,
        OutcomeLedger,
        SourceGathered,
    )

# Why a run ended partial: the limit that cut it, or ``unfinished`` (its engine ended its turn without the file).
PartialReason = Literal["budget", "turns", "time", "stall", "provider", "engine_error", "secret_blocked", "unfinished"]
UNFINISHED: PartialReason = "unfinished"
Action = Literal["continue", "retry", "none"]


@dataclass(frozen=True, slots=True)
class Delivered:
    """What a run's outcome holds: ``file`` (the deliverable's, when one was written), the sources it gathered,
    the notes it kept, and ``kept``: the work was saved for the run that continues it."""

    file: ArtifactWritten | None = None
    sources: tuple[SourceGathered, ...] = ()
    notes: tuple[str, ...] = ()
    kept: bool = False

    @property
    def material(self) -> bool:
        """Whether the run gathered or wrote anything at all."""
        return self.file is not None or bool(self.sources) or bool(self.notes)

    @property
    def continuable(self) -> bool:
        """Whether a "continue" has something to start from: a file, the engine's notes, or the saved handoff."""
        return self.file is not None or bool(self.notes) or self.kept

    def wire(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"sources": len(self.sources), "notes": _bounded_notes(self.notes), "kept": self.kept}
        if self.file is not None:
            out["file"] = {"path": self.file.path[:WIRE_PATH_MAX], "revision": self.file.revision, "by": self.file.by}
        return out


# The settled outcome's contract bounds (contracts/harness/ext/pipeshub/events/x.pipeshub.outcome.settled.schema.json).
# A completion commit carrying more is refused whole (500+ notes ended a finished run failed), so every
# listed field is bounded here, once; tests/unit/harness_core/engines/outcome/test_r20_outcome_bounds.py holds the
# worker to the schema itself.
WIRE_NOTES_MAX: Final = 500
WIRE_PATH_MAX: Final = 1024
WIRE_MISSING_MAX: Final = 8
WIRE_MISSING_CHARS: Final = 256
WIRE_REASON_CHARS: Final = 64


def _bounded_notes(notes: tuple[str, ...]) -> list[JsonValue]:
    """The notes a "continue" reads first (the handoff, the partial answer: notes/ files that are not tool dumps under
    notes/web/, notes/code/, notes/fetched/ or ph's notes/tool-results/), then the rest in order, at most
    ``WIRE_NOTES_MAX``, each path at most ``WIRE_PATH_MAX``."""
    def dump(path: str) -> bool:
        return path.startswith(("notes/web/", "notes/code/", "notes/fetched/", f"{TOOL_RESULTS_DIR}/"))

    ordered = [n for n in notes if not dump(n)] + [n for n in notes if dump(n)]
    return [n[:WIRE_PATH_MAX] for n in ordered[:WIRE_NOTES_MAX]]


@dataclass(frozen=True, slots=True)
class Completed:
    delivered: Delivered

    @property
    def action(self) -> Action:
        return "none"


@dataclass(frozen=True, slots=True)
class Partial:
    """The run stopped before the request was done. ``missing``: the deliverable it still owes, by file name."""

    reason: PartialReason
    delivered: Delivered
    missing: tuple[str, ...] = ()

    @property
    def action(self) -> Action:
        return "continue" if self.delivered.continuable else "retry"


@dataclass(frozen=True, slots=True)
class Failed:
    """The run failed with ``reason`` (its run error's code). ``servers``: the labels of the MCP servers a
    ``mcp_tool_withdrawn`` ending names (WP-84), never ids."""

    reason: str
    delivered: Delivered
    servers: tuple[str, ...] = ()

    @property
    def action(self) -> Action:
        return "continue" if self.delivered.continuable else "retry"


RunOutcome = Completed | Partial | Failed


def settle(
    ledger: OutcomeLedger,
    *,
    file: ArtifactWritten | None,
    owed: str | None,
    failure: str | None = None,
    kept: bool = False,
) -> RunOutcome:
    """The run's outcome from its ledger. ``file``: the deliverable as delivered (the engine's, or what the wrap-up
    wrote in its place); ``owed``: the requested file the engine did not write (``OutcomeLedger.owed_after_cut`` or
    ``owed_at_end``); ``failure``: why a run failed; ``kept``: its work was saved for a "continue". A limit makes the
    outcome partial, and so does a file the engine owed; ``missing`` is that file while nothing stands in for it."""
    delivered = Delivered(file, ledger.sources, ledger.notes, kept)
    if failure is not None:
        return Failed(failure, delivered)
    missing = (owed,) if owed is not None and file is None else ()
    limit: LimitKind | None = ledger.limit
    if limit is not None:
        return Partial(cast("PartialReason", limit), delivered, missing)
    if owed is not None:
        return Partial(UNFINISHED, delivered, missing)
    return Completed(delivered)


def outcome_wire(outcome: RunOutcome) -> dict[str, JsonValue]:
    """The outcome as ``x.pipeshub.outcome.settled`` and a child's result carry it."""
    match outcome:
        case Completed():
            return {"kind": "completed", "delivered": outcome.delivered.wire(), "action": outcome.action}
        case Partial():
            return {
                "kind": "partial", "reason": outcome.reason, "delivered": outcome.delivered.wire(),
                "missing": [m[:WIRE_MISSING_CHARS] for m in outcome.missing[:WIRE_MISSING_MAX]], "action": outcome.action,
            }
        case Failed():
            return {
                "kind": "failed", "reason": outcome.reason[:WIRE_REASON_CHARS], "delivered": outcome.delivered.wire(),
                "action": outcome.action,
            }


# The bound on the presenter's English closing the settled outcome carries for surfaces with no UI.
CLOSING_MAX_CHARS: Final = 16_000


def settled_event(
    outcome: RunOutcome, deliverable: Deliverable, *, closing: str | None = None, opening: bool = False
) -> NewEvent:
    """``closing``: the presenter's English words after the answer's prose, for a journal whose chat words the
    outcome itself (WP-89, ``outcome_words``): the answer then carries only the prose (``opening``: the opening of
    the deliverable's file), and a surface with no UI appends this."""
    data: dict[str, JsonValue] = {"outcome": outcome_wire(outcome), "deliverable": deliverable.wire()}
    if closing is not None:
        data["presented"] = {"closing": closing[:CLOSING_MAX_CHARS], "opening": opening}
    return NewEvent(OUTCOME_SETTLED, data, critical=False)


__all__ = [
    "CLOSING_MAX_CHARS",
    "UNFINISHED",
    "Action",
    "Completed",
    "Delivered",
    "Failed",
    "Partial",
    "PartialReason",
    "RunOutcome",
    "outcome_wire",
    "settle",
    "settled_event",
]
