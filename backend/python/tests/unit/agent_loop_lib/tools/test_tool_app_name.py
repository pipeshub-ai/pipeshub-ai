"""`Tool.app_name`: the app a tool belongs to, read by the domain-agent catalog
(`plan_domain_agents`) and deep mode's domain overview. Tools built from a
`@tool` path must expose the same app their name is prefixed with."""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.toolset import ToolsetBuilder


class _AppLessTool(Tool):
    @property
    def name(self) -> str:
        return "run_code"

    @property
    def short_description(self) -> str:
        return "run code"

    @property
    def description(self) -> str:
        return "run code"

    @property
    def path(self) -> str:
        return "/builtin/run_code"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data="ok")


class _Connector:
    @tool(path="/tools/google_calendar/list_events", short_description="List", description="List events")
    async def list_events(self) -> tuple[bool, str]:
        return True, "[]"


@tool(path="/tools/calculator/evaluate", short_description="Evaluate", description="Evaluate")
async def _evaluate() -> str:
    return "7"


def test_a_tool_outside_any_app_has_no_app_name() -> None:
    assert _AppLessTool().app_name is None


def test_a_function_tool_takes_its_app_from_its_path() -> None:
    assert _evaluate.app_name == "calculator"
    assert _evaluate.name == "calculator__evaluate"


def test_a_toolset_method_takes_its_app_from_its_path() -> None:
    [bound] = ToolsetBuilder(
        _Connector(), name="google_calendar", description="", path_prefix="/tools/google_calendar",
    ).tools

    assert bound.app_name == "google_calendar"
    assert bound.name == f"{bound.app_name}__list_events"
