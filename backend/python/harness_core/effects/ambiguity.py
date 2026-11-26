"""Sends whose outcome a tool could not learn: the signal behind ``EffectUnknown``.

Most tools catch their own exceptions and return an error, so an exception
cannot carry "the request may have been processed" up to the runner. The
client layer that sent the request knows (a read timeout after a ``POST``, a
connection reset before the response): it marks the call's tracker, and the
executor that ran the tool reads the mark. A failed call with a marked send is
``EffectUnknown``; a failed call without one is a definite error, which dedupe
may run again.

The tracker lives in a context variable, so tasks the tool spawns mark the
same call; outside a tracked call marking does nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.effects.types import EffectUnknown

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class SendTracker:
    """What one tool call's client layers reported; ``reasons`` name the failure kinds only."""

    parent: SendTracker | None = None
    reasons: list[str] = field(default_factory=list[str])

    @property
    def ambiguous(self) -> bool:
        return bool(self.reasons)

    def mark(self, reason: str) -> None:
        # A nested call (a delegate running tools) is part of the outer call's effect too.
        tracker: SendTracker | None = self
        while tracker is not None:
            tracker.reasons.append(reason)
            tracker = tracker.parent


_current: ContextVar[SendTracker | None] = ContextVar("harness_send_tracker", default=None)


@contextmanager
def track_sends() -> Iterator[SendTracker]:
    tracker = SendTracker(parent=_current.get())
    token = _current.set(tracker)
    try:
        yield tracker
    finally:
        _current.reset(token)


def note_ambiguous_send(reason: str) -> None:
    """Called by a client layer when a request may have been processed but no answer came back."""
    tracker = _current.get()
    if tracker is not None:
        tracker.mark(reason)


class EffectUnknownFromSend(EffectUnknown):
    """A tool call failed after a send its client could not confirm or rule out."""


def unknown_effect(tracker: SendTracker, cause: str) -> EffectUnknownFromSend:
    return EffectUnknownFromSend(f"{cause}: {', '.join(sorted(set(tracker.reasons)))}")


__all__ = [
    "EffectUnknownFromSend",
    "SendTracker",
    "note_ambiguous_send",
    "track_sends",
    "unknown_effect",
]
