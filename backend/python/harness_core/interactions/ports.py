"""Interaction port the effect runner holds approvals through (ARCHITECTURE §4.5).

The holder asks once, then waits at most ``within_s``; ``None`` means "not
decided yet", and the caller defers (ends the engine turn and suspends) rather
than holding longer. Ids are opaque: a tenant, a scope and a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from harness_core.journal.types import InteractionDecision

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.interactions.connect import ConnectNeed

# connect (G-12): the call waits for its owner to connect the item it needs.
InteractionKind = Literal["approval", "question", "connect"]


@dataclass(frozen=True, slots=True)
class RunBinding:
    tenant: str
    scope: str
    run_id: str


@dataclass(frozen=True, slots=True)
class InteractionRequest:
    interaction_id: str
    kind: InteractionKind
    subject: str
    args_digest: str
    # A ``connect`` request's item, journaled with it for the person's card.
    connect: ConnectNeed | None = None
    # An approval's reason (``interaction.explained``), journaled before it in the same command (WP-97).
    reason: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class InteractionTicket:
    interaction_id: str
    binding: RunBinding


class InteractionBridge(Protocol):
    async def request(self, binding: RunBinding, req: InteractionRequest) -> InteractionTicket:
        """Idempotent per ``interaction_id``: a second request returns the same ticket
        and records nothing new, so a re-call after a deferral finds the first one."""
        ...

    async def wait(
        self, ticket: InteractionTicket, *, within_s: float
    ) -> InteractionDecision | None:
        """The decision, at once if it is already made; ``None`` after ``within_s``."""
        ...


@runtime_checkable
class SaysWhy(Protocol):
    """An optional facet of a bridge or view: what the person typed with a decision (a denial's reason, AP-05)."""

    def said(self, interaction_id: str) -> str | None: ...


class InteractionWakeup(Protocol):
    """A hint that a run's interactions may have changed (Node's ``resolveActive``).

    Only a hint: the journal is the truth, and a holder re-reads it after every
    wake and every ``timeout_s``, so a lost wake costs one poll interval."""

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        """``True`` when woken before ``timeout_s``."""
        ...


__all__ = [
    "InteractionBridge",
    "InteractionDecision",
    "InteractionKind",
    "InteractionRequest",
    "InteractionTicket",
    "InteractionWakeup",
    "RunBinding",
    "SaysWhy",
]
