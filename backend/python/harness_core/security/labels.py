"""Confidentiality labels as sets of sources (REPORT 6.5.1, P2.5a).

A label names the sources whose content reached something (a turn, a file)
and whether that list is complete. Labels only rise: joining unions the
sources, and anything unknown makes the join unknown. Who may read a source is
never part of a label; the host decides it from its own permissions when a
viewer asks (``passes``), so a label cannot disagree with them.

The source type is the host's (PipesHub: a record or a live item).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent

S = TypeVar("S")

# A larger set is recorded as unknown: a bounded document, and fail closed (P3.5's bound).
MAX_LABEL_SOURCES = 2000


@dataclass(frozen=True, slots=True)
class SourceLabel(Generic[S]):
    complete: bool
    sources: frozenset[S]

    def join(self, *others: SourceLabel[S] | None) -> SourceLabel[S]:
        """``None`` is a label nobody recorded: unknown."""
        sources = set(self.sources)
        for other in others:
            if other is None or not other.complete:
                return SourceLabel[S](False, frozenset())
            sources |= other.sources
        return label_of(sources, complete=self.complete)

    def passes(self, readable: Callable[[S], bool]) -> bool:
        """Whether a viewer for whom ``readable`` answers may see what carries this label."""
        return self.complete and all(readable(source) for source in self.sources)


def label_of(sources: Iterable[S], *, complete: bool = True) -> SourceLabel[S]:
    found = frozenset(sources)
    if not complete or len(found) > MAX_LABEL_SOURCES:
        return SourceLabel[S](False, frozenset())
    return SourceLabel[S](True, found)


def join_labels(labels: Iterable[SourceLabel[S] | None]) -> SourceLabel[S]:
    """The join of ``labels``; nothing to join is the empty, complete label."""
    joined = SourceLabel[S](True, frozenset())
    for label in labels:
        joined = joined.join(label)
        if not joined.complete:
            return joined
    return joined


class SourceLedger(Protocol):
    """The records whose content may have reached a run's work (P2.5a, P3.5's rule): what an engine run's
    artifacts derive from, and what the flow rule weighs before an external write (SEC1).

    A session's workspace holds what earlier turns and the goal the parent wrote put there, so the answer is
    session-wide: every source any run of the session recorded so far and the run's own tool results.
    ``apply`` takes the session stream's events in seq order (an event seen before is ignored).
    """

    def apply(self, events: Sequence[JournalEvent]) -> None: ...

    def source_records(self, run_id: str) -> frozenset[str] | None:
        """The record ids; ``None`` when anything that may have reached the run has unknown provenance."""
        ...


@dataclass(frozen=True, slots=True)
class AskSource:
    """A source as a person deciding an approval sees it: a title and where to open it, never a bare id.
    ``record_id`` is for the host's own record link; ``url`` is an outside link (a live item's page)."""

    title: str | None = None
    record_id: str | None = None
    url: str | None = None


@runtime_checkable
class LabelsContext(Protocol):
    """A ``SourceLedger`` facet: a run's context label in the host's wire form, for a child it asks for (UX2)."""

    def context_label(self, run_id: str) -> dict[str, JsonValue] | None: ...


@runtime_checkable
class ReadsUntrusted(Protocol):
    """A ``SourceLedger`` facet: whether content an outsider could write (an email, a chat message, a web page, an
    MCP server's answer) may have reached a run's work: the ``untrusted_content_read`` taint, session-wide like the
    sources. With private sources it is the Rule of Two's second leg (design-plan §4.9)."""

    def untrusted_read(self, run_id: str) -> bool: ...


@runtime_checkable
class DescribesSources(Protocol):
    """A ``SourceLedger`` facet: the sources it can name for a run, titled, most relevant first."""

    def describe_sources(self, run_id: str) -> tuple[AskSource, ...]: ...


__all__ = [
    "MAX_LABEL_SOURCES",
    "AskSource",
    "DescribesSources",
    "LabelsContext",
    "ReadsUntrusted",
    "SourceLabel",
    "SourceLedger",
    "join_labels",
    "label_of",
]
