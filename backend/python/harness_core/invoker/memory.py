"""``InMemoryToolCatalog``: tools as plain async functions, granted by tool id."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.kernel.core.tool_content import UNKNOWN_TOOL_ERROR_PREFIX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Iterable

    from harness_core.effects.types import ToolContext, ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema

    ToolFn = Callable[[ToolCall, ToolContext], Awaitable[ToolExecution]]


@dataclass(frozen=True)
class CatalogTool:
    """One tool a source offers: how the model sees it, its effect, how it runs."""

    schema: ToolSchema
    effect: ToolEffect
    execute: ToolFn


class InMemoryToolCatalog:
    """The tools ``grant`` names (by ``toolId``) out of everything ``tools`` offers."""

    def __init__(self, tools: Iterable[CatalogTool], grant: Collection[str]) -> None:
        offered = {tool.effect.tool_id for tool in tools}
        missing = sorted(set(grant) - offered)
        if missing:
            raise ValueError(f"the grant names tools nobody offers: {missing}")
        self._granted = {
            tool.schema.name: tool for tool in tools if tool.effect.tool_id in grant
        }

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        tool = self._granted.get(call.name)
        return tool.effect if tool is not None else None

    def schemas(self) -> list[ToolSchema] | None:
        if not self._granted:
            return None
        return [self._granted[name].schema for name in sorted(self._granted)]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        tool = self._granted.get(call.name)
        if tool is None:
            return ToolExecution(status="error", content=f"{UNKNOWN_TOOL_ERROR_PREFIX} {call.name}")
        return await tool.execute(call, ctx)


__all__ = ["CatalogTool", "InMemoryToolCatalog"]
