"""Searches run in code go through the search tool's own operation, sized small."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from app.agents.actions.knowledge_graph.ops import search as search_ops
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.coverage.targeted import (
    TARGETED_RESULT_CHARS,
    render_results,
    run_targeted_searches,
)


async def test_results_are_rendered_and_an_empty_or_failed_search_counts_as_nothing(monkeypatch) -> None:
    replies = {
        "found": "Found 2 blocks in 1 record\n...block text...",
        "empty": json.dumps({"status": "success", "message": "No results found", "results": []}),
        "error": json.dumps({"status": "error", "message": "Search error"}),
    }
    execute = AsyncMock(side_effect=lambda state, query, **kwargs: replies[query])
    monkeypatch.setattr(search_ops, "execute_search", execute)
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")

    results = await run_targeted_searches(context, ["found", "empty", "error"], purpose="gate", timeout=5.0)

    assert [r.text is not None for r in results] == [True, False, False]
    assert all(call.kwargs["max_result_chars"] == TARGETED_RESULT_CHARS for call in execute.await_args_list)
    # The keyword phase gets a share of the timeout, so a slow grep cannot lose the semantic results.
    assert all(call.kwargs["keyword_wait_s"] == 1.25 for call in execute.await_args_list)
    assert all(call.args[0] is context.tool_state for call in execute.await_args_list)
    assert render_results(results[:2]).endswith('Search "empty":\nNo records found.')


async def test_a_raising_search_leaves_the_others(monkeypatch) -> None:
    async def _execute(state, query, **kwargs) -> str:
        if query == "bad":
            raise RuntimeError("down")
        return "ok text"

    monkeypatch.setattr(search_ops, "execute_search", _execute)
    context = AgentContext(org_id="o", user_id="u", user_email="u@example.com")
    results = await run_targeted_searches(context, ["bad", "good"], purpose="facet", timeout=5.0)
    assert [r.text for r in results] == [None, "ok text"]
