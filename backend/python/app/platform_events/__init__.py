"""Platform events: application code announces what happened; plug-ins listen.

Application code never imports a plug-in. It calls ``emit`` with a product
term from ``app.platform_events.catalog`` and the facts of what happened::

    from app import platform_events

    platform_events.emit("search.performed", org_id=org_id, query_count=3)

A plug-in subscribes in its ``register(ctx)`` (see ``app.plugins``)::

    unsubscribe = platform_events.on("search.performed", handler)

Rules, on both sides:

* ``emit`` is synchronous and returns ``None``. It never raises to the caller:
  an exception in a listener is swallowed and counted (``listener_errors``).
* ``emit`` never blocks. It takes no lock and does no I/O, so it is safe on a
  hot path, from any thread, and from asyncio code without ``await``.
* A listener is called inline, on the emitter's thread (inside the event loop
  when the emitter is a coroutine). It must be non-blocking: no I/O, no
  ``await``, no lock held across slow work. A listener that has slow work
  hands the payload to its own queue, task or thread and returns.
* Listeners of one name run in the order they subscribed, then the ``"*"``
  listeners in the order they subscribed.
* A payload carries product facts (ids, kinds, statuses, counts, durations in
  seconds), never content, callables or live objects. A listener must not
  change it.
* With no listener for a name (and no ``"*"`` listener) an emit costs two
  dict lookups. To skip work that only exists to build a payload, ask
  ``has_listeners(name)`` first.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

Handler = Callable[[str, Mapping[str, Any]], None]

WILDCARD = "*"

# Read without a lock by ``emit``; a name's tuple is replaced whole (never changed
# in place) under ``_lock`` by ``on`` and its unsubscribe, so a reader sees one
# consistent tuple. The ``"*"`` listeners are kept under that key.
_listeners: dict[str, tuple[Handler, ...]] = {}
_lock = threading.Lock()
_errors: dict[str, int] = {}


def emit(name: str, /, **payload: Any) -> None:  # noqa: ANN401 - a payload value is any product fact
    """Announce that ``name`` happened. Synchronous, never raises, never blocks."""
    handlers = _listeners.get(name)
    wildcard = _listeners.get(WILDCARD)
    if handlers is None:
        if wildcard is None:
            return
        handlers = wildcard
    elif wildcard is not None:
        handlers = handlers + wildcard
    for handler in handlers:
        try:
            handler(name, payload)
        except Exception:
            _count_error(name)


def on(name: str, handler: Handler) -> Callable[[], None]:
    """Call ``handler(name, payload)`` for every ``name`` emitted (``"*"``: every event).

    Returns a function that removes this subscription; calling it twice is harmless.
    """
    with _lock:
        _listeners[name] = (*_listeners.get(name, ()), handler)
    active = True

    def unsubscribe() -> None:
        nonlocal active
        with _lock:
            if not active:
                return
            active = False
            rest = _without(_listeners.get(name, ()), handler)
            if rest:
                _listeners[name] = rest
            else:
                _listeners.pop(name, None)

    return unsubscribe


def has_listeners(name: str) -> bool:
    """True when an emit of ``name`` would reach a listener."""
    return name in _listeners or WILDCARD in _listeners


def listener_errors() -> dict[str, int]:
    """Exceptions raised by listeners since the process started, by event name."""
    with _lock:
        return dict(_errors)


def reset() -> None:
    """Drop every subscription and error count. For tests."""
    with _lock:
        _listeners.clear()
        _errors.clear()


def _without(handlers: tuple[Handler, ...], handler: Handler) -> tuple[Handler, ...]:
    for index, candidate in enumerate(handlers):
        if candidate is handler:
            return handlers[:index] + handlers[index + 1:]
    return handlers


def _count_error(name: str) -> None:
    with _lock:
        _errors[name] = _errors.get(name, 0) + 1


__all__ = ["WILDCARD", "Handler", "emit", "has_listeners", "listener_errors", "on", "reset"]
