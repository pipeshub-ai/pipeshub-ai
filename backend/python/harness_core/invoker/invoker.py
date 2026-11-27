"""The Invoker: the one place a run's tool call is resolved, identified and run.

ARCHITECTURE §3.1 principle 2: tools are reached only through here, in
process for the durable kernel and over MCP for engines (E2's
``RunToolEndpoint``). The Invoker resolves a call within the run's grant
(``ToolCatalog``), gives it an invocation id, builds its ``EffectCall`` and
``ToolContext``, and runs it through ``EffectRunner``.

``invoke`` is the whole path for one call (dedupe → approve → hold → intent
→ execute → outcome). The durable kernel plans a batch before running any of
it, so it uses the steps (``resolve``, ``effect_call``, ``context``, ``read``,
``perform``) with invocation ids its journal already derived.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.effects.types import EffectCall, ToolContext, args_digest, tool_id_for
from harness_core.invoker.types import NOT_GRANTED, ToolRefusal
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.effects.runner import EffectRunner
    from harness_core.effects.types import ToolEffect, ToolExecution
    from harness_core.ids import IdSource
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.invoker.types import InvocationScope, InvokeResult
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema

INVOCATION_PREFIX = "inv"


def idempotency_key(org_id: str, run_id: str, invocation_id: str) -> str:
    """What a tool passes to a provider that deduplicates by key."""
    return f"{org_id}:{run_id}:{invocation_id}"


class Invoker:
    def __init__(
        self,
        catalog: ToolCatalog,
        effects: EffectRunner,
        scope: InvocationScope,
        *,
        ids: IdSource | None = None,
        logs: HarnessLog | None = None,
    ) -> None:
        """``ids`` allocates invocation ids for calls that arrive without one;
        the kernel passes none, its ids come from the journal."""
        self._catalog = catalog
        self._effects = effects
        self._scope = scope
        self._ids = ids
        self._logs = logs or HarnessLog.named(
            "harness_core.invoker",
            orgId=scope.org_id,
            runId=scope.run_id,
            attemptId=scope.attempt_id,
        )

    def granted(self) -> list[ToolSchema]:
        """The run's grant as the model sees it (an MCP ``tools/list``)."""
        return self._catalog.schemas() or []

    def resolve(self, call: ToolCall) -> ToolEffect | None:
        """The call's effect within the grant; ``None``: the run does not have it."""
        return self._catalog.describe_call(call)

    def allocate(self) -> str:
        if self._ids is None:
            raise ValueError("no invocation id given and no id source to allocate one")
        return self._ids.new_id(INVOCATION_PREFIX)

    def effect_call(
        self,
        call: ToolCall,
        effect: ToolEffect,
        invocation_id: str,
        digest: str | None = None,
    ) -> EffectCall:
        """``digest``: the arguments' digest as already journaled, when there is one."""
        return EffectCall(
            self._scope.run_id,
            invocation_id,
            call,
            effect,
            digest if digest is not None else args_digest(call.arguments),
        )

    def context(
        self,
        invocation_id: str,
        turn_text: str = "",
        interaction_id: str | None = None,
        args_digest: str | None = None,
        called_as: str | None = None,
    ) -> ToolContext:
        scope = self._scope
        return ToolContext(
            scope.run_id,
            scope.org_id,
            invocation_id,
            scope.attempt_id,
            idempotency_key(scope.org_id, scope.run_id, invocation_id),
            turn_text,
            interaction_id,
            args_digest,
            called_as,
        )

    async def read(self, call: EffectCall, ctx: ToolContext) -> ToolExecution:
        """A call whose failure leaves nothing behind (``EffectRunner.read``)."""
        return await self._effects.read(call, ctx, self._catalog.execute)

    async def perform(
        self,
        call: EffectCall,
        ctx: ToolContext,
        *,
        write_intent: bool,
        settle: Callable[[ToolExecution], ToolExecution] | None = None,
    ) -> ToolExecution | None:
        """A write/execute call (``EffectRunner.perform``); ``None``: effect unknown."""
        if settle is None:
            return await self._effects.perform(
                call, ctx, self._catalog.execute, write_intent=write_intent
            )
        return await self._effects.perform(
            call, ctx, self._catalog.execute, write_intent=write_intent, settle=settle
        )

    async def invoke(
        self,
        call: ToolCall,
        *,
        invocation_id: str | None = None,
        turn_text: str = "",
    ) -> InvokeResult:
        invocation = invocation_id if invocation_id is not None else self.allocate()
        effect = self.resolve(call)
        if effect is None:
            self._logs.warning(
                "tool.refused",
                invocationId=invocation,
                toolId=tool_id_for(call.name, source="ungranted"),
                reason=NOT_GRANTED,
            )
            return ToolRefusal(
                NOT_GRANTED, invocation, call.name, f"tool {call.name} is not granted to this run"
            )
        return await self._effects.run(
            self.effect_call(call, effect, invocation),
            self.context(invocation, turn_text),
            self._catalog.execute,
        )


__all__ = ["INVOCATION_PREFIX", "Invoker", "idempotency_key"]
