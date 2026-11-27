"""``retrieval.completed``: both searches of ``RetrievalService`` announce how many results they returned,
how long they took and how they ended, and never a query or a result."""

from __future__ import annotations

import contextlib

import pytest

from app.modules.retrieval import retrieval_service as mod
from app.modules.retrieval.lexical_search import LexicalSearchResult
from app.platform_events import catalog
from tests.support.platform_event_capture import captured_events, facts

SECRET = "SEEDED-query-text"
FIELDS = set(catalog.EVENTS["retrieval.completed"].fields)


class _Service:
    """Stands in for the service: the decorator only passes ``self`` through."""

    def __init__(self, answer: object) -> None:
        self.answer = answer

    @mod._announced("semantic")
    async def search(self, queries: list[str], user_id: str, org_id: str, limit: int = 20) -> object:
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


async def _search(answer: object, queries: list[str] | None = None) -> list[dict]:
    with captured_events("retrieval.completed") as events:
        with contextlib.suppress(RuntimeError):
            await _Service(answer).search(queries or [SECRET], "user-1", org_id="org_a")
    return facts(events, "retrieval.completed")


async def test_a_search_is_announced_with_its_counts_and_no_query_or_result() -> None:
    (search,) = await _search({"searchResults": [{"content": SECRET}, {"content": SECRET}], "status_code": 200}, [SECRET, SECRET, SECRET])

    assert set(search) == FIELDS
    assert (search["org_id"], search["search_type"], search["query_count"], search["count"], search["outcome"]) == (
        "org_a", "semantic", 3, 2, "success")
    assert 0 <= search["duration_seconds"] < 5
    assert SECRET not in repr(search) and "user-1" not in repr(search)


async def test_a_failure_answered_with_a_status_and_one_that_raised_are_failures() -> None:
    (answered,) = await _search({"searchResults": [], "status_code": 503})
    (raised,) = await _search(RuntimeError(SECRET))

    assert (answered["outcome"], answered["count"]) == ("failure", 0)
    assert (raised["outcome"], raised["count"]) == ("failure", 0)
    assert SECRET not in repr(raised)


async def test_a_lexical_result_counts_the_hits_of_every_query() -> None:
    (search,) = await _search(LexicalSearchResult(hits=[[object(), object()], [], [object()]], capped=[False] * 3))

    assert (search["count"], search["outcome"]) == (3, "success")


def test_both_searches_of_the_service_are_announced() -> None:
    for name in ("search_with_filters", "search_lexical"):
        assert getattr(mod.RetrievalService, name).__wrapped__.__name__ == name


async def test_the_result_is_not_counted_when_nobody_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_result_count", lambda _r: pytest.fail("built a payload nobody hears"))

    assert await _Service({"searchResults": []}).search([SECRET], "user-1", "org_a") == {"searchResults": []}
