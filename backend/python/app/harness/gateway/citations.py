"""``CitationSink`` adapters: the run's own citation state, as the tool gateway reads and records it.

Knowledge tools label blocks through the ``CitationRefMapper`` on the run's
``tool_state`` (``ref1``, ``ref2``…), so a result the gateway returns already
carries the run's refs. ``cite`` resolves them back to URLs; a ref the mapper
does not hold is dropped, as the answer pipeline drops it. A durable run
journals each call's delta with its outcome (``x.pipeshub.citations.recorded``
via ``CitationJournal``), which is what a resumed attempt and the parent's fold
replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.knowledge.provenance import untrusted_sources
from harness_core.gateway.memory import refs_in
from harness_core.gateway.types import Citation

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.journal.types import NewEvent


def _cite(state: Mapping[str, Any], text: str) -> tuple[Citation, ...]:
    mapper = state.get("citation_ref_mapper")
    if mapper is None:
        return ()
    urls: dict[str, str] = mapper.ref_to_url
    return tuple(Citation(ref, urls[ref]) for ref in refs_in(text) if ref in urls)


class JournalCitationSink:
    """Durable runs: ``citations_for`` is the presenter's (``CitationJournal`` underneath),
    so a delta the tool port's presenter already journaled is not journaled twice. ``read`` keeps what this
    attempt's completed calls returned: what an engine's answer may cite by title, id or link (E2E3-F3)."""

    def __init__(
        self,
        state: Mapping[str, Any],
        citations_for: Callable[[str], NewEvent | None],
    ) -> None:
        self._state = state
        self._citations_for = citations_for
        self.read: list[str] = []

    def cite(self, text: str) -> tuple[Citation, ...]:
        self.read.append(text)
        return _cite(self._state, text)

    def record(self, invocation_id: str) -> tuple[NewEvent, ...]:
        event = self._citations_for(invocation_id)
        return (event,) if event is not None else ()

    def untrusted_sources(self, events: tuple[NewEvent, ...]) -> tuple[str, ...]:
        """``NamesUntrusted`` (KN-08): the outsider-written sources the call's citation delta names."""
        return untrusted_sources(events)


__all__ = ["JournalCitationSink"]
