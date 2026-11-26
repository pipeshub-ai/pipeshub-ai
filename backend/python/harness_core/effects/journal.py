"""``JournalEffectLog``: a durable run's effects as ``tool.*`` journal events.

It commits through the run's own fenced commit (``commit``), so command ids,
fencing and folding stay the run's: a ``write``/``execute`` intent and its
outcome are each their own commit (REPORT 6.1.4). The event builders are
public because the durable kernel batches read intents and outcomes, refusals,
synthetic outcomes and ``tool.unknown`` into commits of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.effects.results import decode_result, encode_result, put_ref
from harness_core.effects.types import RecordedEffect, ToolExecution
from harness_core.gates.tools import is_gate_tool
from harness_core.journal.bounds import bounded_content
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from pydantic import JsonValue

    from harness_core.effects.types import EffectCall, EffectKey, RecordedState
    from harness_core.journal.ports import ContentStorePort
    from harness_core.kernel.fold import Invocation, RunState

    Commit = Callable[[str, list[NewEvent]], Awaitable[object]]


def _labels(execution: ToolExecution) -> dict[str, JsonValue]:
    """The outcome's item labels (REPORT 6.5.1): a public result names no sources, which every ledger reads as public."""
    labels: dict[str, JsonValue] = {"integrity": execution.integrity}
    if execution.public:
        labels["confidentiality"] = {"readers": "public", "sources": []}
    return labels


class JournalEffectLog:
    def __init__(
        self,
        commit: Commit,
        content: ContentStorePort,
        state: Callable[[], RunState],
        *,
        allowed_by: bool = False,
    ) -> None:
        """``commit(kind, events)``: the run's fenced commit; ``state``: the run as folded; ``allowed_by``: the
        claim's journal accepts ``tool.intent`` naming the rule that let a call run (``INTENT_ALLOWED_BY``)."""
        self._commit = commit
        self._content = content
        self._state = state
        self._allowed_by = allowed_by

    async def intent(self, call: EffectCall) -> None:
        _ = await self._commit("intent", [self.intent_event(call)])

    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        events = [
            await self.outcome_event(call.invocation_id, execution),
            *execution.events,
        ]
        if execution.slots:
            events.append(
                NewEvent(
                    "loop.state_saved",
                    {"slots": cast("JsonValue", dict(execution.slots))},
                )
            )
        _ = await self._commit("outcome", events)

    async def unknown(self, call: EffectCall) -> None:
        _ = await self._commit("unknown", [self.unknown_event(call.invocation_id)])

    async def refused(self, call: EffectCall, rule: str, reason: str) -> None:
        _ = await self._commit(
            "refusal", await self.refusal_events(call.invocation_id, rule, reason, tool_id=call.effect.tool_id)
        )

    async def find(self, key: EffectKey) -> RecordedEffect | None:
        run = self._state()
        if run.run_id != key.run_id:
            return None
        found: RecordedEffect | None = None
        for inv in sorted(run.invocations.values(), key=lambda i: (i.turn, i.index)):
            if inv.tool_id != key.tool_id or inv.args_digest != key.args_digest:
                continue
            recorded = await self._recorded(key, inv)
            if recorded is not None:
                found = recorded
        return found

    def intent_event(self, call: EffectCall) -> NewEvent:
        data: dict[str, JsonValue] = {
            "invocationId": call.invocation_id,
            "toolId": call.effect.tool_id,
            "argsDigest": call.args_digest,
            "effect": call.effect.effect,
            "sink": cast(
                "JsonValue",
                call.effect.sink.model_dump(mode="json", by_alias=True, exclude_none=True),
            ),
        }
        if self._allowed_by and call.allowed_by is not None:
            data["allowedBy"] = call.allowed_by
        return NewEvent("tool.intent", data)

    async def outcome_event(
        self, invocation_id: str, execution: ToolExecution, *, status: str | None = None
    ) -> NewEvent:
        """``status`` overrides the execution's (``cancelled`` for a synthetic outcome)."""
        data: dict[str, JsonValue] = {
            "invocationId": invocation_id,
            "status": status or execution.status,
            "resultRef": await put_ref(
                self._content,
                encode_result(
                    # Tool output is untrusted in size; the journal keeps a bounded copy (WP-62).
                    bounded_content(execution.content) if not execution.parts else execution.content,
                    is_error=execution.status != "ok",
                    meta=execution.meta,
                    parts=execution.parts,
                    error_code=execution.error_code,
                ),
            ),
            "labels": _labels(execution),
        }
        if execution.receipt is not None:
            data["receipt"] = cast("JsonValue", execution.receipt)
        if execution.shadow is not None:
            data["shadow"] = {"wouldAsk": execution.shadow.would_ask}
        return NewEvent("tool.outcome", data)

    @staticmethod
    def unknown_event(invocation_id: str) -> NewEvent:
        return NewEvent(
            "tool.unknown",
            {"invocationId": invocation_id, "reason": "effect outcome unknown"},
        )

    async def refusal_events(
        self, invocation_id: str, rule: str, reason: str, *, tool_id: str | None = None
    ) -> list[NewEvent]:
        """``tool_id`` is journaled only for a run gate's own tool (a declined plan, G-04): every other refusal's
        tool is in its model response or its intent, and older folds do not read the field."""
        denied: dict[str, JsonValue] = {"invocationId": invocation_id, "rule": rule}
        if is_gate_tool(tool_id):
            denied["toolId"] = tool_id
        return [
            NewEvent("tool.denied", denied),
            await self.outcome_event(
                invocation_id, ToolExecution(status="error", content=reason)
            ),
        ]

    async def _recorded(self, key: EffectKey, inv: Invocation) -> RecordedEffect | None:
        state: RecordedState
        if inv.state == "completed":
            return RecordedEffect(
                key, inv.invocation_id, "completed", await self._execution(inv)
            )
        if inv.state == "unknown":
            state = "unknown"
        elif inv.intent_attempt_id is not None and inv.state in ("intent", "approved"):
            state = "intent"
        else:
            return None
        return RecordedEffect(key, inv.invocation_id, state)

    async def _execution(self, inv: Invocation) -> ToolExecution | None:
        if inv.result_ref is None:
            return None
        content, _ = decode_result(await self._content.get(inv.result_ref))
        status = inv.status if inv.status in ("ok", "timeout") else "error"
        return ToolExecution(status=status, content=content, receipt=inv.receipt)


# What a call left open when its run ended reads as: a write or an execute may have happened.
STOPPED_UNKNOWN = "run stopped; effect status unknown"
STOPPED = "cancelled"


async def closing_outcomes(
    log: JournalEffectLog, invocations: Iterable[Invocation], *, with_results: bool = True
) -> list[NewEvent]:
    """An outcome for each call whose intent is journaled and whose outcome is not, for the commit that ends the
    run (EV-06, C2.49: every call ends with exactly one outcome). A read never ran to an effect, so it is
    cancelled; a write or an execute may have been applied, so its outcome is an error saying the effect is
    unknown. ``with_results`` false: the attempt has no content store yet (it ends before its runtime is
    assembled), so the outcomes carry their status alone, as Node's do."""
    events: list[NewEvent] = []
    for inv in invocations:
        if inv.state != "intent":
            continue
        unsure = inv.effect in ("write", "execute")
        status = "error" if unsure else "cancelled"
        if with_results:
            execution = ToolExecution(status="error", content=STOPPED_UNKNOWN if unsure else STOPPED)
            events.append(await log.outcome_event(inv.invocation_id, execution, status=status))
        else:
            events.append(
                NewEvent(
                    "tool.outcome",
                    {"invocationId": inv.invocation_id, "status": status, "labels": {"integrity": "internal"}},
                )
            )
    return events


__all__ = ["STOPPED", "STOPPED_UNKNOWN", "JournalEffectLog", "closing_outcomes"]
