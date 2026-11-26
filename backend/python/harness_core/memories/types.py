"""Governed memory (P7.3): what a run is given from memory and what it may propose.

The core knows no memory store and no label algebra: a recalled memory carries
its label as the host's wire value, which the host joins into the run's own
label. Who may be given which memory is decided before the run starts, by the
host, with the actor's own access; the core renders what it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

MemoryScope = Literal["user", "session", "agent"]
MemoryKind = Literal["fact", "preference"]
MEMORY_SCOPES: tuple[MemoryScope, ...] = ("user", "session", "agent")
MEMORY_KINDS: tuple[MemoryKind, ...] = ("fact", "preference")


@dataclass(frozen=True)
class RecalledMemory:
    memory_id: str
    scope: MemoryScope
    kind: MemoryKind
    content: str
    # The host's label (P2.5a ``PipeshubTurnLabel`` for PipesHub), opaque here.
    label: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])


@dataclass(frozen=True)
class MemoryProposal:
    content: str
    kind: MemoryKind
    scope: MemoryScope
    # The memory this one corrects (FIX-R2 C2): one the run was given, or proposed itself, in the same scope.
    # Once the host accepts the correction the old memory is kept for audit and erasure but no longer recalled.
    replaces: str | None = None


@dataclass(frozen=True)
class ProposedMemory:
    memory_id: str
    # ``pending`` until the run ends and the host settles it.
    status: str
    created: bool


class MemoryRefused(Exception):
    """The host refused a proposal; ``detail`` is its code (scope not granted, invalid, over the run's cap)."""

    reason_code: ClassVar[str] = "memory_refused"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class MemoryUnavailable(Exception):
    """The memory store could not be reached; the run goes on without what it asked."""

    reason_code: ClassVar[str] = "memory_unavailable"


__all__ = [
    "MEMORY_KINDS",
    "MEMORY_SCOPES",
    "MemoryKind",
    "MemoryProposal",
    "MemoryRefused",
    "MemoryScope",
    "MemoryUnavailable",
    "ProposedMemory",
    "RecalledMemory",
]
