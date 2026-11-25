"""A save of the run's own deliverable stays in the run (WP-97, ADR-RUN-01 D1).

In a long research run, the harness had already published the engine's ``out/report.md`` as the run's report when the engine
saved the same file again with ``artifacts__save_artifact``. The save was a write to the person's PipesHub space, so
the org's ``writes: ask`` held it, and the run waited two hours on a card for a copy of what was already there.

The run's deliverable is published by the harness from the workspace (``Deliverable.owns`` names its path), and a
changed file is a new version of the same artifact. So a ``save_artifact`` whose name is the deliverable's is not a
write outside the run: the catalog describes it as a workspace call (nothing asks), makes no second artifact, and
tells the engine where the deliverable is kept.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolEffect, ToolExecution

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.engines.outcome.deliverable import Deliverable
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.logs import HarnessLog

SAVE_TOOL_ID: Final = "pipeshub@artifacts/save_artifact"
_IN_RUN: Final = SinkDescriptor(egress="none", args_leave_boundary=False)


def deliverable_saved(path: str) -> str:
    """What the engine is told: the save is the deliverable's, which PipesHub keeps from the workspace."""
    return (
        f"Not saved again: this file is the task's deliverable, and PipesHub saves /workspace/{path} as a new version "
        f"of it whenever the file changes. To change it, write the new content to /workspace/{path}."
    )


class DeliverableSaves:
    """``inner`` (``ToolCatalog``) where ``save_artifact`` of the run's deliverable is a workspace call."""

    def __init__(self, inner: ToolCatalog, deliverable: Deliverable, log: HarnessLog) -> None:
        self._inner = inner
        self._deliverable = deliverable
        self._log = log

    def _owned(self, call: ToolCall, effect: ToolEffect | None) -> str | None:
        if effect is None or effect.tool_id != SAVE_TOOL_ID:
            return None
        name = call.arguments.get("name")
        return self._deliverable.owns(name) if isinstance(name, str) and name else None

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        effect = self._inner.describe_call(call)
        if self._owned(call, effect) is None or effect is None:
            return effect
        return ToolEffect(tool_id=effect.tool_id, effect="workspace", idempotent=True, sink=_IN_RUN)

    def schemas(self) -> list[ToolSchema] | None:
        return self._inner.schemas()

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        path = self._owned(call, self._inner.describe_call(call))
        if path is None:
            return await self._inner.execute(call, ctx)
        self._log.info("artifacts.deliverable_save_kept", toolId=SAVE_TOOL_ID, path=path)
        return ToolExecution(status="ok", content=deliverable_saved(path))


__all__ = ["SAVE_TOOL_ID", "DeliverableSaves", "deliverable_saved"]
