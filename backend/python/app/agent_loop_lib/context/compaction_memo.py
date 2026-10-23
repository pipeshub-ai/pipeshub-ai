"""What the context layers already compacted in this run, so a later call
compacts the same messages the same way.

Shaping is per call and nothing shaped is stored, so without a memory of
earlier calls every call re-shaped the whole history: the loop summary moved
with the recent tail, the LLM summariser ran again on each call over its
threshold (and gave a different summary each time), and the message that had
just aged past a layer's window was rewritten on every call. Each of those
changed the sent prefix and defeated the provider's prompt cache.

The memo maps a content hash of what a layer compacted (one message, or a run
of messages) to what the layer sent instead. Before acting, a layer reapplies
the replacements it already made; it compacts anything new only when the
context is still over its trigger. The sent prefix then changes only when
budget pressure makes a layer act again, and the summariser runs once per new
run of messages.

The memo lives in the run's scope (``RunScope``), never in stored history and
never shared with a sub-agent's run.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import Message, MessageRole

logger = logging.getLogger(__name__)


def message_digest(message: Message) -> str:
    """Content hash of one message: every field the transports send."""
    return hashlib.blake2b(message.model_dump_json().encode(), digest_size=16).hexdigest()


def _run_key(layer: str, digests: Sequence[str]) -> str:
    return hashlib.blake2b("\x1f".join((layer, *digests)).encode(), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class MemoHit:
    """A memoized run found at ``start`` in a message list."""

    start: int
    stop: int
    replacement: tuple[Message, ...]


@dataclass
class CompactionMemo:
    """Per-run record of every compaction a layer made, keyed by content."""

    _replacements: dict[str, tuple[Message, ...]] = field(default_factory=dict)
    # Run lengths each layer has memoized, longest first, so a prefix lookup
    # tries only lengths that can match.
    _lengths: dict[str, list[int]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @property
    def entries(self) -> int:
        return len(self._replacements)

    def remember(self, layer: str, run: Sequence[Message], replacement: Sequence[Message]) -> None:
        self._replacements[_run_key(layer, [message_digest(m) for m in run])] = tuple(replacement)
        lengths = self._lengths.setdefault(layer, [])
        if len(run) not in lengths:
            lengths.append(len(run))
            lengths.sort(reverse=True)

    def recall_message(self, layer: str, message: Message) -> Message | None:
        """The single message ``layer`` sent in place of ``message`` before."""
        found = self._replacements.get(_run_key(layer, [message_digest(message)]))
        self._count(found is not None)
        return found[0] if found else None

    def recall_prefix(self, layer: str, digests: Sequence[str], start: int, stop: int) -> MemoHit | None:
        """The longest memoized run of ``layer`` that starts at ``start`` and
        ends at or before ``stop`` (indices into ``digests``)."""
        for length in self._lengths.get(layer, ()):
            if start + length > stop:
                continue
            found = self._replacements.get(_run_key(layer, digests[start:start + length]))
            if found is not None:
                self._count(True)
                return MemoHit(start=start, stop=start + length, replacement=found)
        self._count(False)
        return None

    def _count(self, hit: bool) -> None:
        if hit:
            self.hits += 1
        else:
            self.misses += 1

    def log_stats(self, layer: str, run_id: str | None, turn: int) -> None:
        logger.debug(
            "compaction_memo: %s hits=%d misses=%d entries=%d (run=%s, turn=%d)",
            layer, self.hits, self.misses, self.entries, run_id, turn,
        )


def reapply_replacements(
    memo: CompactionMemo | None,
    layer: str,
    messages: Sequence[Message],
    keep: frozenset[int] = frozenset(),
) -> tuple[list[Message], int]:
    """``messages`` with every message ``layer`` replaced on an earlier call of
    this run replaced the same way again, except the indices in ``keep``.
    Returns the list and how many messages were replaced."""
    shaped = list(messages)
    if memo is None:
        return shaped, 0
    replaced = 0
    for i, msg in enumerate(messages):
        if i in keep:
            continue
        replacement = memo.recall_message(layer, msg)
        if replacement is not None:
            shaped[i] = replacement
            replaced += 1
    return shaped, replaced


@dataclass(frozen=True, slots=True)
class RunPlan:
    """How one compactable run is sent: the memoized pieces at its start,
    then ``rest``, which no earlier call compacted."""

    run: range
    hits: tuple[MemoHit, ...]
    rest: range


def plan_runs(
    memo: CompactionMemo | None,
    layer: str,
    messages: Sequence[Message],
    runs: Sequence[range],
) -> list[RunPlan]:
    """For each run, the longest chain of runs ``layer`` compacted on earlier
    calls that the run starts with. A memoized piece never ends inside a
    tool-call group: one that would is not reused."""
    if memo is None:
        return [RunPlan(run=run, hits=(), rest=run) for run in runs]
    digests = [message_digest(m) for m in messages]
    plans: list[RunPlan] = []
    for run in runs:
        hits: list[MemoHit] = []
        position = run.start
        while position < run.stop:
            hit = memo.recall_prefix(layer, digests, position, run.stop)
            if hit is None or not _is_group_boundary(messages, hit.stop):
                break
            hits.append(hit)
            position = hit.stop
        plans.append(RunPlan(run=run, hits=tuple(hits), rest=range(position, run.stop)))
    return plans


def _is_group_boundary(messages: Sequence[Message], index: int) -> bool:
    return index >= len(messages) or messages[index].role != MessageRole.TOOL


def assemble(
    messages: Sequence[Message],
    plans: Sequence[RunPlan],
    rest_replacements: dict[int, Sequence[Message]] | None = None,
) -> list[Message]:
    """``messages`` with each run sent as its plan says: memoized pieces
    replaced, and each ``rest`` replaced by ``rest_replacements[rest.start]``
    when given, else verbatim."""
    rest_replacements = rest_replacements or {}
    shaped: list[Message] = []
    position = 0
    for plan in plans:
        shaped.extend(messages[position:plan.run.start])
        for hit in plan.hits:
            shaped.extend(hit.replacement)
        if plan.rest and plan.rest.start in rest_replacements:
            shaped.extend(rest_replacements[plan.rest.start])
        else:
            shaped.extend(messages[plan.rest.start:plan.rest.stop])
        position = plan.run.stop
    shaped.extend(messages[position:])
    return shaped


COMPACTION_MEMO: StateSlot[CompactionMemo] = StateSlot(
    key="context.compaction_memo", default_factory=CompactionMemo,
)


__all__ = [
    "COMPACTION_MEMO",
    "CompactionMemo",
    "MemoHit",
    "RunPlan",
    "assemble",
    "message_digest",
    "plan_runs",
    "reapply_replacements",
]
