"""The exact-arithmetic tools as the agent meets them: visible from the first
turn, returning structured results, and named in the prompt only when
granted."""

from __future__ import annotations

import json

import pytest

from app.agents.actions.calculator.calculator import Calculator
from app.agents.agent_loop.prompt_builder import _build_finding_information
from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.agents.context.tool_surface import ToolSurfaces

_KNOWLEDGE = "knowledgegraph__search"
_EVALUATE = "calculator__evaluate_expression"
_DATE_DIFF = "calculator__date_difference"


def _finding_information(tool_names: list[str]) -> str:
    state = {"has_knowledge": True}
    surfaces = ToolSurfaces.resolve(tool_names, state)
    return _build_finding_information(
        surfaces, SourceCatalog.from_state(state), has_attachments=False,
    )


class TestVisibility:
    def test_the_calculator_is_essential(self) -> None:
        """Essential toolsets are pinned under lazy disclosure; otherwise the
        tool sits behind a discovery call the model skips and the arithmetic
        happens in its head."""
        assert Calculator._toolset_metadata["essential"] is True


class TestTools:
    @pytest.mark.asyncio
    async def test_evaluate_expression_returns_the_result(self) -> None:
        payload = json.loads(await Calculator().evaluate_expression("(90 - 16) * 1954"))

        assert payload == {"expression": "(90 - 16) * 1954", "result": 144_596}

    @pytest.mark.asyncio
    async def test_a_refused_expression_is_an_error_payload_not_an_exception(self) -> None:
        payload = json.loads(await Calculator().evaluate_expression("__import__('os')"))

        assert "error" in payload

    @pytest.mark.asyncio
    async def test_date_difference_returns_every_breakdown(self) -> None:
        payload = json.loads(await Calculator().date_difference("1916-04-09", "1916-11-04"))

        assert payload["total_days"] == 209
        assert (payload["years"], payload["months"], payload["days"]) == (0, 6, 26)

    @pytest.mark.asyncio
    async def test_a_bad_date_is_an_error_payload(self) -> None:
        payload = json.loads(await Calculator().date_difference("sometime", "1916-11-04"))

        assert "YYYY-MM-DD" in payload["error"]


class TestPromptRule:
    def test_named_when_granted(self) -> None:
        text = _finding_information([_KNOWLEDGE, _EVALUATE, _DATE_DIFF])

        assert f"`{_EVALUATE}`" in text
        assert f"`{_DATE_DIFF}`" in text

    def test_absent_when_not_granted(self) -> None:
        """Every tool name the prompt prints must be callable."""
        text = _finding_information([_KNOWLEDGE])

        assert "calculator__" not in text
        assert "in your head" not in text

    def test_only_the_granted_tool_is_named(self) -> None:
        text = _finding_information([_KNOWLEDGE, _DATE_DIFF])

        assert f"`{_DATE_DIFF}`" in text
        assert _EVALUATE not in text
