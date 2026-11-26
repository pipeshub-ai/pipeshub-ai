"""How a worker fits a step's events into commits the journal accepts.

A commit carries at most ``MAX_COMMIT_EVENTS`` events (the contract's
``journal.maxCommitEvents``; Node refuses more). A step that makes more is sent
as consecutive commits under the same attempt and fence. ``pack`` decides where
they are cut: a ``CommitUnit`` is what one piece of work journals and stays in
one commit, so a crash between two commits leaves that work either journaled
whole or not at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from pydantic import JsonValue

from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

MAX_COMMIT_EVENTS: Final[int] = RELEASE_DEFAULTS.journal.max_commit_events
STATE_SAVED: Final = "loop.state_saved"
_NO_SLOTS: Final[Mapping[str, JsonValue]] = MappingProxyType({})


@dataclass(frozen=True)
class CommitUnit:
    """Events that commit together, and the slots they set.

    The slots of every unit in a part are journaled as one ``loop.state_saved``
    at the part's end, so a unit's slots are never in a later commit than its
    events.
    """

    events: tuple[NewEvent, ...]
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    @property
    def size(self) -> int:
        return len(self.events)


def units(events: Iterable[NewEvent | CommitUnit]) -> list[CommitUnit]:
    """Each bare event is a unit of its own."""
    return [e if isinstance(e, CommitUnit) else CommitUnit((e,)) for e in events]


def pack(batch: Sequence[NewEvent | CommitUnit], limit: int = MAX_COMMIT_EVENTS) -> list[list[NewEvent]]:
    """``batch`` as consecutive parts of at most ``limit`` events, in order.

    A unit larger than a commit cannot be atomic: it is cut at event
    boundaries, first events first, and its slots go with its first piece.
    """
    if limit < 2:
        # One event and the slots it sets must fit together.
        raise ValueError(f"a commit holds at least two events, got {limit}")
    parts: list[list[NewEvent]] = []
    current: list[NewEvent] = []
    slots: dict[str, JsonValue] = {}

    def close() -> None:
        nonlocal current, slots
        if slots:
            current.append(NewEvent(STATE_SAVED, {"slots": slots}))
        if current:
            parts.append(current)
        current, slots = [], {}

    def room(adding: Mapping[str, JsonValue]) -> int:
        return limit - len(current) - (1 if slots or adding else 0)

    for unit in units(batch):
        if unit.size > room(unit.slots):
            close()
        pending = list(unit.events)
        adding: Mapping[str, JsonValue] = unit.slots
        while len(pending) > room(adding):
            take = room(adding)
            current.extend(pending[:take])
            slots.update(adding)
            pending, adding = pending[take:], _NO_SLOTS
            close()
        current.extend(pending)
        slots.update(adding)
    close()
    return parts


__all__ = ["MAX_COMMIT_EVENTS", "STATE_SAVED", "CommitUnit", "pack", "units"]
