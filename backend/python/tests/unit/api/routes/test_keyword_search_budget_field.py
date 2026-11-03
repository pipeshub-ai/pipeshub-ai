"""`ChatQuery.keywordSearchBudget` on both chat routes (`agent.py`, `chatbot.py`).

The field reaches the search tools through the chat state
(`chat_state.build_initial_state`), where `pattern_match_wait_budget` turns it
into how long a search waits for grep after its semantic results.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError


@pytest.fixture(params=["app.api.routes.agent", "app.api.routes.chatbot"])
def chat_query_cls(request: pytest.FixtureRequest) -> type:
    module = __import__(request.param, fromlist=["ChatQuery"])
    return module.ChatQuery


def test_absent_by_default(chat_query_cls) -> None:
    assert chat_query_cls(query="q").keywordSearchBudget is None


@pytest.mark.parametrize("value", ["short", "full"])
def test_accepts_the_two_budgets(chat_query_cls, value) -> None:
    assert chat_query_cls(query="q", keywordSearchBudget=value).keywordSearchBudget == value


def test_rejects_anything_else(chat_query_cls) -> None:
    with pytest.raises(ValidationError):
        chat_query_cls(query="q", keywordSearchBudget="none")
