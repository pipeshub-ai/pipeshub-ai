"""A typed value is confirmed only to a user who can read a record mentioning it.

User U asks about a value V (an amount, a date, a name) that only records U
cannot open mention. U must get exactly the reply a value nobody mentions gets,
with none of V's record ids in it, whatever stopped the walk.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import values
from tests.unit.agents.actions.knowledge_graph.test_find_records_by_value import (
    APP,
    _context,
    _cursor,
    _graph,
    _linker,
    _listed,
    _rows,
    _state,
)

pytestmark = pytest.mark.asyncio

# More hits than one page checks before handing back a cursor.
_MANY = values._MAX_SCAN + 1000


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(values, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(values, "resolve_frontend_url", AsyncMock(return_value=None))
    context = AsyncMock(return_value=_context())
    monkeypatch.setattr(values, "load_entity_access_context", context)
    return context


def _too_broad(rows: list[dict[str, Any]], readable: set[str]) -> AsyncMock:
    """V is too broad across the org; listed up to the scan limit it is ``rows``."""
    graph = _graph(rows, readable)
    graph.get_records_for_named_entities = AsyncMock(side_effect=[
        {"hits": [], "truncated": True},
        {"hits": [{"virtualRecordId": f"v-{r['recordId']}", "connectorId": APP, **r} for r in rows], "truncated": False},
    ])
    return graph


async def _ask(graph: AsyncMock, **args: object) -> tuple[bool, str]:
    return await values.execute_find_records_by_value(_state(graph), **args)


def _assert_hidden(reply: tuple[bool, str], unknown: tuple[bool, str]) -> None:
    assert reply == unknown
    assert not _listed(reply[1]) and "r000" not in reply[1] and "cursor=" not in reply[1]


@pytest.mark.parametrize("args", [
    {"amount_min": 50_000, "currency": "USD"},
    {"amount_min": 50_000},
    {"percent_min": 15},
])
async def test_a_value_only_unreadable_records_mention_reads_as_unmentioned(env: AsyncMock, args: dict) -> None:
    unknown = await _ask(_graph([]), **args)
    _assert_hidden(await _ask(_too_broad(_rows(_MANY), set()), **args), unknown)
    _assert_hidden(await _ask(_graph(_rows(_MANY), set()), **args), unknown)


async def test_a_made_up_cursor_reads_as_it_does_for_an_unmentioned_value(env: AsyncMock) -> None:
    for cursor in ("10", "9990:r010", "r3:9990:r010"):
        unknown = await _ask(_graph([]), kinds=["date"], cursor=cursor)
        _assert_hidden(await _ask(_graph(_rows(_MANY), set()), kinds=["date"], cursor=cursor), unknown)


async def test_a_malformed_cursor_is_refused_before_any_lookup(env: AsyncMock) -> None:
    graph = _graph(_rows(_MANY), set())
    unknown = await _ask(_graph([]), kinds=["date"], cursor="not-a-cursor")
    assert await _ask(graph, kinds=["date"], cursor="not-a-cursor") == unknown
    graph.get_records_for_named_entities.assert_not_called()


async def test_a_walk_that_runs_out_of_time_reads_as_unmentioned(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(values, "LISTING_DEADLINE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(values, "_CHECK_BATCH", 10)
    graph = _graph(_rows(_MANY), set())
    unknown = await _ask(_graph([]), kinds=["date"])
    _assert_hidden(await _ask(graph, kinds=["date"]), unknown)
    # It stopped at the deadline, after the first batch, not at the end of the hits.
    assert graph.filter_accessible_record_ids.await_count == 1


async def test_a_too_broad_value_for_a_user_with_no_sources_reads_as_unmentioned(env: AsyncMock) -> None:
    env.return_value = _context(app_level_app_ids=frozenset())
    args = {"kinds": ["date"], "source_ids": ["kb-other"]}
    unknown = await _ask(_graph([]), **args)
    _assert_hidden(await _ask(_too_broad(_rows(3), set()), **args), unknown)


async def test_a_name_whose_access_checks_were_cut_short_reads_as_an_unknown_name(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linker(monkeypatch, "not_found")
    unknown = await _ask(_graph([]), name="Pinecrest", amount_min=1)
    _linker(monkeypatch, "not_found", timed_out=True)
    assert await _ask(_graph(_rows(3), set()), name="Pinecrest", amount_min=1) == unknown


async def test_a_readable_mention_past_the_first_scan_window_is_listed(env: AsyncMock) -> None:
    target = f"r{values._MAX_SCAN + 500:03}"
    ok, body = await _ask(_too_broad(_rows(_MANY), {target}), kinds=["date"])
    assert ok and _listed(body) == [target] and "That is all 1." in body


async def test_more_may_match_is_said_only_beside_readable_rows(
    env: AsyncMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(values, "_CHECK_BATCH", 10)
    monkeypatch.setattr(values, "_MAX_SCAN", 20)
    graph = _graph(_rows(100), readable={"r050", "r090"})
    ok, body = await _ask(graph, kinds=["date"])
    assert ok and _listed(body) == ["r050"] and "More records may match: " in body
    ok, body = await _ask(graph, kinds=["date"], cursor=_cursor(body))
    assert ok and _listed(body) == ["r090"] and "That was the last page." in body


async def test_the_scan_budget_of_a_page_starts_at_its_first_readable_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Records the walk passed before the first readable hit do not count toward ``_MAX_SCAN``, even in the
    batch that holds it: the page checks a full budget after its first row."""
    from app.modules.retrieval.entity_filters import EntityFilterHit

    monkeypatch.setattr(values, "_CHECK_BATCH", 10)
    monkeypatch.setattr(values, "_MAX_SCAN", 20)
    hits = [EntityFilterHit(record_id=f"r{i:03}", virtual_record_id=f"v{i}", connector_id=APP) for i in range(100)]
    graph = AsyncMock()
    graph.filter_accessible_record_ids = AsyncMock(side_effect=lambda ids, *_: [i for i in ids if i == "r005"])
    context = _context()
    page, cursor, stopped = await values._readable_page(graph, context, hits, "u", "o", 0, 50)
    assert [h.record_id for h in page] == ["r005"] and stopped
    # 20 counted from r005 ends inside the third batch, so the page stops after r029, not after r019.
    assert cursor is not None and cursor.endswith(":r029")
