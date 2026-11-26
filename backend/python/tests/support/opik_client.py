"""Keeping the Opik SDK's process client inside the test that built it.

The first span opened while Opik is turned on builds a process-wide client whose daemon threads
(senders, a replay manager pinging the server every ten seconds, a flusher) outlive the test. With
no Opik server configured they keep dialling ``www.comet.com:443`` for the rest of the xdist
worker, and a later test that patches ``socket.socket.connect`` saw those dials as its own (H8).
"""

from __future__ import annotations

import contextlib
import sys
import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Iterator

_CLIENT_MODULE = "opik.api_objects.opik_client"
# Above Opik's connection-check timeout (5 s), so a ping in flight finishes.
_THREAD_EXIT_TIMEOUT_S = 10.0


def _current() -> Any:  # noqa: ANN401
    module = sys.modules.get(_CLIENT_MODULE)
    return None if module is None else module.get_current_client_raw()


@contextlib.contextmanager
def opik_global_client_ended() -> Iterator[None]:
    """End a process client built inside the block and put back the one that was there before.

    Looks the SDK up in ``sys.modules`` so a test that never touches Opik does not import it."""
    before = _current()
    threads_before = set(threading.enumerate())
    try:
        yield
    finally:
        built = _current()
        if built is not None and built is not before:
            from opik.api_objects import opik_client

            if before is None:
                opik_client.reset_global_client(end_client=False)
            else:
                opik_client.set_global_client(before)
            built.end(flush=False)
            # end() only signals its threads; a ping already in flight still dials into the next test.
            for thread in set(threading.enumerate()) - threads_before:
                if type(thread).__module__.startswith("opik."):
                    thread.join(timeout=_THREAD_EXIT_TIMEOUT_S)


@contextlib.contextmanager
def _offline_span(**_kwargs: Any) -> Iterator[SimpleNamespace]:  # noqa: ANN401
    yield SimpleNamespace()


@contextlib.contextmanager
def opik_spans_offline() -> Iterator[None]:
    """Opik's span and trace entry points answered locally, for tests that turn Opik on to check
    wiring: no client is built and nothing is sent."""
    with (
        patch("opik.start_as_current_span", side_effect=lambda **kw: _offline_span(**kw)),
        patch("opik.start_as_current_trace", side_effect=lambda **kw: _offline_span(**kw)),
    ):
        yield


@dataclass
class RecordedOpikSpan:
    """What a span or trace handed Opik: its opening arguments and what was set on it before it closed."""

    opened: dict[str, Any]
    span: SimpleNamespace = field(default_factory=SimpleNamespace)


@contextlib.contextmanager
def opik_spans_recorded() -> Iterator[list[RecordedOpikSpan]]:
    """As ``opik_spans_offline``, keeping every span and trace for a test to read what Opik would receive."""
    recorded: list[RecordedOpikSpan] = []

    @contextlib.contextmanager
    def record(**kwargs: Any) -> Iterator[SimpleNamespace]:  # noqa: ANN401
        entry = RecordedOpikSpan(dict(kwargs))
        recorded.append(entry)
        yield entry.span

    with (
        patch("opik.start_as_current_span", side_effect=record),
        patch("opik.start_as_current_trace", side_effect=record),
    ):
        yield recorded
