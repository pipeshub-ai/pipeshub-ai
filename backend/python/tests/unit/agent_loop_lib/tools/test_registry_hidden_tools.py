"""``ToolRegistry.register_hidden_tool``: a tool callable by its exact name
that nothing lists, grants or finds (a retired name kept for saved calls)."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.errors import DuplicateToolNameError
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.toolset import ToolsetBuilder


class _Box:
    @tool(path="/tools/box/new", short_description="New", description="The new tool")
    async def new(self) -> tuple[bool, str]:
        return True, "new"

    @tool(path="/tools/box/old", short_description="Old", description="The retired tool")
    async def old(self) -> tuple[bool, str]:
        return True, "old"


def _registry() -> ToolRegistry:
    new, old = ToolsetBuilder(_Box(), name="box", description="", path_prefix="/tools/box").tools
    registry = ToolRegistry()
    registry.register_tool(new)
    registry.register_toolset("box", "", [new.name])
    registry.register_hidden_tool(old)
    return registry


def test_a_hidden_tool_is_in_no_listing() -> None:
    registry = _registry()
    assert registry.names() == ["box__new"]
    assert [s.name for s in registry.schemas()] == ["box__new"]
    assert registry.tools_in_toolset("box") == ["box__new"]
    assert registry.expand_tool_names(["box"]) == ["box__new"]
    assert not registry.has("box__old")


def test_a_hidden_tool_resolves_by_its_exact_name_with_its_path() -> None:
    registry = _registry()
    assert registry.resolve_by_name("box__old").name == "box__old"
    assert registry.path_for_name("box__old") == "/tools/box/old"


def test_a_name_is_hidden_or_listed_never_both() -> None:
    registry = _registry()
    new, old = ToolsetBuilder(_Box(), name="box", description="", path_prefix="/tools/box").tools
    with pytest.raises(DuplicateToolNameError):
        registry.register_hidden_tool(new)
    with pytest.raises(DuplicateToolNameError):
        registry.register_hidden_tool(old)


async def test_the_executor_runs_a_hidden_tool_called_by_name() -> None:
    from app.agent_loop_lib.core.messages import ToolCall

    executor = ToolExecutor(_registry())
    result = await executor.call_tool(ToolCall(id="c1", name="box__old", input={}))
    assert "old" in str(result)
