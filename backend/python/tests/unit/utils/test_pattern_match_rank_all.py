"""GREP_RANK_ALL_OUTPUT: a pattern match ranks every record a grep lists, and
narrows output cut at its size limit within the search's time budget."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import pattern_match
from app.utils.pattern_match import (
    _MAX_GREP_OUTPUT_CHARS,
    _RANK_ALL_OUTPUT_CHARS,
    run_pattern_match,
    run_pattern_match_with_llm_grep,
)


async def _find_kwargs(**kwargs: object) -> dict:
    find = AsyncMock(return_value=(True, json.dumps({"records": []})))
    tool = MagicMock()
    tool.find_records = find
    graph = AsyncMock()
    graph.get_accessible_containers = AsyncMock(return_value=None)
    with patch("app.utils.pattern_match.StoragePatternMatch", return_value=tool):
        await run_pattern_match(
            config_service=MagicMock(), org_id="org", user_id="u", graph_provider=graph,
            command='grep -rci "x" .', connector_ids=["c1"], logger_instance=MagicMock(), **kwargs,
        )
    return find.await_args.kwargs


@pytest.mark.asyncio
async def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GREP_RANK_ALL_OUTPUT", raising=False)
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")

    kwargs = await _find_kwargs(deadline=time.monotonic() + 30)

    assert kwargs["rank_all_output"] is False
    assert kwargs["deadline"] is None
    assert kwargs["max_output_chars"] == _MAX_GREP_OUTPUT_CHARS


@pytest.mark.asyncio
async def test_on_every_grep_ranks_its_whole_output_within_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREP_RANK_ALL_OUTPUT", "true")
    deadline = time.monotonic() + 3

    kwargs = await _find_kwargs(deadline=deadline)

    assert kwargs["rank_all_output"] is True
    assert kwargs["deadline"] == deadline, "the search's budget ends before the source's timeout"
    assert kwargs["max_output_chars"] == _RANK_ALL_OUTPUT_CHARS


@pytest.mark.asyncio
async def test_without_a_budget_the_source_timeout_bounds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GREP_RANK_ALL_OUTPUT", "true")
    before = time.monotonic()

    kwargs = await _find_kwargs(timeout=12)

    assert before + 12 <= kwargs["deadline"] <= time.monotonic() + 12


@pytest.mark.asyncio
async def test_the_search_budget_reaches_the_pipeline() -> None:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"storageType": "local"})
    pattern_match._grep_commands_cache.clear()
    before = time.monotonic()
    with patch(
        "app.utils.pattern_match.generate_grep_command_via_llm",
        new_callable=AsyncMock, return_value=['grep -rci "x" .'],
    ), patch(
        "app.utils.pattern_match.execute_pattern_match_pipeline", new_callable=AsyncMock, return_value=[],
    ) as pipeline:
        await run_pattern_match_with_llm_grep(
            query="q", config_service=config, org_id="org", user_id="u", graph_provider=AsyncMock(),
            filters=None, logger_instance=MagicMock(), llm=MagicMock(),
        )
    pattern_match._grep_commands_cache.clear()

    deadline = pipeline.await_args.kwargs["deadline"]
    assert before + pattern_match._PATTERN_MATCH_TOTAL_BUDGET <= deadline
    assert deadline <= time.monotonic() + pattern_match._PATTERN_MATCH_TOTAL_BUDGET
