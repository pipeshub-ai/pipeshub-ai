"""``EXACT_TEXT_SEARCH_GATE``: the search tool's exact-text pass runs for
queries with an exact anchor and on ``exact_match``, not for plain language;
with the gate off it runs on every search, as before."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.modules.retrieval.exact_terms import EXACT_TEXT_SEARCH_GATE_ENV_VAR

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_PLAIN = "how the team handles refund requests"


def _state(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [], "virtual_to_record_map": {},
    }
    state = {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": MagicMock(),
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []},
    }
    state.update(extra)
    return state


async def _grep_ran(query: str, state: dict[str, Any] | None = None, **kwargs: Any) -> bool:  # noqa: ANN401
    grep = AsyncMock(return_value=[])
    with patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)), \
            patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", grep):
        await execute_search(state or _state(), query, **kwargs)
    return grep.called


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, "true")


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [_PLAIN, "rollback for PAY-4312"])
async def test_without_the_gate_every_search_runs_it(monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    monkeypatch.delenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, raising=False)
    assert await _grep_ran(query)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gated")
async def test_a_plain_language_search_skips_it() -> None:
    assert not await _grep_ran(_PLAIN)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gated")
@pytest.mark.parametrize("query", ["rollback for PAY-4312", 'the "warm standby" runbook'])
async def test_an_identifier_or_quoted_phrase_runs_it(query: str) -> None:
    assert await _grep_ran(query)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gated")
async def test_exact_match_runs_it_for_any_query() -> None:
    assert await _grep_ran(_PLAIN, exact_match=True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gated")
async def test_exact_match_does_not_widen_an_entity_scoped_search() -> None:
    """Exact-text search knows no entity scope, so its hits would leak past it."""
    assert not await _grep_ran("rollback for PAY-4312", scope_virtual_record_ids=["v1"], exact_match=True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("gated")
@pytest.mark.parametrize(("sent", "forwarded"), [(True, True), ("true", True), (False, False), ("false", False)])
async def test_the_tool_forwards_exact_match(sent: object, forwarded: bool) -> None:
    with patch(f"{_SEARCH}.execute_search", new_callable=AsyncMock, return_value="ok") as search:
        await KnowledgeGraph(state={}).search(query=_PLAIN, exact_match=sent)
    assert search.await_args.kwargs["exact_match"] is forwarded
