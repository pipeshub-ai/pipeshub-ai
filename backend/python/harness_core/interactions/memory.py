"""In-memory interaction adapters: a bridge a test decides, and a process-local wakeup."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.contracts import canonical_json
from harness_core.effects.edits import approved_edit
from harness_core.interactions.ports import InteractionTicket
from harness_core.journal.inline import inline_ref
from harness_core.journal.types import InteractionNotOpen

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.edits import ApprovedEdit
    from harness_core.interactions.ports import (
        InteractionDecision,
        InteractionRequest,
        RunBinding,
    )


class InMemoryInteractionWakeup:
    """``InteractionWakeup`` within one process; ``wake`` is what Node's notify does."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def wake(self, run_id: str) -> None:
        self._events.setdefault(run_id, asyncio.Event()).set()

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        event = self._events.setdefault(run_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout_s)
        except TimeoutError:
            return False
        event.clear()
        return True


class NoWakeup:
    """No notifications: a holder reads the journal every poll interval."""

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        await asyncio.sleep(timeout_s)
        return False


class InMemoryInteractionBridge:
    """``InteractionBridge`` in one process: a person decides with ``decide``."""

    def __init__(self) -> None:
        self.requests: dict[str, InteractionRequest] = {}
        self._decisions: dict[str, InteractionDecision] = {}
        self._said: dict[str, str] = {}
        self._edits: dict[str, Mapping[str, JsonValue]] = {}
        self._wakeup = InMemoryInteractionWakeup()

    async def request(self, binding: RunBinding, req: InteractionRequest) -> InteractionTicket:
        _ = self.requests.setdefault(req.interaction_id, req)
        return InteractionTicket(req.interaction_id, binding)

    def decide(
        self,
        interaction_id: str,
        decision: InteractionDecision,
        *,
        said: str | None = None,
        edits: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """``edits``: the arguments a person approved in place of the call's own (AP-06)."""
        if interaction_id not in self.requests or interaction_id in self._decisions:
            raise InteractionNotOpen(interaction_id)
        self._decisions[interaction_id] = decision
        if said:
            self._said[interaction_id] = said
        if edits is not None and decision == "approved":
            self._edits[interaction_id] = edits
        self._wakeup.wake(interaction_id)

    def said(self, interaction_id: str) -> str | None:
        return self._said.get(interaction_id)

    def edit(self, interaction_id: str) -> ApprovedEdit | None:
        edits = self._edits.get(interaction_id)
        return None if edits is None else approved_edit(inline_ref(canonical_json(dict(edits))))

    async def wait(
        self, ticket: InteractionTicket, *, within_s: float
    ) -> InteractionDecision | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within_s
        while True:
            decision = self._decisions.get(ticket.interaction_id)
            if decision is not None:
                return decision
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            _ = await self._wakeup.wait(ticket.interaction_id, remaining)


__all__ = ["InMemoryInteractionBridge", "InMemoryInteractionWakeup", "NoWakeup"]
