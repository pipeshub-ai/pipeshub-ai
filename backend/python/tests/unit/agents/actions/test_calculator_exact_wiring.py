"""The exact calculator tools as the agent sees them: names, schemas, outputs,
and a toolset that is loaded on demand rather than pinned."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.calculator.calculator import Calculator
from app.agents.actions.calculator.exact import MAX_TEXT_CHARS
from app.agents.agent_loop.tool_loader import AgentLoopToolsetBuilder

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.base import Tool

_NEW_TOOLS = ("calculator__evaluate_expression", "calculator__date_difference", "calculator__count_text")


def _toolset() -> AgentLoopToolsetBuilder:
    return AgentLoopToolsetBuilder(
        Calculator(), name="calculator", description="", path_prefix="/tools/calculator",
    )


def _tool(name: str) -> Tool:
    return next(tool for tool in _toolset().tools if tool.name == name)


class TestToolset:
    def test_new_tools_sit_beside_the_existing_ones(self) -> None:
        names = {tool.name for tool in _toolset().tools}
        assert set(_NEW_TOOLS) <= names
        # Saved agents reference these by name.
        assert {"calculator__calculate_single_operand", "calculator__calculate_two_operands"} <= names

    def test_not_essential_so_lazy_disclosure_does_not_pin_it(self) -> None:
        assert Calculator._toolset_metadata["essential"] is False

    def test_description_names_what_it_can_do(self) -> None:
        # The one line a lazily disclosed agent sees before loading the toolset.
        description = Calculator._toolset_metadata["description"].lower()
        for capability in ("exact", "date", "duration", "count"):
            assert capability in description
        assert len(description.split()) <= 35

    @pytest.mark.parametrize(
        "name", ["calculator__calculate_single_operand", "calculator__calculate_two_operands"],
    )
    def test_legacy_tools_point_to_evaluate_expression(self, name: str) -> None:
        # A model that loads the toolset sees these too; one picked the
        # two-operand tool for a step of a longer calculation.
        tool = _tool(name)
        assert "evaluate_expression" in tool.description
        assert "evaluate_expression" in tool.short_description

    @pytest.mark.parametrize(
        ("name", "required"),
        [
            ("calculator__evaluate_expression", {"expression"}),
            ("calculator__date_difference", {"start", "end"}),
            ("calculator__count_text", {"text"}),
        ],
    )
    def test_schemas(self, name: str, required: set[str]) -> None:
        schema = _tool(name).to_schema()
        assert set(schema.input_schema["required"]) == required
        assert schema.description


class TestOutputs:
    async def test_evaluate_expression(self) -> None:
        payload = json.loads(await Calculator().evaluate_expression(expression="(90 - 16) * 1954"))
        assert payload == {"expression": "(90 - 16) * 1954", "result": 144596}

    async def test_evaluate_expression_reports_errors_instead_of_raising(self) -> None:
        payload = json.loads(await Calculator().evaluate_expression(expression="1/0"))
        assert payload == {"expression": "1/0", "error": "Division by zero."}

    async def test_an_oversized_clock_time_is_an_error_not_an_exception(self) -> None:
        expression = 'hms("' + "1" * 5000 + ':00:00")'
        payload = json.loads(await Calculator().evaluate_expression(expression=expression))
        assert "h:mm:ss" in payload["error"]

    @pytest.mark.parametrize("expression", [None, 42, ["1+1"]])
    async def test_evaluate_expression_with_a_non_string(self, expression: object) -> None:
        payload = json.loads(await Calculator().evaluate_expression(expression=expression))  # type: ignore[arg-type]
        assert "error" in payload

    async def test_date_difference(self) -> None:
        payload = json.loads(await Calculator().date_difference(start="2024-01-15", end="2024-08-11"))
        assert (payload["total_days"], payload["months"], payload["days"]) == (209, 6, 27)

    async def test_date_difference_reports_errors_instead_of_raising(self) -> None:
        payload = json.loads(await Calculator().date_difference(start="2023-02-29", end="2024-01-01"))
        assert "YYYY-MM-DD" in payload["error"]

    async def test_count_text(self) -> None:
        payload = json.loads(await Calculator().count_text(text="Hello World", letter="l"))
        assert payload["letters"] == 10
        assert payload["letter_occurrences"] == 3

    @pytest.mark.parametrize("letter", ["", "   "])
    async def test_count_text_with_an_empty_letter_counts_everything(self, letter: str) -> None:
        # Models send `letter: ""` when they have no letter to count.
        payload = json.loads(await Calculator().count_text(text="Hello World", letter=letter))
        assert payload == {"letters": 10, "characters": 11, "characters_excluding_spaces": 10, "words": 2}

    async def test_count_text_reports_errors_instead_of_raising(self) -> None:
        payload = json.loads(await Calculator().count_text(text="a" * (MAX_TEXT_CHARS + 1)))
        assert str(MAX_TEXT_CHARS) in payload["error"]

    async def test_the_registered_tool_runs_end_to_end(self) -> None:
        output = await _tool("calculator__evaluate_expression").execute(expression="0.1 + 0.2")
        assert output.success
        assert json.loads(output.data)["result"] == 0.3
