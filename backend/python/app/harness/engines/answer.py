"""A durable engine run's answer with its citations resolved (E2E3-F3).

The loop's finalizer resolves ``[label](refN)`` links into the answer's citations; an engine run has no loop, so
its answer reached the conversation with ``citations: []`` and every ref the engine wrote, an invented one
included. Here the engine's text is first rewritten to the refs it read (S1b's rewrite: footnotes, titles, ids
and record links map to a ``refN`` of a record the run read; anything else is dropped), then the product's own
normaliser numbers the links and builds the citation chunks from the run's citation state, as the loop does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.engines.engine_citations import records_read, rewrite_engine_citations
from app.harness.engines.repository_citations import (
    NO_REPOSITORIES,
    cite_repository_links,
)
from app.utils.citations import normalize_citations_and_chunks

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.harness.engines.repository_citations import CitableRepositories


def _ref_to_url(collector: Any) -> dict[str, str]:  # noqa: ANN401 - CitationCollector
    mapper = collector.citation_ref_mapper
    return dict(mapper.ref_to_url) if mapper is not None else {}


def cited_by_ref(output: str, context: Any, read: Callable[[], Sequence[str]]) -> str:  # noqa: ANN401 - AgentContext
    """The engine's text with every citation it made, in whatever form, as ``[label](refN)`` of a block the run
    read (one it did not read is dropped): the one form the answer pipeline, and a parent that adopts this run's
    sources (ENGCITE2), resolve."""
    from app.agents.agent_loop.hooks.citations import CitationCollector

    ref_to_url = _ref_to_url(CitationCollector(context))
    return rewrite_engine_citations(output, records_read(read(), ref_to_url), known=frozenset(ref_to_url)).text


class EngineAnswer:
    """``(text, citations)`` for an engine's final text, over the run's ``AgentContext`` citation state and the texts
    this attempt's calls returned (``read``). ``repositories``: the repositories the session fetched publicly, from its
    ``ProvenanceLedger``: links into them at the fetched commit are cited as their files (ADR-RUN-01 D3)."""

    def __init__(
        self,
        context: Any,  # noqa: ANN401 - AgentContext
        read: Callable[[], Sequence[str]],
        repositories: CitableRepositories = NO_REPOSITORIES,
    ) -> None:
        self._context = context
        self._read = read
        self._repositories = repositories

    def __call__(self, output: str) -> tuple[str, list[dict[str, Any]]]:
        from app.agents.agent_loop.hooks.citations import CitationCollector

        output = cite_repository_links(output, self._context, self._repositories)
        collector = CitationCollector(self._context)
        ref_to_url = _ref_to_url(collector)
        text, citations = normalize_citations_and_chunks(
            cited_by_ref(output, self._context, self._read),
            collector.final_results,
            collector.tool_records,
            ref_to_url=ref_to_url,
            virtual_record_id_to_result=collector.virtual_records,
            web_records=collector.web_records,
        )
        return text, list(citations)


__all__ = ["EngineAnswer", "cited_by_ref"]
