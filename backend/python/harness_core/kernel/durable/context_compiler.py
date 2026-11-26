"""The context compiler on the durable kernel (P4.1, P4.2): the journal rendered
into history items, the reductions it recorded, and the request compiled from them.

Everything here reads the fold and the content store only, so a resumed attempt
and a replay compile the bytes the first attempt sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness_core.context.compiler import compile_context
from harness_core.context.labels import labels_of
from harness_core.context.ladder import LadderPolicy, Measurement
from harness_core.context.results import RESULT_CHARS_PER_TOKEN
from harness_core.context.types import (
    Compaction,
    CompilerInput,
    HistoryItem,
    HostBands,
    SeqSpan,
    ToolEntry,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.context.ports import CompactionStrategy
    from harness_core.context.types import CompiledContext
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.durable.transcript import TranscriptRenderer
    from harness_core.kernel.fold import RunState, SessionState

# What a summary may take of the window, in characters (about 4 per token).
DEFAULT_SUMMARY_CHARS = 16_000
# What the range a summary reads may take of the window (BUDGETEND): at three characters a token, a quarter.
SUMMARY_INPUT_SHARE = 0.25
# The ledger unreachable during a compaction: the run yields and asks again.
LEDGER_BACKOFF_S = 30.0


class ContextHost(Protocol):
    """The host's bands for a turn: its system text, tools with their ids, pinned items
    and the volatile tail. A function of the journal (``state``), like ``TurnShaper``;
    ``call.messages`` is the session's history as the journal renders it, before any
    reduction (the loop step's stale rounds read the results' content, as the shaper does)."""

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands: ...


@dataclass(frozen=True)
class ContextCompiler:
    """Turns the compiler on for a kernel. ``window_tokens``: the model's context window."""

    window_tokens: int
    strategy: CompactionStrategy
    host: ContextHost | None = None
    policy: LadderPolicy = LadderPolicy()
    summary_chars: int = DEFAULT_SUMMARY_CHARS
    ledger_backoff_s: float = LEDGER_BACKOFF_S
    # None: the summary reads the range whole, as before BUDGETEND.
    summary_input_share: float | None = SUMMARY_INPUT_SHARE

    @property
    def summary_input_chars(self) -> int | None:
        """The most the summarised range may take of the summarising request, in characters."""
        if self.summary_input_share is None:
            return None
        return int(self.window_tokens * self.summary_input_share * RESULT_CHARS_PER_TOKEN)


def default_bands(call: ModelCall) -> HostBands:
    """A host without bands of its own: the kernel's system and tools, ids by name."""
    return HostBands(
        system=call.system,
        tools=tuple(ToolEntry(tool_id=t.name, schema=t) for t in call.tools or ()),
    )


def latest_measurement(session: SessionState) -> Measurement | None:
    """The provider's count of the session's latest request."""
    for entry in reversed(session.entries):
        response = entry.response
        if response is not None and response.usage is not None:
            return Measurement(seq=response.seq, tokens=response.usage.input_tokens)
    return None


JournalContext = tuple[list[HistoryItem], tuple[Compaction, ...], tuple[SeqSpan, ...]]


async def journal_context(renderer: TranscriptRenderer, session: SessionState, run_id: str | None) -> JournalContext:
    items = await renderer.render_items(session, run_id)
    compactions = tuple(
        [
            Compaction(
                span=SeqSpan(c.range.from_seq, c.range.to_seq),
                summary=(await renderer.blob(c.summary_ref)).decode("utf-8"),
                labels=labels_of(c.labels),
            )
            for c in session.compactions
        ]
    )
    cleared = tuple(SeqSpan(r.from_seq, r.to_seq) for r in session.cleared)
    return items, compactions, cleared


async def compile_session(
    renderer: TranscriptRenderer,
    session: SessionState,
    *,
    run_id: str | None,
    bands: HostBands,
    tool_order: Sequence[str],
    pending: Sequence[Message] = (),
    pending_input: int | None = None,
    rendered: JournalContext | None = None,
) -> CompiledContext:
    """The request for ``session`` plus ``pending`` messages not journaled yet (the
    run's first input, a strategy's injections); ``pending_input`` is the index in
    ``pending`` of the run's own input. ``rendered``: the session already rendered."""
    items, compactions, cleared = rendered or await journal_context(renderer, session, run_id)
    items = list(items)
    after = session.seq + 1
    for index, message in enumerate(pending):
        items.append(
            HistoryItem(first_seq=after, last_seq=after, messages=(message,), own_input=index == pending_input)
        )
    return compile_context(
        CompilerInput(
            host=bands,
            history=tuple(items),
            tool_order=tuple(tool_order),
            compactions=compactions,
            cleared=cleared,
        )
    )


__all__ = [
    "DEFAULT_SUMMARY_CHARS",
    "LEDGER_BACKOFF_S",
    "ContextCompiler",
    "ContextHost",
    "JournalContext",
    "compile_session",
    "default_bands",
    "journal_context",
    "latest_measurement",
]
