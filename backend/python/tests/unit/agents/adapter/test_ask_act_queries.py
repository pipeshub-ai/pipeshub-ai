"""Sanity checks on the ask-vs-act eval dataset itself
(`ask_act_queries.py`) — catches dataset rot (duplicate ids, empty
queries, an all-one-sided split) independent of the scorer or harness."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.evals.ask_act_queries import ASK_ACT_EVAL_QUERIES, query_by_id


class TestDatasetShape:
    def test_has_at_least_ten_queries(self) -> None:
        assert len(ASK_ACT_EVAL_QUERIES) >= 10

    def test_ids_are_unique(self) -> None:
        ids = [q.id for q in ASK_ACT_EVAL_QUERIES]
        assert len(ids) == len(set(ids))

    def test_every_query_has_nonempty_text(self) -> None:
        for q in ASK_ACT_EVAL_QUERIES:
            assert q.query.strip(), q.id

    def test_split_is_not_all_one_sided(self) -> None:
        """A dataset that is all `expect_ask=True` (or all `False`) can't
        actually distinguish "the model always asks" from "the model got
        the decision right" — both tiers need real coverage."""
        ask_count = sum(1 for q in ASK_ACT_EVAL_QUERIES if q.expect_ask)
        act_count = len(ASK_ACT_EVAL_QUERIES) - ask_count
        assert ask_count >= 3
        assert act_count >= 3


class TestQueryById:
    def test_known_id_returns_the_query(self) -> None:
        first = ASK_ACT_EVAL_QUERIES[0]
        assert query_by_id(first.id) is first

    def test_unknown_id_raises_key_error_with_known_ids(self) -> None:
        with pytest.raises(KeyError, match="no_such_query"):
            query_by_id("no_such_query")
