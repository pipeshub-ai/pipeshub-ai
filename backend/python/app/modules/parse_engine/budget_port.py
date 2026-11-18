"""The member budget as the class gate and the engines use it.

The ledger (``app.capacity.member_budget``) is the only implementation. This module re-exports the
ledger's own value types (``Demand``, ``Family``, the two errors) so there is one definition of each,
and writes its permit API down as ``Protocol`` classes so the gate is built and tested against the
contract: the real ledger and the in-memory fake in ``tests/support`` both satisfy it, and one
contract suite runs against both.

Where this description and the ledger differ, the ledger wins, except
``CancelToken.cancelled`` which is a method in the contract and now in the ledger too. The
ledger's ``snapshot()`` returns a plain mapping (``cpu_tokens``, ``used``, ``held_floor``,
``families``, ``by_class`` ...), not a dataclass; the gate reads only ``cpu_tokens`` and
``held_floor`` from it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.capacity.budget_policy import Demand, Family
from app.capacity.classes import CostClass
from app.capacity.member_budget import AdmissionTimeout, Cancelled

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AdmissionTimeout",
    "CancelToken",
    "Cancelled",
    "CostClass",
    "Demand",
    "Family",
    "MemberBudget",
    "Permit",
    "capacity_of",
    "held_floors_of",
]


@runtime_checkable
class CancelToken(Protocol):
    def cancelled(self) -> bool: ...


@runtime_checkable
class Permit(Protocol):
    family: Family
    cpu: int
    mem_bytes: int
    cls: str | None

    def release(self) -> None:
        """Idempotent."""

    def adopt(self) -> Permit:
        """The lane or pool running the unit takes over the release; it never reserves a second time."""


class MemberBudget(Protocol):
    def try_reserve(self, d: Demand) -> Permit | None:
        """None means no capacity now; it never raises "busy"."""

    async def reserve(
        self, d: Demand, *, deadline_s: float | None = None, cancel: CancelToken | None = None
    ) -> Permit:
        """``deadline_s=None`` is allowed only with ``cancel``, else ``ValueError``.
        Raises ``AdmissionTimeout`` or ``Cancelled``."""

    def snapshot(self) -> Mapping[str, Any]: ...


def capacity_of(snapshot: Mapping[str, Any]) -> int:
    """C: the member's CPU tokens."""
    return int(snapshot["cpu_tokens"])


def held_floors_of(snapshot: Mapping[str, Any]) -> int:
    """The sum of the held floors: the tokens big work never takes."""
    return int(snapshot["held_floor"])
