"""Outbound context carriers.

A call that leaves this process (an HTTP request to another service, a message
put on the broker) already carries the request id. A plug-in that keeps more
context for the request registers a carrier here, and its fields travel with
the same calls: ``inject_request_headers`` and ``inject_envelope`` in
``app.utils.request_context`` hand every carrier the headers or the envelope
they are about to return, and ``context_from_envelope`` shows it a message that
arrived.

Nothing is registered by default, and those functions then behave exactly as
they did without this module. This module imports nothing from the application.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

HTTP: Literal["http"] = "http"
MESSAGE: Literal["message"] = "message"
Kind = Literal["http", "message"]


class OutboundContextCarrier(Protocol):
    # Lower-case names of the HTTP headers this carrier may add, for code that forwards only an allow-list of headers.
    http_headers: tuple[str, ...]

    def inject(self, carrier: dict[str, Any], kind: Kind) -> None:
        """Add fields to ``carrier``, in place; it is already a copy. Never overwrite a field that is present.
        For ``http`` every value must be a string; anything else is dropped."""

    def accept(self, message: Mapping[str, Any]) -> None:
        """A message envelope arrived and is about to be handled in the calling context."""


# Replaced whole on every change, never edited in place: an inject that is walking the tuple is not disturbed.
_carriers: tuple[OutboundContextCarrier, ...] = ()
_lock = threading.Lock()


def register(carrier: OutboundContextCarrier) -> Callable[[], None]:
    """Add ``carrier``. Returns a function that removes it; calling it twice is harmless."""
    global _carriers  # noqa: PLW0603 - the registry
    with _lock:
        _carriers = (*_carriers, carrier)

    def unregister() -> None:
        global _carriers  # noqa: PLW0603 - the registry
        with _lock:
            _carriers = tuple(c for c in _carriers if c is not carrier)

    return unregister


def registered() -> bool:
    return bool(_carriers)


def header_names() -> tuple[str, ...]:
    """Lower-case names of every HTTP header a registered carrier may add."""
    return tuple(dict.fromkeys(name.lower() for carrier in _carriers for name in carrier.http_headers))


def inject(carrier: dict[str, Any], kind: Kind) -> None:
    """Let every registered carrier add its fields to ``carrier``. Never raises: a carrier that fails is skipped."""
    for registered_carrier in _carriers:
        before = dict(carrier)
        try:
            registered_carrier.inject(carrier, kind)
        except Exception:
            carrier.clear()
            carrier.update(before)
            continue
        for key in list(carrier):
            if key in before:
                carrier[key] = before[key]  # a carrier adds; what was there stays
            elif kind == HTTP and not isinstance(carrier[key], str):
                del carrier[key]


def accept_message(message: Mapping[str, Any]) -> None:
    """Show an arrived message envelope to every registered carrier. Never raises."""
    for registered_carrier in _carriers:
        try:
            registered_carrier.accept(message)
        except Exception:
            continue


def reset() -> None:
    """Forget every carrier. For tests."""
    global _carriers  # noqa: PLW0603 - the registry
    with _lock:
        _carriers = ()
