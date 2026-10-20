"""`search_internal_knowledge`'s args summary. Its result summary is the
shared `search_result_summary` (tests in `knowledge_graph/test_search_results.py`)."""

from __future__ import annotations

from app.agents.actions.retrieval import retrieval
from app.agents.actions.retrieval.retrieval import (
    _search_internal_knowledge_args_summary as args_summary,
)


class TestSearchInternalKnowledgeArgsSummary:
    def test_query_only(self) -> None:
        assert args_summary({"query": "bug bash"}) == 'Searched for "bug bash"'

    def test_query_and_connector_ids(self) -> None:
        summary = args_summary({"query": "bug bash", "connector_ids": ["c1", "c2"]})
        assert summary == 'Searched for "bug bash"\n2 sources'

    def test_known_connector_ids_show_their_labels(self, monkeypatch) -> None:
        monkeypatch.setitem(retrieval._SOURCE_LABELS, "c1", "Jira")
        summary = args_summary({"query": "bug bash", "connector_ids": ["c1"]})
        assert summary == 'Searched for "bug bash"\nSources: Jira'

    def test_missing_query_returns_none(self) -> None:
        assert args_summary({}) is None
