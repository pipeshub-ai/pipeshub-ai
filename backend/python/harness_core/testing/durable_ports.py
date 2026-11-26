"""In-memory adapters of the durable kernel's run ports, the reference each contract in
``harness_core.testing.contracts.durable_ports`` and ``durable_turns`` runs against first.

Each does the least its port allows while keeping every promise the kernel relies on:
results are JSON the journal holds, decisions are functions of the journal, events are
returned, never written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import TextPart
from harness_core.kernel.durable.context_compiler import default_bands
from harness_core.kernel.durable.ports import RenderedInput, TurnPlan
from harness_core.models.errors import ModelRouteNotPinned

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import Amount, Reservation, RunAccountRef
    from harness_core.context.types import HostBands
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable.ports import (
        DurableModelPort,
        ModelCall,
        RunEnding,
        RunEndingOutcome,
        SteeringInput,
        TurnResult,
    )
    from harness_core.kernel.fold import RunState
    from harness_core.models.policy import PolicyRoute

# The ``loop.state_saved`` events ``SlotRunRestore`` rebuilds its state from.
SLOTS_EVENT = "loop.state_saved"


@dataclass(frozen=True)
class StaticPreRunStep:
    """``PreRunStep`` that settles on ``result`` whatever the input."""

    key: str
    result: JsonValue = None

    async def run(self, input_text: str) -> JsonValue:
        return self.result


@dataclass
class StaticPostRunStep:
    """``PostRunStep`` that records each ending it was given and reports its outcome."""

    key: str
    endings: list[RunEnding] = field(default_factory=list["RunEnding"])

    async def run(self, ending: RunEnding) -> JsonValue:
        self.endings.append(ending)
        return {"outcome": ending.outcome}


@dataclass(frozen=True)
class SlotPreRunGate:
    """``PreRunGate`` that asks when the journaled ``slot`` holds a truthy value."""

    slot: str

    def needs_input(self, slots: Mapping[str, JsonValue]) -> bool:
        return bool(slots.get(self.slot))


@dataclass
class StaticCapabilityCheck:
    """``CapabilityCheck`` over a table of digests a test may change between attempts."""

    digests: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # What the kernel asked it to withhold (WP79-FU-5).
    withheld: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    async def observe(self) -> Mapping[str, JsonValue]:
        return dict(self.digests)

    async def withhold(self, marks: Mapping[str, JsonValue]) -> None:
        self.withheld.update(marks)
        self.digests.update(marks)


class SilentRunPresenter:
    """``RunPresenter`` that presents nothing: the kernel's facts are the whole projection."""

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return []

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        return []


class SlotRunRestore:
    """``RunRestore`` of a product that keeps the run's slots in process: ``save`` returns
    the event that journals a change, ``restore`` folds those events back."""

    def __init__(self) -> None:
        self.slots: dict[str, JsonValue] = {}

    def save(self, slots: Mapping[str, JsonValue]) -> NewEvent:
        self.slots.update(slots)
        return NewEvent(SLOTS_EVENT, {"slots": dict(slots)}, critical=False)

    async def restore(self, events: Sequence[JournalEvent]) -> None:
        for event in events:
            saved = event.data.get("slots") if event.type == SLOTS_EVENT else None
            if isinstance(saved, dict):
                self.slots.update(saved)


class PlainInputRenderer:
    """``InputRenderer`` without stored references: the message's text, as a new turn's would be."""

    async def render(self, message: SteeringInput) -> RenderedInput:
        return RenderedInput(parts=[TextPart(text=message.text)])


class PassThroughTurnShaper:
    """``TurnShaper`` of a product that shapes nothing: each turn sends what the kernel built."""

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        return call

    async def enter(self, turn: int, state: RunState) -> None:
        return None


class AcceptingResponseReview:
    """``ResponseReview`` that accepts every answer as it is."""

    async def review(self, message: AssistantMessage, turn: int, state: RunState) -> str | None:
        return None


class IdentityChildResultShaper:
    """``ChildResultShaper`` of a product that adds nothing to a child's result."""

    async def shape(self, document: dict[str, JsonValue], outcome: RunEndingOutcome) -> dict[str, JsonValue]:
        return dict(document)


class IdleLoopDirector:
    """``LoopDirector`` of a strategy without phases: the model takes every turn."""

    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        return TurnPlan()


class DefaultContextHost:
    """``ContextHost`` without bands of its own: the kernel's system and tools, ids by name."""

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        return default_bands(call)


@dataclass(frozen=True)
class StaticRoutePorts:
    """``RoutePorts`` over a table of ports by route id; a route it lacks is not pinned here."""

    ports: Mapping[str, DurableModelPort]

    async def port(self, route: PolicyRoute) -> DurableModelPort:
        found = self.ports.get(route.route_id)
        if found is None:
            raise ModelRouteNotPinned(f"no port for route {route.route_id!r}")
        return found



@dataclass
class LedgerAfterRunStep:
    """``AfterRunStep`` over a budget ledger: an answered run reserves ``cost`` while it is prepared, the work commits
    it (or, with ``fail``, releases it and raises), a discard releases it. ``done`` records the work done."""

    accounts: BudgetAccounts
    account: RunAccountRef
    cost: Amount
    key: str = "after_answer"
    fail: bool = False
    done: list[str] = field(default_factory=list[str])
    _n: int = 0

    async def prepare(self, ending: RunEnding) -> object | None:
        if ending.outcome != "answered":
            return None
        self._n += 1
        return await self.accounts.reserve(
            self.account, f"{self.key}-{self._n}", self.cost, minimum=self.cost, ttl_s=60
        )

    async def run(self, prepared: object) -> None:
        reservation = cast("Reservation", prepared)
        if self.fail:
            _ = await self.accounts.release(reservation)
            raise RuntimeError("the after-run work failed")
        _ = await self.accounts.commit(reservation, self.cost)
        self.done.append(reservation.key)

    async def discard(self, prepared: object) -> None:
        _ = await self.accounts.release(cast("Reservation", prepared))

__all__ = [
    "SLOTS_EVENT",
    "LedgerAfterRunStep",
    "AcceptingResponseReview",
    "DefaultContextHost",
    "IdentityChildResultShaper",
    "IdleLoopDirector",
    "PassThroughTurnShaper",
    "PlainInputRenderer",
    "SilentRunPresenter",
    "SlotPreRunGate",
    "SlotRunRestore",
    "StaticCapabilityCheck",
    "StaticPostRunStep",
    "StaticPreRunStep",
    "StaticRoutePorts",
]
