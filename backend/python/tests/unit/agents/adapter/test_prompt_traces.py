"""Tests for ``app.agents.agent_loop.prompt_traces``."""
from __future__ import annotations

from app.agents.agent_loop.prompt_traces import traces_text


class TestTracesText:
    def test_returns_string(self) -> None:
        result = traces_text()
        assert isinstance(result, str)

    def test_not_empty(self) -> None:
        assert len(traces_text()) > 100

    def test_contains_worked_examples_header(self) -> None:
        assert "## Worked Examples" in traces_text()

    def test_contains_example_1(self) -> None:
        result = traces_text()
        assert "Example 1" in result
        assert "Single knowledge lookup" in result

    def test_contains_example_2(self) -> None:
        result = traces_text()
        assert "Example 2" in result
        assert "Two parallel sources" in result

    def test_contains_example_3(self) -> None:
        result = traces_text()
        assert "Example 3" in result
        assert "reformulation" in result

    def test_contains_example_4(self) -> None:
        result = traces_text()
        assert "Example 4" in result
        assert "confirmation" in result

    def test_contains_tool_call_format(self) -> None:
        result = traces_text()
        assert "TOOL knowledgegraph__search" in result

    def test_idempotent(self) -> None:
        assert traces_text() == traces_text()

    def test_no_leading_trailing_whitespace(self) -> None:
        result = traces_text()
        assert result == result.strip()
