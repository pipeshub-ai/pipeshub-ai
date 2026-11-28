"""What the context compiler reads and what it produces (REPORT 6.3).

Inputs are rendered from the journal (history items, reductions, the journaled
tool order) or handed over by the host (its bands). The output is the request,
band by band, so tests and provider adapters can see each band's bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.contracts import canonical_digest, canonical_json

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.labels import Labels
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.core.tool_schema import ToolSchema

BandName = Literal["system", "tools", "pinned", "history", "tail"]
BANDS: tuple[BandName, ...] = ("system", "tools", "pinned", "history", "tail")


@dataclass(frozen=True)
class Attribution:
    """Where quoted content came from: shown to the model with it."""

    source: str
    labels: Labels | None = None


@dataclass(frozen=True)
class ToolEntry:
    """A tool as the run grants it; ``tool_id`` orders the band (instance-scoped where the host has one)."""

    tool_id: str
    schema: ToolSchema


@dataclass(frozen=True)
class PinnedItem:
    """Host content that holds for the session (instructions, a skills catalog).
    ``provenance`` set: another principal wrote it, so it is quoted."""

    key: str
    text: str
    provenance: Attribution | None = None


@dataclass(frozen=True)
class HostBands:
    """The host's part of a request: everything the journal does not hold."""

    system: str | None = None
    tools: tuple[ToolEntry, ...] = ()
    pinned: tuple[PinnedItem, ...] = ()
    # Volatile: rendered after history as the last user message, never journaled.
    tail: tuple[str, ...] = ()
    # Holds until it changes (PipesHub: the time and request context, the deadline warning): the kernel journals
    # each text this run has not journaled yet as a note, before the run's input at its start and after the history
    # later, so no request ends with text the next one drops (``harness_core.context.notes``).
    notes: tuple[str, ...] = ()
    # How the current run's own input renders (PipesHub: the goal step's goal). A function of
    # the journal, so the message is the same bytes on every turn and attempt.
    input_text: str | None = None
    # Appended to the run's own input (attachment images).
    input_parts: tuple[Part, ...] = ()
    # The provider drops images inside tool results: repeat them in a user message after the item.
    tool_images_after_results: bool = False
    # Each result's text is cut to this many characters (both ends kept); None: no cap.
    max_result_chars: int | None = None
    # The model's window: a batch's results share a slice of it (``batch_result_cap``, BUDGETEND); None: each
    # result gets ``max_result_chars`` whatever the batch.
    window_tokens: int | None = None
    # Tools whose results the clear rung never clears: the host's answer depends on their
    # text (PipesHub: results that carry citation markers).
    keep_results: frozenset[str] = frozenset()
    # A footer the host appends to a result, by call id (PipesHub: the loop step after that
    # result's batch). Fixed once the batch is complete, so the history stays append-only.
    result_footers: Mapping[str, str] = field(default_factory=dict[str, str])
    # The session's tool list this request takes (``ModelCall.tool_list_key``); None: the tools as given.
    tool_list_key: str | None = None
    # Run slots the host records about this request (PipesHub: what the system prompt showed), journaled with the
    # turn's response when they changed, as a shaper's additions to ``ModelCall.slots`` are on the shaped path.
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])


@dataclass(frozen=True)
class HistoryItem:
    """One rendered entry of the session (an entry with its tool results, or a
    foreign run's stub or passed turn). A reduction covers an item only when
    ``[first_seq, last_seq]`` lies inside its range, so a call and its results
    are covered together or not at all."""

    first_seq: int
    last_seq: int
    messages: tuple[Message, ...]
    # Item labels of its tool results by call id (from ``tool.outcome``).
    result_labels: Mapping[str, Labels] = field(default_factory=dict[str, "Labels"])
    # Invocation ids by call id, named by a cleared result's marker.
    invocations: Mapping[str, str] = field(default_factory=dict[str, str])
    # Set: another principal's content, rendered as quoted data.
    provenance: Attribution | None = None
    # The current run's own input: re-injected after a summary that covers it.
    own_input: bool = False
    # A message the user wrote into the current run (its input, a steering message): what the
    # run is for is never left to a summariser's paraphrase, so it comes back after a summary.
    user_input: bool = False
    # Where the own input sits in ``messages`` when it is not the last (a summary's merged item).
    input_at: int | None = None


@dataclass(frozen=True)
class SeqSpan:
    from_seq: int
    to_seq: int

    def covers(self, item: HistoryItem) -> bool:
        return self.from_seq <= item.first_seq and item.last_seq <= self.to_seq


@dataclass(frozen=True)
class Compaction:
    """A journaled ``context.compacted``: its range, the summary text, the range's label."""

    span: SeqSpan
    summary: str
    labels: Labels | None = None


@dataclass(frozen=True)
class CompilerInput:
    host: HostBands
    history: tuple[HistoryItem, ...]
    # The journaled tool order (slot ``context.toolOrder``); empty before the first compile.
    tool_order: tuple[str, ...] = ()
    compactions: tuple[Compaction, ...] = ()
    cleared: tuple[SeqSpan, ...] = ()


def _messages_json(messages: tuple[Message, ...]) -> JsonValue:
    return [m.model_dump(mode="json") for m in messages]


@dataclass(frozen=True)
class CompiledContext:
    """The request, band by band. ``cache_marks`` are the message indices that end a
    cacheable band (the history's last message); system blocks end the system and
    pinned bands; the last tool ends the tools band."""

    system: str | None
    pinned: str | None
    tools: tuple[ToolSchema, ...]
    tool_order: tuple[str, ...]
    history: tuple[Message, ...]
    tail: tuple[Message, ...]
    input_index: int | None = None

    @property
    def messages(self) -> list[Message]:
        return [*self.history, *self.tail]

    @property
    def system_blocks(self) -> list[str]:
        return [block for block in (self.system, self.pinned) if block]

    @property
    def system_text(self) -> str | None:
        return "\n\n".join(self.system_blocks) or None

    @property
    def cache_marks(self) -> tuple[int, ...]:
        return (len(self.history) - 1,) if self.history else ()

    def band(self, name: BandName) -> bytes:
        """Each band's bytes (canonical JSON), the unit the cache-stability properties compare."""
        doc: JsonValue
        match name:
            case "system":
                doc = self.system
            case "pinned":
                doc = self.pinned
            case "tools":
                doc = [t.model_dump(mode="json") for t in self.tools]
            case "history":
                doc = _messages_json(self.history)
            case "tail":
                doc = _messages_json(self.tail)
        return canonical_json(doc)

    def digest(self) -> str:
        """``model.request.contextDigest`` of a compiled request."""
        return canonical_digest({name: self.band(name).decode("utf-8") for name in BANDS})


__all__ = [
    "BANDS",
    "BandName",
    "Compaction",
    "CompiledContext",
    "CompilerInput",
    "HistoryItem",
    "HostBands",
    "PinnedItem",
    "Attribution",
    "SeqSpan",
    "ToolEntry",
]
