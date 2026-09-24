"""How much of a record may be rendered into one tool result, and where it
stopped.

`knowledgegraph__fetch_record` reads whole documents, and until this existed
the only limit was a count of blocks. A block is not a unit of size: it is a
table cell or a 40 KB passage depending on the document, and a single
`TABLE_ROW` block expands its entire table group -- so a 50,000-row table
counted as one block and passed any count-based cap untouched. One fetch could
exceed the model's context window outright, and the failure arrived as a
provider 400 rather than a truncation.

This module owns the accounting and nothing else: no blocks, no records, no
providers, no I/O. That keeps it exhaustively testable without a database, and
keeps the renderer free of arithmetic.

One instance is threaded through every record of a call, so the same object
answers three questions the fetch path needs: how much room is left overall
(the cap across records), how much this record may still use, and where each
record stopped so the model can continue it. `ImageAdmission` composes
`ImageBudget` the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.utils.env_utils import env_int

# Share of the model's context window one fetch may spend. The rest has to
# hold the system prompt, the conversation so far, the other tool results and
# the answer itself.
_CONTEXT_SHARE = 0.25
# The token estimator this codebase uses throughout (`core/tokens.py`).
_CHARS_PER_TOKEN = 4

# A model whose configuration reports an optimistic context window -- unknown
# and local models routinely claim 128k -- must not be handed 128k of document.
MIN_RENDER_CHARS = 40_000
# Beyond this, a single tool result is unusable regardless of window size: the
# model stops attending to the middle long before it runs out of room.
MAX_RENDER_CHARS = 240_000

DEFAULT_CONTEXT_LENGTH = 128_000

MAX_CHARS_ENV_VAR = "PIPESHUB_FULL_RECORD_MAX_CHARS"

# Marker left in place of the characters a block lost when it alone exceeded
# the whole budget.
TRUNCATION_MARKER = "\n[…block truncated to fit the context budget]\n"


@dataclass(frozen=True)
class TableTruncation:
    """A table that ran out of budget partway through its rows."""

    group_index: int
    rows_shown: int
    rows_total: int


@dataclass(frozen=True)
class RecordRenderOutcome:
    """What happened while rendering one record."""

    record_id: str
    # First block index NOT rendered. `None` means the record finished, which
    # is what makes it safe to mark fully fetched.
    stopped_at_block: int | None = None
    blocks_rendered: int = 0
    chars_rendered: int = 0
    table_truncation: TableTruncation | None = None

    @property
    def complete(self) -> bool:
        return self.stopped_at_block is None and self.table_truncation is None


@dataclass
class _RecordState:
    blocks_rendered: int = 0
    chars_rendered: int = 0
    stopped_at_block: int | None = None
    table_truncation: TableTruncation | None = None


@dataclass
class RenderBudget:
    """Character and block allowance for one fetch, shared across its records.

    Characters and blocks are counted separately on purpose. Characters bound
    the size of the result; the block count is the pre-existing cap and keeps
    its original meaning -- one *rendered unit*, where a whole table group
    counts once however many rows it holds.
    """

    max_chars: int
    max_blocks: int | None = None
    chars_used: int = 0
    blocks_used: int = 0
    _records: dict[str, _RecordState] = field(default_factory=dict)
    _current: str | None = None

    # -- per-record framing -------------------------------------------------

    def begin_record(self, record_id: str) -> None:
        """Start attributing spend to `record_id`. The pools are shared; only
        the bookkeeping is per record."""
        self._records.setdefault(record_id, _RecordState())
        self._current = record_id

    def outcome(self, record_id: str) -> RecordRenderOutcome:
        state = self._records.get(record_id) or _RecordState()
        return RecordRenderOutcome(
            record_id=record_id,
            stopped_at_block=state.stopped_at_block,
            blocks_rendered=state.blocks_rendered,
            chars_rendered=state.chars_rendered,
            table_truncation=state.table_truncation,
        )

    # -- spending -----------------------------------------------------------

    @property
    def chars_remaining(self) -> int:
        return max(0, self.max_chars - self.chars_used)

    @property
    def exhausted(self) -> bool:
        return self.chars_remaining <= 0

    @property
    def blocks_exhausted(self) -> bool:
        return self.max_blocks is not None and self.blocks_used >= self.max_blocks

    def can_afford(self, text: str) -> bool:
        return len(text) <= self.chars_remaining

    def charge(self, text: str) -> None:
        """Record characters spent. Callers that build a block's text in
        pieces (a table's rows) charge as they go."""
        self.chars_used += len(text)
        if self._current is not None:
            self._records[self._current].chars_rendered += len(text)

    def take(self, text: str) -> str | None:
        """The text to emit, or None when there is no room left.

        Returns a truncated prefix rather than nothing when a single block is
        larger than the entire budget and nothing has been rendered yet: a
        fetch that returns a prefix is useful, one that returns an empty
        record is not.
        """
        if not text:
            return text
        if self.can_afford(text):
            self.charge(text)
            return text
        if self.chars_used == 0:
            # Nothing rendered at all yet: emit what fits.
            room = max(0, self.max_chars - len(TRUNCATION_MARKER))
            clipped = text[:room] + TRUNCATION_MARKER
            self.charge(clipped)
            return clipped
        return None

    def count_block(self) -> None:
        """One renderable unit was emitted -- a top-level block, or a whole
        group however many children it rendered."""
        self.blocks_used += 1
        if self._current is not None:
            self._records[self._current].blocks_rendered += 1

    # -- stopping -----------------------------------------------------------

    def stop_at(self, block_index: int) -> None:
        """Record the first block index that was NOT rendered. The first call
        wins: the earliest unrendered block is where continuation resumes."""
        if self._current is None:
            return
        state = self._records[self._current]
        if state.stopped_at_block is None:
            state.stopped_at_block = block_index

    def note_table_truncation(self, group_index: int, shown: int, total: int) -> None:
        if self._current is None:
            return
        state = self._records[self._current]
        if state.table_truncation is None:
            state.table_truncation = TableTruncation(group_index, shown, total)


def resolve_render_budget(
    context_length: int | None,
    max_blocks: int | None = None,
) -> RenderBudget:
    """A budget sized for the model actually answering this request.

    Derived from the context window rather than fixed, because the same
    default is wrong at both ends: wasteful for a 1M-token model and far too
    large for an 8k local one. Clamped at both ends because the reported
    window cannot be trusted -- unknown and local models routinely claim 128k.
    """
    window = context_length if context_length and context_length > 0 else DEFAULT_CONTEXT_LENGTH
    derived = int(window * _CONTEXT_SHARE * _CHARS_PER_TOKEN)
    max_chars = max(MIN_RENDER_CHARS, min(MAX_RENDER_CHARS, derived))

    override = env_int(MAX_CHARS_ENV_VAR, default=None, lo=1_000, hi=2_000_000)
    if override is not None:
        max_chars = override

    return RenderBudget(max_chars=max_chars, max_blocks=max_blocks)


__all__ = [
    "DEFAULT_CONTEXT_LENGTH",
    "MAX_CHARS_ENV_VAR",
    "MAX_RENDER_CHARS",
    "MIN_RENDER_CHARS",
    "TRUNCATION_MARKER",
    "RecordRenderOutcome",
    "RenderBudget",
    "TableTruncation",
    "resolve_render_budget",
]
