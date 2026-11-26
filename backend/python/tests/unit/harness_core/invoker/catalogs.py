"""The two ``ToolCatalog`` adapters built over the contract's offered tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from harness_core.effects.types import ToolExecution
from harness_core.invoker import CatalogTool, InMemoryToolCatalog
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.testing.contracts.tool_catalog import OfferedTool

_Q = {"type": "object", "properties": {"q": {"type": "string", "description": "q"}}}


class MemoryCatalogs:
    def __init__(self) -> None:
        self.runs: list[str] = []

    def build(self, offered: Sequence[OfferedTool], grant: Collection[str]) -> InMemoryToolCatalog:
        def run(name: str):  # noqa: ANN202 - a ToolFn
            async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
                self.runs.append(name)
                return ToolExecution(status="ok", content=f"{name}:{call.arguments.get('q')}")

            return execute

        return InMemoryToolCatalog(
            [CatalogTool(ToolSchema(name=t.name, description=t.name, input_schema=_Q), t.effect, run(t.name)) for t in offered],
            grant,
        )


class _Offered(Tool):
    def __init__(self, name: str, runs: list[str]) -> None:
        self._name = name
        self._runs = runs

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return self._name

    @property
    @override
    def description(self) -> str:
        return self._name

    @property
    @override
    def path(self) -> str:
        return f"/tools/crm/{self._name}"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="q", required=False)]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        self._runs.append(self._name)
        return ToolOutput(success=True, data=f"{self._name}:{kwargs.get('q')}")


class RegistryCatalogs:
    def __init__(self) -> None:
        self.runs: list[str] = []

    def build(self, offered: Sequence[OfferedTool], grant: Collection[str]) -> RegistryToolPort:
        registry = ToolRegistry()
        for tool in offered:
            registry.register_tool(_Offered(tool.name, self.runs))
        return RegistryToolPort(
            registry, {t.name: t.effect for t in offered if t.effect.tool_id in grant}
        )
