"""Collect the platform events code under test emits.

    with captured_events("record.retention.swept") as events:
        ...
    assert events == [("record.retention.swept", {"kind": "revision", "outcome": "kept"})]

With no names, every event is collected. ``facts(events, name)`` gives the payloads of one event.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app import platform_events

if TYPE_CHECKING:
    from collections.abc import Iterator

Captured = list[tuple[str, dict[str, Any]]]


@contextmanager
def captured_events(*names: str) -> Iterator[Captured]:
    events: Captured = []

    def collect(name: str, payload) -> None:
        events.append((name, dict(payload)))

    unsubscribes = [platform_events.on(name, collect) for name in (names or ("*",))]
    try:
        yield events
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()


def facts(events: Captured, name: str) -> list[dict[str, Any]]:
    return [payload for event, payload in events if event == name]


def _positional(function: Any, fields: tuple[str, ...]) -> platform_events.Handler:  # noqa: ANN401
    def handle(_name: str, payload) -> None:
        if fields:
            function(*[payload[field] for field in fields if field in payload])
        else:
            function(**payload)

    return handle


@contextmanager
def event_calls(name: str, *fields: str, to: Any = None) -> Iterator[Any]:  # noqa: ANN401
    """Call a mock (or ``to``) once per ``name`` event, instead of whoever listens to it.

    The listed fields are passed positionally, in that order, leaving out a field the
    emitter did not send; with no fields listed, every field is passed by keyword.

        with event_calls("record.soft_deleted", "source", "count") as counted:
            ...
        counted.assert_called_once_with("user", 3)
    """
    from unittest.mock import MagicMock

    target = MagicMock() if to is None else to
    listeners = platform_events._listeners
    previous = listeners.get(name)
    listeners[name] = (_positional(target, fields),)
    try:
        yield target
    finally:
        if previous is None:
            listeners.pop(name, None)
        else:
            listeners[name] = previous


def replace_event_listener(monkeypatch, name: str, function: Any, *fields: str) -> None:  # noqa: ANN401
    """``event_calls`` for a test that uses ``monkeypatch``: undone with the rest of its patches."""
    monkeypatch.setattr(
        platform_events, "_listeners", {**platform_events._listeners, name: (_positional(function, fields),)}
    )
