"""The ``EffectLog`` port and its in-memory adapter for one request (tests, Loom hosts without a journal)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from harness_core.effects.types import RecordedEffect
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from harness_core.effects.types import EffectCall, EffectKey, ToolExecution


class EffectLog(Protocol):
    """Where a run records its tool calls' intents and outcomes, and finds them again.

    ``find`` answers with the latest invocation recorded under the key; a
    refused call is not findable (it never had an intent).
    """

    async def intent(self, call: EffectCall) -> None: ...

    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None: ...

    async def unknown(self, call: EffectCall) -> None:
        """The effect may or may not have happened; it is never retried."""
        ...

    async def refused(self, call: EffectCall, rule: str, reason: str) -> None:
        """The call was not run: ``rule`` names why (``policy``, ``interaction_denied``)."""
        ...

    async def find(self, key: EffectKey) -> RecordedEffect | None: ...


class RequestEffectLog:
    """In memory, for the life of one request; each record is mirrored to the logs."""

    def __init__(self, log: HarnessLog | None = None) -> None:
        self._log = log or HarnessLog.named("harness_core.effects")
        self._latest: dict[EffectKey, RecordedEffect] = {}

    async def intent(self, call: EffectCall) -> None:
        self._latest[call.key] = RecordedEffect(call.key, call.invocation_id, "intent")
        self._mirror("effect.intent", call)

    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self._latest[call.key] = RecordedEffect(
            call.key, call.invocation_id, "completed", execution
        )
        self._mirror("effect.outcome", call, status=execution.status)

    async def unknown(self, call: EffectCall) -> None:
        self._latest[call.key] = RecordedEffect(call.key, call.invocation_id, "unknown")
        self._mirror("effect.unknown", call)

    async def refused(self, call: EffectCall, rule: str, reason: str) -> None:
        self._mirror("effect.refused", call, rule=rule)

    async def find(self, key: EffectKey) -> RecordedEffect | None:
        return self._latest.get(key)

    def outcomes(self) -> tuple[ToolExecution, ...]:
        """What the request's completed calls returned, latest per effect, in the order the effects were first called."""
        return tuple(r.execution for r in self._latest.values() if r.state == "completed" and r.execution is not None)

    def _mirror(self, event: str, call: EffectCall, **fields: str) -> None:
        self._log.info(
            event,
            runId=call.run_id,
            invocationId=call.invocation_id,
            toolId=call.effect.tool_id,
            effect=call.effect.effect,
            **fields,
        )


__all__ = ["EffectLog", "RequestEffectLog"]
