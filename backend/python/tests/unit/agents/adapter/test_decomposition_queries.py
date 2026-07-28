"""Sanity checks on the decomposition eval dataset itself
(`decomposition_queries.py`) — catches dataset rot (duplicate ids, empty
queries, inverted ranges) independent of the scorer or harness."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.evals.decomposition_queries import (
    DECOMPOSITION_EVAL_QUERIES,
    query_by_id,
)


class TestDatasetShape:
    def test_has_at_least_twenty_queries(self) -> None:
        assert len(DECOMPOSITION_EVAL_QUERIES) >= 20

    def test_ids_are_unique(self) -> None:
        ids = [q.id for q in DECOMPOSITION_EVAL_QUERIES]
        assert len(ids) == len(set(ids))

    def test_every_query_has_nonempty_text(self) -> None:
        for q in DECOMPOSITION_EVAL_QUERIES:
            assert q.query.strip()

    def test_step_ranges_are_valid(self) -> None:
        for q in DECOMPOSITION_EVAL_QUERIES:
            assert q.min_steps >= 0, q.id
            assert q.max_steps >= q.min_steps, q.id

    def test_dependency_queries_allow_at_least_two_steps(self) -> None:
        """A query that requires a dependency between two parts of the
        plan cannot possibly be satisfied by a 1-step (or 0-step) plan —
        a `max_steps < 2` here would make the query self-contradictory."""
        for q in DECOMPOSITION_EVAL_QUERIES:
            if q.requires_dependency:
                assert q.max_steps >= 2, q.id


class TestQueryById:
    def test_known_id_returns_the_query(self) -> None:
        first = DECOMPOSITION_EVAL_QUERIES[0]
        assert query_by_id(first.id) is first

    def test_unknown_id_raises_key_error_with_known_ids(self) -> None:
        with pytest.raises(KeyError, match="no_such_query"):
            query_by_id("no_such_query")
