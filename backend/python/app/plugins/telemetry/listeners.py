"""Platform events this plug-in listens to, and what each one records.

Every handler is non-blocking: it increments a counter, observes a histogram,
sets a gauge or appends to the bounded event buffer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cache
from typing import Any

from app import platform_events

Subscribe = Callable[[str, platform_events.Handler], Callable[[], None]]
Payload = Mapping[str, Any]


def subscribe(on: Subscribe) -> None:
    """Subscribe every handler through ``on`` unless this process already has.

    Several services are imported into one process in tests, each loading the
    plug-ins; a second set of the same handlers would count every event twice.
    """
    for name, handler in handlers().items():
        if not platform_events.is_listening(name, handler):
            on(name, handler)


@cache
def handlers() -> dict[str, platform_events.Handler]:
    """Event name -> handler. One handler per event, built once."""
    return {}
