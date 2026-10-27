"""get_issues must reject a filter it cannot parse instead of querying unfiltered."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.linear.linear import Linear


def _tool() -> Linear:
    tool = Linear(MagicMock())
    tool.client = MagicMock()
    tool.client.issues = AsyncMock(return_value=MagicMock(success=True, data={"issues": []}))
    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "", "{not json", "[1, 2]", '"text"', "42", "null", "   ", '"{\\"state\\": 1}"',
    "true", "1.5", "NaN", '{"a": 1} x', '[{"a": 1}]', "\ufeff{}",
    pytest.param("[" * 200_000, id="deeply-nested"),
    pytest.param(42, id="int-not-a-string"),
    pytest.param({"state": {"name": {"eq": "Done"}}}, id="dict-not-a-string"),
])
async def test_get_issues_rejects_unparsable_filter(bad: object) -> None:
    tool = _tool()
    ok, out = await tool.get_issues(filter=bad)
    assert ok is False
    assert "Invalid filter" in json.loads(out)["error"]
    tool.client.issues.assert_not_called()


@pytest.mark.asyncio
async def test_get_issues_passes_valid_filter_through() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues(first=5, filter='{"state": {"name": {"eq": "Done"}}}')
    assert ok is True
    tool.client.issues.assert_awaited_once_with(
        first=5, after=None, filter={"state": {"name": {"eq": "Done"}}}
    )


@pytest.mark.asyncio
async def test_get_issues_without_filter_queries_unfiltered() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues()
    assert ok is True
    tool.client.issues.assert_awaited_once_with(first=None, after=None, filter=None)


@pytest.mark.asyncio
async def test_get_issues_passes_an_empty_filter_object_through() -> None:
    tool = _tool()
    ok, _ = await tool.get_issues(filter="{}")
    assert ok is True
    tool.client.issues.assert_awaited_once_with(first=None, after=None, filter={})
