"""``JournalInteractionBridge``: interactions on a durable run's journal.

The worker journals ``interaction.requested``; only Node journals
``interaction.resolved`` (``resolveActive`` while the run is running, plus
``run.resumed`` once it suspended). The bridge reads decisions from the run as
folded, so a restarted worker or engine finds every request and decision its
predecessor saw. A wake (``InteractionWakeup``) only shortens the wait; the
journal is read after every wake and at least every ``poll_s``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness_core.contracts import canonical_digest
from harness_core.effects.edits import EditsApproved
from harness_core.interactions.approval_request import approval_request
from harness_core.interactions.ports import InteractionTicket, SaysWhy
from harness_core.journal.inline import inline_bytes
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.effects.edits import ApprovedEdit
    from harness_core.interactions.ports import (
        InteractionDecision,
        InteractionRequest,
        InteractionWakeup,
        RunBinding,
    )

POLL_S = 2.0


class InteractionView(Protocol):
    """A run's interactions as its journal holds them."""

    async def refresh(self) -> None:
        """Read the journal up to its head."""
        ...

    def requested(self, interaction_id: str) -> bool: ...

    def decision(self, interaction_id: str) -> InteractionDecision | None: ...

    async def append(self, events: Sequence[NewEvent]) -> None:
        """Commit worker events to the run, fenced by the caller's attempt."""
        ...


def said_in(ref: ContentRef | None) -> str | None:
    """The text a person gave with a decision (``interaction.resolved.answerRef``). Node bounds it and always
    carries it inline, so no content store is read; any other ref reads as nothing said."""
    if ref is None:
        return None
    raw = inline_bytes(ref)
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    return text or None


def binding_digest(req: InteractionRequest) -> str:
    return canonical_digest(
        {
            "interactionId": req.interaction_id,
            "kind": req.kind,
            "subject": req.subject,
            "argsDigest": req.args_digest,
        }
    )


def requested_event(req: InteractionRequest) -> NewEvent:
    data: dict[str, JsonValue] = {
        "interactionId": req.interaction_id,
        "kind": req.kind,
        "bindingDigest": binding_digest(req),
    }
    if req.connect is not None:
        data["connect"] = req.connect.journaled()
    return NewEvent("interaction.requested", data)


def _request_events(req: InteractionRequest) -> list[NewEvent]:
    """An approval that carries its reason is journaled with it; any other request alone."""
    if req.kind == "approval" and req.reason is not None:
        return list(approval_request(req.interaction_id, binding_digest(req), req.reason))
    return [requested_event(req)]


@dataclass
class JournalInteractionBridge:
    view: InteractionView
    wakeup: InteractionWakeup
    clock: Clock
    poll_s: float = POLL_S

    async def request(self, binding: RunBinding, req: InteractionRequest) -> InteractionTicket:
        await self.view.refresh()
        if not self.view.requested(req.interaction_id):
            await self.view.append(_request_events(req))
        return InteractionTicket(req.interaction_id, binding)

    def said(self, interaction_id: str) -> str | None:
        return self.view.said(interaction_id) if isinstance(self.view, SaysWhy) else None

    def edit(self, interaction_id: str) -> ApprovedEdit | None:
        return self.view.edit(interaction_id) if isinstance(self.view, EditsApproved) else None

    async def wait(
        self, ticket: InteractionTicket, *, within_s: float
    ) -> InteractionDecision | None:
        woke = await self.wait_any([ticket], within_s=within_s)
        return None if woke is None else woke[1]

    async def wait_any(
        self, tickets: Sequence[InteractionTicket], *, within_s: float
    ) -> tuple[str, InteractionDecision] | None:
        """The first of ``tickets`` (in their order) found decided: a decision on any one of a
        batch ends the wait, so a later card decided first is not held behind an earlier one."""
        deadline = self.clock.suspend_aware() + within_s
        while True:
            await self.view.refresh()
            for ticket in tickets:
                decision = self.view.decision(ticket.interaction_id)
                if decision is not None:
                    return ticket.interaction_id, decision
            remaining = deadline - self.clock.suspend_aware()
            if remaining <= 0 or not tickets:
                return None
            _ = await self.wakeup.wait(tickets[0].binding.run_id, min(remaining, self.poll_s))


__all__ = [
    "POLL_S",
    "InteractionView",
    "JournalInteractionBridge",
    "binding_digest",
    "requested_event",
    "said_in",
]
