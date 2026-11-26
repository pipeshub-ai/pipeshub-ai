"""The ``ToolCatalog`` port: the tools one run was granted, and nothing else."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext, ToolEffect, ToolExecution
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema


class ToolCatalog(Protocol):
    """A run's granted tools: what the model may see, what a call is, how it runs.

    The grant is fixed when the catalog is built. ``describe_call`` is ``None``
    for any call outside it (a tool the source holds but the grant withholds,
    or a name nobody offers); ``execute`` of such a call answers with an
    ``error`` execution and runs nothing.
    """

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        """The call's effect, which may depend on its arguments."""
        ...

    def schemas(self) -> list[ToolSchema] | None:
        """The granted tools as the model sees them; ``None`` when nothing is granted."""
        ...

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution: ...


__all__ = ["ToolCatalog"]
