"""``CompactionStrategy`` (P4.4): how a range of history becomes a summary.

The kernel journals the result as ``context.compacted`` whatever the strategy,
so a provider-native compaction plugs in here without changing the journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from harness_core.context.types import SeqSpan
    from harness_core.contracts.generated.labels import Labels
    from harness_core.kernel.core.messages import Message


@dataclass(frozen=True)
class CompactionRequest:
    """``messages``: the range as the model last saw it (earlier summaries included);
    ``max_chars``: the size the summary must fit; ``focus``: what the person asked it to keep (``/compact``)."""

    span: SeqSpan
    messages: tuple[Message, ...]
    labels: Labels | None = None
    max_chars: int = 8000
    focus: str | None = None
    # The most the range may take of the summarising request, in characters (BUDGETEND): its longest messages are
    # cut first. None: the range as it is. Without it, summaries re-read every kept Slack result whole, about 78k tokens each.
    input_chars: int | None = None


@dataclass(frozen=True)
class CompactionSummary:
    text: str
    strategy: str


class CompactionRefused(HarnessRefusal):
    reason_code: str = "compaction_refused"


class CompactionInputEmpty(CompactionRefused):
    """Nothing to summarise: the kernel never asks for an empty range."""

    reason_code = "compaction_input_empty"


class CompactionFailed(CompactionRefused):
    """The strategy produced no summary (an empty model answer)."""

    reason_code = "compaction_failed"


class CompactionStrategy(Protocol):
    @property
    def name(self) -> str: ...

    async def compact(self, request: CompactionRequest) -> CompactionSummary:
        """A summary of ``request.messages`` within ``request.max_chars``; raises
        ``CompactionRefused`` (or a budget refusal from a metered model)."""
        ...


__all__ = [
    "CompactionFailed",
    "CompactionInputEmpty",
    "CompactionRefused",
    "CompactionRequest",
    "CompactionStrategy",
    "CompactionSummary",
]
